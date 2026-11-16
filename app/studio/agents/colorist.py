"""Agent Kolorysta & Lighting Artist: analiza ekspozycji, CST LOG→Rec.709, ratowanie prześwietleń,
shot matching, ochrona skóry, looki i kompilacja LUT - równolegle per klip, na końcu atomowy zapis osi.

Graf zadań (agent "colorist"):
  Analiza ekspozycji (per klip) ─┬─ Konwersja LOG (CST) ─┬─ Shot Matching ─┐
                                 ├─ Ratowanie prześwietleń┘                 ├─ Kompilacja LUT (per klip) ─ Zapis osi
                                 └─ Ochrona skóry & Look ───────────────────┘
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from .. import paths
from ..color import analyze
from ..color.filters import grade_chain, grade_is_neutral
from ..color.looks import is_monochrome, look_exists, normalize_look
from ..color.lut import (LOG_PROFILES, LUMA, PROFILE_NAMES, _base, compile_grade_lut, describe_plan, grade_hash,
                         plan_grade)
from ..config import get_config
from ..events import log
from ..media import ffmpeg
from ..project.model import Grade, MediaItem, Timeline, VClip
from ..tasks import Task, TaskContext, orchestrator

AGENT = "colorist"
SAMPLES_BY_EFFORT = [4, 6, 8, 12]


def _pl(v: float, nd: int = 1) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def _lut_size(level: int) -> int:
    return 65 if level >= 3 else 33


def _clip_name(c: VClip, m: MediaItem | None) -> str:
    return c.label or (m.name if m else c.id)


def _lut_path(project, clip_id: str, grade: Grade, stats: dict, ref: dict | None, size: int) -> Path:
    return Path(project.cache) / "luts" / f"{clip_id}_{grade_hash(grade, stats, ref, size)}.cube"


def _predicted_luma(grade: Grade, stats: dict) -> float:
    """Przewidywana średnia luminancja po korekcji technicznej (z histogramu)."""
    plan = plan_grade(grade, stats)
    hist = np.asarray(stats.get("hist") or [], float)
    if hist.size < 4 or hist.sum() <= 0:
        return float(plan.get("target_luma", 0.42))
    c = (np.arange(hist.size) + 0.5) / hist.size
    out = _base(np.repeat(c[:, None], 3, axis=1), plan, plan["total_ev"]) @ LUMA
    return float((out * hist / hist.sum()).sum())


def _highlight_amount(stats: dict, media: MediaItem) -> tuple[float, float]:
    """(highlight_recovery 0..1, % przepaleń) - z analizy klipu i flag archiwisty."""
    clipped = float(stats.get("clipped_pct", 0.0))
    if "NEEDS_EXPOSURE_RECOVERY" in (media.flags or []):
        try:
            clipped = max(clipped, float(media.flag_details.get("clipped_pct", 0.0)))  # procenty (0..100)
        except (TypeError, ValueError):
            pass
    luma = float(stats.get("luma_mean", 0.4))
    if clipped < 1.5 and luma <= 0.72 and "NEEDS_EXPOSURE_RECOVERY" not in (media.flags or []):
        return 0.0, clipped
    hr = 0.3 + clipped / 25.0
    if luma > 0.72:
        hr = max(hr, 0.35)
    return float(np.clip(hr, 0.25, 1.0)), clipped


def _scene_groups(order: list[str], stats: dict[str, dict], media_of: dict[str, str],
                  profiles: dict[str, str]) -> list[list[str]]:
    """Kolejne klipy tej samej lokacji: to samo źródło lub podobne statystyki Lab i ten sam profil."""
    groups: list[list[str]] = []
    for cid in order:
        st = stats.get(cid)
        if st is None:
            continue
        if groups:
            prev = groups[-1][-1]
            ps = stats[prev]
            same_media = media_of[cid] == media_of[prev]
            lm, pm = np.array(st["lab_mean"], float), np.array(ps["lab_mean"], float)
            d_ab = float(np.hypot(lm[1] - pm[1], lm[2] - pm[2]))
            d_l = abs(lm[0] - pm[0])
            hist_sim = float(np.minimum(np.array(st["hist"]), np.array(ps["hist"])).sum())
            similar = d_ab < 7.0 and d_l < 18.0 and hist_sim > 0.55
            if profiles.get(cid) == profiles.get(prev) and (same_media or similar):
                groups[-1].append(cid)
                continue
        groups.append([cid])
    return groups


def auto_grade_tasks(project, timeline: Timeline, *, look: str | None, group: str, deps: list[str],
                     clip_ids: list[str] | None = None) -> list[Task]:
    """Automatyczny grading osi (patrz ARCHITECTURE.md → Kolor). Ostatnie zadanie zwraca numer wersji osi."""
    pid = project.id
    tl = timeline.model_copy(deep=True).normalize()
    order: list[str] = []
    clips: dict[str, VClip] = {}
    media: dict[str, MediaItem] = {}
    for track in ("V1", "V2"):
        for c in tl.v(track):
            if clip_ids is not None and c.id not in clip_ids:
                continue
            try:
                m = project.get_media(c.media_id)
            except KeyError:
                continue
            if m.kind not in ("video", "image") or not Path(m.path).exists():
                continue
            order.append(c.id)
            clips[c.id] = c
            media[c.id] = m
    look_n = normalize_look(look)
    lock = threading.Lock()
    S: dict[str, Any] = {"stats": {}, "profile": {}, "hr": {}, "clipped": {}, "match": {}, "look": {},
                         "grades": {}}

    if not order:
        def empty(ctx: TaskContext):
            ctx.log("Brak klipów wideo do korekcji koloru na osi")
            return project.timeline().version
        return [orchestrator.add(AGENT, "Kompilacja LUT", "Kolorysta: brak klipów", empty, project_id=pid,
                                 deps=list(deps), group=group)]

    # -- 1. analiza (równolegle per klip) ------------------------------------------------------
    def analysis_fn(cid: str):
        def fn(ctx: TaskContext):
            c, m = clips[cid], media[cid]
            n = SAMPLES_BY_EFFORT[max(0, min(3, ctx.effort_level))]
            ctx.progress(0.1, "próbkowanie klatek")
            t1 = None if m.kind == "image" else float(c.src_out)
            st = analyze.load_or_compute(Path(project.cache) / "color" / "stats", m.id, m.path,
                                         t0=float(c.src_in), t1=t1, samples=n)
            with lock:
                S["stats"][cid] = st
            ctx.log(f"{_clip_name(c, m)}: jasność {_pl(st['luma_mean'] * 100, 0)}%, przepalone "
                    f"{_pl(st['clipped_pct'])}%, zgniecione {_pl(st['crushed_pct'])}%, nasycenie "
                    f"{_pl(st['sat_mean'] * 100, 0)}%, skóra {_pl(st['skin_pct'], 0)}% kadru")
            return {k: st[k] for k in ("luma_mean", "clipped_pct", "crushed_pct", "skin_pct")}
        return fn

    t_an = [orchestrator.add(AGENT, "Analiza ekspozycji", f"Analiza: {_clip_name(clips[cid], media[cid])}",
                             analysis_fn(cid), project_id=pid, deps=list(deps), group=group) for cid in order]
    an_ids = [t.id for t in t_an]

    # -- 2a. CST -----------------------------------------------------------------------------------
    def cst_fn(ctx: TaskContext):
        n_log = 0
        for cid in order:
            st = S["stats"].get(cid)
            if st is None:
                continue
            c, m = clips[cid], media[cid]
            user = (c.grade.input_profile or "rec709").lower()
            prof = user if user not in ("rec709", "") else (m.analysis.log_profile or st.get("log_profile") or "")
            if "LOG" in (m.flags or []) and not prof:
                prof = "flat"
            prof = prof or "rec709"
            S["profile"][cid] = prof
            if prof in LOG_PROFILES:
                n_log += 1
                ctx.log(f"{_clip_name(c, m)}: wykryto {PROFILE_NAMES.get(prof, prof)} → CST do Rec.709 "
                        f"(macierz gamutu + filmowy tone-map z miękkim roll-off świateł)")
            elif prof == "flat":
                n_log += 1
                ctx.log(f"{_clip_name(c, m)}: płaski obraz (brak czerni i bieli, nasycenie "
                        f"{_pl(st['sat_mean'] * 100, 0)}%) → rozciągnięcie zakresu, kontrast i saturacja")
        if not n_log:
            ctx.log("Wszystkie klipy w Rec.709 - konwersja przestrzeni barw niepotrzebna")
        return n_log

    t_cst = orchestrator.add(AGENT, "Konwersja LOG (CST)", "Wykrywanie profili LOG/HDR", cst_fn, project_id=pid,
                             deps=an_ids, group=group, soft_deps=True)

    # -- 2b. ratowanie prześwietleń ------------------------------------------------------------------
    def hl_fn(ctx: TaskContext):
        n = 0
        for cid in order:
            st = S["stats"].get(cid)
            if st is None:
                continue
            c, m = clips[cid], media[cid]
            hr, clipped = _highlight_amount(st, m)
            S["hr"][cid] = hr
            S["clipped"][cid] = clipped
            lift = 0.0
            if "NEEDS_EXPOSURE_LIFT" in (m.flags or []) or st.get("crushed_pct", 0) > 12:
                lift = float(np.clip(st.get("crushed_pct", 0) / 40.0, 0.2, 0.6))
            S.setdefault("lift", {})[cid] = lift
            if hr > 0:
                n += 1
                plan = plan_grade(Grade(highlight_recovery=hr), st)
                ctx.log(f"{_clip_name(c, m)}: przepalone {_pl(clipped)}% - odzyskuję światła, "
                        f"{_pl(plan['hl_ev'])} EV, kolano {_pl(plan['knee'], 2)}, kolor w światłach "
                        f"(tone-map z zachowaniem proporcji), obszary w pełni przepalone zostają białe")
            if lift > 0:
                ctx.log(f"{_clip_name(c, m)}: zgniecione cienie {_pl(st.get('crushed_pct', 0))}% - "
                        f"podnoszę cienie ({_pl(lift, 2)})")
        if not n:
            ctx.log("Brak przepaleń wymagających ratowania")
        return n

    t_hl = orchestrator.add(AGENT, "Ratowanie prześwietleń", "Highlight recovery (float32)", hl_fn,
                            project_id=pid, deps=an_ids, group=group, soft_deps=True)

    def base_grade(cid: str) -> Grade:
        old = clips[cid].grade
        g = Grade(input_profile=S["profile"].get(cid, "rec709"), highlight_recovery=S["hr"].get(cid, 0.0),
                  shadow_lift=S.get("lift", {}).get(cid, 0.0), sky_darken=old.sky_darken, relight=old.relight)
        return g

    # -- 3. shot matching ---------------------------------------------------------------------------
    def match_fn(ctx: TaskContext):
        stats = S["stats"]
        media_of = {cid: media[cid].id for cid in order}
        n_matched = 0
        for track in ("V1", "V2"):
            tr_order = [cid for cid in order if any(c.id == cid for c in tl.v(track))]
            for grp in _scene_groups(tr_order, stats, media_of, S["profile"]):
                if len(grp) < 2:
                    continue
                def score(cid):
                    st = stats[cid]
                    return abs(st["luma_mean"] - 0.45) * 2 + st["clipped_pct"] / 20 + st["crushed_pct"] / 20
                ref = min(grp, key=score)
                ref_stats = stats[ref]
                target = _predicted_luma(base_grade(ref), ref_stats)
                names = ", ".join(_clip_name(clips[c], media[c]) for c in grp if c != ref)
                ctx.log(f"Scena ({len(grp)} klipy): referencja {_clip_name(clips[ref], media[ref])} "
                        f"(najlepiej naświetlona) → dopasowuję {names} (Lab, jasność docelowa "
                        f"{_pl(target * 100, 0)}%)")
                for cid in grp:
                    if cid == ref:
                        continue
                    S["match"][cid] = {"ref": {"clip_id": ref, "media_id": media[ref].id,
                                               "lab_mean": ref_stats["lab_mean"], "lab_std": ref_stats["lab_std"]},
                                       "strength": 0.6, "target_luma": round(target, 4), "target_band": 0.015}
                    n_matched += 1
        if not n_matched:
            ctx.log("Każdy klip to osobna scena - dopasowanie ujęć niepotrzebne")
        return n_matched

    t_match = orchestrator.add(AGENT, "Shot Matching", "Dopasowanie ujęć w scenach", match_fn, project_id=pid,
                               deps=[t_cst.id, t_hl.id], group=group, soft_deps=True)

    # -- 3b. skóra i look ---------------------------------------------------------------------------
    def skin_fn(ctx: TaskContext):
        lk = look_n
        if lk and not look_exists(lk):
            ctx.log(f"Nieznany look „{look}” - pomijam (dostępne: teal_orange, warm_film, clean_doc, moody, "
                    f"bw_film, vibrant_social, pastel lub plik .cube z LUTs)", level="warn")
            lk = None
        strength = 1.0 if is_monochrome(lk) else float(tl.look_strength or 0.8)
        for cid in order:
            st = S["stats"].get(cid)
            if st is None:
                continue
            skin = float(st.get("skin_pct", 0))
            sp = 0.85 if skin > 8 else 0.75 if skin > 2 else 0.6
            S["look"][cid] = {"look": lk, "look_strength": strength, "skin_protect": sp}
            if skin > 2 and lk and not is_monochrome(lk):
                ctx.log(f"{_clip_name(clips[cid], media[cid])}: skóra {_pl(skin, 0)}% kadru - ochrona odcieni "
                        f"skóry {_pl(sp, 2)} (linia skóry ~123° na wektoroskopie) przy looku {lk}")
        ctx.log(f"Look: {lk or 'brak (korekcja techniczna)'}" + (f", siła {int(strength * 100)}%" if lk else ""))
        if any(clips[c].grade.relight for c in order):
            log(AGENT, "Zachowuję ustawienia relightingu klipów (maska kierunkowa w filtrze renderu)",
                path="Relighting", project_id=pid)
        return lk

    t_skin = orchestrator.add(AGENT, "Ochrona skóry & Look", "Look i ochrona skóry", skin_fn, project_id=pid,
                              deps=an_ids, group=group, soft_deps=True)

    # -- 4. kompilacja LUT (równolegle per klip) ------------------------------------------------------
    def compile_fn(cid: str):
        def fn(ctx: TaskContext):
            st = S["stats"].get(cid)
            if st is None:
                raise RuntimeError("brak analizy klipu")
            c, m = clips[cid], media[cid]
            g = base_grade(cid)
            lk = S["look"].get(cid, {})
            g.look = lk.get("look")
            g.look_strength = lk.get("look_strength", 0.8)
            g.skin_protect = lk.get("skin_protect", 0.7)
            mt = S["match"].get(cid)
            g.match = dict(mt) if mt else {}
            if mt:
                g.match_to = mt["ref"]["media_id"]
            ref = mt["ref"] if mt else None
            size = _lut_size(ctx.effort_level)
            out = _lut_path(project, cid, g, st, ref, size)
            ctx.progress(0.3, f"LUT {size}³")
            if not out.exists():
                compile_grade_lut(g, st, out, size=size, ref_stats=ref)
            g.lut = str(out)
            plan = plan_grade(g, st, ref)
            with lock:
                S["grades"][cid] = g
            ctx.log(f"{_clip_name(c, m)}: {describe_plan(plan)} → {out.name}")
            return str(out)
        return fn

    t_comp = [orchestrator.add(AGENT, "Kompilacja LUT", f"LUT: {_clip_name(clips[cid], media[cid])}",
                               compile_fn(cid), project_id=pid, deps=[t_match.id, t_skin.id], group=group,
                               soft_deps=True) for cid in order]

    # -- 5. zapis na aktualnej osi ---------------------------------------------------------------------
    def merge_fn(ctx: TaskContext):
        grades: dict[str, Grade] = dict(S["grades"])
        if not grades:
            raise RuntimeError("Nie skompilowano żadnego LUT")
        applied: list[str] = []

        def mut(t: Timeline) -> Timeline:
            t.normalize()
            applied.clear()
            for cid, g in grades.items():
                hit = t.find_clip(cid)
                if not hit:
                    continue
                c = hit[1]
                if isinstance(c, VClip):
                    keep = c.grade
                    g2 = g.model_copy(deep=True)
                    # lokalne korekcje ustawione w międzyczasie przez użytkownika zostają
                    g2.relight = keep.relight if keep.relight is not None else g2.relight
                    g2.sky_darken = keep.sky_darken or g2.sky_darken
                    c.grade = g2
                    applied.append(cid)
            if look_n and look_exists(look_n):
                t.look = look_n
            return t

        new = project.mutate_timeline(mut, f"Kolorysta: grading {len(grades)} klipów"
                                      + (f" (look {look_n})" if look_n else ""), AGENT)
        missing = len(order) - len(grades)
        ctx.log(f"Zapisano grading {len(applied)} klipów na osi v{new.version}"
                + (f" ({missing} klipów pominięto - błąd analizy)" if missing else ""),
                level="warn" if missing else "info")
        return new.version

    t_merge = orchestrator.add(AGENT, "Kompilacja LUT", "Zapis gradingu na osi", merge_fn, project_id=pid,
                               deps=[t.id for t in t_comp], group=group, soft_deps=True)
    return [*t_an, t_cst, t_hl, t_match, t_skin, *t_comp, t_merge]


# ---------------------------------------------------------------------------------------------
# Polecenia z czatu
# ---------------------------------------------------------------------------------------------
_FIELDS = ("exposure", "temperature", "tint", "contrast", "saturation", "look", "look_strength", "sky_darken",
           "highlight_recovery", "shadow_lift", "skin_protect", "input_profile")
_RANGES = {"exposure": (-4.0, 4.0), "temperature": (-1.0, 1.0), "tint": (-1.0, 1.0), "contrast": (-1.0, 1.0),
           "saturation": (0.0, 2.0), "look_strength": (0.0, 1.0), "sky_darken": (0.0, 1.0),
           "highlight_recovery": (0.0, 1.0), "shadow_lift": (0.0, 1.0), "skin_protect": (0.0, 1.0)}


def _apply_params(g: Grade, params: dict) -> Grade:
    g = g.model_copy(deep=True)
    for k in _FIELDS:
        if k in params and params[k] is not None:
            v = params[k]
            if k in ("look", "input_profile"):
                v = normalize_look(v) if k == "look" else str(v)
            else:
                v = float(v)
            setattr(g, k, v)
        dk = f"{k}_delta"
        if dk in params and k in _RANGES and params[dk] is not None:
            setattr(g, k, float(getattr(g, k)) + float(params[dk]))
    for k, (lo, hi) in _RANGES.items():
        setattr(g, k, float(np.clip(float(getattr(g, k)), lo, hi)))
    if "look" in params and params["look"] in (None, "", "none", "brak"):
        g.look = None
    if "relight" in params:
        r = params["relight"]
        if not r or (isinstance(r, dict) and r.get("enabled") is False):
            g.relight = None
        elif isinstance(r, dict):
            g.relight = {**(g.relight or {}), **r}
    return g


def apply_color_command(project, timeline: Timeline, clip_ids: list[str], params: dict) -> Timeline:
    """Zmienia Grade wybranych klipów i rekompiluje ich LUT. Zwraca zmodyfikowaną kopię osi (bez commit).

    params: wartości bezwzględne (exposure, temperature, tint, contrast, saturation, look, look_strength,
    sky_darken, highlight_recovery, shadow_lift, skin_protect, input_profile, relight{direction,strength,color})
    lub względne z sufiksem `_delta` (np. exposure_delta=+0.5). Pusta lista clip_ids = wszystkie klipy V1/V2.
    """
    tl = timeline.model_copy(deep=True).normalize()
    ids = list(clip_ids or [c.id for c in tl.v("V1") + tl.v("V2")])
    size = _lut_size(get_config().effort_level(AGENT))
    changed = 0
    for cid in ids:
        hit = tl.find_clip(cid)
        if not hit or not isinstance(hit[1], VClip):
            continue
        c: VClip = hit[1]
        try:
            m = project.get_media(c.media_id)
        except KeyError:
            continue
        g = _apply_params(c.grade, params)
        if g.look and not look_exists(g.look):
            log(AGENT, f"Nieznany look „{g.look}” - pomijam", path="Ochrona skóry & Look", level="warn",
                project_id=project.id)
            g.look = None
        if grade_is_neutral(g):
            g.lut = None
        else:
            st = analyze.load_or_compute(Path(project.cache) / "color" / "stats", m.id, m.path, t0=float(c.src_in),
                                         t1=None if m.kind == "image" else float(c.src_out),
                                         samples=SAMPLES_BY_EFFORT[get_config().effort_level(AGENT)])
            if g.input_profile in ("rec709", "") and st.get("log_profile") and not c.grade.lut:
                g.input_profile = st["log_profile"]
            ref = g.match.get("ref") if isinstance(g.match, dict) else None
            out = _lut_path(project, cid, g, st, ref, size)
            if not out.exists():
                compile_grade_lut(g, st, out, size=size, ref_stats=ref)
            g.lut = str(out)
            log(AGENT, f"{_clip_name(c, m)}: {describe_plan(plan_grade(g, st, ref))}", path="Kompilacja LUT",
                project_id=project.id)
        c.grade = g
        changed += 1
    log(AGENT, f"Zmieniono korekcję {changed} klipów ({', '.join(k for k in params)})", path="Kompilacja LUT",
        project_id=project.id)
    return tl


def preview_lut_frame(media_path: str, t: float, grade: Grade, out_jpg: Path, *, width: int = 960,
                      face_box: list[float] | None = None) -> Path:
    """Klatka z zastosowanym gradem (podgląd „Przed/Po”); render główny używa color.filters.video_filter."""
    info = ffmpeg.media_info(media_path)
    lut_path = grade.lut if grade.lut and Path(grade.lut).exists() else None
    if lut_path is None and not grade_is_neutral(grade):
        dur = float(info.get("duration") or 0)
        st = analyze.clip_stats(media_path, t0=max(0.0, t - 1.5), t1=min(dur, t + 1.5) if dur else None, samples=3)
        ref = grade.match.get("ref") if isinstance(grade.match, dict) else None
        out = paths.CACHE / "color_preview" / f"{grade_hash(grade, st, ref, 33)}.cube"
        if not out.exists():
            compile_grade_lut(grade, st, out, size=33, ref_stats=ref)
        lut_path = str(out)
    vf = grade_chain(grade, info, lut_path=lut_path, face_box=face_box) or None
    return ffmpeg.extract_frame(media_path, t, out_jpg, width=width, vf=vf)

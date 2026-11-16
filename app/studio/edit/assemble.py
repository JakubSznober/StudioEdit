"""Składanie osi czasu z planu reżysera - rzemiosło montażysty w kodzie.

Realizuje: pacing z naturalnym oddechem, J-cut / L-cut, unikanie jump cutów (cut-in / przebitka),
punch-in na kluczowych słowach, slow zoom (Ken Burns) na statycznych ujęciach, B-roll dopasowany
semantycznie (V2), slow motion z materiałów HFR i speed ramp, świadome przejścia, markery dramaturgii,
tryb montażu obrazowego (bez mowy) z cięciem na ruchu i do bitu.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..config import get_config
from ..project.model import (AClip, Keyframe, Marker, SpeedPoint, SubtitleSettings, Timeline, TitleClip, Transition,
                             VClip, Variant)
from .material import SentenceUnit, ShotUnit, keywords
from .takes import keep_ranges

PLATFORM_VARIANTS = {
    "youtube": dict(aspect="16:9", width=1920, height=1080),
    "youtube_4k": dict(aspect="16:9", width=3840, height=2160),
    "tiktok": dict(aspect="9:16", width=1080, height=1920, max_duration=600),
    "reels": dict(aspect="9:16", width=1080, height=1920, max_duration=180),
    "shorts": dict(aspect="9:16", width=1080, height=1920, max_duration=180),
    "instagram_feed": dict(aspect="4:5", width=1080, height=1350, max_duration=90),
    "master": dict(aspect="16:9", width=1920, height=1080),
}


def make_variant(name: str, fps: float) -> Variant:
    try:
        from ..render.presets import variant_for  # moduł renderu (jeśli dostępny)

        v = variant_for(name)
        v.fps = fps
        return v
    except Exception:
        spec = PLATFORM_VARIANTS.get(name, PLATFORM_VARIANTS["youtube"])
        return Variant(name=name, fps=fps, **spec)


def pick_fps(media_fps: list[float]) -> float:
    if not media_fps:
        return 30.0
    fam = Counter(25.0 if abs(f % 25) < 0.6 or abs(f - 50) < 1 else 24.0 if abs(f - 24) < 0.6 or abs(f - 23.976) < 0.1
                  else 30.0 for f in media_fps)
    return fam.most_common(1)[0][0]


@dataclass
class Piece:
    unit: Any  # SentenceUnit | ShotUnit
    part: str
    s: float  # źródło
    e: float
    first_of_unit: bool
    last_of_unit: bool
    why: str = ""
    emphasis: list[str] = field(default_factory=list)
    broll_query: str = ""
    speed: float = 1.0


def _resolve(plan: dict, sentences: list[SentenceUnit], shots: list[ShotUnit]) -> tuple[list[tuple[str, Any, dict]], list[str]]:
    by_s = {u.id: u for u in sentences}
    by_sh = {s.id: s for s in shots}
    seq, missing, seen = [], [], set()
    for sec in plan.get("structure", []):
        part = sec.get("part", "rozwinięcie")
        for it in sec.get("items", []):
            iid = str(it.get("id", "")).strip()
            if iid in seen:
                continue
            u = by_s.get(iid) or by_sh.get(iid)
            if u is None:
                missing.append(iid)
                continue
            seen.add(iid)
            seq.append((part, u, it))
    return seq, missing


def _fit_target(seq, target: float | None, pieces_dur) -> list:
    if not target:
        return seq
    total = sum(pieces_dur(u) for _p, u, _i in seq)
    if total <= target * 1.08:
        return seq
    # usuwaj najsłabsze elementy rozwinięcia, potem kulminacji (hook i zakończenie zostają)
    order = sorted([i for i, (p, _u, _it) in enumerate(seq) if p == "rozwinięcie"],
                   key=lambda i: getattr(seq[i][1], "score", seq[i][1].shot.score if hasattr(seq[i][1], "shot") else 0.5))
    drop = set()
    for i in order:
        if total <= target * 1.02:
            break
        drop.add(i)
        total -= pieces_dur(seq[i][1])
    return [x for i, x in enumerate(seq) if i not in drop]


def _motion_peak(unit: ShotUnit, media, a: float, b: float) -> float | None:
    curve = media.analysis.motion_curve
    fps = media.analysis.motion_fps or 0
    if not curve or not fps:
        return None
    i0, i1 = int(a * fps), int(b * fps)
    seg = curve[i0:i1]
    if not seg:
        return None
    return (i0 + int(np.argmax(seg))) / fps


def assemble(project, plan: dict, sentences: list[SentenceUnit], shots: list[ShotUnit], *, target: float | None,
             platforms: list[str], style: str, look: str | None, effort_level: int, log=lambda *a, **k: None,
             subtitles: bool | None = None) -> Timeline:
    cfg = get_config()
    ecfg = cfg.get("edit") or {}
    acfg = cfg.get("audio") or {}
    media = {m.id: m for m in project.media()}
    fps = pick_fps([m.info.get("fps", 30) for m in media.values() if m.kind == "video" and m.bin != "Odrzucone_Duble"])
    variants = [make_variant(p, fps) for p in (platforms or ["youtube"])]
    primary = variants[0]
    tl = Timeline(fps=fps, width=primary.width, height=primary.height, variants=variants,
                  brief=plan.get("_brief", ""), look=look or plan.get("look") or None)
    frame = 1.0 / fps
    breath = float(acfg.get("breath_ms", 200)) / 1000.0
    pace = plan.get("pace", "umiarkowane")
    dynamic = pace in ("dynamiczne", "bardzo dynamiczne")

    seq, missing = _resolve(plan, sentences, shots)
    if missing:
        log(f"Pomijam {len(missing)} nieznanych identyfikatorów z planu LLM: {', '.join(missing[:5])}", level="warn")
    speech_mode = any(isinstance(u, SentenceUnit) for _p, u, _i in seq)

    def unit_dur(u):
        if isinstance(u, SentenceUnit):
            return sum(b - a for a, b in keep_ranges(u.sentence, breath=breath))
        return min(u.dur, 2.2 if dynamic else 3.5)

    seq = _fit_target(seq, target, unit_dur)

    # ---------------------------------------------------------------- kawałki źródła
    pieces: list[Piece] = []
    for part, u, it in seq:
        if isinstance(u, SentenceUnit):
            rngs = keep_ranges(u.sentence, breath=breath, max_gap=0.4 if dynamic else 0.55)
            for k, (a, b) in enumerate(rngs):
                pieces.append(Piece(unit=u, part=part, s=a, e=b, first_of_unit=k == 0, last_of_unit=k == len(rngs) - 1,
                                    why=it.get("why", ""), emphasis=list(it.get("emphasis") or []),
                                    broll_query=it.get("broll", "") or ""))
        else:
            m = media[u.media_id]
            want = min(u.dur, (1.4 if pace == "bardzo dynamiczne" else 2.2) if dynamic else 3.5)
            peak = _motion_peak(u, m, u.s, u.e)
            # cięcie na ruchu: okno kończy się tuż po szczycie ruchu
            if peak is not None and peak - want * 0.75 >= u.s:
                a = max(u.s, min(u.e - want, peak - want * 0.75))
            else:
                a = u.s + max(0.0, (u.dur - want) / 2)
            speed = 1.0
            if u.hfr and (u.shot.motion > 0.6 or u.shot.shot_size in ("detal", "zbliżenie")) and effort_level >= 1:
                speed = 0.5
            src_len = want * speed
            pieces.append(Piece(unit=u, part=part, s=round(a, 3), e=round(min(u.e, a + src_len), 3), first_of_unit=True,
                                last_of_unit=True, why=it.get("why", ""), speed=speed))

    # ---------------------------------------------------------------- V1 + A1
    cursor = 0.0
    v1: list[VClip] = []
    a1: list[AClip] = []
    part_starts: dict[str, float] = {}
    for pc in pieces:
        u = pc.unit
        m = media[u.media_id]
        dur = (pc.e - pc.s) / pc.speed
        if dur < frame * 3:
            continue
        part_starts.setdefault(pc.part, cursor)
        reason = f"{pc.part}" + (f": {pc.why}" if pc.why else "")
        if isinstance(u, SentenceUnit):
            reason += f" · „{u.text[:70]}{'…' if len(u.text) > 70 else ''}”"
            if u.take_group:
                reason += " · HERO TAKE" if u.hero else ""
        else:
            reason += f" · {u.shot.shot_size or ''} {u.shot.description[:60]}"
        vc = VClip(media_id=u.media_id, src_in=pc.s, src_out=pc.e, start=round(cursor, 4), speed=pc.speed,
                   role="a_roll" if speech_mode else "b_roll", label=m.name, reason=reason.strip(),
                   interpolate="none")
        if m.kind == "image":
            vc.role = "image"
        v1.append(vc)
        if isinstance(u, SentenceUnit):
            a1.append(AClip(media_id=u.media_id, src_in=pc.s, src_out=pc.e, start=round(cursor, 4), role="dialog",
                            link=vc.id, label=m.name, reason="dialog"))
        cursor += dur
    piece_of = {v.id: p for v, p in zip(v1, [p for p in pieces if (p.e - p.s) / p.speed >= frame * 3])}

    # ---------------------------------------------------------------- ruch kamery: punch-in, cut-in, slow zoom
    punch = float(ecfg.get("punch_in_scale", 1.15))
    slow = float(ecfg.get("slow_zoom_scale", 1.08))
    alt = False
    for i, vc in enumerate(v1):
        pc = piece_of[vc.id]
        prev = v1[i - 1] if i else None
        same_take_cut = prev is not None and prev.media_id == vc.media_id and abs(prev.src_out - vc.src_in) > frame * 2
        kfs: list[Keyframe] = []
        base = 1.0
        if same_take_cut:
            # cięcie w tym samym ujęciu = potencjalny jump cut → zmiana skali (cut-in) naprzemiennie
            alt = not alt
            base = 1.12 if alt else 1.0
        else:
            alt = False
        kfs.append(Keyframe(t=0.0, scale=base, ease="linear"))
        # punch-in na słowie kluczowym
        if isinstance(pc.unit, SentenceUnit) and pc.emphasis and effort_level >= 1:
            words = [w for w in pc.unit.sentence.words if pc.s <= w.s < pc.e]
            targets = {e.lower().strip(".,!?") for e in pc.emphasis}
            hit = next((w for w in words if w.w.lower().strip(".,!?") in targets), None)
            if hit is not None:
                t_hit = (hit.s - pc.s) / vc.speed
                if 0.3 < t_hit < vc.duration - 0.4:
                    kfs.append(Keyframe(t=round(t_hit - frame, 4), scale=base, ease="hold"))
                    kfs.append(Keyframe(t=round(t_hit, 4), scale=round(base * punch / 1.0 if base == 1.0 else base * 1.06, 3),
                                        ease="hold"))
                    vc.reason += f" · punch-in na „{hit.w}”"
        if len(kfs) == 1:
            sh = getattr(pc.unit, "shot", None)
            static = sh is not None and (sh.camera_move in ("statyczny", "") and sh.motion < 1.2)
            if static and vc.duration >= 2.5:
                kfs.append(Keyframe(t=round(vc.duration, 4), scale=round(base * slow, 3), ease="linear"))
                vc.reason += f" · slow zoom {int(base * 100)}→{int(base * slow * 100)}%"
        vc.transform = kfs if (len(kfs) > 1 or base != 1.0) else []

    # ---------------------------------------------------------------- J-cut / L-cut (tylko z mową)
    jl = max(6, int(acfg.get("jl_frames", 12)))
    flip = True
    if speech_mode and effort_level >= 1:
        for i in range(1, len(v1)):
            prev, cur = v1[i - 1], v1[i]
            pp, pcur = piece_of[prev.id], piece_of[cur.id]
            if not (pp.last_of_unit and pcur.first_of_unit):
                continue  # wewnątrz zdania - nie przesuwamy
            if prev.media_id == cur.media_id and abs(prev.src_out - cur.src_in) < 1.0:
                continue
            if prev.duration < 1.4 or cur.duration < 1.4:
                continue
            d = jl * frame
            mp, mc = media[prev.media_id], media[cur.media_id]
            if flip:  # J-cut: dźwięk nowej sceny wyprzedza obraz
                if prev.src_out + d * prev.speed <= float(mp.info.get("duration", 0)) - frame:
                    prev.src_out = round(prev.src_out + d * prev.speed, 4)
                    cur.src_in = round(cur.src_in + d * cur.speed, 4)
                    cur.start = round(cur.start + d, 4)
                    _shift_kf(cur, -d)
                    cur.reason += f" · J-cut {jl} kl."
            else:  # L-cut: dźwięk poprzedniej sceny wybrzmiewa na nowym obrazie
                if cur.src_in - d * cur.speed >= 0:
                    prev.src_out = round(prev.src_out - d * prev.speed, 4)
                    cur.src_in = round(cur.src_in - d * cur.speed, 4)
                    cur.start = round(cur.start - d, 4)
                    _shift_kf(cur, d)
                    cur.reason += f" · L-cut {jl} kl."
            flip = not flip

    # ---------------------------------------------------------------- B-roll (V2)
    v2: list[VClip] = []
    if speech_mode and shots:
        v2 = _place_broll(v1, piece_of, shots, media, fps=fps, dynamic=dynamic, effort_level=effort_level,
                          max_len=float(ecfg.get("max_broll_s", 5.0)), log=log)

    # ---------------------------------------------------------------- przejścia
    transitions: list[Transition] = []
    by_unit_last = {}
    for vc in v1:
        by_unit_last[piece_of[vc.id].unit.id] = vc
    for tr in plan.get("transitions", []) or []:
        vc = by_unit_last.get(str(tr.get("after", "")))
        if vc is None or vc is v1[-1]:
            continue
        kind = tr.get("kind", "dissolve")
        transitions.append(Transition(at=round(vc.end, 4), kind=kind if kind in Transition.model_fields["kind"].annotation.__args__ else "dissolve",
                                      duration=0.35 if kind == "whip" else 0.6, reason=tr.get("why", "z planu")))
    # reguła: zmiana lokacji między różnymi mediami (bez przejścia z planu)
    have = {round(t.at, 2) for t in transitions}
    last_tr = -999.0
    for i in range(1, len(v1)):
        prev, cur = v1[i - 1], v1[i]
        if prev.media_id == cur.media_id or round(prev.end, 2) in have:
            continue
        lp, lc = _location(media[prev.media_id], prev.src_out), _location(media[cur.media_id], cur.src_in)
        if lp and lc and lp != lc and prev.end - last_tr > 20 and not dynamic:
            transitions.append(Transition(at=round(prev.end, 4), kind="dissolve", duration=0.5,
                                          reason=f"zmiana miejsca: {lp} → {lc}"))
            last_tr = prev.end
    transitions.sort(key=lambda t: t.at)

    # ---------------------------------------------------------------- markery, tytuł, napisy
    markers = [Marker(t=0.0, label="HOOK", kind="hook", color="#f59e0b")]
    if "kulminacja" in part_starts:
        markers.append(Marker(t=round(part_starts["kulminacja"], 3), label="KULMINACJA", kind="climax", color="#ef4444"))
    if "zakończenie" in part_starts:
        markers.append(Marker(t=round(part_starts["zakończenie"], 3), label="ZAKOŃCZENIE", kind="outro", color="#10b981"))
    titles: list[TitleClip] = []
    if plan.get("title_card") and style in ("vlog", "dokument", "tutorial", "podcast") and effort_level >= 1:
        titles.append(TitleClip(text=str(plan["title_card"])[:60], start=0.4, duration=2.6, style="title",
                                position="bottom"))
    vertical = any(v.aspect in ("9:16", "4:5") for v in variants)
    subs = SubtitleSettings()
    subs.enabled = speech_mode if subtitles is None else (subtitles and speech_mode)
    subs.mode = "word_pop" if vertical or dynamic else "static"
    subs.style = "bold_social" if vertical else "modern"
    subs.uppercase = vertical and dynamic
    tl.tracks = {"V1": v1, "V2": v2, "V3": titles, "A1": a1, "A2": [], "A3": [], "A4": [], "A5": []}
    tl.transitions = transitions
    tl.markers = markers
    tl.subtitles = subs
    tl.mix.target_lufs = float(acfg.get("target_lufs", -14.0))
    tl.mix.duck_db = float(acfg.get("duck_db", -15.0))
    tl.mix.crossfade_ms = float(acfg.get("micro_fade_ms", 80))
    tl.plan = {k: v for k, v in plan.items() if not k.startswith("_")}
    tl.plan["source"] = plan.get("_source", "llm")
    tl.plan["speech_mode"] = speech_mode
    tl.plan["pace"] = pace
    return tl.normalize()


def _shift_kf(vc: VClip, dt: float) -> None:
    if not vc.transform:
        return
    for k in vc.transform:
        if k.t > 0:
            k.t = round(max(0.0, k.t + dt), 4)


def _location(m, t: float) -> str:
    for sh in m.analysis.shots:
        if sh.s <= t <= sh.e:
            for tag in sh.tags:
                if tag.startswith("miejsce:"):
                    return tag.split(":", 1)[1].strip().lower()
    return ""


def _place_broll(v1: list[VClip], piece_of: dict, shots: list[ShotUnit], media: dict, *, fps: float, dynamic: bool,
                 effort_level: int, max_len: float, log) -> list[VClip]:
    frame = 1.0 / fps
    out: list[VClip] = []
    busy: list[tuple[float, float]] = []
    total = v1[-1].end if v1 else 0
    used: set[str] = set()

    def free(a, b):
        return all(b <= x or a >= y for x, y in busy)

    def choose(query_kw: set[str], avoid_media: str, avoid_t: float) -> ShotUnit | None:
        best, best_sc = None, -1.0
        for s in shots:
            if s.id in used or s.dur < 1.0:
                continue
            if s.media_id == avoid_media and abs(s.s - avoid_t) < 8:
                continue
            overlap = len(query_kw & s.keywords) / max(1, len(query_kw)) if query_kw else 0.0
            sc = 0.6 * overlap + 0.4 * s.shot.score - (0.15 if s.is_aroll_gap else 0)
            if sc > best_sc:
                best, best_sc = s, sc
        return best

    def place(a: float, b: float, query: str, anchor: VClip, why: str):
        a, b = max(a, 1.8 if anchor is v1[0] else 0.0), min(b, total)
        if b - a < 1.0 or not free(a, b):
            return
        s = choose(keywords(query), anchor.media_id, anchor.src_in)
        if s is None:
            return
        dur = min(b - a, s.dur, max_len)
        speed = 1.0
        ramp: list[SpeedPoint] = []
        if s.hfr and effort_level >= 1:
            speed = 0.5  # natywne slow motion z HFR
            if dynamic and effort_level >= 2 and dur >= 2.0:
                ramp = [SpeedPoint(t=0.0, speed=1.0), SpeedPoint(t=0.25, speed=0.4), SpeedPoint(t=0.75, speed=0.4),
                        SpeedPoint(t=1.0, speed=1.0)]
        src_need = dur * (sum(p.speed for p in ramp) / len(ramp) if ramp else speed)
        if src_need > s.dur:
            src_need = s.dur
            dur = src_need / (sum(p.speed for p in ramp) / len(ramp) if ramp else speed)
        m = media[s.media_id]
        peak = _motion_peak(s, m, s.s, s.e)
        src_a = s.s + max(0.0, (s.dur - src_need) / 2)
        if peak is not None:
            src_a = min(max(s.s, peak - src_need / 2), s.e - src_need)
        vc = VClip(media_id=s.media_id, src_in=round(src_a, 3), src_out=round(src_a + src_need, 3), start=round(a, 4),
                   speed=speed, speed_ramp=ramp, role="b_roll", label=m.name,
                   reason=f"B-roll: {why} · {s.shot.description[:70]}" + (" · slow motion (HFR)" if speed < 1 else "")
                   + (" · speed ramp" if ramp else ""))
        if s.shot.camera_move in ("statyczny", "") and dur >= 2.5:
            vc.transform = [Keyframe(t=0, scale=1.0, ease="linear"), Keyframe(t=round(dur, 3), scale=1.06, ease="linear")]
        out.append(vc)
        busy.append((a, a + dur))
        used.add(s.id)

    # 1) przykrywanie jump cutów (cięcia wewnątrz tego samego ujęcia)
    for i in range(1, len(v1)):
        prev, cur = v1[i - 1], v1[i]
        if prev.media_id == cur.media_id and abs(prev.src_out - cur.src_in) > frame * 2 and effort_level >= 1:
            pc = piece_of[cur.id]
            q = pc.broll_query or getattr(pc.unit, "text", "")
            place(cur.start - 0.9, cur.start + 1.8, q, cur, "przykrywa cięcie w wypowiedzi")
    # 2) przebitki zamówione w planie
    for vc in v1:
        pc = piece_of[vc.id]
        if pc.broll_query and pc.first_of_unit:
            mid = vc.start + min(1.0, vc.duration * 0.25)
            place(mid, mid + min(max_len, max(2.0, vc.duration * 0.6)), pc.broll_query, vc, f"„{pc.broll_query}”")
    # 3) gęstość: długie odcinki gadającej głowy bez przebitki
    gap_need = 7.0 if dynamic else 12.0
    t = 3.0
    while t < total - 3.0:
        if all(abs(t - x) > gap_need / 2 and abs(t - y) > gap_need / 2 for x, y in busy) and free(t, t + 3.0):
            anchor = next((v for v in v1 if v.start <= t < v.end), v1[-1])
            pc = piece_of[anchor.id]
            place(t, t + (2.5 if dynamic else 3.5), getattr(pc.unit, "text", ""), anchor, "utrzymanie uwagi")
        t += gap_need / 2
    if out:
        log(f"Ułożono {len(out)} przebitek B-roll na V2")
    return sorted(out, key=lambda c: c.start)


# ------------------------------------------------------------------------------------------------
def beat_sync(tl: Timeline, beats: list[float], *, tolerance: float = 0.3, log=lambda *a, **k: None) -> Timeline:
    """Przyciąga cięcia do bitu: B-roll (V2) zawsze; V1 w trybie montażu obrazowego."""
    if not beats:
        return tl
    b = np.array(sorted(beats))

    def snap(t: float) -> float:
        i = int(np.argmin(np.abs(b - t)))
        return float(b[i]) if abs(b[i] - t) <= tolerance else t

    moved = 0
    for vc in tl.v("V2"):
        ns = snap(vc.start)
        if ns != vc.start:
            d = ns - vc.start
            vc.start = round(ns, 4)
            moved += 1
            _ = d
    tl.tracks["V2"] = sorted(tl.v("V2"), key=lambda c: c.start)
    if not tl.plan.get("speech_mode", True):
        v1 = tl.v("V1")
        cursor = 0.0
        for i, vc in enumerate(v1):
            vc.start = round(cursor, 4)
            if i < len(v1) - 1:
                # tylko skracanie do najbliższego wcześniejszego bitu (nie wychodzimy poza materiał)
                earlier = b[b <= vc.end + 1e-6]
                if len(earlier) and vc.end - earlier[-1] <= tolerance and earlier[-1] - vc.start > 0.5:
                    new_dur = float(earlier[-1]) - vc.start
                    vc.src_out = round(vc.src_in + new_dur * vc.speed, 4)
                    moved += 1
            cursor = vc.start + vc.duration
        tl.tracks["V1"] = v1
    tl.normalize()
    # przejścia trzymają się cięć
    ends = [round(c.end, 4) for c in tl.v("V1")[:-1]]
    for tr in tl.transitions:
        if ends:
            tr.at = min(ends, key=lambda e: abs(e - tr.at))
    log(f"Rytm muzyki: przyciągnięto {moved} cięć do bitu")
    return tl


def total_duration(tl: Timeline) -> float:
    return max((c.end for c in tl.v("V1")), default=0.0)


def ceil_frames(t: float, fps: float) -> float:
    return math.ceil(t * fps) / fps

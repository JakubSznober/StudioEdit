"""Agent 1 - Asystent & Archiwista: ingest, proxy, ujęcia, jakość, Video-VLM, indeks projektu i biny.

Graf zadań na każde medium (osobne ścieżki w puli wątków archiwisty):
  Metadane ─┬─ Proxy NVENC ─ Detekcja ujęć + Jakość/ruch ─ Video-VLM ─┐
            └─ (Reżyser Dźwięku) STT / diaryzacja / analiza audio ─────┴─ Indeks i biny (wspólne)
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from ..config import get_config
from ..events import log
from ..project.model import MediaItem, Shot
from ..tasks import Task, TaskContext, orchestrator
from ..analysis import ingest, shots as shotmod, vision

AGENT = "archivist"


def analysis_pipeline(project, media_ids: list[str] | None = None, *, group: str | None = None,
                      deps: list[str] | None = None, force: bool = False) -> tuple[str, list[Task]]:
    """Tworzy graf zadań analizy. Zwraca (group, lista zadań); ostatnie zadanie = indeks."""
    group = group or f"analyze-{uuid.uuid4().hex[:8]}"
    items = [project.get_media(m) for m in media_ids] if media_ids else \
        [m for m in project.media() if m.bin != "Odrzucone_Duble"]
    if not force:
        items = [m for m in items if not _fully_analyzed(m)]
    tasks: list[Task] = []
    finals: list[str] = []
    deps_by_media: dict[str, list[str]] = {}
    prof = ingest.scale_profile(project)
    pid = project.id
    project.update_meta(status="ingesting")
    for m in items:
        mid = m.id
        t_probe = orchestrator.add(AGENT, "Metadane", f"Metadane: {m.name}", _probe_fn(pid, mid),
                                   project_id=pid, group=group, deps=list(deps or []), priority=2)
        tasks.append(t_probe)
        deps_by_media[mid] = [t_probe.id]
        if m.kind in ("video", "image"):
            t_proxy = orchestrator.add(AGENT, "Proxy NVENC", f"Proxy: {m.name}", _proxy_fn(pid, mid),
                                       project_id=pid, group=group, deps=[t_probe.id], priority=3)
            t_shots = orchestrator.add(AGENT, "Detekcja ujęć", f"Ujęcia i jakość: {m.name}",
                                       _shots_fn(pid, mid, prof), project_id=pid, group=group,
                                       deps=[t_proxy.id], priority=4)
            t_vlm = orchestrator.add(AGENT, "Video-VLM", f"Opis wizyjny: {m.name}", _vlm_fn(pid, mid, prof),
                                     project_id=pid, group=group, deps=[t_shots.id], priority=6, soft_deps=False)
            tasks += [t_proxy, t_shots, t_vlm]
            finals += [t_vlm.id, t_shots.id]
        else:
            t_thumb = orchestrator.add(AGENT, "Proxy NVENC", f"Miniatura: {m.name}", _audio_thumb_fn(pid, mid),
                                       project_id=pid, group=group, deps=[t_probe.id], priority=3)
            tasks.append(t_thumb)
            finals.append(t_thumb.id)
    # Reżyser Dźwięku: STT, diaryzacja, analiza audio (równolegle z obrazem)
    try:
        from . import sound

        s_tasks = sound.analysis_tasks(project, [m.id for m in items if m.kind in ("video", "audio")],
                                       group=group, deps_by_media=deps_by_media)
        tasks += s_tasks
        finals += [t.id for t in s_tasks]
    except Exception as e:  # moduł dźwięku niedostępny
        log(AGENT, f"Analiza dźwięku niedostępna: {e}", path="Indeks i biny", level="warn", project_id=pid)
    t_index = orchestrator.add(AGENT, "Indeks i biny", "Indeks projektu i segregacja binów", _index_fn(pid),
                               project_id=pid, group=group, deps=finals or [t.id for t in tasks],
                               soft_deps=True, priority=8)
    tasks.append(t_index)
    return group, tasks


def _fully_analyzed(m: MediaItem) -> bool:
    st = m.analysis.stage
    need = ["probe", "proxy", "shots", "vlm"] if m.kind == "video" else ["probe"]
    return all(st.get(k) in ("done", "skipped") for k in need)


# ------------------------------------------------------------------------------------------------
def _probe_fn(pid: str, mid: str):
    def fn(ctx: TaskContext):
        p = ctx.project
        m = p.get_media(mid)
        ingest.probe_media(m)
        p.save_media(m)
        info = m.info
        desc = f"{info.get('width', '?')}x{info.get('height', '?')} @ {info.get('fps', 0):g} fps, " \
               f"{info.get('bit_depth', 8)}-bit, {info.get('vcodec', info.get('acodec', ''))}, " \
               f"{info.get('duration', 0):.1f} s" if m.kind == "video" else \
               f"audio {info.get('sample_rate', 0)} Hz, {info.get('channels', 0)} kan., {info.get('duration', 0):.1f} s"
        extra = []
        if "HFR" in m.flags:
            extra.append(f"HFR — możliwe slow-motion do {m.flag_details.get('max_slowmo')}x")
        if "LOG" in m.flags or "HDR" in m.flags:
            extra.append(f"profil {m.analysis.log_profile.upper()} → konwersja do Rec.709")
        ctx.log(f"{m.name}: {desc}" + (f" · {'; '.join(extra)}" if extra else ""))
        return {"kind": m.kind}
    return fn


def _proxy_fn(pid: str, mid: str):
    def fn(ctx: TaskContext):
        p = ctx.project
        m = p.get_media(mid)
        out_dir = p.dir("cache", "proxies")
        if m.kind == "image":
            m.proxy = m.path
        else:
            t0 = time.time()
            proxy = ingest.make_proxy(m, out_dir, progress=ctx.sub(0, 0.9), cancel=ctx.cancel)
            m.proxy = str(proxy)
            ctx.log(f"Proxy {m.name} gotowe w {time.time() - t0:.1f} s")
        thumb = ingest.make_thumb(m, p.dir("cache", "thumbs"))
        m.thumb = str(thumb) if thumb else None
        wave = ingest.make_waveform(m, p.dir("cache", "thumbs"))
        m.waveform = str(wave) if wave else None
        m.analysis.stage["proxy"] = "done"
        p.update_media(mid, lambda x: (setattr(x, "proxy", m.proxy), setattr(x, "thumb", m.thumb),
                                       setattr(x, "waveform", m.waveform), x.analysis.stage.update(proxy="done")))
        return {"proxy": m.proxy}
    return fn


def _audio_thumb_fn(pid: str, mid: str):
    def fn(ctx: TaskContext):
        p = ctx.project
        m = p.get_media(mid)
        thumb = ingest.make_thumb(m, p.dir("cache", "thumbs"))
        p.update_media(mid, lambda x: (setattr(x, "thumb", str(thumb) if thumb else None),
                                       x.analysis.stage.update(proxy="done", shots="skipped", vlm="skipped")))
        return {}
    return fn


def _shots_fn(pid: str, mid: str, prof: dict):
    def fn(ctx: TaskContext):
        p = ctx.project
        m = p.get_media(mid)
        src = m.proxy or m.path
        dur = float(m.info.get("duration") or 0)
        if m.kind == "image":
            shots_t = [(0.0, 5.0)]
            res = {"shots": [{"s": 0.0, "e": 5.0, "luma_mean": 0.5, "clipped_pct": 0, "clipped_max": 0,
                              "crushed_pct": 0, "sharpness": 100, "motion": 0, "shake": 0, "face_box": None,
                              "people_hint": 0}], "motion_curve": [], "motion_fps": 0}
        else:
            frame_skip = {"small": 0, "medium": 1, "massive": 2}.get(p.meta.scale, 1)
            ctx.progress(0.02, "detekcja ujęć")
            shots_t = shotmod.detect_shots(src, dur, frame_skip=frame_skip,
                                           min_shot=float(get_config().get("edit.min_shot_s", 0.8)) * 0.75)
            ctx.progress(0.35, f"{len(shots_t)} ujęć — analiza jakości i ruchu")
            fps = float(prof.get("motion_fps", 4))
            res = shotmod.analyze_frames(src, dur, shots_t, fps=fps, progress=ctx.sub(0.35, 0.95), cancel=ctx.cancel)
        shots = []
        short = mid[2:6]
        for i, s in enumerate(res["shots"]):
            sh = Shot(id=f"{short}_s{i:03d}", s=s["s"], e=s["e"], luma_mean=s["luma_mean"],
                      clipped_pct=s["clipped_pct"], crushed_pct=s["crushed_pct"], sharpness=s["sharpness"],
                      motion=s["motion"], shake=s["shake"], face_box=s["face_box"], people=s["people_hint"])
            # wstępna ocena (zanim VLM się wypowie): ostrość, ekspozycja, stabilność
            sharp_n = min(1.0, s["sharpness"] / 250.0)
            expo = 1.0 - min(1.0, abs(s["luma_mean"] - 0.45) * 1.6)
            sh.score = round(0.45 * sharp_n + 0.35 * expo + 0.2 * (1 - s["shake"]), 3)
            shots.append(sh)
        flags, details = shotmod.quality_flags(res["shots"], dur)

        def upd(x: MediaItem):
            x.analysis.shots = shots
            x.analysis.motion_curve = res.get("motion_curve", [])
            x.analysis.motion_fps = res.get("motion_fps", 0)
            x.flags = sorted(set([f for f in x.flags if f not in ("NEEDS_EXPOSURE_RECOVERY", "NEEDS_EXPOSURE_LIFT",
                                                                    "BLURRY", "SHAKY", "JUNK")] + flags))
            x.flag_details.update(details)
            x.analysis.stage["shots"] = "done"

        p.update_media(mid, upd)
        msg = f"{m.name}: {len(shots)} ujęć"
        if "NEEDS_EXPOSURE_RECOVERY" in flags:
            msg += f" · PRZEPALENIA {details.get('clipped_pct')}% → oznaczam NEEDS_EXPOSURE_RECOVERY dla Kolorysty " \
                   f"(nie odrzucam)"
        if "SHAKY" in flags:
            msg += " · drgania kamery → kandydat do stabilizacji"
        if "BLURRY" in flags:
            msg += " · nieostre"
        ctx.log(msg)
        return {"shots": len(shots), "flags": flags}
    return fn


def _vlm_fn(pid: str, mid: str, prof: dict):
    def fn(ctx: TaskContext):
        p = ctx.project
        m = p.get_media(mid)
        shots = list(m.analysis.shots)
        if not shots:
            return {"described": 0}
        cfg = get_config()
        n_frames = int(prof.get("vlm_frames_per_shot", 3))
        n_frames = {0: 1, 1: 2, 2: n_frames, 3: n_frames + 1}.get(ctx.effort_level, n_frames)
        max_shots = int(prof.get("max_shots_for_vlm", 1000))
        # przy bardzo wielu ujęciach opisz najlepiej rokujące + równomierną próbkę
        order = list(range(len(shots)))
        if len(shots) > max_shots:
            order = sorted(order, key=lambda i: -shots[i].score)[:max_shots]
        kf_dir = p.dir("cache", "keyframes", mid)
        src = m.proxy or m.path
        described = 0
        failed = 0
        for k, i in enumerate(sorted(order)):
            ctx.check()
            sh = shots[i]
            kfs = shotmod.shot_keyframes(src, sh, kf_dir, n=n_frames)
            sh.keyframes = kfs
            try:
                d = vision.describe_shot(sh, kfs, project_id=pid)
                vision.apply_description(sh, d)
                described += 1
            except Exception as e:
                failed += 1
                if failed <= 2:
                    ctx.log(f"Video-VLM niedostępny dla ujęcia {sh.id}: {e}", level="warn")
                if failed >= 3 and described == 0:
                    ctx.log("Pomijam opisy wizyjne (model niedostępny) — montaż użyje analizy technicznej",
                            level="warn")
                    break
            ctx.progress((k + 1) / len(order), f"ujęcie {k + 1}/{len(order)}")
            if described and described % 8 == 0:
                p.update_media(mid, lambda x: setattr(x.analysis, "shots", shots))
        summary = " | ".join(f"[{s.s:.1f}-{s.e:.1f}] {s.shot_size}: {s.description}" for s in shots
                             if s.description)[:1500]

        def upd(x: MediaItem):
            x.analysis.shots = shots
            x.analysis.summary = summary
            x.analysis.stage["vlm"] = "done" if described else "skipped"

        p.update_media(mid, upd)
        if described:
            best = max(shots, key=lambda s: s.score)
            ctx.log(f"{m.name}: opisano {described} ujęć; najlepsze {best.id} ({best.shot_size}, "
                    f"ocena {best.score:.2f}): {best.description[:120]}")
        return {"described": described}
    return fn


def _index_fn(pid: str):
    def fn(ctx: TaskContext):
        p = ctx.project
        rejected = 0
        rebinned = 0
        for m in p.media():
            if m.bin == "Odrzucone_Duble":
                continue
            new_bin = m.bin
            speech = m.analysis.speech_ratio
            if m.kind == "video" and not m.generated:
                has_words = bool(m.transcript and m.transcript.sentences)
                broll_votes = sum(1 for s in m.analysis.shots if "b-roll" in s.tags)
                if has_words and speech >= 0.25:
                    new_bin = "A-Roll"
                elif m.analysis.shots and broll_votes >= max(1, len(m.analysis.shots) // 2):
                    new_bin = "B-Roll"
                elif not has_words:
                    new_bin = "B-Roll"
            elif m.kind == "audio" and m.bin == "Audio":
                if m.analysis.bpm and speech < 0.2:
                    new_bin = "Muzyka"
            if new_bin != m.bin:
                p.update_media(m.id, lambda x, b=new_bin: setattr(x, "bin", b))
                rebinned += 1
            if "JUNK" in m.flags:
                p.reject_media(m.id, m.flag_details.get("junk_reason", "materiał bezużyteczny"))
                rejected += 1
                ctx.log(f"Odrzucam {m.name} → Odrzucone_Duble ({m.flag_details.get('junk_reason')}); plik nie "
                        f"jest kasowany")
        index = build_index(p)
        (p.root / "project_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        total = sum(m.info.get("duration", 0) for m in p.media() if m.bin != "Odrzucone_Duble")
        p.update_meta(status="analyzed", stats={**p.meta.stats, "footage_s": round(total, 1),
                                                "media": len(p.media()), "analyzed_at": time.time()})
        if not p.meta.thumb:
            first = next((m for m in p.media() if m.thumb), None)
            if first:
                p.update_meta(thumb=first.thumb)
        ctx.log(f"Indeks gotowy: {len(index['media'])} plików, {total / 60:.1f} min materiału, "
                f"{sum(len(m['shots']) for m in index['media'])} ujęć; przebinowano {rebinned}, odrzucono {rejected}. "
                f"ProjectManager odblokowany.")
        return {"media": len(index["media"]), "rejected": rejected}
    return fn


def build_index(p) -> dict:
    """project_index.json - zwięzła baza wiedzy o materiale (dla reżysera i do wglądu)."""
    out = {"project": p.meta.model_dump(mode="json"), "generated_at": time.time(), "media": []}
    for m in p.media():
        out["media"].append({
            "id": m.id, "name": m.name, "bin": m.bin, "kind": m.kind, "flags": m.flags,
            "duration": m.info.get("duration"), "fps": m.info.get("fps"),
            "resolution": f"{m.info.get('width')}x{m.info.get('height')}" if m.kind == "video" else None,
            "log_profile": m.analysis.log_profile, "summary": m.analysis.summary,
            "speech_ratio": m.analysis.speech_ratio, "speakers": m.transcript.speakers if m.transcript else [],
            "shots": [{"id": s.id, "s": s.s, "e": s.e, "size": s.shot_size, "move": s.camera_move,
                       "desc": s.description, "score": s.score, "tags": s.tags} for s in m.analysis.shots],
            "sentences": [{"id": s.id, "s": s.s, "e": s.e, "text": s.text, "spk": s.spk, "fluency": s.fluency}
                          for s in (m.transcript.sentences if m.transcript else [])],
        })
    return out

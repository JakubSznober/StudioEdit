"""Agent Artysta VFX & Generator B-Roll: stabilizacja, usuwanie obiektów, rozmycie/anonimizacja, B-Roll AI.

Przetwarzany jest TYLKO wskazany fragment. Wynik trafia do VClip jako:

    Effect(type=<kind>, status="done", enabled=True, output=<plik>,
           params={..., "src_t0": <s>, "src_t1": <s>, "out_offset": 0.0, "graded": False})

gdzie [src_t0, src_t1] to zakres czasu ŹRÓDŁA klipu, który pokrywa plik `output` (plik zaczyna się dokładnie
w src_t0, ma tę samą rozdzielczość i fps co źródło, dźwięk źródła). Render podmienia źródło na `output`
w tym zakresie (czas w pliku = t_src - src_t0 + out_offset) i nakłada grading (graded=False).
generate_broll tworzy nowy MediaItem (bin „Wygenerowane”) i VClip role="generated" na V2.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ..events import log
from ..media import ffmpeg
from ..project.model import Effect, Timeline, VClip
from ..tasks import Task, TaskContext, orchestrator

AGENT = "vfx"
KINDS = {"stabilize": "Stabilizacja", "remove_object": "Usuwanie obiektów", "generate_broll": "Generowanie B-Roll",
         "blur_region": "Śledzenie obiektu"}
TITLES = {"stabilize": "Stabilizacja fragmentu", "remove_object": "Usuwanie obiektu",
          "generate_broll": "Generowanie przebitki (Wan 2.2)", "blur_region": "Rozmycie / anonimizacja"}


def _fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}".replace(".", ",")


def _num(v: float, nd: int = 1) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def timeline_to_source(clip: VClip, t0: float, t1: float) -> tuple[float, float]:
    """Zakres osi [t0, t1] → zakres źródła klipu (src_in + (t - start) * speed), przycięty do klipu."""
    sp = max(0.05, float(clip.speed or 1.0))
    a, b = max(t0, clip.start), min(t1, clip.end)
    if b - a < 0.04:  # brak części wspólnej → cały klip
        return float(clip.src_in), float(clip.src_out)
    s0 = clip.src_in + (a - clip.start) * sp
    s1 = clip.src_in + (b - clip.start) * sp
    return max(clip.src_in, s0), min(clip.src_out, s1)


def _find_vclip(tl: Timeline, clip_id: str) -> VClip | None:
    tl.normalize()
    hit = tl.find_clip(clip_id)
    if hit and isinstance(hit[1], VClip):
        return hit[1]
    return None


def _attach_effect(tl: Timeline, clip_id: str, eff: Effect) -> bool:
    c = _find_vclip(tl, clip_id)
    if c is None:
        return False
    e0, e1 = float(eff.params["src_t0"]), float(eff.params["src_t1"])
    keep = []
    for e in c.effects:
        if e.type == eff.type and e.output:
            a = float(e.params.get("src_t0", c.src_in))
            b = float(e.params.get("src_t1", c.src_out))
            if a < e1 and b > e0:  # nakładający się poprzedni wynik tego samego typu - zastępujemy
                continue
        keep.append(e)
    keep.append(eff)
    c.effects = keep
    return True


def _clear_v2(tl: Timeline, a: float, b: float) -> int:
    """Robi miejsce na V2 w [a, b]: klipy w większości w zakresie usuwa, nachodzące przycina."""
    out, n = [], 0
    for c in tl.v("V2"):
        if c.end <= a or c.start >= b:
            out.append(c)
            continue
        inside = min(c.end, b) - max(c.start, a)
        if inside >= 0.5 * c.duration:
            n += 1
            continue
        sp = max(0.05, c.speed)
        if c.start < a:  # przytnij koniec
            c.src_out = c.src_in + (a - c.start) * sp
        else:  # przytnij początek
            cut = (b - c.start) * sp
            c.src_in += cut
            c.start = b
        out.append(c)
        n += 1
    tl.tracks["V2"] = out
    return n


def vfx_task(project, timeline: Timeline, kind: str, clip_id: str, t0: float, t1: float, params: dict, *,
             group: str, deps: list[str] = []) -> Task:  # noqa: B006 - kontrakt ARCHITECTURE.md
    """Zadanie VFX na fragmencie osi [t0, t1] (czas osi). Wynik: Effect w VClip (lub nowy klip B-Roll), commit."""
    if kind not in KINDS:
        raise ValueError(f"Nieznany rodzaj VFX: {kind} (dostępne: {', '.join(KINDS)})")
    params = dict(params or {})
    pid = project.id
    path = KINDS[kind]
    tl = timeline.model_copy(deep=True)
    clip = _find_vclip(tl, clip_id) if kind != "generate_broll" else None
    if kind != "generate_broll" and clip is None:
        raise ValueError(f"Nie znaleziono klipu {clip_id} na osi")
    out_dir = Path(project.cache) / "vfx"

    def fn(ctx: TaskContext) -> dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        if kind == "generate_broll":
            return _broll(ctx)
        assert clip is not None
        media = project.get_media(clip.media_id)
        s0, s1 = timeline_to_source(clip, float(t0), float(t1))
        if kind == "stabilize" and (params.get("whole") or (s1 - s0) >= 0.9 * (clip.src_out - clip.src_in)):
            s0, s1 = float(clip.src_in), float(clip.src_out)
        if media.kind != "video":
            raise RuntimeError(f"{TITLES[kind]} wymaga klipu wideo ({media.name} to {media.kind})")
        tag = uuid.uuid4().hex[:6]
        out = out_dir / f"{kind}_{clip.id}_{s0:.2f}-{s1:.2f}_{tag}.mp4"
        name = clip.label or media.name
        ctx.log(f"{TITLES[kind]}: {name}, oś {_fmt(t0)}–{_fmt(t1)} → źródło {_fmt(s0)}–{_fmt(s1)} "
                f"({_num(s1 - s0)} s)")
        extra: dict[str, Any] = {}
        if kind == "stabilize":
            from ..vfx.stabilize import estimate_shake, stabilize

            strength = float(params.get("strength", 0.5))
            try:
                before = estimate_shake(media.path, s0, s1)
            except Exception:
                before = None
            stabilize(media.path, s0, s1, out, strength=strength, progress=ctx.sub(0.0, 0.95), cancel=ctx.cancel)
            extra["strength"] = strength
            if before is not None:
                try:
                    after = estimate_shake(str(out), 0.0, s1 - s0)
                    extra.update({"shake_before": round(before, 3), "shake_after": round(after, 3)})
                    ctx.log(f"Drgania: {_num(before, 2)} → {_num(after, 2)} (vid.stab, optzoom=1)")
                except Exception:
                    pass
        elif kind == "remove_object":
            from ..vfx.inpaint import remove_object

            remove_object(media.path, s0, s1, out, points=params.get("points"), box=params.get("box"),
                          prompt=params.get("prompt"), progress=ctx.sub(0.0, 0.97), cancel=ctx.cancel)
            extra.update({k: params[k] for k in ("points", "box", "prompt") if params.get(k)})
        elif kind == "blur_region":
            from ..vfx.inpaint import blur_region

            _p, mode = blur_region(media.path, s0, s1, out, box=params.get("box"), prompt=params.get("prompt"),
                                   strength=float(params.get("strength", 0.8)), progress=ctx.sub(0.0, 0.97),
                                   cancel=ctx.cancel)
            extra.update({"mode": mode, **{k: params[k] for k in ("box", "prompt") if params.get(k)}})
            ctx.log(f"Rozmycie: {mode}")
        eff = Effect(type=kind, status="done", enabled=True, output=str(out),
                     params={**extra, "src_t0": round(s0, 4), "src_t1": round(s1, 4), "out_offset": 0.0,
                             "graded": False})
        attached = {"ok": False}

        def mut(t: Timeline) -> Timeline:
            attached["ok"] = _attach_effect(t, clip.id, eff)
            return t

        new = project.mutate_timeline(mut, f"VFX: {TITLES[kind].lower()} ({name}, {_fmt(t0)}–{_fmt(t1)})", AGENT)
        if not attached["ok"]:
            ctx.log(f"Klip {name} zniknął z osi w trakcie pracy - wynik zapisany tylko w cache: {out.name}",
                    level="warn")
        return {"version": new.version, "output": str(out), "src_t0": s0, "src_t1": s1, "effect": eff.model_dump()}

    def _broll(ctx: TaskContext) -> dict[str, Any]:
        from ..vfx.broll import generate_broll

        prompt = str(params.get("prompt") or params.get("text") or "").strip()
        if not prompt:
            raise RuntimeError("Opisz, jaką przebitkę wygenerować (np. „zachód słońca nad morzem, ujęcie z drona”)")
        dur = float(t1) - float(t0)
        if dur <= 0.2:
            dur = float(params.get("duration", 4.0))
        dur = max(1.0, min(8.0, dur))
        vertical = tl.height > tl.width
        w, h = (480, 832) if vertical else (832, 480)
        if ctx.effort_level >= 3:
            w, h = (704, 1280) if vertical else (1280, 704)
        out = out_dir / f"broll_{uuid.uuid4().hex[:8]}.mp4"
        ctx.log(f"Generuję przebitkę {w}×{h}, {_num(dur)} s: „{prompt[:80]}”")
        generate_broll(prompt, dur, out, width=w, height=h, progress=ctx.sub(0.0, 0.95), cancel=ctx.cancel,
                       steps=20 if ctx.effort_level <= 1 else 24 if ctx.effort_level == 2 else 30, project_id=pid)
        item = project.add_media_file(out, bin_name="Wygenerowane", generated=True)
        info = ffmpeg.media_info(out)

        def _upd(m):
            m.info = info
            m.analysis.summary = f"Wygenerowano (Wan 2.2 TI2V 5B): {prompt}"
            m.flags = sorted(set(m.flags + ["GENERATED"]))
        project.update_media(item.id, _upd)
        length = float(info.get("duration") or dur)
        start = float(t0) if t1 > t0 else float(params.get("at", 0.0))
        vc = VClip(media_id=item.id, src_in=0.0, src_out=round(min(length, dur if t1 > t0 else length), 4),
                   start=start, role="generated", label=f"B-Roll AI: {prompt[:40]}", reason=f"Wygenerowano: {prompt}")

        def mut(t: Timeline) -> Timeline:
            t.normalize()
            n = _clear_v2(t, vc.start, vc.start + vc.duration)
            if n:
                log(AGENT, f"Zrobiono miejsce na V2 ({n} klipów usunięto/przycięto)", path=path, project_id=pid)
            t.tracks["V2"] = [*t.v("V2"), vc]
            return t

        new = project.mutate_timeline(mut, f"VFX: wygenerowana przebitka ({_fmt(vc.start)})", AGENT)
        ctx.log(f"Przebitka wstawiona na V2 w {_fmt(vc.start)}–{_fmt(vc.end)} (v{new.version})")
        return {"version": new.version, "media_id": item.id, "clip_id": vc.id, "output": str(out)}

    title = TITLES[kind] + (f": {params.get('prompt')[:40]}" if params.get("prompt") else "")
    return orchestrator.add(AGENT, path, title, fn, project_id=pid, deps=list(deps), group=group)

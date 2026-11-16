"""Smart Reframing: ścieżka kadrowania dla wariantów o innych proporcjach (np. 9:16 z 16:9 i odwrotnie)."""
from __future__ import annotations

from ..project.model import Timeline, VClip


def _aspect(w: float, h: float) -> float:
    return w / h if h else 1.0


def needs_reframe(media, variant) -> bool:
    w, h = media.info.get("width", 0), media.info.get("height", 0)
    if not w or not h:
        return False
    return abs(_aspect(w, h) - _aspect(variant.width, variant.height)) > 0.05


def clip_path(media, vc: VClip, *, fps: float = 4.0, use_tracker: bool = True, log=lambda *a, **k: None) -> list[dict]:
    """[{t (sek. od początku klipu na osi), cx, cy}] - śledzi twarz/osobę; fallback: twarz z analizy ujęcia."""
    pts: list[dict] = []
    if use_tracker:
        try:
            from ..vfx.track import track_subject

            raw = track_subject(media.proxy or media.path, vc.src_in, vc.src_out, fps=fps)
            for r in raw:
                t_clip = (float(r["t"]) - vc.src_in) / max(0.05, vc.speed)
                if -0.01 <= t_clip <= vc.duration + 0.01:
                    pts.append({"t": round(max(0.0, t_clip), 3), "cx": round(float(r["cx"]), 4),
                                "cy": round(float(r.get("cy", 0.45)), 4)})
        except Exception as e:  # tracker niedostępny
            log(f"Śledzenie obiektu niedostępne ({e}) — używam pozycji twarzy z analizy", level="warn")
            pts = []
    if not pts:
        face = None
        for sh in media.analysis.shots:
            if sh.s <= (vc.src_in + vc.src_out) / 2 <= sh.e and sh.face_box:
                face = sh.face_box
                break
        cx, cy = (face[0], face[1]) if face else (0.5, 0.45)
        pts = [{"t": 0.0, "cx": round(cx, 4), "cy": round(cy, 4)}, {"t": round(vc.duration, 3), "cx": round(cx, 4),
                                                                      "cy": round(cy, 4)}]
    return pts


def reframe_timeline(project, tl: Timeline, *, effort_level: int = 2, log=lambda *a, **k: None, progress=None) -> int:
    media = {m.id: m for m in project.media()}
    clips = [c for c in tl.v("V1") + tl.v("V2")]
    todo = []
    for v in tl.variants:
        for c in clips:
            m = media.get(c.media_id)
            if m is not None and m.kind in ("video", "image") and needs_reframe(m, v):
                todo.append((v, c, m))
    done = 0
    cache: dict[tuple, list] = {}
    for i, (v, c, m) in enumerate(todo):
        key = (c.media_id, c.src_in, c.src_out, c.speed)
        if key not in cache:
            cache[key] = clip_path(m, c, fps=2.0 + effort_level, use_tracker=effort_level >= 1 and m.kind == "video",
                                   log=log)
        c.reframe[v.name] = cache[key]
        done += 1
        if progress:
            progress((i + 1) / max(1, len(todo)))
    # zapisz zmiany w torach
    by_id = {c.id: c for c in clips}
    for k in ("V1", "V2"):
        tl.tracks[k] = [by_id.get(c.id, c) for c in tl.v(k)]
    return done

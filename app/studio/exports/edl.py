"""Eksport dla innych NLE: CMX3600 EDL (jedna ścieżka wideo) oraz OpenTimelineIO JSON (.otio)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..project.model import Timeline, VClip
from . import (audio_items, effective_speed, fileurl, frames, media_of, rate_info, safe_name, title_clips,
               video_clips)


def _tc(f: int, fps: float) -> str:
    tb, _ = rate_info(fps)
    f = max(0, int(f))
    hh, rem = divmod(f, tb * 3600)
    mm, rem = divmod(rem, tb * 60)
    ss, ff_ = divmod(rem, tb)
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff_:02d}"


def _reel(name: str, used: dict[str, str], key: str) -> str:
    if key in used:
        return used[key]
    base = re.sub(r"[^A-Z0-9]", "", Path(name).stem.upper())[:6] or "AX"
    reel = base
    i = 1
    while reel in used.values():
        reel = f"{base[:5]}{i}"[:8]
        i += 1
    used[key] = reel
    return reel


def export_edl(project, timeline: Timeline, out_path: Path, *, track: str = "V1") -> Path:
    """CMX3600: zdarzenia cięć (C), przenikania (D nnn), prędkość (M2), komentarze z nazwą i ścieżką pliku."""
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    fps = float(tl.fps or 30)
    tb, ntsc = rate_info(fps)
    name = getattr(getattr(project, "meta", None), "name", "") or "StudioEdit"
    clips = video_clips(tl, track)
    trans = {round(t.at, 3): t for t in tl.transitions if t.kind != "cut" and track == "V1"}
    reels: dict[str, str] = {}
    lines = [f"TITLE: {safe_name(name)[:60]}", "FCM: NON-DROP FRAME", ""]
    ev = 0
    tol = 1.5 / fps + 0.02
    for i, c in enumerate(clips):
        m = media_of(project, c.media_id)
        if m is None:
            continue
        reel = _reel(m.name, reels, m.id)
        sp = effective_speed(c)
        rec_in, rec_out = frames(c.start, fps), frames(c.end, fps)
        src_in = frames(c.src_in, fps)
        tr = next((t for k, t in trans.items() if abs(k - c.start) <= tol), None)
        prev = clips[i - 1] if i > 0 else None
        nxt = clips[i + 1] if i + 1 < len(clips) else None
        if nxt is not None and abs(c.end - nxt.start) <= tol:
            tr_next = next((t for k, t in trans.items() if abs(k - nxt.start) <= tol), None)
            if tr_next is not None:
                # klip kończy się tam, gdzie zaczyna się przenikanie do następnego
                rec_out = frames(nxt.start, fps) - max(1, frames(tr_next.duration, fps)) // 2
        if tr is not None and prev is not None and abs(prev.end - c.start) <= tol:
            # przenikanie: zdarzenie zerowej długości klipu A + zdarzenie D dla klipu B
            d = max(1, frames(tr.duration, fps))
            pm = media_of(project, prev.media_id)
            preel = _reel(pm.name if pm else "AX", reels, prev.media_id)
            t_a = rec_in - d // 2
            pa_src = frames(prev.src_in + (t_a / fps - prev.start) * effective_speed(prev), fps)
            ev += 1
            lines.append(f"{ev:03d}  {preel:<8} V     C        {_tc(pa_src, fps)} {_tc(pa_src, fps)} "
                         f"{_tc(t_a, fps)} {_tc(t_a, fps)}")
            b_src = src_in - round((d // 2) * sp)
            lines.append(f"{ev:03d}  {reel:<8} V     D    {d:03d} {_tc(b_src, fps)} "
                         f"{_tc(src_in + round((rec_out - rec_in) * sp), fps)} {_tc(t_a, fps)} {_tc(rec_out, fps)}")
            kind = {"dip_black": "DIP TO BLACK", "dip_white": "DIP TO WHITE"}.get(tr.kind, "DISSOLVE")
            lines.append(f"* EFFECT NAME: {kind}")
        else:
            ev += 1
            lines.append(f"{ev:03d}  {reel:<8} V     C        {_tc(src_in, fps)} "
                         f"{_tc(src_in + round((rec_out - rec_in) * sp), fps)} {_tc(rec_in, fps)} {_tc(rec_out, fps)}")
        if abs(sp - 1.0) > 1e-4:
            lines.append(f"M2   {reel:<8}       {fps * sp:05.1f}                {_tc(src_in, fps)}")
        lines.append(f"* FROM CLIP NAME: {m.name}")
        lines.append(f"* SOURCE FILE: {m.path}")
        if c.label:
            lines.append(f"* COMMENT: {c.label}")
        lines.append("")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------------------------
# OpenTimelineIO (schemat JSON zgodny z OTIO: Timeline.1 / Stack.1 / Track.1 / Clip.2 / Gap.1 / Transition.1)
# ---------------------------------------------------------------------------------------------
def _rt(value: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(rate), "value": float(value)}


def _range(start: float, dur: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start, rate), "duration": _rt(dur, rate)}


def _gap(dur_f: int, rate: float) -> dict:
    return {"OTIO_SCHEMA": "Gap.1", "name": "", "source_range": _range(0, dur_f, rate), "effects": [],
            "markers": [], "enabled": True, "metadata": {}}


def _clip(name: str, path: str, src_in_f: float, dur_f: int, rate: float, speed: float, meta: dict) -> dict:
    eff = []
    if abs(speed - 1.0) > 1e-4:
        eff.append({"OTIO_SCHEMA": "LinearTimeWarp.1", "name": "speed", "effect_name": "LinearTimeWarp",
                    "time_scalar": float(speed), "metadata": {}})
    return {"OTIO_SCHEMA": "Clip.2", "name": name, "source_range": _range(src_in_f, dur_f, rate), "effects": eff,
            "markers": [], "enabled": True, "metadata": {"studioedit": meta},
            "media_references": {"DEFAULT_MEDIA": {"OTIO_SCHEMA": "ExternalReference.1", "name": Path(path).name,
                                                   "target_url": fileurl(path), "available_range": None,
                                                   "available_image_bounds": None, "metadata": {}}},
            "active_media_reference_key": "DEFAULT_MEDIA"}


def _track(name: str, kind: str, children: list) -> dict:
    return {"OTIO_SCHEMA": "Track.1", "name": name, "kind": kind, "children": children, "effects": [],
            "markers": [], "enabled": True, "source_range": None, "metadata": {}}


def build_otio(project, timeline: Timeline) -> dict:
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    fps = float(tl.fps or 30)
    tracks = []
    tol = 1.5 / fps + 0.02
    for tr_name in ("V1", "V2", "V3"):
        children: list = []
        cur = 0
        clips = video_clips(tl, tr_name)
        for i, c in enumerate(clips):
            m = media_of(project, c.media_id)
            if m is None:
                continue
            s, e = frames(c.start, fps), frames(c.end, fps)
            if s > cur:
                children.append(_gap(s - cur, fps))
            if tr_name == "V1" and i > 0:
                t = next((t for t in tl.transitions if t.kind != "cut" and abs(t.at - c.start) <= tol), None)
                if t is not None and children and children[-1].get("OTIO_SCHEMA") == "Clip.2":
                    h = frames(t.duration, fps) / 2
                    children.append({"OTIO_SCHEMA": "Transition.1", "name": t.kind,
                                     "transition_type": "SMPTE_Dissolve" if t.kind in ("dissolve", "light_leak")
                                     else "Custom_Transition", "in_offset": _rt(h, fps), "out_offset": _rt(h, fps),
                                     "metadata": {"studioedit": {"kind": t.kind}}})
            sp = effective_speed(c)
            children.append(_clip(c.label or m.name, m.path, c.src_in * fps, max(1, e - s), fps, sp,
                                  {"clip_id": c.id, "role": c.role, "speed": sp, "opacity": c.opacity,
                                   "transform": [k.model_dump() for k in c.transform]}))
            cur = max(cur, e)
        tracks.append(_track(tr_name, "Video", children))
    items = audio_items(project, tl)
    for tr_name in ("A1", "A2", "A3", "A4", "A5"):
        children = []
        cur = 0
        for it in sorted((i for i in items if i.track == tr_name), key=lambda i: i.start):
            s = frames(it.start, fps)
            d = max(1, frames(it.duration, fps))
            if s < cur:
                continue  # nakładające się klipy - OTIO wymaga sekwencji na ścieżce
            if s > cur:
                children.append(_gap(s - cur, fps))
            children.append(_clip(it.label, it.path, it.src_in * fps, d, fps, it.speed, {"gain_db": it.gain_db}))
            cur = s + d
        tracks.append(_track(tr_name, "Audio", children))
    markers = [{"OTIO_SCHEMA": "Marker.2", "name": mk.label, "color": "RED" if mk.kind == "qc" else "PURPLE",
                "marked_range": _range(frames(mk.t, fps), 0, fps), "comment": mk.kind, "metadata": {}}
               for mk in tl.markers]
    markers += [{"OTIO_SCHEMA": "Marker.2", "name": f"Tytuł: {t.text}", "color": "YELLOW",
                 "marked_range": _range(frames(t.start, fps), frames(t.duration, fps), fps), "comment": t.style,
                 "metadata": {}} for t in title_clips(tl)]
    name = getattr(getattr(project, "meta", None), "name", "") or "StudioEdit"
    return {"OTIO_SCHEMA": "Timeline.1", "name": name, "global_start_time": None,
            "metadata": {"studioedit": {"version": tl.version, "width": tl.width, "height": tl.height}},
            "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks, "effects": [],
                       "markers": markers, "enabled": True, "source_range": None, "metadata": {}}}


def export_otio(project, timeline: Timeline, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(build_otio(project, timeline), ensure_ascii=False, indent=1), encoding="utf-8")
    return out_path

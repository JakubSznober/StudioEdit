"""Eksport do Adobe Premiere Pro: Final Cut Pro 7 XML (xmeml v4).

Zawartość: biny wg MediaItem.bin (zawsze także „Odrzucone_Duble”), sekwencja na każdy włączony wariant
(rozmiar wariantu, fps osi), ścieżki V1 A-Roll / V2 B-Roll / V3 VFX+Napisy, A1 Dialogi / A2 Room Tone /
A3 Muzyka / A4 SFX / A5 Lektor, prędkość (Time Remap), Basic Motion (skala + środek, klatki kluczowe
punch-in / slow zoom / reframe), Cross Dissolve / Dip to Black/White, markery, tytuły jako generator „Text”,
napisy w pliku SRT obok XML.

Uwaga: `<when>` klatek kluczowych liczone w klatkach mediów (ta sama przestrzeń co in/out klipu).
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from ..project.model import Timeline, VClip, Variant
from . import (audio_items, base_scale, bins_of, effective_speed, enabled_variants, fit_mode, frames, kf_value,
               media_dims, media_of, pathurl, rate_info, reframe_at, safe_name, title_clips, video_clips, write_srt)

TRACK_NAMES = {"V1": "A-Roll", "V2": "B-Roll", "V3": "VFX / Napisy", "A1": "Dialogi", "A2": "Room Tone",
               "A3": "Muzyka", "A4": "SFX", "A5": "Lektor"}
TRANSITION_FX = {"dip_black": ("Dip to Black", "Dip to Black"), "dip_white": ("Dip to White", "Dip to White")}


def _sub(parent: ET.Element, tag: str, text=None, **attrib) -> ET.Element:
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attrib.items()})
    if text is not None:
        e.text = str(text)
    return e


def _rate(parent: ET.Element, fps: float) -> ET.Element:
    tb, ntsc = rate_info(fps)
    r = _sub(parent, "rate")
    _sub(r, "timebase", tb)
    _sub(r, "ntsc", "TRUE" if ntsc else "FALSE")
    return r


def _param(eff: ET.Element, pid: str, name: str, value=None, vmin=None, vmax=None) -> ET.Element:
    p = _sub(eff, "parameter", authoringApp="PremierePro")
    _sub(p, "parameterid", pid)
    _sub(p, "name", name)
    if vmin is not None:
        _sub(p, "valuemin", vmin)
    if vmax is not None:
        _sub(p, "valuemax", vmax)
    if value is not None:
        _sub(p, "value", value)
    return p


class _Ctx:
    def __init__(self, project, tl: Timeline):
        self.project = project
        self.tl = tl
        self.fps = float(tl.fps or 30.0)
        self.file_ids: dict[str, str] = {}
        self.defined: set[str] = set()
        self.n = 0

    def nid(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}-{self.n}"

    def file_el(self, parent: ET.Element, path: str, m=None) -> ET.Element:
        key = str(Path(path).resolve()).lower()
        if key not in self.file_ids:
            self.file_ids[key] = f"file-{len(self.file_ids) + 1}"
        fid = self.file_ids[key]
        f = _sub(parent, "file", id=fid)
        if fid in self.defined:
            return f
        self.defined.add(fid)
        info = (m.info if m is not None else {}) or {}
        if not info:
            try:
                from ..media.ffmpeg import media_info

                info = media_info(path)
            except Exception:
                info = {}
        ffps = float(info.get("fps") or self.fps) or self.fps
        _sub(f, "name", Path(path).name)
        _sub(f, "pathurl", pathurl(path))
        _rate(f, ffps)
        dur = float(info.get("duration") or 0)
        _sub(f, "duration", max(1, frames(dur, ffps)) if dur else 1)
        tc = _sub(f, "timecode")
        _rate(tc, ffps)
        _sub(tc, "string", "00:00:00:00")
        _sub(tc, "frame", 0)
        _sub(tc, "displayformat", "NDF")
        media = _sub(f, "media")
        if info.get("has_video", info.get("width")) and info.get("width"):
            v = _sub(media, "video")
            sc = _sub(v, "samplecharacteristics")
            _rate(sc, ffps)
            _sub(sc, "width", info.get("width"))
            _sub(sc, "height", info.get("height"))
            _sub(sc, "anamorphic", "FALSE")
            _sub(sc, "pixelaspectratio", "square")
            _sub(sc, "fielddominance", "none")
        if info.get("has_audio"):
            a = _sub(media, "audio")
            sc = _sub(a, "samplecharacteristics")
            _sub(sc, "depth", 16)
            _sub(sc, "samplerate", info.get("sample_rate") or 48000)
            _sub(a, "channelcount", info.get("channels") or 2)
        return f


def _basic_motion(ctx: _Ctx, ci: ET.Element, clip: VClip, variant: Variant, track: str, in_f: int) -> None:
    m = media_of(ctx.project, clip.media_id)
    sw, sh = media_dims(m)
    W, H = variant.width, variant.height
    mode = fit_mode(clip, sw, sh, variant, track)
    bs = base_scale(mode, sw, sh, W, H, variant.width)
    sp = effective_speed(clip)
    taus = sorted({0.0, clip.duration, *[k.t for k in clip.transform if 0 <= k.t <= clip.duration]})
    path = clip.reframe.get(variant.name) if mode == "reframe" else None
    if path:
        taus = sorted(set(taus) | {float(p.get("t", 0)) for p in path if 0 <= float(p.get("t", 0)) <= clip.duration})
    scales = [bs * kf_value(clip, t, "scale") * 100 for t in taus]
    centers = []
    for t in taus:
        s = bs * kf_value(clip, t, "scale")
        cx, cy = reframe_at(clip, variant.name, t) if path else (0.5, 0.5)
        horiz = kf_value(clip, t, "x") + (0.5 - cx) * sw * s / W
        vert = kf_value(clip, t, "y") + (0.5 - cy) * sh * s / H
        centers.append((horiz, vert))
    rots = [kf_value(clip, t, "rot") for t in taus]
    static = max(scales) - min(scales) < 1e-3 and len({(round(a, 4), round(b, 4)) for a, b in centers}) == 1 \
        and max(rots) - min(rots) < 1e-3
    if static and abs(scales[0] - 100) < 1e-3 and abs(centers[0][0]) < 1e-6 and abs(centers[0][1]) < 1e-6 \
            and abs(rots[0]) < 1e-6:
        return
    flt = _sub(ci, "filter")
    eff = _sub(flt, "effect")
    _sub(eff, "name", "Basic Motion")
    _sub(eff, "effectid", "basic")
    _sub(eff, "effectcategory", "motion")
    _sub(eff, "effecttype", "motion")
    _sub(eff, "mediatype", "video")
    whens = [in_f + frames(t * sp, ctx.fps) for t in taus]
    p = _param(eff, "scale", "Scale", None if not static else round(scales[0], 3), 0, 1000)
    if not static:
        _sub(p, "value", round(scales[0], 3))
        for w, s in zip(whens, scales):
            k = _sub(p, "keyframe")
            _sub(k, "when", w)
            _sub(k, "value", round(s, 3))
    p = _param(eff, "rotation", "Rotation", round(rots[0], 3), -8640, 8640)
    p = _param(eff, "center", "Center", None)
    v = _sub(p, "value")
    _sub(v, "horiz", round(centers[0][0], 6))
    _sub(v, "vert", round(centers[0][1], 6))
    if not static:
        for w, (h_, v_) in zip(whens, centers):
            k = _sub(p, "keyframe")
            _sub(k, "when", w)
            kv = _sub(k, "value")
            _sub(kv, "horiz", round(h_, 6))
            _sub(kv, "vert", round(v_, 6))


def _time_remap(ci: ET.Element, speed: float) -> None:
    flt = _sub(ci, "filter")
    eff = _sub(flt, "effect")
    _sub(eff, "name", "Time Remap")
    _sub(eff, "effectid", "timeremap")
    _sub(eff, "effectcategory", "motion")
    _sub(eff, "effecttype", "motion")
    _sub(eff, "mediatype", "video")
    _param(eff, "variablespeed", "variablespeed", 0, 0, 1)
    _param(eff, "speed", "speed", round(speed * 100, 4), -100000, 100000)
    _param(eff, "reverse", "reverse", "FALSE")
    _param(eff, "frameblending", "frameblending", "FALSE")


def _opacity(ci: ET.Element, op: float) -> None:
    flt = _sub(ci, "filter")
    eff = _sub(flt, "effect")
    _sub(eff, "name", "Opacity")
    _sub(eff, "effectid", "opacity")
    _sub(eff, "effectcategory", "motion")
    _sub(eff, "effecttype", "motion")
    _sub(eff, "mediatype", "video")
    _param(eff, "opacity", "Opacity", round(op * 100, 2), 0, 100)


def _video_clipitem(ctx: _Ctx, track_el: ET.Element, clip: VClip, variant: Variant, track: str) -> None:
    m = media_of(ctx.project, clip.media_id)
    if m is None:
        return
    fps = ctx.fps
    sp = effective_speed(clip)
    start = frames(clip.start, fps)
    end = start + max(1, frames(clip.duration, fps))
    still = m.kind == "image" or clip.role == "image"
    in_f = 0 if still else frames(clip.src_in, fps)
    out_f = (end - start) if still else in_f + max(1, round((end - start) * sp))
    ci = _sub(track_el, "clipitem", id=ctx.nid("clipitem"))
    _sub(ci, "masterclipid", f"masterclip-{m.id}")
    _sub(ci, "name", clip.label or m.name)
    _sub(ci, "enabled", "TRUE")
    dur = float((m.info or {}).get("duration") or 0)
    _sub(ci, "duration", max(out_f, frames(dur, fps)) if dur else out_f)
    _rate(ci, fps)
    _sub(ci, "start", start)
    _sub(ci, "end", end)
    _sub(ci, "in", in_f)
    _sub(ci, "out", out_f)
    _sub(ci, "alphatype", "straight" if track == "V3" else "none")
    ctx.file_el(ci, m.path, m)
    if not still and abs(sp - 1.0) > 1e-4:
        _time_remap(ci, sp)
    _basic_motion(ctx, ci, clip, variant, track, in_f)
    if clip.opacity < 0.999:
        _opacity(ci, clip.opacity)
    if clip.speed_ramp:
        _sub(ci, "comments").text = "Speed ramp uproszczony do średniej prędkości"


def _transition(ctx: _Ctx, track_el: ET.Element, tr, fps: float) -> None:
    d = max(2, frames(tr.duration, fps))
    cut = frames(tr.at, fps)
    ti = _sub(track_el, "transitionitem")
    _rate(ti, fps)
    _sub(ti, "start", cut - d // 2)
    _sub(ti, "end", cut - d // 2 + d)
    _sub(ti, "alignment", "center")
    eff = _sub(ti, "effect")
    name, eid = TRANSITION_FX.get(tr.kind, ("Cross Dissolve", "Cross Dissolve"))
    _sub(eff, "name", name)
    _sub(eff, "effectid", eid)
    _sub(eff, "effectcategory", "Dissolve")
    _sub(eff, "effecttype", "transition")
    _sub(eff, "mediatype", "video")
    _sub(eff, "wipecode", 0)
    _sub(eff, "wipeaccuracy", 100)
    _sub(eff, "startratio", 0)
    _sub(eff, "endratio", 1)
    _sub(eff, "reverse", "FALSE")


def _title_item(ctx: _Ctx, track_el: ET.Element, t, variant: Variant) -> None:
    fps = ctx.fps
    start = frames(t.start, fps)
    end = start + max(1, frames(t.duration, fps))
    g = _sub(track_el, "generatoritem", id=ctx.nid("generator"))
    _sub(g, "name", f"Tytuł: {t.text[:40]}")
    _sub(g, "duration", end - start)
    _rate(g, fps)
    _sub(g, "start", start)
    _sub(g, "end", end)
    _sub(g, "in", 0)
    _sub(g, "out", end - start)
    _sub(g, "enabled", "TRUE")
    _sub(g, "anamorphic", "FALSE")
    _sub(g, "alphatype", "black")
    eff = _sub(g, "effect")
    _sub(eff, "name", "Text")
    _sub(eff, "effectid", "Text")
    _sub(eff, "effectcategory", "Text")
    _sub(eff, "effecttype", "generator")
    _sub(eff, "mediatype", "video")
    p = t.params or {}
    sub = p.get("subtitle")
    _param(eff, "str", "Text", t.text + (f"\r{sub}" if sub else ""))
    _param(eff, "fontname", "Font", p.get("font") or "Segoe UI Black")
    size = float(p.get("size") or min(variant.width, variant.height) * (0.09 if t.style == "title" else 0.055))
    _param(eff, "fontsize", "Size", round(size, 1), 0, 1000)
    _param(eff, "fontstyle", "Style", 1, 1, 4)
    _param(eff, "fontalign", "Alignment", 2 if t.style != "lower_third" else 1, 1, 3)
    pos = t.position or "bottom"
    vert = {"top": -0.35, "middle": 0.0, "bottom": 0.33}.get(pos, 0.33)
    horiz = -0.3 if t.style == "lower_third" else 0.0
    o = _param(eff, "origin", "Origin")
    v = _sub(o, "value")
    _sub(v, "horiz", horiz)
    _sub(v, "vert", vert)
    c = _param(eff, "fontcolor", "Font Color")
    cv = _sub(c, "value")
    col = str(p.get("color") or "#FFFFFF").lstrip("#")
    try:
        r_, g_, b_ = int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16)
    except (ValueError, IndexError):
        r_, g_, b_ = 255, 255, 255
    for k, val in (("alpha", 255), ("red", r_), ("green", g_), ("blue", b_)):
        _sub(cv, k, val)


def _audio_clipitem(ctx: _Ctx, track_el: ET.Element, it) -> None:
    fps = ctx.fps
    m = media_of(ctx.project, it.media_id)
    start = frames(it.start, fps)
    end = start + max(1, frames(it.duration, fps))
    in_f = frames(it.src_in, fps)
    out_f = in_f + max(1, round((end - start) * it.speed))
    ci = _sub(track_el, "clipitem", id=ctx.nid("clipitem"))
    if m is not None:
        _sub(ci, "masterclipid", f"masterclip-{m.id}")
    _sub(ci, "name", it.label)
    _sub(ci, "enabled", "TRUE")
    _sub(ci, "duration", out_f)
    _rate(ci, fps)
    _sub(ci, "start", start)
    _sub(ci, "end", end)
    _sub(ci, "in", in_f)
    _sub(ci, "out", out_f)
    ctx.file_el(ci, it.path, m)
    st = _sub(ci, "sourcetrack")
    _sub(st, "mediatype", "audio")
    _sub(st, "trackindex", 1)
    if abs(it.gain_db) > 0.01:
        flt = _sub(ci, "filter")
        eff = _sub(flt, "effect")
        _sub(eff, "name", "Audio Levels")
        _sub(eff, "effectid", "audiolevels")
        _sub(eff, "effectcategory", "audiolevels")
        _sub(eff, "effecttype", "audiolevels")
        _sub(eff, "mediatype", "audio")
        _param(eff, "level", "Level", round(10 ** (it.gain_db / 20), 5), 0, 3.98109)


def _sequence(ctx: _Ctx, parent: ET.Element, variant: Variant, name: str) -> ET.Element:
    tl, fps = ctx.tl, ctx.fps
    seq = _sub(parent, "sequence", id=ctx.nid("sequence"))
    _sub(seq, "name", f"{name} – {variant.name}")
    total = max(1, frames(tl.duration, fps))
    _sub(seq, "duration", total)
    _rate(seq, fps)
    tc = _sub(seq, "timecode")
    _rate(tc, fps)
    _sub(tc, "string", "00:00:00:00")
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")
    media = _sub(seq, "media")
    video = _sub(media, "video")
    fmt = _sub(video, "format")
    sc = _sub(fmt, "samplecharacteristics")
    _rate(sc, fps)
    _sub(sc, "width", variant.width)
    _sub(sc, "height", variant.height)
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")
    trans = sorted([t for t in tl.transitions if t.kind != "cut"], key=lambda t: t.at)
    for track in ("V1", "V2", "V3"):
        video.append(ET.Comment(f" {track} {TRACK_NAMES[track]} "))
        tr_el = _sub(video, "track")
        items: list[tuple[float, int, object]] = [(c.start, 1, c) for c in video_clips(tl, track)]
        if track == "V1":
            items += [(t.at, 0, t) for t in trans]
        if track == "V3":
            items += [(t.start, 2, t) for t in title_clips(tl)]
        for _s, kind, obj in sorted(items, key=lambda x: (x[0], x[1])):
            if kind == 1:
                _video_clipitem(ctx, tr_el, obj, variant, track)  # type: ignore[arg-type]
            elif kind == 0:
                _transition(ctx, tr_el, obj, fps)
            else:
                _title_item(ctx, tr_el, obj, variant)
        _sub(tr_el, "enabled", "TRUE")
        _sub(tr_el, "locked", "FALSE")
    audio = _sub(media, "audio")
    _sub(audio, "numOutputChannels", 2)
    afmt = _sub(audio, "format")
    asc = _sub(afmt, "samplecharacteristics")
    _sub(asc, "depth", 16)
    _sub(asc, "samplerate", tl.sample_rate or 48000)
    items = audio_items(ctx.project, tl)
    for track in ("A1", "A2", "A3", "A4", "A5"):
        audio.append(ET.Comment(f" {track} {TRACK_NAMES[track]} "))
        tr_el = _sub(audio, "track")
        for it in sorted((i for i in items if i.track == track), key=lambda i: i.start):
            _audio_clipitem(ctx, tr_el, it)
        _sub(tr_el, "enabled", "TRUE")
        _sub(tr_el, "locked", "FALSE")
        _sub(tr_el, "outputchannelindex", 1)
    for mk in tl.markers:
        me = _sub(seq, "marker")
        _sub(me, "comment", mk.kind)
        _sub(me, "name", mk.label)
        _sub(me, "in", frames(mk.t, fps))
        _sub(me, "out", -1)
    return seq


def _master_clip(ctx: _Ctx, parent: ET.Element, m) -> None:
    info = m.info or {}
    ffps = float(info.get("fps") or ctx.fps) or ctx.fps
    clip = _sub(parent, "clip", id=f"masterclip-{m.id}")
    _sub(clip, "masterclipid", f"masterclip-{m.id}")
    _sub(clip, "ismasterclip", "TRUE")
    dur = float(info.get("duration") or 0)
    _sub(clip, "duration", max(1, frames(dur, ffps)) if dur else 1)
    _rate(clip, ffps)
    _sub(clip, "name", m.name)
    media = _sub(clip, "media")
    kind = "audio" if m.kind == "audio" else "video"
    tr = _sub(_sub(media, kind), "track")
    ci = _sub(tr, "clipitem", id=f"clipitem-m-{m.id}")
    _sub(ci, "masterclipid", f"masterclip-{m.id}")
    _sub(ci, "name", m.name)
    _rate(ci, ffps)
    ctx.file_el(ci, m.path, m)
    if m.flags:
        _sub(clip, "comments").text = ", ".join(m.flags)


def build_xml(project, timeline: Timeline) -> ET.ElementTree:
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    ctx = _Ctx(project, tl)
    root = ET.Element("xmeml", version="4")
    proj = _sub(root, "project")
    name = getattr(getattr(project, "meta", None), "name", "") or "StudioEdit"
    _sub(proj, "name", name)
    children = _sub(proj, "children")
    for bin_name, items in sorted(bins_of(project).items(), key=lambda kv: (kv[0] == "Odrzucone_Duble", kv[0])):
        b = _sub(children, "bin")
        _sub(b, "name", bin_name)
        bc = _sub(b, "children")
        for m in items:
            if not Path(m.path).exists():
                continue
            _master_clip(ctx, bc, m)
    seqbin = _sub(children, "bin")
    _sub(seqbin, "name", "Sekwencje")
    sc = _sub(seqbin, "children")
    for v in enabled_variants(tl):
        _sequence(ctx, sc, v, name)
    return ET.ElementTree(root)


def validate_xml(path: Path, timeline: Timeline) -> dict:
    """Parsowanie XML + kontrola liczby klatek (end-start = długość klipu, zgodność z osią)."""
    tree = ET.parse(str(path))
    root = tree.getroot()
    assert root.tag == "xmeml" and root.get("version") == "4"
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    fps = float(tl.fps or 30)
    problems: list[str] = []
    seqs = root.findall(".//sequence")
    for seq in seqs:
        dur = int(seq.findtext("duration") or 0)
        if abs(dur - frames(tl.duration, fps)) > 1:
            problems.append(f"Długość sekwencji {dur} != {frames(tl.duration, fps)}")
        v1 = seq.find("./media/video/track")
        items = v1.findall("clipitem") if v1 is not None else []
        clips = video_clips(tl, "V1")
        if len(items) != len(clips):
            problems.append(f"V1: {len(items)} klipów w XML vs {len(clips)} na osi")
        for ci, c in zip(items, clips):
            s, e = int(ci.findtext("start")), int(ci.findtext("end"))
            if abs((e - s) - frames(c.duration, fps)) > 1:
                problems.append(f"Klip {c.id}: {e - s} klatek vs {frames(c.duration, fps)}")
        ids = [f.get("id") for f in root.iter("file")]
        defined = [f.get("id") for f in root.iter("file") if f.find("pathurl") is not None]
        missing = set(ids) - set(defined)
        if missing:
            problems.append(f"Pliki bez definicji: {sorted(missing)}")
    return {"ok": not problems, "sequences": len(seqs), "problems": problems}


def export_premiere_xml(project, timeline: Timeline, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree = build_xml(project, timeline)
    ET.indent(tree, space=" ")
    tmp = out_path.with_suffix(".xml.tmp")
    with open(tmp, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)
    tmp.replace(out_path)
    rep = validate_xml(out_path, timeline)
    if not rep["ok"]:
        from ..events import log

        log("render", "Premiere XML: " + "; ".join(rep["problems"][:5]), path="Premiere XML", level="warn",
            project_id=getattr(project, "id", None))
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    if tl.subtitles.enabled:
        write_srt(project, tl, out_path.with_suffix(".srt"))
    return out_path

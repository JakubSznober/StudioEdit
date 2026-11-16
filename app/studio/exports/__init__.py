"""Eksport osi do innych programów: Premiere Pro (FCP7 XML), After Effects (JSX), CMX3600 EDL, OTIO JSON.

Wspólne narzędzia: ścieżki URL, klatki, mapowanie dopasowania kadru, napisy SRT, lista elementów audio.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..project.model import AClip, TitleClip, Timeline, VClip, Variant


def pathurl(path: str | Path) -> str:
    """file://localhost/C:/... z kodowaniem URL (spacje, polskie znaki)."""
    p = str(Path(path).resolve()).replace("\\", "/")
    return "file://localhost/" + quote(p, safe="/:")


def fileurl(path: str | Path) -> str:
    p = str(Path(path).resolve()).replace("\\", "/")
    return "file:///" + quote(p, safe="/:")


def rate_info(fps: float) -> tuple[int, bool]:
    """(timebase, ntsc) wg konwencji FCP7: 29.97 -> (30, TRUE)."""
    r = round(fps)
    ntsc = abs(fps - r) > 0.005 and abs(fps - r * 1000 / 1001) < 0.02
    return max(1, int(r)), ntsc


def frames(t: float, fps: float) -> int:
    return int(math.floor(t * fps + 0.5))


def safe_name(name: str, maxlen: int = 60) -> str:
    s = unicodedata.normalize("NFKC", str(name or "projekt"))
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", s).strip(" .")
    return (s or "projekt")[:maxlen]


def media_of(project, mid: str | None):
    if not mid:
        return None
    try:
        return project.get_media(mid)
    except Exception:
        return None


def media_dims(m) -> tuple[int, int]:
    info = (m.info or {}) if m is not None else {}
    w, h = int(info.get("width") or 0), int(info.get("height") or 0)
    if (not w or not h) and m is not None:
        try:
            from ..media.ffmpeg import media_info

            i2 = media_info(m.path)
            w, h = int(i2.get("width") or 0), int(i2.get("height") or 0)
        except Exception:
            pass
    return w or 1920, h or 1080


def fit_mode(clip: VClip, src_w: int, src_h: int, variant: Variant, track: str) -> str:
    """Ta sama reguła co w renderze: reframe | cover | blur (contain z rozmytym tłem) | contain (grafiki V3)."""
    if track == "V3":
        return "contain"
    ratio = (src_w / max(1, src_h)) / (variant.width / max(1, variant.height))
    path = clip.reframe.get(variant.name) if isinstance(clip.reframe, dict) else None
    if path and 1 / 3.5 <= ratio <= 3.5:
        return "reframe"
    if abs(math.log(ratio)) < 0.03:
        return "cover"
    if ratio < 0.7 or ratio > 1.4:
        return "blur"
    return "cover"


def base_scale(mode: str, src_w: int, src_h: int, W: int, H: int, variant_w: int | None = None) -> float:
    """Skala (1.0 = piksele źródła) dopasowująca materiał do kadru."""
    cover = max(W / src_w, H / src_h)
    contain = min(W / src_w, H / src_h)
    if mode in ("cover", "reframe"):
        return cover
    if mode == "contain":
        return min(contain, W / max(1, variant_w or W))
    return contain


def kf_value(clip: VClip, tau: float, attr: str) -> float:
    kf = sorted(clip.transform, key=lambda k: k.t)
    if not kf:
        return 1.0 if attr == "scale" else 0.0
    if tau <= kf[0].t:
        return getattr(kf[0], attr)
    for a, b in zip(kf[:-1], kf[1:]):
        if tau < b.t:
            u = (tau - a.t) / max(1e-6, b.t - a.t)
            if a.ease == "hold":
                u = 0.0
            elif a.ease == "ease":
                u = u * u * (3 - 2 * u)
            return getattr(a, attr) + (getattr(b, attr) - getattr(a, attr)) * u
    return getattr(kf[-1], attr)


def reframe_at(clip: VClip, variant: str, tau: float) -> tuple[float, float]:
    path = sorted((p for p in (clip.reframe.get(variant) or []) if isinstance(p, dict)), key=lambda p: p.get("t", 0))
    if not path:
        return 0.5, 0.5
    if tau <= path[0].get("t", 0):
        return float(path[0].get("cx", .5)), float(path[0].get("cy", .5))
    for a, b in zip(path[:-1], path[1:]):
        if tau < b.get("t", 0):
            u = (tau - a.get("t", 0)) / max(1e-6, b.get("t", 0) - a.get("t", 0))
            return (a.get("cx", .5) + (b.get("cx", .5) - a.get("cx", .5)) * u,
                    a.get("cy", .5) + (b.get("cy", .5) - a.get("cy", .5)) * u)
    return float(path[-1].get("cx", .5)), float(path[-1].get("cy", .5))


def effective_speed(clip: VClip) -> float:
    return max(0.01, (clip.src_out - clip.src_in) / max(1e-6, clip.duration))


@dataclass
class AudioItem:
    track: str
    path: str
    media_id: str | None
    start: float
    src_in: float
    duration: float
    gain_db: float
    label: str
    speed: float = 1.0
    link: str | None = None


def audio_items(project, tl: Timeline) -> list[AudioItem]:
    """A1..A5; gdy A1 puste - audio z klipów V1 (a_roll) trafia na A1."""
    out: list[AudioItem] = []
    a1 = [c for c in tl.tracks.get("A1", []) if isinstance(c, AClip)]
    if not a1:
        for c in tl.tracks.get("V1", []):
            if not isinstance(c, VClip) or c.role == "image":
                continue
            m = media_of(project, c.media_id)
            if m is None or m.kind == "image" or (m.info and m.info.get("has_audio") is False):
                continue
            out.append(AudioItem("A1", m.path, m.id, c.start, c.src_in, c.duration, 0.0, c.label or m.name,
                                 effective_speed(c), c.id))
    for tr in ("A1", "A2", "A3", "A4", "A5"):
        for c in tl.tracks.get(tr, []):
            if not isinstance(c, AClip):
                continue
            m = media_of(project, c.media_id)
            path = c.path or (m.path if m else None)
            if not path:
                continue
            out.append(AudioItem(tr, path, c.media_id, c.start, c.src_in, c.duration, c.gain_db,
                                 c.label or Path(path).name, 1.0, c.link))
    return out


def video_clips(tl: Timeline, track: str) -> list[VClip]:
    return sorted([c for c in tl.tracks.get(track, []) if isinstance(c, VClip)], key=lambda c: c.start)


def title_clips(tl: Timeline) -> list[TitleClip]:
    out = []
    for k, items in tl.tracks.items():
        if k.startswith("V"):
            out += [c for c in items if isinstance(c, TitleClip)]
    return sorted(out, key=lambda c: c.start)


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(project, tl: Timeline, out: Path, variant: Variant | None = None) -> Path | None:
    """SRT: z modułu napisów (gdy dostępny), inaczej z transkrypcji klipów dialogowych zmapowanych na oś."""
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from ..subtitles.build import build_subtitles  # type: ignore

        v = variant or (tl.variants[0] if tl.variants else Variant(name="youtube"))
        res = build_subtitles(project, tl, v, out.parent / "_subs_tmp") or {}
        srt = res.get("srt")
        if srt and Path(srt).exists():
            out.write_bytes(Path(srt).read_bytes())
            return out
    except Exception:
        pass
    cues: list[tuple[float, float, str]] = []
    for it in audio_items(project, tl):
        if it.track != "A1" or not it.media_id:
            continue
        m = media_of(project, it.media_id)
        if m is None or not m.transcript:
            continue
        s0, s1 = it.src_in, it.src_in + it.duration * it.speed
        for s in m.transcript.sentences:
            if s.e <= s0 or s.s >= s1:
                continue
            a = it.start + (max(s.s, s0) - s0) / it.speed
            b = it.start + (min(s.e, s1) - s0) / it.speed
            if b - a > 0.2:
                cues.append((a, b, s.text.strip()))
    if not cues:
        return None
    cues.sort()
    lines = []
    for i, (a, b, t) in enumerate(cues, 1):
        lines += [str(i), f"{_srt_time(a)} --> {_srt_time(b)}", t, ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def enabled_variants(tl: Timeline) -> list[Variant]:
    vs = [v for v in tl.variants if v.enabled] or [Variant(name="youtube", width=tl.width, height=tl.height,
                                                          fps=tl.fps)]
    return vs


def bins_of(project) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for m in project.media():
        out.setdefault(m.bin or "A-Roll", []).append(m)
    out.setdefault("Odrzucone_Duble", [])
    return out

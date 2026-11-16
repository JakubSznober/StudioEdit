"""Presety platform (warianty) i mapowanie ustawień eksportu na argumenty ffmpeg."""
from __future__ import annotations

from fractions import Fraction
from typing import Any

from ..config import get_config
from ..media.ffmpeg import video_codec_args
from ..project.model import Timeline, Variant

PRESETS: dict[str, dict[str, Any]] = {
    "youtube": {"aspect": "16:9", "width": 1920, "height": 1080, "fps": 30.0, "max_duration": None,
                "label": "YouTube 16:9 (1080p)"},
    "youtube_4k": {"aspect": "16:9", "width": 3840, "height": 2160, "fps": 30.0, "max_duration": None,
                   "label": "YouTube 4K"},
    "tiktok": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 30.0, "max_duration": 60.0,
               "label": "TikTok 9:16"},
    "reels": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 30.0, "max_duration": 90.0,
              "label": "Instagram Reels 9:16"},
    "shorts": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 30.0, "max_duration": 180.0,
               "label": "YouTube Shorts 9:16"},
    "instagram_feed": {"aspect": "4:5", "width": 1080, "height": 1350, "fps": 30.0, "max_duration": None,
                       "label": "Instagram feed 4:5"},
    "master": {"aspect": "16:9", "width": 1920, "height": 1080, "fps": 30.0, "max_duration": None,
               "label": "Master (parametry osi)"},
}

CODECS = ("h264", "hevc", "av1", "prores", "dnxhr")
CONTAINERS = ("mp4", "mov", "mkv")
RESOLUTIONS = {"720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160, "4k": 2160}


def variant_for(name: str) -> Variant:
    """Wariant z presetu platformy (nieznana nazwa -> youtube 16:9 z tą nazwą)."""
    p = PRESETS.get(name, PRESETS["youtube"])
    return Variant(name=name, aspect=p["aspect"], width=p["width"], height=p["height"], fps=p["fps"],
                   max_duration=p["max_duration"])


def resolve_variant(timeline: Timeline, name: str | None) -> Variant:
    """Wariant z osi czasu (jeśli zdefiniowany), inaczej z presetu. "master" = rozmiar i fps osi."""
    name = name or (timeline.variants[0].name if timeline.variants else "youtube")
    for v in timeline.variants:
        if v.name == name:
            return v
    if name == "master":
        g = Fraction(timeline.width, timeline.height)
        return Variant(name="master", aspect=f"{g.numerator}:{g.denominator}", width=timeline.width,
                       height=timeline.height, fps=timeline.fps)
    v = variant_for(name)
    if name not in PRESETS:
        v.fps = timeline.fps
    return v


def fps_fraction(f: float | Fraction | str | None, default: float = 30.0) -> Fraction:
    """Klatkaż jako ułamek (29.97 -> 30000/1001)."""
    if isinstance(f, Fraction):
        return f
    if isinstance(f, str) and "/" in f:
        return Fraction(f)
    x = float(f or default)
    if x <= 0:
        x = default
    if abs(x - round(x)) < 0.005:
        return Fraction(int(round(x)), 1)
    ntsc = round(x * 1.001)
    if abs(x - ntsc * 1000 / 1001) < 0.005:
        return Fraction(ntsc * 1000, 1001)
    return Fraction(x).limit_denominator(1001)


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def normalize_settings(settings: dict | None) -> dict:
    """Ustawienia eksportu z API -> postać znormalizowana."""
    s = dict(settings or {})
    cfg = get_config()
    codec = str(s.get("codec") or "").lower()
    if codec in ("h265", "hevc_nvenc"):
        codec = "hevc"
    if codec in ("h264_nvenc", "avc"):
        codec = "h264"
    if codec not in CODECS:
        vc = str(cfg.get("render.video_codec", "hevc_nvenc"))
        codec = "hevc" if "hevc" in vc else "av1" if "av1" in vc else "h264"
    container = str(s.get("container") or "").lower().lstrip(".")
    if container not in CONTAINERS:
        container = "mov" if codec in ("prores", "dnxhr") else "mp4"
    if codec in ("prores", "dnxhr") and container == "mp4":
        container = "mov"
    acodec = str(s.get("audio_codec") or cfg.get("render.audio_codec", "aac")).lower()
    if acodec in ("wav", "pcm", "pcm_s24le", "pcm_s16le"):
        acodec = "pcm"
        if container == "mp4":
            container = "mov"
    else:
        acodec = "aac"
    q = s.get("quality", "auto")
    base_cq = int(cfg.get("render.cq", 19))
    if isinstance(q, (int, float)) and not isinstance(q, bool):
        cq = int(q)
    elif str(q).isdigit():
        cq = int(str(q))
    elif q == "high":
        cq = max(10, base_cq - 3)
    elif q == "max":
        cq = max(8, base_cq - 6)
    else:
        cq = base_cq
    res = s.get("resolution") or "source"
    return {"codec": codec, "container": container, "audio_codec": acodec, "cq": cq,
            "resolution": str(res).lower(), "fps": s.get("fps"),
            "ten_bit": bool(cfg.get("render.ten_bit", True)) and codec in ("hevc", "av1"),
            "preset": str(cfg.get("render.preset", "p6")),
            "audio_bitrate": str(cfg.get("render.audio_bitrate", "320k")),
            "title": s.get("title")}


def render_size(variant: Variant, settings: dict | None, quality: str) -> tuple[int, int]:
    """Rozmiar klatki renderu (plastrów) dla wariantu, jakości i ustawień."""
    w, h = int(variant.width or 1920), int(variant.height or 1080)
    if quality == "preview":
        ph = int(get_config().get("render.preview_height", 720) or 720)
        if h > ph:
            return _even(w * ph / h), _even(ph)
        return _even(w), _even(h)
    res = (settings or {}).get("resolution") or "source"
    short = RESOLUTIONS.get(str(res).lower())
    if short:
        k = short / min(w, h)
        return _even(w * k), _even(h * k)
    return _even(w), _even(h)


def output_fps(variant: Variant, timeline: Timeline, settings: dict | None = None) -> Fraction:
    f = (settings or {}).get("fps") or variant.fps or timeline.fps
    return fps_fraction(f)


def container_ext(settings: dict | None) -> str:
    return "." + normalize_settings(settings)["container"]


def video_encode_args(norm: dict, fps: Fraction) -> list[str]:
    codec = norm["codec"]
    f = float(fps)
    if codec == "h264":
        return video_codec_args("h264_nvenc", cq=norm["cq"], preset=norm["preset"], ten_bit=False, fps=f)
    if codec == "hevc":
        return video_codec_args("hevc_nvenc", cq=norm["cq"], preset=norm["preset"], ten_bit=norm["ten_bit"], fps=f)
    if codec == "av1":
        return video_codec_args("av1_nvenc", cq=norm["cq"], preset=norm["preset"], ten_bit=norm["ten_bit"], fps=f)
    return video_codec_args(codec)


def audio_encode_args(norm: dict, sample_rate: int = 48000) -> list[str]:
    if norm["audio_codec"] == "pcm":
        return ["-c:a", "pcm_s24le", "-ar", str(sample_rate), "-ac", "2"]
    return ["-c:a", "aac", "-b:a", norm.get("audio_bitrate", "320k"), "-ar", str(sample_rate), "-ac", "2"]


def chunk_codec_args(quality: str, fps: Fraction) -> tuple[list[str], str]:
    """Kodek plastrów pośrednich: (argumenty, pix_fmt filtrów)."""
    cfg = get_config()
    f = float(fps)
    if quality == "preview":
        args = video_codec_args("h264_nvenc", cq=24, preset="p3", ten_bit=False, fps=f, gop_seconds=1.0)
        return _no_bframes(args), "yuv420p"
    codec = str(cfg.get("render.chunk_codec", "h264_nvenc"))
    cq = int(cfg.get("render.chunk_cq", 16))
    if "hevc" in codec:
        args = video_codec_args("hevc_nvenc", cq=min(cq, 16), preset="p5", ten_bit=True, fps=f, gop_seconds=1.0)
        return _no_bframes(args), "yuv420p10le"
    args = video_codec_args("h264_nvenc", cq=cq, preset="p5", ten_bit=False, fps=f, gop_seconds=1.0)
    return _no_bframes(args), "yuv420p"


def _no_bframes(args: list[str]) -> list[str]:
    """Plastry bez B-klatek (czyste łączenie concat bez przesunięć kompozycji)."""
    return [*args, "-bf", "0"]


def timescale(fps: Fraction) -> int:
    return int(fps.numerator * (1000 if fps.denominator == 1 else 1))


def describe() -> list[dict]:
    return [{"name": k, **v} for k, v in PRESETS.items()]

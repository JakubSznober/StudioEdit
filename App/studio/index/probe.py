"""ffprobe → metadane i heurystyka profilu koloru."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .. import FFPROBE

# granice słów przez lookaround, bo "_" w nazwach plików (A7S3_slog3_0001) nie jest granicą dla \b
LOG_HINTS = re.compile(
    r"(?<![a-z0-9])(s-?log ?[23]?|c-?log ?[23]?|v-?log|n-?log|d-?log|f-?log2?|log3?g10|logc|hlg)(?![a-z0-9])", re.I
)


def fingerprint(path: Path) -> str:
    """Szybki odcisk pliku: rozmiar + pierwsze i ostatnie 1 MB. Wystarcza do wykrycia podmiany."""
    st = path.stat()
    h = hashlib.sha1()
    h.update(str(st.st_size).encode())
    with path.open("rb") as f:
        h.update(f.read(1 << 20))
        if st.st_size > (2 << 20):
            f.seek(-(1 << 20), 2)
            h.update(f.read(1 << 20))
    return h.hexdigest()


def ffprobe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [str(FFPROBE), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    return json.loads(out)


def _fps(stream: dict[str, Any]) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        v = stream.get(key, "")
        if "/" in v:
            n, d = v.split("/")
            if float(d) > 0:
                return round(float(n) / float(d), 3)
    return None


def _bit_depth(stream: dict[str, Any]) -> int | None:
    v = stream.get("bits_per_raw_sample")
    if v and str(v).isdigit():
        return int(v)
    pix = stream.get("pix_fmt", "")
    m = re.search(r"p(\d{2})(le|be)?$", pix)
    if m:
        return int(m.group(1))
    return 8 if pix else None


def color_profile(stream: dict[str, Any], fmt_tags: dict[str, Any], path: Path) -> str:
    trc = (stream.get("color_transfer") or "").lower()
    prim = (stream.get("color_primaries") or "").lower()
    if trc in ("smpte2084",):
        return "hdr-pq"
    if trc in ("arib-std-b67",):
        return "hdr-hlg"
    haystack = " ".join(str(v) for v in fmt_tags.values()) + " " + path.name
    if LOG_HINTS.search(haystack):
        return "log"
    if trc in ("bt709", "iec61966-2-1", "bt470bg", "smpte170m", "bt470m", "gamma22", "gamma28"):
        return "sdr"
    if prim in ("bt709", "bt470bg", "smpte170m"):
        return "sdr"
    if trc or prim:
        return "unknown"
    # brak metadanych koloru: typowe dla telefonów i GoPro w SDR, ale LOG też ich nie ma
    return "unknown"


def probe_media(path: Path) -> dict[str, Any]:
    info = ffprobe(path)
    fmt = info.get("format", {})
    tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)

    # obrazy (jpg/png) też mają strumień video z 1 klatką
    is_image = video is not None and video.get("codec_name") in ("mjpeg", "png", "webp", "tiff", "heic", "hevc") and (
        fmt.get("format_name", "").split(",")[0] in ("image2", "png_pipe", "webp_pipe", "tiff_pipe")
    )
    kind = "image" if is_image else ("video" if video else ("audio" if audio else "unknown"))

    row: dict[str, Any] = {
        "kind": kind,
        "duration": float(fmt.get("duration", 0) or 0) or None,
        "camera_make": tags.get("com.apple.quicktime.make") or tags.get("make"),
        "camera_model": tags.get("com.apple.quicktime.model") or tags.get("model"),
        "created_at": tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate"),
        "probe_json": json.dumps(info, ensure_ascii=False),
    }
    if video:
        row.update(
            width=video.get("width"), height=video.get("height"), fps=_fps(video),
            video_codec=video.get("codec_name"), pix_fmt=video.get("pix_fmt"), bit_depth=_bit_depth(video),
            color_primaries=video.get("color_primaries"), color_transfer=video.get("color_transfer"),
            color_space=video.get("color_space"), color_range=video.get("color_range"),
            color_profile=color_profile(video, tags, path),
        )
        if row["duration"] is None and video.get("duration"):
            row["duration"] = float(video["duration"])
    if audio:
        row.update(
            audio_codec=audio.get("codec_name"), audio_channels=audio.get("channels"),
            sample_rate=int(audio["sample_rate"]) if audio.get("sample_rate") else None,
        )
    return row

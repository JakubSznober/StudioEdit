"""Proxy 720p (NVENC) i ścieżka audio 16 kHz mono. Oryginał nigdy nie jest dotykany."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .. import FFMPEG
from ..config import SETTINGS

log = logging.getLogger(__name__)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def proxy_paths(media_id: int, src: Path) -> tuple[Path, Path]:
    base = Path(SETTINGS.proxies_dir) / f"{media_id:06d}_{src.stem}"
    return base.with_suffix(".proxy.mp4"), base.with_suffix(".16k.wav")


def make_proxy(src: Path, dst: Path, color_profile: str | None, has_video: bool = True) -> Path | None:
    if not has_video:
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    h = SETTINGS.proxy_height
    vf = [f"scale=-2:'min({h},ih)'"]
    if color_profile in ("hdr-pq", "hdr-hlg"):
        # proxy SDR do analizy: tone mapping na CPU (zscale+tonemap); kolor finalny robimy osobno
        vf = [
            "zscale=t=linear:npl=100", "format=gbrpf32le", "zscale=p=bt709",
            "tonemap=tonemap=hable:desat=0", "zscale=t=bt709:m=bt709:r=tv", "format=yuv420p",
        ] + vf
    vf.append("format=yuv420p")
    base = [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", ",".join(vf),
            "-map", "0:v:0", "-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart"]
    attempts = [
        base + ["-c:v", SETTINGS.proxy_encoder, "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0", str(dst)],
        base + ["-c:v", "libx264", "-preset", "fast", "-crf", "20", str(dst)],
    ]
    for cmd in attempts:
        r = _run(cmd)
        if r.returncode == 0 and dst.exists():
            return dst
        log.warning("proxy %s nie powiodło się (%s): %s", cmd[cmd.index("-c:v") + 1], src.name, r.stderr.strip()[-400:])
    return None


def extract_audio(src: Path, dst: Path) -> Path | None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = _run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
              "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)])
    if r.returncode != 0:
        log.warning("audio %s: %s", src.name, r.stderr.strip()[-400:])
        return None
    return dst

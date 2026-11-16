"""Archiwista: metadane, flagi techniczne, proxy, miniatury, waveform."""
from __future__ import annotations

from pathlib import Path

from ..config import get_config
from ..media import ffmpeg as ff
from ..project.model import MediaItem

LOG_TAG_HINTS = {
    "slog3": ("s-log3", "slog3", "s-log", "slog"),
    "clog3": ("c-log3", "clog3", "c-log", "clog"),
    "dlog": ("d-log", "dlog", "dji"),
    "vlog": ("v-log", "vlog"),
}


def probe_media(item: MediaItem) -> MediaItem:
    """Wypełnia item.info i flagi wynikające z metadanych (HFR, 10-bit, HDR/LOG z tagów)."""
    info = ff.media_info(item.path)
    item.info = info
    item.kind = info.get("kind", item.kind) if item.kind != "image" else "image"
    flags = set(f for f in item.flags if f not in ("HFR", "10BIT", "HDR", "LOG", "NO_AUDIO", "VERTICAL"))
    if item.kind == "video":
        fps = info.get("fps", 0) or 0
        if fps >= 47:
            flags.add("HFR")
            item.flag_details["hfr_fps"] = fps
            item.flag_details["max_slowmo"] = round(fps / 30.0, 2) if fps < 100 else round(fps / 30.0, 1)
        if info.get("bit_depth", 8) >= 10:
            flags.add("10BIT")
        trc = info.get("color_transfer", "")
        if trc in ("arib-std-b67", "smpte2084"):
            flags.add("HDR")
            item.analysis.log_profile = "hlg" if trc == "arib-std-b67" else "pq"
        tags = " ".join(str(v).lower() for v in (info.get("tags") or {}).values())
        name = item.name.lower()
        for prof, hints in LOG_TAG_HINTS.items():
            if any(h in tags or h in name for h in hints):
                flags.add("LOG")
                item.analysis.log_profile = prof
                break
        if not info.get("has_audio"):
            flags.add("NO_AUDIO")
        if info.get("height", 0) > info.get("width", 0):
            flags.add("VERTICAL")
    item.flags = sorted(flags)
    item.analysis.stage["probe"] = "done"
    return item


def proxy_size(w: int, h: int, short_side: int = 720) -> tuple[int, int]:
    if not w or not h:
        return 1280, 720
    if min(w, h) <= short_side:
        return w - w % 2, h - h % 2
    if w >= h:
        nh = short_side
        nw = int(round(w * nh / h / 2) * 2)
    else:
        nw = short_side
        nh = int(round(h * nw / w / 2) * 2)
    return nw, nh


def make_proxy(item: MediaItem, out_dir: Path, *, progress=None, cancel=None) -> Path:
    """Lekkie proxy 720p H.264 NVENC + AAC (płynny podgląd w przeglądarce)."""
    out = out_dir / f"{item.id}.mp4"
    if out.exists() and out.stat().st_size > 1000:
        return out
    info = item.info
    w, h = proxy_size(info.get("width", 0), info.get("height", 0))
    fps = info.get("fps") or 30
    tmp = out.with_suffix(".part.mp4")
    vf = f"scale={w}:{h}:flags=bicubic,format=yuv420p"
    trc = info.get("color_transfer", "")
    if trc in ("arib-std-b67", "smpte2084") and "zscale" in ff.filters():
        vf = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
              f"zscale=t=bt709:m=bt709:r=tv,scale={w}:{h},format=yuv420p")
    args = [*ff.hw_decode_args(), "-i", item.path, "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
            *ff.video_codec_args("h264_nvenc", cq=26, preset="p4", ten_bit=False, fps=min(fps, 60)),
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000", "-movflags", "+faststart", str(tmp)]
    ff.run(args, duration=info.get("duration"), progress=progress, cancel=cancel)
    tmp.replace(out)
    return out


def make_thumb(item: MediaItem, out_dir: Path) -> Path | None:
    out = out_dir / f"{item.id}.jpg"
    if out.exists():
        return out
    src = item.proxy or item.path
    if item.kind == "audio":
        try:
            ff.run_capture(["-y", "-i", item.path, "-filter_complex",
                            "showwavespic=s=640x360:colors=0x7c5cff|0x5ab0ff", "-frames:v", "1", str(out)])
            return out
        except ff.FFmpegError:
            return None
    t = min(1.5, (item.info.get("duration") or 0) * 0.3) if item.kind == "video" else 0
    try:
        ff.extract_frame(src, t, out, width=480)
        return out
    except ff.FFmpegError:
        return None


def make_waveform(item: MediaItem, out_dir: Path) -> Path | None:
    if not item.info.get("has_audio"):
        return None
    out = out_dir / f"{item.id}_wave.png"
    if out.exists():
        return out
    try:
        ff.run_capture(["-y", "-i", item.proxy or item.path, "-filter_complex",
                        "aformat=channel_layouts=mono,showwavespic=s=1600x120:colors=0x8b7bff", "-frames:v", "1",
                        str(out)], timeout=300)
        return out
    except ff.FFmpegError:
        return None


def scale_profile(project) -> dict:
    prof = get_config().get(f"scale_profiles.{project.meta.scale}") or get_config().get("scale_profiles.medium")
    return prof or {}

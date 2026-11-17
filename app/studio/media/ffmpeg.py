"""Opakowanie FFmpeg/FFprobe: uruchamianie z postępem, probe, NVENC, ekstrakcja klatek i audio."""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

from .. import paths

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class FFmpegError(RuntimeError):
    def __init__(self, msg: str, stderr: str = "", returncode: int | None = None):
        super().__init__(msg)
        self.stderr = stderr
        self.returncode = returncode


class Cancelled(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    return str(paths.FFMPEG) if paths.FFMPEG.exists() else "ffmpeg"


def ffprobe_exe() -> str:
    return str(paths.FFPROBE) if paths.FFPROBE.exists() else "ffprobe"


def _popen(cmd: Sequence[str], **kw) -> subprocess.Popen:
    return subprocess.Popen(list(map(str, cmd)), creationflags=CREATE_NO_WINDOW, **kw)


def run(args: Sequence[str | os.PathLike], *, duration: float | None = None,
        progress: Callable[[float], None] | None = None, cancel: threading.Event | None = None,
        timeout: float | None = None, input_bytes: bytes | None = None) -> str:
    """Uruchamia ffmpeg. `args` bez nazwy programu. Zwraca stderr (log). Rzuca FFmpegError."""
    cmd = [ffmpeg_exe(), "-hide_banner", *(["-nostdin"] if input_bytes is None else []),
           "-y", "-progress", "pipe:1", "-nostats", *map(str, args)]
    proc = _popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL)
    err_chunks: list[bytes] = []

    def _drain_err():
        assert proc.stderr
        for line in iter(proc.stderr.readline, b""):
            err_chunks.append(line)
            if len(err_chunks) > 4000:
                del err_chunks[:2000]

    t = threading.Thread(target=_drain_err, daemon=True)
    t.start()
    if input_bytes is not None:
        assert proc.stdin
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    start = time.time()
    assert proc.stdout
    for raw in iter(proc.stdout.readline, b""):
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise Cancelled("Przerwano")
        if timeout and time.time() - start > timeout:
            proc.kill()
            raise FFmpegError("Przekroczono limit czasu ffmpeg")
        line = raw.decode("utf-8", "ignore").strip()
        if progress and duration and line.startswith("out_time_us="):
            try:
                us = int(line.split("=", 1)[1])
                progress(max(0.0, min(1.0, us / 1e6 / duration)))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=2)
    stderr = b"".join(err_chunks).decode("utf-8", "ignore")
    if proc.returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"ffmpeg zakończył się kodem {proc.returncode}:\n{tail}", stderr, proc.returncode)
    if progress:
        progress(1.0)
    return stderr


def run_capture(args: Sequence[str | os.PathLike], timeout: float | None = 600) -> tuple[bytes, str]:
    """Uruchamia ffmpeg i zwraca (stdout bytes, stderr text) - dla filtrów analizujących."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", *map(str, args)]
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    err = p.stderr.decode("utf-8", "ignore")
    if p.returncode != 0:
        raise FFmpegError(f"ffmpeg błąd {p.returncode}: {err[-1500:]}", err)
    return p.stdout, err


_FFPROBE_BLOCKED = False

_TRC = {1: "bt709", 4: "gamma22", 5: "gamma28", 6: "smpte170m", 8: "linear", 13: "iec61966-2-1", 14: "bt2020-10",
        15: "bt2020-12", 16: "smpte2084", 18: "arib-std-b67"}
_PRIM = {1: "bt709", 5: "bt470bg", 6: "smpte170m", 9: "bt2020", 12: "smpte432"}
_SPACE = {0: "gbr", 1: "bt709", 5: "bt470bg", 6: "smpte170m", 9: "bt2020nc", 10: "bt2020c"}


def _probe_ffprobe(path: str) -> dict:
    global _FFPROBE_BLOCKED
    cmd = [ffprobe_exe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
    except OSError:
        # np. Windows Smart App Control blokuje ffprobe.exe - przechodzimy na PyAV
        _FFPROBE_BLOCKED = True
        raise
    if p.returncode != 0:
        raise FFmpegError(f"ffprobe nie odczytał pliku: {path}", p.stderr.decode("utf-8", "ignore"))
    return json.loads(p.stdout.decode("utf-8", "ignore") or "{}")


def _stream_rotation(container, stream) -> int:
    """Obrót z macierzy wyświetlania (stopnie, konwencja `ffmpeg -i`: „rotation of -90.00 degrees”).

    iPhone i większość telefonów nagrywa pion jako 3840x2160 + obrót -90° w macierzy wyświetlania.
    PyAV ≥ 14 nie wystawia jej na strumieniu (`side_data` zniknęło), ale ma `frame.rotation` na
    zdekodowanej klatce - dekodujemy jedną klatkę (wynik probe jest cache'owany).
    """
    try:
        sd = getattr(stream, "side_data", None)
        if sd is not None:
            dm = sd.get("DISPLAYMATRIX")
            if dm is not None:
                return int(round(float(dm)))
    except Exception:
        pass
    try:
        container.seek(0)
        for frame in container.decode(stream):
            return int(round(float(getattr(frame, "rotation", 0) or 0)))
    except Exception:
        pass
    return 0


def _probe_pyav(path: str) -> dict:
    """Probe przez PyAV, zwraca strukturę zgodną z ffprobe -show_format -show_streams."""
    import av  # type: ignore

    with av.open(path) as c:
        fmt = {"duration": str((c.duration or 0) / 1e6), "size": str(os.path.getsize(path)),
               "format_name": c.format.name, "tags": dict(c.metadata or {})}
        streams = []
        for s in c.streams:
            d: dict = {"codec_type": s.type, "index": s.index, "tags": dict(s.metadata or {}),
                       "disposition": {"attached_pic": 1 if getattr(s, "disposition", 0) and
                                       "attached_pic" in str(s.disposition).lower() else 0}}
            cc = s.codec_context
            d["codec_name"] = getattr(cc, "name", "") or ""
            if s.duration is not None and s.time_base is not None:
                d["duration"] = str(float(s.duration * s.time_base))
            if s.type == "video":
                d["width"], d["height"] = cc.width, cc.height
                rate = s.average_rate or s.guessed_rate or s.base_rate
                d["avg_frame_rate"] = f"{rate.numerator}/{rate.denominator}" if rate else "0/0"
                d["r_frame_rate"] = d["avg_frame_rate"]
                d["pix_fmt"] = getattr(cc, "pix_fmt", "") or ""
                d["nb_frames"] = str(s.frames or 0)
                d["color_transfer"] = _TRC.get(int(getattr(cc, "color_trc", 2) or 2), "")
                d["color_primaries"] = _PRIM.get(int(getattr(cc, "color_primaries", 2) or 2), "")
                d["color_space"] = _SPACE.get(int(getattr(cc, "colorspace", 2) or 2), "")
                d["color_range"] = {1: "tv", 2: "pc"}.get(int(getattr(cc, "color_range", 0) or 0), "")
                rot = (s.metadata or {}).get("rotate")
                if rot:
                    d["side_data_list"] = [{"rotation": -int(rot)}]
                else:
                    r = _stream_rotation(c, s)
                    if r:
                        d["side_data_list"] = [{"rotation": r}]
            elif s.type == "audio":
                d["sample_rate"] = str(cc.sample_rate or 0)
                d["channels"] = getattr(cc, "channels", 0) or (cc.layout.nb_channels if cc.layout else 0)
            streams.append(d)
    return {"format": fmt, "streams": streams}


def _probe_ffmpeg_stderr(path: str) -> dict:
    """Ostatnia deska ratunku: parsowanie `ffmpeg -i` (stderr)."""
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, timeout=60,
                       creationflags=CREATE_NO_WINDOW)
    txt = p.stderr.decode("utf-8", "ignore")
    fmt: dict = {"size": str(os.path.getsize(path)), "format_name": ""}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", txt)
    if m:
        fmt["duration"] = str(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)))
    streams = []
    rm = re.search(r"rotation of (-?[\d.]+) degrees", txt)
    for line in txt.splitlines():
        if "Stream #" not in line:
            continue
        if "Video:" in line:
            d = {"codec_type": "video", "codec_name": line.split("Video:")[1].split()[0].strip(",")}
            sm = re.search(r", (\d{2,5})x(\d{2,5})", line)
            if sm:
                d["width"], d["height"] = int(sm.group(1)), int(sm.group(2))
            fm = re.search(r"([\d.]+) fps", line)
            d["avg_frame_rate"] = f"{float(fm.group(1)) * 1000:.0f}/1000" if fm else "0/0"
            pm = re.search(r"Video: \w+[^,]*, (\w+)", line)
            d["pix_fmt"] = pm.group(1) if pm else ""
            for k, pat in (("color_transfer", r"bt709|smpte2084|arib-std-b67"),):
                cm = re.search(pat, line)
                d[k] = cm.group(0) if cm else ""
            if rm and not any(s.get("codec_type") == "video" for s in streams):
                d["side_data_list"] = [{"rotation": int(round(float(rm.group(1))))}]
            streams.append(d)
        elif "Audio:" in line:
            d = {"codec_type": "audio", "codec_name": line.split("Audio:")[1].split()[0].strip(",")}
            am = re.search(r"(\d+) Hz", line)
            d["sample_rate"] = am.group(1) if am else "0"
            d["channels"] = 1 if "mono" in line else 2
            streams.append(d)
    return {"format": fmt, "streams": streams}


@functools.lru_cache(maxsize=512)
def _probe_cached(path: str, mtime: float) -> dict:
    # PyAV jako pierwszy: ffprobe.exe bywa blokowany przez Smart App Control (zawieszenie ~13 s)
    try:
        return _probe_pyav(path)
    except Exception as e:
        err = e
    if not _FFPROBE_BLOCKED:
        try:
            return _probe_ffprobe(path)
        except (OSError, FFmpegError):
            pass
    try:
        return _probe_ffmpeg_stderr(path)
    except Exception:
        raise FFmpegError(f"Nie można odczytać pliku: {path} ({err})")


def probe(path: str | os.PathLike) -> dict:
    p = Path(path)
    return _probe_cached(str(p), p.stat().st_mtime)


def _frac(s: str | None) -> float:
    if not s or s in ("0/0", "N/A"):
        return 0.0
    try:
        return float(Fraction(s))
    except (ValueError, ZeroDivisionError):
        return 0.0


def media_info(path: str | os.PathLike) -> dict:
    """Znormalizowane metadane: kind, duration, width, height, fps, codec, pix_fmt, bit_depth, color, audio."""
    d = probe(path)
    fmt = d.get("format", {})
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"
              and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(fmt.get("duration") or 0) or float((v or a or {}).get("duration") or 0)
    info: dict = {"duration": dur, "size": int(fmt.get("size") or 0), "format": fmt.get("format_name", ""),
                  "has_video": v is not None, "has_audio": a is not None, "tags": fmt.get("tags", {})}
    if v is not None:
        pix = v.get("pix_fmt", "") or ""
        depth = 10 if ("10" in pix or "p010" in pix) else (12 if "12" in pix else 8)
        if v.get("bits_per_raw_sample"):
            try:
                depth = int(v["bits_per_raw_sample"])
            except ValueError:
                pass
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd.get("rotation") or 0)
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        if abs(rot) in (90, 270):
            w, h = h, w
        fps = _frac(v.get("avg_frame_rate")) or _frac(v.get("r_frame_rate"))
        is_image = dur == 0 or v.get("codec_name") in ("png", "mjpeg", "webp", "bmp", "tiff") and dur < 0.1
        info.update({
            "kind": "image" if is_image else "video",
            "width": w, "height": h, "fps": round(fps, 3), "rotation": rot,
            "vcodec": v.get("codec_name", ""), "pix_fmt": pix, "bit_depth": depth,
            "color_space": v.get("color_space", ""), "color_transfer": v.get("color_transfer", ""),
            "color_primaries": v.get("color_primaries", ""), "color_range": v.get("color_range", ""),
            "nb_frames": int(v.get("nb_frames") or 0),
        })
    else:
        info["kind"] = "audio" if a is not None else "unknown"
    if a is not None:
        info.update({"acodec": a.get("codec_name", ""), "sample_rate": int(a.get("sample_rate") or 0),
                     "channels": int(a.get("channels") or 0)})
    return info


@functools.lru_cache(maxsize=1)
def encoders() -> frozenset[str]:
    try:
        out, _ = run_capture(["-encoders"], timeout=30)
    except Exception:
        return frozenset()
    names = set()
    for line in out.decode("utf-8", "ignore").splitlines():
        m = re.match(r"\s*[VAS][\w.]{5}\s+(\S+)", line)
        if m:
            names.add(m.group(1))
    return frozenset(names)


@functools.lru_cache(maxsize=1)
def filters() -> frozenset[str]:
    try:
        out, _ = run_capture(["-filters"], timeout=30)
    except Exception:
        return frozenset()
    names = set()
    for line in out.decode("utf-8", "ignore").splitlines():
        m = re.match(r"\s*[TSC.]{2,3}\s+(\S+)\s+\S*->", line)
        if m:
            names.add(m.group(1))
    return frozenset(names)


@functools.lru_cache(maxsize=1)
def nvenc_ok() -> bool:
    """Sprawdza realnie, czy NVENC działa (krótkie kodowanie testowe)."""
    if "h264_nvenc" not in encoders():
        return False
    try:
        run_capture(["-f", "lavfi", "-i", "color=black:s=256x144:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
                    timeout=30)
        return True
    except Exception:
        return False


def video_codec_args(codec: str = "hevc_nvenc", *, cq: int = 19, preset: str = "p6", ten_bit: bool = True,
                     fps: float | None = None, gop_seconds: float = 2.0) -> list[str]:
    """Argumenty kodeka wideo z automatycznym przejściem na CPU, gdy NVENC niedostępny."""
    nv = nvenc_ok()
    g = ["-g", str(int(round((fps or 30) * gop_seconds)))]
    if codec in ("hevc_nvenc", "h265", "hevc") and nv:
        return ["-c:v", "hevc_nvenc", "-preset", preset, "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0",
                "-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "32",
                "-pix_fmt", "p010le" if ten_bit else "yuv420p", "-profile:v", "main10" if ten_bit else "main",
                "-tag:v", "hvc1", *g]
    if codec in ("h264_nvenc", "h264") and nv:
        return ["-c:v", "h264_nvenc", "-preset", preset, "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0",
                "-spatial-aq", "1", "-rc-lookahead", "32", "-pix_fmt", "yuv420p", "-profile:v", "high", *g]
    if codec in ("av1_nvenc", "av1") and nv and "av1_nvenc" in encoders():
        return ["-c:v", "av1_nvenc", "-preset", preset, "-rc", "vbr", "-cq", str(cq + 6), "-b:v", "0",
                "-pix_fmt", "p010le" if ten_bit else "yuv420p", *g]
    if codec == "prores":
        return ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0"]
    if codec == "dnxhr":
        return ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p"]
    # Fallback CPU
    if codec in ("hevc_nvenc", "h265", "hevc"):
        return ["-c:v", "libx265", "-preset", "medium", "-crf", str(cq + 1),
                "-pix_fmt", "yuv420p10le" if ten_bit else "yuv420p", "-tag:v", "hvc1", *g]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(max(14, cq - 1)), "-pix_fmt", "yuv420p", *g]


def hw_decode_args() -> list[str]:
    return ["-hwaccel", "cuda"] if nvenc_ok() else []


def extract_frame(path: str | os.PathLike, t: float, out: str | os.PathLike, width: int | None = None,
                  vf: str | None = None, quality: int = 3) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    chain = []
    if vf:
        chain.append(vf)
    if width:
        chain.append(f"scale={width}:-2:flags=bicubic")
    args = ["-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1"]
    if chain:
        args += ["-vf", ",".join(chain)]
    args += ["-q:v", str(quality), str(out)]
    run_capture(["-y", *args], timeout=120)
    return out


def read_frames(path: str | os.PathLike, *, fps: float, width: int, start: float = 0.0,
                duration: float | None = None, gray: bool = False) -> Iterable[np.ndarray]:
    """Strumieniowo czyta klatki (numpy HxW[x3] uint8) w zadanym fps i szerokości."""
    info = media_info(path)
    w = width
    h = int(round(info.get("height", 1) * w / max(1, info.get("width", 1)) / 2) * 2) or w
    pix = "gray" if gray else "rgb24"
    args = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{start:.3f}"]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args += ["-i", str(path), "-vf", f"fps={fps},scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", pix, "-"]
    proc = _popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = w * h * (1 if gray else 3)
    assert proc.stdout
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            arr = np.frombuffer(buf, np.uint8)
            yield arr.reshape((h, w) if gray else (h, w, 3))
    finally:
        proc.kill()
        proc.wait()


def read_audio(path: str | os.PathLike, *, sr: int = 48000, mono: bool = True, start: float = 0.0,
               duration: float | None = None, af: str | None = None) -> np.ndarray:
    """Zwraca float32: (n,) dla mono lub (n, 2) dla stereo."""
    args = ["-v", "error", "-ss", f"{max(0.0, start):.4f}"]
    if duration is not None:
        args += ["-t", f"{max(0.0, duration):.4f}"]
    args += ["-i", str(path), "-vn"]
    if af:
        args += ["-af", af]
    args += ["-ac", "1" if mono else "2", "-ar", str(sr), "-f", "f32le", "-"]
    out, _ = run_capture(args, timeout=3600)
    a = np.frombuffer(out, np.float32).copy()
    if not mono:
        a = a.reshape(-1, 2)
    return a


def write_wav(path: str | os.PathLike, audio: np.ndarray, sr: int = 48000) -> Path:
    import soundfile as sf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio.astype(np.float32), sr, subtype="FLOAT")
    return path


def escape_filter_path(p: str | os.PathLike) -> str:
    """Ścieżka do użycia w filtrze ffmpeg (lut3d=, ass=, subtitles=) na Windows."""
    s = str(Path(p)).replace("\\", "/")
    s = s.replace(":", "\\:").replace("'", "\\'")
    return s

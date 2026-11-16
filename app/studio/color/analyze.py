"""Analiza koloru i ekspozycji klipu: luminancja, przepalenia, balans bieli, Lab, skóra, niebo, profil LOG.

Klatki próbkowane w małej rozdzielczości (~480 px) równolegle (kilka procesów ffmpeg z szybkim
przewijaniem), więc analiza klipu trwa zwykle < 1-2 s.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from ..media import ffmpeg

ANALYSIS_WIDTH = 384
HIST_BINS = 32


def _grab_rgb(path: str, t: float, w: int, h: int) -> np.ndarray | None:
    """Jedna klatka jako RGB uint8 (h, w, 3) - szybkie przewijanie -ss przed -i."""
    try:
        out, _ = ffmpeg.run_capture(["-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
                                     "-vf", f"scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                    timeout=120)
    except ffmpeg.FFmpegError:
        return None
    if len(out) < w * h * 3:
        return None
    return np.frombuffer(out[: w * h * 3], np.uint8).reshape(h, w, 3)


def sample_frames(media_path: str, *, t0: float = 0.0, t1: float | None = None, samples: int = 8,
                  width: int = ANALYSIS_WIDTH, info: dict | None = None) -> list[np.ndarray]:
    """Równomiernie rozłożone klatki RGB uint8 z zakresu [t0, t1]."""
    info = info or ffmpeg.media_info(media_path)
    dur = float(info.get("duration") or 0)
    if info.get("kind") == "image":
        t0, t1, samples = 0.0, 0.0, 1
    t1 = dur if t1 is None or t1 <= 0 else min(t1, dur or t1)
    t0 = max(0.0, min(t0, max(0.0, t1 - 0.05)))
    span = max(0.0, t1 - t0)
    n = max(1, int(samples))
    # środki n równych odcinków - unika pierwszej (często czarnej/rozmytej) i ostatniej klatki
    times = [t0 + span * (i + 0.5) / n for i in range(n)] if span > 0.05 else [t0]
    sw, sh = int(info.get("width") or 0), int(info.get("height") or 0)
    w = min(width, sw) if sw else width
    w -= w % 2
    h = int(round(sh * w / max(1, sw) / 2) * 2) if sw else w
    frames: list[np.ndarray] = []
    if 0.05 < span <= 30 and n > 1:
        # 1) jeden proces, dekodowanie tylko klatek kluczowych (najszybsze dla typowych GOP 0.5-2 s)
        frames = _keyframes(str(media_path), t0, span, w, h)
        if len(frames) >= max(2, n // 2):
            idx = np.linspace(0, len(frames) - 1, min(n, len(frames))).round().astype(int)
            return [frames[i] for i in sorted(set(idx.tolist()))]
    if n > 1:
        # 2) jeden proces, wiele wejść z szybkim przewijaniem (długie GOP / długie klipy)
        frames = _multi_seek(str(media_path), times, w, h)
        if len(frames) >= max(1, n // 2):
            return frames
    # 3) awaryjnie: osobny proces na klatkę
    with ThreadPoolExecutor(max_workers=min(4, len(times))) as ex:
        frames = list(ex.map(lambda t: _grab_rgb(str(media_path), t, w, h), times))
    return [f for f in frames if f is not None]


def _split_raw(out: bytes, w: int, h: int) -> list[np.ndarray]:
    fb = w * h * 3
    return [np.frombuffer(out[i * fb:(i + 1) * fb], np.uint8).reshape(h, w, 3) for i in range(len(out) // fb)]


def _keyframes(path: str, t0: float, span: float, w: int, h: int) -> list[np.ndarray]:
    try:
        out, _ = ffmpeg.run_capture(["-v", "error", "-skip_frame", "nokey", "-ss", f"{t0:.3f}", "-t", f"{span:.3f}",
                                     "-i", path, "-vf", f"scale={w}:{h}:flags=area", "-fps_mode", "passthrough",
                                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], timeout=300)
    except ffmpeg.FFmpegError:
        return []
    return _split_raw(out, w, h)


def _multi_seek(path: str, times: list[float], w: int, h: int) -> list[np.ndarray]:
    args: list[str] = ["-v", "error", "-threads", "2"]
    fc = []
    for i, t in enumerate(times):
        # przewinięcie do klatki kluczowej <= t i dekodowanie tylko jej (bez klatek pośrednich)
        args += ["-skip_frame", "nokey", "-ss", f"{max(0.0, t):.3f}", "-t", "1.0", "-i", path]
        fc.append(f"[{i}:v]trim=end_frame=1,setpts=PTS-STARTPTS,scale={w}:{h}:flags=area,setsar=1[v{i}]")
    graph = ";".join(fc) + ";" + "".join(f"[v{i}]" for i in range(len(times))) + \
        f"concat=n={len(times)}:v=1:a=0[out]"
    try:
        out, _ = ffmpeg.run_capture([*args, "-filter_complex", graph, "-map", "[out]", "-fps_mode", "passthrough",
                                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], timeout=300)
    except ffmpeg.FFmpegError:
        return []
    return _split_raw(out, w, h)


def _skin_mask(rgb: np.ndarray, hsv: np.ndarray) -> np.ndarray:
    ycc = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    cr, cb = ycc[..., 1], ycc[..., 2]
    m_ycc = (cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]  # OpenCV: H 0..180
    m_hsv = (h <= 25) & (s >= 38) & (s <= 190) & (v >= 50)  # odcień ~0-50°, umiarkowane nasycenie
    return m_ycc & m_hsv


def _sky_mask(hsv: np.ndarray) -> np.ndarray:
    hh = hsv.shape[0]
    top = np.zeros(hsv.shape[:2], bool)
    top[: int(hh * 0.45)] = True
    h, s, v = hsv[..., 0].astype(np.int16), hsv[..., 1], hsv[..., 2]
    blue = (h >= 90) & (h <= 130) & (s >= 40) & (v >= 110)
    overcast = (s < 35) & (v >= 205)
    return top & (blue | overcast)


def frame_stats(rgb: np.ndarray) -> dict:
    """Statystyki pojedynczej klatki RGB uint8 (używane też przez QC/archiwistę)."""
    return _stats_from_frames([rgb])


def _stats_from_frames(frames: list[np.ndarray]) -> dict:
    ys, sats, labs, wb_px, skin_n, sky_n, total = [], [], [], [], 0, 0, 0
    clipped_n = crushed_n = 0
    for rgb in frames:
        f = rgb.astype(np.float32) / 255.0
        y = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
        ys.append(y[::2, ::2].ravel())
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        s = hsv[..., 1].astype(np.float32) / 255.0
        v = hsv[..., 2].astype(np.float32) / 255.0
        sats.append(s[::2, ::2].ravel())
        lab = cv2.cvtColor(np.ascontiguousarray(f[::2, ::2]), cv2.COLOR_RGB2Lab)  # co drugi piksel wystarcza
        labs.append(lab.reshape(-1, 3))
        # balans bieli: piksele prawie neutralne, nie przepalone i nie zgniecione
        neutral = (s < 0.22) & (v > 0.12) & (v < 0.94)
        if neutral.mean() > 0.02:
            wb_px.append(f[neutral])
        else:  # gray-world z odrzuceniem nasyconych
            wb_px.append(f[(s < 0.5) & (v > 0.05) & (v < 0.97)])
        skin_n += int(_skin_mask(rgb, hsv).sum())
        sky_n += int(_sky_mask(hsv).sum())
        total += y.size
        clipped_n += int((y >= 250 / 255.0).sum())
        crushed_n += int((y <= 5 / 255.0).sum())
    if not ys:
        raise RuntimeError("Nie udało się odczytać klatek do analizy koloru")
    y = np.concatenate(ys)
    s = np.concatenate(sats)
    lab = np.concatenate(labs)
    wbp = np.concatenate(wb_px) if wb_px and sum(len(p) for p in wb_px) else np.full((1, 3), 0.5, np.float32)
    wb = wbp.mean(axis=0)
    p01, p50, p99 = np.percentile(y, [1, 50, 99])
    hist, _ = np.histogram(y, bins=HIST_BINS, range=(0.0, 1.0))
    hist = hist.astype(np.float64) / max(1, hist.sum())
    return {
        "luma_mean": round(float(y.mean()), 4),
        "luma_std": round(float(y.std()), 4),
        "luma_p01": round(float(p01), 4),
        "luma_median": round(float(p50), 4),
        "luma_p99": round(float(p99), 4),
        "clipped_pct": round(clipped_n / max(1, total) * 100, 3),
        "crushed_pct": round(crushed_n / max(1, total) * 100, 3),
        "wb_rgb": [round(float(c), 4) for c in wb],
        "sat_mean": round(float(s.mean()), 4),
        "lab_mean": [round(float(c), 3) for c in lab.mean(axis=0)],
        "lab_std": [round(float(c), 3) for c in lab.std(axis=0)],
        "skin_pct": round(skin_n / max(1, total) * 100, 3),
        "sky_pct": round(sky_n / max(1, total) * 100, 3),
        "hist": [round(float(v), 5) for v in hist],
        "frames": len(frames),
    }


def clip_stats(media_path: str, *, t0: float = 0, t1: float | None = None, samples: int = 8) -> dict:
    """Statystyki koloru/ekspozycji fragmentu klipu (patrz ARCHITECTURE.md → Kolor).

    Zwraca m.in.: luma_mean, luma_p01, luma_p99, clipped_pct (% pikseli Y>=250), crushed_pct (% Y<=5),
    wb_rgb [r,g,b] (0..1), sat_mean, lab_mean/lab_std (OpenCV Lab, L 0..100), skin_pct, sky_pct (% kadru),
    hist (32 biny luminancji, suma 1) oraz informacje o źródle (color_transfer, color_range, bit_depth).
    """
    info = ffmpeg.media_info(media_path)
    frames = sample_frames(str(media_path), t0=t0, t1=t1, samples=samples, info=info)
    st = _stats_from_frames(frames)
    st.update({
        "t0": float(t0), "t1": float(t1) if t1 is not None else float(info.get("duration") or 0),
        "color_transfer": info.get("color_transfer", ""), "color_primaries": info.get("color_primaries", ""),
        "color_space": info.get("color_space", ""), "color_range": info.get("color_range", "") or "tv",
        "bit_depth": int(info.get("bit_depth") or 8), "width": info.get("width"), "height": info.get("height"),
    })
    st["log_profile"] = detect_log_profile(info, st)
    st["night"] = bool(st["luma_mean"] < 0.2 and st["luma_p99"] < 0.75)
    return st


_TAG_PROFILES = [
    (("s-log3", "slog3", "s_log3", "s-log 3"), "slog3"),
    (("c-log3", "clog3", "canon log 3", "canonlog3", "c_log3"), "clog3"),
    (("d-log", "dlog", "d_log"), "dlog"),
    (("v-log", "vlog", "v_log"), "vlog"),
    (("hlg", "hybrid log"), "hlg"),
]


def detect_log_profile(info: dict, stats: dict) -> str:
    """Rozpoznaje profil wejściowy: "" (Rec.709) | slog3 | clog3 | dlog | vlog | hlg | pq | flat.

    Kolejność: metadane transferu (HLG/PQ) → tagi producenta → heurystyka obrazu ("flat" = płaski log
    nieznanego producenta: brak czerni i bieli, niskie nasycenie, wąski histogram).
    """
    trc = str(info.get("color_transfer") or stats.get("color_transfer") or "").lower()
    if trc == "arib-std-b67":
        return "hlg"
    if trc == "smpte2084":
        return "pq"
    tags = json.dumps(info.get("tags") or {}, ensure_ascii=False).lower()
    for keys, prof in _TAG_PROFILES:
        if any(k in tags for k in keys):
            return prof
    p01, p99 = float(stats.get("luma_p01", 0)), float(stats.get("luma_p99", 1))
    sat = float(stats.get("sat_mean", 0.3))
    std = float(stats.get("luma_std", 0.2))
    crushed = float(stats.get("crushed_pct", 0))
    clipped = float(stats.get("clipped_pct", 0))
    no_blacks = p01 > 0.07 and crushed < 0.05
    no_whites = p99 < 0.86 and clipped < 0.2
    if no_blacks and no_whites and sat < 0.2 and std < 0.17 and (p99 - p01) < 0.72:
        return "flat"
    return ""


def load_or_compute(cache_dir: Path, key: str, media_path: str, *, t0: float, t1: float | None,
                    samples: int) -> dict:
    """Statystyki z cache JSON (klucz = media + zakres + próbki) lub liczone od nowa."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{key}_{t0:.2f}_{(t1 or 0):.2f}_{samples}.json"
    try:
        mt = Path(media_path).stat().st_mtime
    except OSError:
        mt = 0
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("_mtime") == mt:
                return d
        except (OSError, json.JSONDecodeError):
            pass
    st = clip_stats(media_path, t0=t0, t1=t1, samples=samples)
    st["_mtime"] = mt
    f.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return st

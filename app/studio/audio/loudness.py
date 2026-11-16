"""Pomiar głośności wg ITU-R BS.1770 / EBU R128: LUFS zintegrowany, true peak (4x), LRA, krzywe M/S.

Używane przez miks (normalizacja), QC (raport) i UI (wykres głośności).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

SR = 48000


def _meter(sr: int):
    import pyloudnorm as pyln

    return pyln.Meter(sr)


def k_weight(a: np.ndarray, sr: int) -> np.ndarray:
    m = _meter(sr)
    x = a.astype(np.float64)
    for f in m._filters.values():  # high_shelf + high_pass (K-weighting)
        from scipy.signal import lfilter

        x = lfilter(f.b, f.a, x, axis=0)
    return x


def integrated(a: np.ndarray, sr: int) -> float:
    if len(a) < int(0.45 * sr):
        a = np.concatenate([a, np.zeros((int(0.45 * sr) - len(a),) + a.shape[1:], a.dtype)])
    try:
        v = float(_meter(sr).integrated_loudness(a.astype(np.float64)))
    except Exception:
        return -70.0
    return v if np.isfinite(v) else -70.0


def lra(a: np.ndarray, sr: int) -> float:
    try:
        if len(a) < 3.2 * sr:
            return 0.0
        v = float(_meter(sr).loudness_range(a.astype(np.float64)))
        return round(v, 2) if np.isfinite(v) else 0.0
    except Exception:
        return 0.0


def true_peak(a: np.ndarray, sr: int, oversample: int = 4) -> float:
    """True peak (dBTP) przez nadpróbkowanie 4x (polifazowo, w blokach - oszczędnie z pamięcią)."""
    from scipy.signal import resample_poly

    if a.ndim == 1:
        a = a[:, None]
    blk = sr * 10
    pad = 64
    peak = 0.0
    n = len(a)
    for s in range(0, n, blk):
        s0, s1 = max(0, s - pad), min(n, s + blk + pad)
        up = resample_poly(a[s0:s1].astype(np.float64), oversample, 1, axis=0)
        i0 = (s - s0) * oversample
        i1 = i0 + (min(n, s + blk) - s) * oversample
        peak = max(peak, float(np.max(np.abs(up[i0:i1]))) if i1 > i0 else 0.0)
    peak = max(peak, float(np.max(np.abs(a))) if n else 0.0)
    return round(20 * np.log10(max(peak, 1e-9)), 2)


def curves(a: np.ndarray, sr: int, hop: float = 0.4) -> tuple[list[float], list[float]]:
    """Momentary (400 ms) i short-term (3 s) LUFS co `hop` sekund."""
    if a.ndim == 1:
        a = a[:, None]
    x = k_weight(a, sr)
    sq = np.sum(x ** 2, axis=1)  # wagi kanałów L/R = 1.0
    cs = np.concatenate([[0.0], np.cumsum(sq)])
    n = len(sq)
    h = int(hop * sr)
    out_m, out_s = [], []
    for end in range(h, n + h, h):
        e = min(end, n)
        for win, out in ((int(0.4 * sr), out_m), (int(3.0 * sr), out_s)):
            s = max(0, e - win)
            ms = (cs[e] - cs[s]) / max(1, win)
            out.append(round(float(-0.691 + 10 * np.log10(ms + 1e-12)), 2) if ms > 0 else -70.0)
    clamp = lambda v: max(-70.0, v)  # noqa: E731
    return [clamp(v) for v in out_m], [clamp(v) for v in out_s]


def measure_array(a: np.ndarray, sr: int = SR, with_curves: bool = True) -> dict:
    a = np.asarray(a, np.float32)
    res = {"lufs": round(integrated(a, sr), 2), "true_peak": true_peak(a, sr), "lra": lra(a, sr),
           "peak_db": round(20 * np.log10(max(float(np.max(np.abs(a))) if len(a) else 0.0, 1e-9)), 2),
           "duration": round(len(a) / sr, 3)}
    if with_curves:
        m, s = curves(a, sr)
        res.update({"hop": 0.4, "momentary": m, "short_term": s})
    return res


def loudness(path: str) -> dict:
    """{"lufs","true_peak","lra","peak_db","duration","hop","momentary":[..],"short_term":[..]}"""
    p = Path(path)
    a = None
    sr = SR
    if p.suffix.lower() in (".wav", ".flac", ".aiff", ".aif", ".ogg"):
        try:
            import soundfile as sf

            a, sr = sf.read(str(p), dtype="float32", always_2d=False)
        except Exception:
            a = None
    if a is None:
        from ..media.ffmpeg import read_audio

        a = read_audio(os.fspath(p), sr=SR, mono=False)
        sr = SR
    if a.ndim == 2 and a.shape[1] > 2:
        a = a[:, :2]
    return measure_array(a, sr)

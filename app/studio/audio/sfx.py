"""Efekty dźwiękowe: proceduralna synteza (numpy) + opcjonalny wybór z biblioteki Audio-Library/SFX.

Rodzaje: whoosh, swoosh, riser, downlifter, hit, impact, click, ambience (room | outdoor | city),
np. "ambience:city" albo "ambience_city". Wynik: WAV 48 kHz stereo float32.
"""
from __future__ import annotations

import re
import zlib
from pathlib import Path

import numpy as np

from .. import paths
from . import SR, write_wav

KINDS = ("whoosh", "swoosh", "riser", "downlifter", "hit", "impact", "click", "ambience")
_SYN = {"whoosh": ["whoosh", "woosh", "swish", "transition", "przejscie"], "swoosh": ["swoosh", "swipe", "swish"],
        "riser": ["riser", "rise", "build", "uplifter", "narastanie"], "downlifter": ["downlifter", "down", "drop"],
        "hit": ["hit", "boom", "uderzenie"], "impact": ["impact", "boom", "hit", "slam"],
        "click": ["click", "klik", "tick", "ui"], "ambience": ["ambience", "ambient", "atmos", "room", "tlo"]}


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _stft_filter_noise(n: int, center_hz: np.ndarray, bw_oct: np.ndarray, amp: np.ndarray, seed: int,
                       nfft: int = 2048) -> np.ndarray:
    """Szum filtrowany w dziedzinie STFT pasmem gaussowskim (w oktawach) zmiennym w czasie."""
    from scipy.signal import istft, stft

    x = _rng(seed).standard_normal(n + nfft)
    f, t, Z = stft(x, fs=SR, nperseg=nfft, noverlap=nfft * 3 // 4)
    tt = np.clip((t * SR).astype(int), 0, n - 1)
    fc = center_hz[tt]
    bw = np.maximum(0.15, bw_oct[tt])
    lf = np.log2(np.maximum(f, 10.0))[:, None]
    H = np.exp(-0.5 * ((lf - np.log2(fc)[None, :]) / bw[None, :]) ** 2)
    H *= amp[tt][None, :]
    _, y = istft(Z * H, fs=SR, nperseg=nfft, noverlap=nfft * 3 // 4)
    return y[:n].astype(np.float32)


def _pink(n: int, seed: int, slope: float = 0.5) -> np.ndarray:
    X = np.fft.rfft(_rng(seed).standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = f[1]
    X /= f ** slope
    y = np.fft.irfft(X, n)
    return (y / (np.std(y) + 1e-9)).astype(np.float32)


def _env_attack_decay(n: int, peak: float, a_curve: float = 2.0, d_curve: float = 2.5) -> np.ndarray:
    t = np.linspace(0, 1, n)
    e = np.where(t < peak, (t / max(peak, 1e-6)) ** a_curve, ((1 - t) / max(1 - peak, 1e-6)) ** d_curve)
    return e.astype(np.float32)


def _pan(mono: np.ndarray, pan: np.ndarray) -> np.ndarray:
    """pan -1..1 (equal-power)."""
    th = (np.clip(pan, -1, 1) + 1) * np.pi / 4
    return np.stack([mono * np.cos(th), mono * np.sin(th)], axis=1).astype(np.float32)


def _reverb_tail(x: np.ndarray, seconds: float, seed: int, wet: float = 0.25) -> np.ndarray:
    """Prosty pogłos splotowy (szum z wykładniczym zanikiem, dekorelacja L/R)."""
    from scipy.signal import fftconvolve

    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = []
    for ch in range(2):
        ir = _rng(seed + ch).standard_normal(n) * np.exp(-6.9 * t / seconds)
        # zmiękczenie wysokich w ogonie
        ir = np.convolve(ir, np.ones(4) / 4, mode="same")
        ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
        src = x[:, ch] if x.ndim == 2 else x
        wet_sig = np.pad(fftconvolve(src, ir), (0, 1))[:len(src) + n]
        dry = np.pad(src, (0, n))
        out.append(dry * (1 - wet * 0.5) + wet * wet_sig)
    return np.stack(out, axis=1).astype(np.float32)


def _normalize(y: np.ndarray, peak_db: float = -3.0) -> np.ndarray:
    m = float(np.max(np.abs(y))) + 1e-9
    return (y * (10 ** (peak_db / 20) / m)).astype(np.float32)


def _edge_fade(y: np.ndarray, fi: float = 0.003, fo: float = 0.02) -> np.ndarray:
    a, b = int(fi * SR), int(fo * SR)
    if a:
        y[:a] *= np.linspace(0, 1, a, dtype=np.float32)[:, None]
    if b:
        y[-b:] *= np.linspace(1, 0, b, dtype=np.float32)[:, None]
    return y


# ------------------------------------------------------------------------------------------
def synth_whoosh(duration: float = 1.0, seed: int = 1, short: bool = False) -> np.ndarray:
    n = int(duration * SR)
    t = np.linspace(0, 1, n)
    peak = 0.55 if not short else 0.45
    env = _env_attack_decay(n, peak, 2.2, 1.8)
    lo, hi = (500, 5500) if short else (250, 3200)
    # środek pasma rośnie do szczytu i opada (efekt Dopplera)
    fc = lo * (hi / lo) ** np.where(t < peak, t / peak, 1 - (t - peak) / (1 - peak) * 0.7)
    bw = 0.9 - 0.4 * np.sin(np.pi * t)
    y = _stft_filter_noise(n, fc, bw, env, seed)
    # "powietrze" - delikatny szum wysokotonowy
    air = _stft_filter_noise(n, np.full(n, 8000.0), np.full(n, 0.8), env ** 1.5, seed + 9) * 0.25
    mono = y + air
    st = _pan(mono, np.linspace(-0.8, 0.8, n))
    st = _reverb_tail(st, 0.6, seed, wet=0.18)[:n + int(0.25 * SR)]
    return _edge_fade(_normalize(st, -3.0))


def synth_riser(duration: float = 2.0, seed: int = 2) -> np.ndarray:
    n = int(duration * SR)
    t = np.linspace(0, 1, n)
    env = (t ** 2.2).astype(np.float32)
    fc = 300 * (6000 / 300) ** (t ** 1.3)
    noise = _stft_filter_noise(n, fc, np.full(n, 0.7), env, seed)
    # narastający akord pił (odstrojony), filtrowany
    f0 = 110 * (2 ** (2 * t ** 1.5))  # +2 oktawy
    ph = 2 * np.pi * np.cumsum(f0) / SR
    tone = np.zeros(n)
    for det, w in ((1.0, 1.0), (1.007, 0.7), (0.993, 0.7), (1.5, 0.4), (2.0, 0.35)):
        saw = 2 * ((ph * det / (2 * np.pi)) % 1.0) - 1
        tone += w * saw
    from scipy.signal import butter, sosfilt

    tone = sosfilt(butter(2, 3500, fs=SR, output="sos"), tone)
    trem = 1 - 0.25 * (0.5 + 0.5 * np.sin(2 * np.pi * np.cumsum(2 + 14 * t ** 2) / SR))
    mono = noise + 0.08 * tone * env * trem
    st = _pan(mono.astype(np.float32), 0.3 * np.sin(2 * np.pi * 0.5 * t * duration))
    st = _reverb_tail(st, 1.2, seed, wet=0.25)[:n + int(0.05 * SR)]
    st = _normalize(st, -3.0)
    return _edge_fade(st, 0.01, 0.04)


def synth_downlifter(duration: float = 2.0, seed: int = 3) -> np.ndarray:
    n = int(duration * SR)
    t = np.linspace(0, 1, n)
    env = ((1 - t) ** 2.0 * (1 - np.exp(-t * 60))).astype(np.float32)
    fc = 5000 * (200 / 5000) ** (t ** 0.8)
    noise = _stft_filter_noise(n, fc, np.full(n, 0.7), env, seed)
    f0 = 220 * (2 ** (-2.5 * t))
    tone = np.sin(2 * np.pi * np.cumsum(f0) / SR) * env
    mono = noise + 0.25 * tone
    st = _pan(mono.astype(np.float32), np.linspace(0.6, -0.6, n))
    st = _reverb_tail(st, 1.5, seed, wet=0.3)
    return _edge_fade(_normalize(st, -3.0), 0.005, 0.1)


def synth_hit(duration: float = 1.2, seed: int = 4, big: bool = False) -> np.ndarray:
    n = int(max(duration, 0.3) * SR)
    t = np.arange(n) / SR
    rng = _rng(seed)
    # sub-bas: sinus ze spadkiem wysokości 70 -> 38 Hz
    f = 38 + 32 * np.exp(-t * 18)
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / (0.55 if big else 0.32))
    # korpus: szum pasmowy 150 Hz - 2 kHz, szybki zanik
    from scipy.signal import butter, sosfilt

    body = sosfilt(butter(2, [150, 2000], btype="band", fs=SR, output="sos"), rng.standard_normal(n))
    body *= np.exp(-t / (0.12 if big else 0.07))
    # transjent (klik ataku)
    tr = sosfilt(butter(2, 2500, btype="high", fs=SR, output="sos"), rng.standard_normal(n)) * np.exp(-t / 0.004)
    mono = 1.0 * sub + 0.55 * body + 0.35 * tr
    # lekka saturacja dla "mięsa"
    mono = np.tanh(1.6 * mono) / np.tanh(1.6)
    st = np.stack([mono, mono], axis=1).astype(np.float32)
    st = _reverb_tail(st, 2.2 if big else 1.2, seed, wet=0.35 if big else 0.22)[:n]
    return _edge_fade(_normalize(st, -1.5), 0.0005, 0.05)


def synth_click(duration: float = 0.08, seed: int = 5) -> np.ndarray:
    n = int(max(0.03, duration) * SR)
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * 3200 * t) * np.exp(-t / 0.0025) + 0.4 * np.sin(2 * np.pi * 1200 * t) * np.exp(-t / 0.006)
    y += 0.1 * _rng(seed).standard_normal(n) * np.exp(-t / 0.001)
    st = np.stack([y, y], axis=1).astype(np.float32)
    return _edge_fade(_normalize(st, -6.0), 0.0002, 0.005)


def synth_ambience(duration: float = 10.0, kind: str = "room", seed: int = 6) -> np.ndarray:
    n = int(max(1.0, duration) * SR)
    t = np.arange(n) / SR
    from scipy.signal import butter, sosfilt

    L, R = _pink(n, seed, 0.5), _pink(n, seed + 1, 0.5)
    if kind == "outdoor":
        # wiatr: szum brązowy z wolną modulacją + szelest liści (wysokie pasmo, nieregularny)
        bl, br = _pink(n, seed + 2, 1.0), _pink(n, seed + 3, 1.0)
        mod = 0.6 + 0.4 * np.sin(2 * np.pi * 0.07 * t + 1.0) * np.sin(2 * np.pi * 0.023 * t)
        hp = butter(2, 2500, btype="high", fs=SR, output="sos")
        rust = 0.5 + 0.5 * np.clip(np.sin(2 * np.pi * 0.31 * t) + 0.5 * np.sin(2 * np.pi * 0.83 * t + 2), 0, None)
        L = 0.8 * bl * mod + 0.25 * sosfilt(hp, L) * rust
        R = 0.8 * br * mod[::-1] + 0.25 * sosfilt(hp, R) * rust[::-1]
    elif kind == "city":
        bl, br = _pink(n, seed + 4, 1.0), _pink(n, seed + 5, 1.0)
        lp = butter(2, 400, fs=SR, output="sos")
        # przejeżdżające auta: wolne "falowanie" średnich częstotliwości
        swell = np.zeros(n)
        rng = _rng(seed)
        for _ in range(max(1, int(duration / 4))):
            c = rng.uniform(0, duration)
            w = rng.uniform(1.5, 4.0)
            swell += np.exp(-0.5 * ((t - c) / (w / 2)) ** 2)
        mid = butter(2, [300, 2500], btype="band", fs=SR, output="sos")
        L = 1.0 * sosfilt(lp, bl) + 0.3 * sosfilt(mid, L) * (0.4 + swell)
        R = 1.0 * sosfilt(lp, br) + 0.3 * sosfilt(mid, R) * (0.4 + swell[::-1])
    else:  # room
        lp = butter(2, 3000, fs=SR, output="sos")
        L = sosfilt(lp, L) * 0.7 + 0.3 * sosfilt(lp, _pink(n, seed + 6, 1.0))
        R = sosfilt(lp, R) * 0.7 + 0.3 * sosfilt(lp, _pink(n, seed + 7, 1.0))
    st = np.stack([L, R], axis=1).astype(np.float32)
    rms = float(np.sqrt(np.mean(st ** 2))) + 1e-9
    st *= 10 ** (-30 / 20) / rms  # tło: -30 dBFS RMS
    return _edge_fade(st, 0.3, 0.3)


def library_match(kind: str, root: Path | None = None) -> Path | None:
    root = root or (paths.AUDIO_LIBRARY / "SFX")
    if not root.exists():
        return None
    parts = re.split(r"[:_]", kind.lower(), maxsplit=1)
    base, sub = parts[0], (parts[1] if len(parts) > 1 else "")
    words = _SYN.get(base, [base])
    best = None
    for p in root.rglob("*"):
        if p.suffix.lower() not in (".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff", ".m4a") or not p.is_file():
            continue
        name = " ".join(p.relative_to(root).parts).lower()
        sc = sum(2 for w in words if w in name) + (2 if sub and sub in name else 0)
        if sc > 0 and (best is None or sc > best[0]):
            best = (sc, p)
    return best[1] if best else None


def make_sfx(kind: str, out_path: Path, duration: float = 1.0, *, prefer_library: bool = True,
             seed: int | None = None) -> Path:
    out_path = Path(out_path)
    k = kind.lower().strip()
    sub = ""
    if ":" in k:
        k, sub = k.split(":", 1)
    elif k.startswith("ambience_"):
        k, sub = "ambience", k.split("_", 1)[1]
    if k not in KINDS:
        raise ValueError(f"Nieznany rodzaj SFX: {kind} (dostępne: {', '.join(KINDS)})")
    if prefer_library:
        lib = library_match(kind)
        if lib is not None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            from ..media.ffmpeg import run

            dst = out_path.with_suffix(".wav")
            af = []
            if k == "ambience":
                af = ["-af", f"atrim=0:{max(1.0, duration):.3f}"]
            run(["-i", str(lib), *af, "-ac", "2", "-ar", str(SR), "-c:a", "pcm_f32le", str(dst)])
            return dst
    sd = seed if seed is not None else (zlib.crc32(kind.encode("utf-8")) % 1000 + 11)
    if k == "whoosh":
        y = synth_whoosh(duration, sd)
    elif k == "swoosh":
        y = synth_whoosh(max(0.2, duration * 0.6), sd, short=True)
    elif k == "riser":
        y = synth_riser(duration, sd)
    elif k == "downlifter":
        y = synth_downlifter(duration, sd)
    elif k == "hit":
        y = synth_hit(duration, sd, big=False)
    elif k == "impact":
        y = synth_hit(max(duration, 1.5), sd, big=True)
    elif k == "click":
        y = synth_click(min(duration, 0.15), sd)
    else:
        y = synth_ambience(duration, sub or "room", sd)
    return write_wav(out_path.with_suffix(".wav"), y, SR)


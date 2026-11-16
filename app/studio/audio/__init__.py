"""Obszar audio StudioEdit: STT, diaryzacja, synchronizacja, czyszczenie głosu, miks, TTS, muzyka, SFX.

Moduły importuj bezpośrednio (np. `from studio.audio.stt import transcribe`) - ciężkie zależności
(torch, faster-whisper, librosa) ładowane są leniwie. Tutaj tylko lekkie narzędzia wspólne.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import numpy as np

from .. import paths

SR = 48000
SPEECH_OWNER = "torch:whisper"  # wspólny właściciel VRAM dla modeli mowy (Whisper + WavLM mieszczą się razem)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def file_lock(key: str) -> threading.Lock:
    """Blokada per plik wynikowy (żeby dwa wątki nie liczyły tego samego cache)."""
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def cache_dir(*parts: str) -> Path:
    p = paths.CACHE.joinpath("audio", *parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def file_sig(path: str | os.PathLike) -> str:
    p = Path(path)
    try:
        st = p.stat()
        return f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return str(p)


def param_hash(*items, n: int = 16) -> str:
    raw = json.dumps(items, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:n]


def extract_wav(media_path: str | os.PathLike, sr: int = 16000, mono: bool = True,
                progress=None, cancel=None) -> Path:
    """Wyciąga ścieżkę audio do WAV (PCM16) w cache (raz na plik/parametry)."""
    from ..media.ffmpeg import media_info, run

    key = param_hash(file_sig(media_path), sr, mono)
    out = cache_dir("wav") / f"{Path(media_path).stem[:40]}_{key}_{sr}{'m' if mono else 's'}.wav"
    with file_lock(str(out)):
        if out.exists() and out.stat().st_size > 44:
            return out
        dur = None
        try:
            dur = float(media_info(media_path).get("duration") or 0) or None
        except Exception:
            pass
        tmp = out.with_suffix(".tmp.wav")
        run(["-i", str(media_path), "-vn", "-map", "0:a:0", "-ac", "1" if mono else "2", "-ar", str(sr),
             "-c:a", "pcm_s16le", str(tmp)], duration=dur, progress=progress, cancel=cancel)
        os.replace(tmp, out)
    return out


def load_wav(path: str | os.PathLike, start: float = 0.0, duration: float | None = None,
             sr_expected: int | None = None) -> tuple[np.ndarray, int]:
    """Szybki odczyt fragmentu WAV/FLAC (soundfile). Zwraca (float32 [n] lub [n,ch], sr)."""
    import soundfile as sf

    with sf.SoundFile(str(path)) as f:
        sr = f.samplerate
        if sr_expected and sr != sr_expected:
            raise ValueError(f"Oczekiwano {sr_expected} Hz, plik ma {sr} Hz")
        s0 = max(0, int(round(start * sr)))
        f.seek(min(s0, f.frames))
        n = -1 if duration is None else max(0, int(round(duration * sr)))
        a = f.read(frames=n, dtype="float32", always_2d=False)
    return np.asarray(a, dtype=np.float32), sr


def has_audio(media_path: str | os.PathLike) -> bool:
    from ..media.ffmpeg import media_info

    try:
        return bool(media_info(media_path).get("has_audio"))
    except Exception:
        return False


def db(x: float | np.ndarray, floor: float = -120.0):
    v = 20 * np.log10(np.maximum(np.abs(x), 10 ** (floor / 20)))
    return float(v) if np.ndim(v) == 0 else v


def frame_rms_db(a: np.ndarray, sr: int, hop_s: float = 0.05, win_s: float | None = None) -> np.ndarray:
    """RMS w dB (dBFS) w ramkach co hop_s (okno = hop domyślnie)."""
    if a.ndim > 1:
        a = a.mean(axis=1)
    hop = max(1, int(round(hop_s * sr)))
    win = max(hop, int(round((win_s or hop_s) * sr)))
    n = max(1, int(np.ceil(len(a) / hop)))
    pad = np.zeros(n * hop + win, np.float32)
    pad[:len(a)] = a
    sq = pad.astype(np.float64) ** 2
    cs = np.concatenate([[0.0], np.cumsum(sq)])
    idx = np.arange(n) * hop
    ms = (cs[idx + win] - cs[idx]) / win
    return (10 * np.log10(np.maximum(ms, 1e-12))).astype(np.float32)


def equal_power(n: int, fade_in: bool = True) -> np.ndarray:
    """Krzywa equal-power (sin/cos) długości n: suma kwadratów fade_in i fade_out = 1."""
    if n <= 0:
        return np.ones(0, np.float32)
    x = (np.arange(n, dtype=np.float64) + 0.5) / n
    c = np.sin(0.5 * np.pi * x) if fade_in else np.cos(0.5 * np.pi * x)
    return c.astype(np.float32)


def resample(a: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(a) == 0:
        return a.astype(np.float32)
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(int(sr_in), int(sr_out))
    return resample_poly(a, sr_out // g, sr_in // g, axis=0).astype(np.float32)


def write_wav(path: str | os.PathLike, a: np.ndarray, sr: int = SR) -> Path:
    """Zapis atomowy WAV float32."""
    import soundfile as sf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".part.wav")
    sf.write(str(tmp), np.asarray(a, np.float32), sr, subtype="FLOAT")
    os.replace(tmp, path)
    return path

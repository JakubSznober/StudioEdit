"""Czyszczenie głosu: highpass, odszumianie, lekka redukcja pogłosu, EQ czytelności, de-esser, kompresja, limiter.

Odszumianie: DeepFilterNet 3 (ONNX przez onnxruntime, moduł `dfnet`, gdy wagi są pobrane) albo RNNoise
(`arnndn`, model `tools/rnnoise/std.rnnn`) + łagodny `afftdn`. Wynik: WAV 48 kHz mono float32, cache wg hasha
parametrów.
"""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path

import numpy as np

from .. import paths
from ..events import log
from ..media.ffmpeg import escape_filter_path, filters, media_info, run
from . import extract_wav, file_lock, file_sig, frame_rms_db, load_wav, param_hash

VERSION = 4  # 4: kompensacja opóźnienia filtrów, usuwanie NaN z arnndn
DEFAULT_CHAIN = {"highpass": 80, "denoise": "auto", "dereverb": 0.0, "presence_db": 2.5, "compress": True,
                 "deess": True, "mud_db": -2.0, "gain_db": 0.0}


@functools.lru_cache(maxsize=1)
def _onnxruntime_ok() -> bool:
    try:
        import onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def deepfilter_available() -> bool:
    """Bez cache: po pobraniu wag z panelu Modele DeepFilterNet włącza się bez restartu serwera."""
    from . import dfnet

    return _onnxruntime_ok() and dfnet.installed()


def load_deepfilter():
    from . import dfnet

    return dfnet.get_model()


def _deepfilter(audio48: np.ndarray, strength: float) -> np.ndarray:
    return load_deepfilter().enhance(audio48, atten_lim_db=12 + 18 * strength)


def rnnoise_model() -> Path | None:
    p = paths.RNNOISE_DIR / "std.rnnn"
    return p if p.exists() else None


def noise_profile(media_path: str) -> dict:
    """Szacuje poziom szumu i mowy (dBFS) na podstawie rozkładu RMS ramek 50 ms (16 kHz)."""
    a, sr = load_wav(extract_wav(media_path, 16000, True))
    if len(a) < sr // 4:
        return {"noise_db": -90.0, "speech_db": -90.0, "snr": 0.0}
    r = frame_rms_db(a, sr, 0.05)
    r = r[r > -100]
    if len(r) == 0:
        return {"noise_db": -100.0, "speech_db": -100.0, "snr": 0.0}
    noise = float(np.percentile(r, 10))
    speech = float(np.percentile(r, 90))
    return {"noise_db": round(noise, 1), "speech_db": round(speech, 1), "snr": round(speech - noise, 1)}


ACCESS_VIOLATION = 3221225477  # 0xC0000005: losowa awaria arnndn w FFmpeg 9.0.2 na Windows
MAX_DELAY_S = 0.15


def measure_delay(ref: np.ndarray, out: np.ndarray, sr: int, max_s: float = MAX_DELAY_S) -> int | None:
    """Opóźnienie `out` względem `ref` w próbkach (dodatnie = `out` spóźniony).

    Korelacja na 4 najgłośniejszych 2-sekundowych fragmentach; wynik to mediana fragmentów
    z wyraźnym szczytem (znormalizowana korelacja > 0,3). None, gdy nie da się jej wiarygodnie ustalić.
    """
    from scipy.signal import correlate

    n = min(len(ref), len(out))
    win, max_lag, neg = 2 * sr, int(max_s * sr), int(0.01 * sr)
    if n < win + max_lag:
        win = n - max_lag - neg
        if win < sr // 4:
            return None
    hop = sr
    starts = np.arange(0, n - win - max_lag + 1, hop)
    if len(starts) == 0:
        return None
    energy = np.array([float(np.dot(ref[s:s + win], ref[s:s + win])) for s in starts])
    if energy.max() <= 1e-8:
        return None
    padded = np.concatenate([np.zeros(neg, np.float32), out[:n]])
    lags = []
    for s in starts[np.argsort(energy)[::-1][:4]]:
        r = ref[s:s + win].astype(np.float64)
        o = padded[s: s + neg + win + max_lag].astype(np.float64)  # o[k] = out[s - neg + k]
        c = correlate(o, r, mode="valid", method="fft")
        k = int(np.argmax(c))
        seg = o[k: k + win]
        score = c[k] / (np.linalg.norm(r) * np.linalg.norm(seg) + 1e-12)
        if score > 0.3:
            lags.append(k - neg)
    return int(np.median(lags)) if lags else None


def nominal_delay(flt: str, sr: int = 48000) -> int:
    """Zapasowe oszacowanie opóźnienia łańcucha (zmierzone na FFmpeg 9.0.2, 48 kHz)."""
    d = 0
    if "arnndn=" in flt:
        d += 480
    if "afftdn=" in flt:
        d += int(0.025 * sr)
    if "alimiter=" in flt:
        d += int(0.003 * sr)
    return d


def shift_and_fit(a: np.ndarray, lag: int, n: int) -> np.ndarray:
    """Cofa sygnał o `lag` próbek i dopasowuje długość do `n` (dopełnienie zerami)."""
    if lag > 0:
        a = a[lag:]
    elif lag < 0:
        a = np.concatenate([np.zeros(-lag, np.float32), a])
    if len(a) >= n:
        return a[:n].astype(np.float32, copy=False)
    return np.concatenate([a, np.zeros(n - len(a), np.float32)])


def build_filter(chain: dict, effort: str, prof: dict, use_df: bool, rnnoise: bool = True) -> str:
    c = {**DEFAULT_CHAIN, **(chain or {})}
    f: list[str] = []
    hp = float(c.get("highpass") or 0)
    if hp > 0:
        f.append(f"highpass=f={hp:.0f}:poles=2")
    # wyrównanie poziomu mowy do ~-20 dBFS przed procesorami dynamiki
    pre = float(np.clip(-20.0 - prof.get("speech_db", -20.0), -12.0, 24.0)) + float(c.get("gain_db") or 0)
    if abs(pre) > 0.2:
        f.append(f"volume={pre:.2f}dB")
    den = c.get("denoise", "auto")
    if den is True:
        den = "auto"
    if den in (False, None):
        den = "off"
    snr = prof.get("snr", 40.0)
    noise_after = prof.get("noise_db", -80.0) + pre
    if den == "auto":
        den = "off" if noise_after < -68 or snr > 45 else ("strong" if snr < 18 else "light")
    rn = rnnoise_model()
    has = filters()
    if den != "off" and not use_df:
        if rnnoise and rn is not None and "arnndn" in has:
            mix = 0.75 if den == "light" else 1.0
            f.append(f"arnndn=m='{escape_filter_path(rn)}':mix={mix}")
        if "afftdn" in has and (den == "strong" or effort in ("high", "max") or not rnnoise):
            nr = 6 if den == "light" else 10
            nf = float(np.clip(noise_after, -80, -25))
            f.append(f"afftdn=nr={nr}:nf={nf:.0f}:tn=1:om=o")
    derev = float(c.get("dereverb") or 0.0)
    if derev > 0.01 and "agate" in has:
        # łagodny ekspander w dół: wycisza ogon pogłosu między słowami, max ~(-4..-12 dB)
        rng = 10 ** (-(4 + 8 * min(1.0, derev)) / 20)
        f.append(f"agate=threshold={10 ** (-38 / 20):.4f}:ratio={1.6 + 1.4 * derev:.2f}:range={rng:.3f}:"
                 f"attack=4:release={160 - 60 * derev:.0f}:knee=6:detection=rms")
    mud = float(c.get("mud_db") or 0)
    if abs(mud) > 0.1:
        f.append(f"equalizer=f=320:t=q:w=1.1:g={mud:.1f}")
    pres = float(c.get("presence_db") or 0)
    if abs(pres) > 0.1:
        f.append(f"equalizer=f=4200:t=q:w=0.9:g={pres:.1f}")
        f.append(f"highshelf=f=11000:g={min(1.5, pres * 0.4):.1f}")
    if c.get("deess", True) and "deesser" in has:
        f.append("deesser=i=0.35:m=0.5:f=0.5:s=o")
    if c.get("compress", True):
        f.append("acompressor=threshold=0.1:ratio=2.8:attack=8:release=140:knee=6:makeup=1.6:detection=rms")
    f.append("alimiter=limit=0.891:attack=3:release=60:level=false:asc=1")
    return ",".join(f)


def clean_voice(media_path: str, out_wav: Path, *, chain: dict, effort: str = "high") -> Path:
    """Przetwarza ścieżkę dialogową całego pliku → WAV 48 kHz mono (cache wg hasha parametrów)."""
    out_wav = Path(out_wav)
    c = {**DEFAULT_CHAIN, **(chain or {})}
    use_df = deepfilter_available() and c.get("denoise", "auto") not in ("off", False, None) and \
        c.get("engine", "auto") in ("auto", "deepfilter")
    key = param_hash(VERSION, file_sig(media_path), c, effort, use_df)
    side = out_wav.with_suffix(".json")
    with file_lock(str(out_wav)):
        if out_wav.exists() and side.exists():
            try:
                if json.loads(side.read_text(encoding="utf-8")).get("key") == key:
                    return out_wav
            except (OSError, json.JSONDecodeError):
                pass
        prof = noise_profile(media_path)
        flt = build_filter(c, effort, prof, use_df)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_wav.with_name(out_wav.stem + ".ff.wav")
        dur = float(media_info(media_path).get("duration") or 0) or None
        src = str(media_path)
        if use_df:
            try:
                from ..media.ffmpeg import read_audio

                from . import write_wav

                a = read_audio(media_path, sr=48000, mono=True, af=f"highpass=f={float(c.get('highpass') or 60):.0f}")
                strength = {"light": 0.3, "strong": 1.0}.get(str(c.get("denoise")), 0.6)
                a = _deepfilter(a, strength)
                src = str(write_wav(out_wav.with_name(out_wav.stem + ".df.wav"), a, 48000))
            except Exception as e:
                log("sound", f"DeepFilterNet nie zadziałał ({e}) - używam RNNoise", path="Czyszczenie głosu",
                    level="warn")
                flt = build_filter(c, effort, prof, False)
        from ..media.ffmpeg import FFmpegError, read_audio

        from . import write_wav

        for attempt in range(4):
            try:
                run(["-i", src, "-vn", "-map", "0:a:0", "-af", flt, "-ac", "1", "-ar", "48000", "-c:a", "pcm_f32le",
                     str(tmp)], duration=dur)
                break
            except FFmpegError as e:
                if e.returncode != ACCESS_VIOLATION or "arnndn=" not in flt:
                    raise
                if attempt < 2:
                    log("sound", "RNNoise (arnndn) przerwał FFmpeg - ponawiam", path="Czyszczenie głosu", level="warn")
                    continue
                log("sound", "RNNoise (arnndn) wciąż zawodzi - czyszczę bez niego (afftdn)", path="Czyszczenie głosu",
                    level="warn")
                flt = build_filter(c, effort, prof, False, rnnoise=False)
        y, _ = load_wav(tmp, sr_expected=48000)
        bad = ~np.isfinite(y)
        n_bad = int(bad.sum())
        if n_bad:
            y[bad] = 0.0  # arnndn zostawia NaN w ostatniej ramce
        # filtry (arnndn ~10 ms, afftdn ~25 ms, alimiter 3 ms) opóźniają dźwięk względem obrazu - cofamy go
        ref = read_audio(media_path, sr=48000, mono=True, duration=240.0)
        lag = measure_delay(ref, y, 48000)
        measured = lag is not None
        if lag is None:
            lag = nominal_delay(flt)
        write_wav(out_wav, shift_and_fit(y, lag, len(y)), 48000)
        for f in (tmp, Path(src) if src != str(media_path) else None):
            if f is not None:
                try:
                    os.remove(f)
                except OSError:
                    pass
        side.write_text(json.dumps({"key": key, "filter": flt, "profile": prof, "delay_samples": lag,
                                    "delay_measured": measured, "nan_samples_fixed": n_bad}, ensure_ascii=False),
                        encoding="utf-8")
    return out_wav

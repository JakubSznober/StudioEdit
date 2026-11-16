"""Czyszczenie głosu: highpass, odszumianie, lekka redukcja pogłosu, EQ czytelności, de-esser, kompresja, limiter.

Odszumianie: DeepFilterNet 3 (gdy pakiet `deepfilternet` działa) albo RNNoise (`arnndn`, model
`tools/rnnoise/std.rnnn`) + łagodny `afftdn`. Wynik: WAV 48 kHz mono float32, cache wg hasha parametrów.
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

VERSION = 3
DEFAULT_CHAIN = {"highpass": 80, "denoise": "auto", "dereverb": 0.0, "presence_db": 2.5, "compress": True,
                 "deess": True, "mud_db": -2.0, "gain_db": 0.0}


@functools.lru_cache(maxsize=1)
def deepfilter_available() -> bool:
    try:
        from df.enhance import enhance, init_df  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


_df_state: dict = {}


def load_deepfilter():
    if "m" not in _df_state:
        from df.enhance import init_df  # type: ignore

        model, state, _ = init_df(model_base_dir="DeepFilterNet3", log_level="ERROR")
        _df_state["m"] = (model, state)
    return _df_state["m"]


def _deepfilter(audio48: np.ndarray, strength: float) -> np.ndarray:
    import torch
    from df.enhance import enhance  # type: ignore

    model, state = load_deepfilter()
    with torch.inference_mode():
        out = enhance(model, state, torch.from_numpy(audio48[None, :].astype(np.float32)),
                      atten_lim_db=12 + 18 * strength)
    return out[0].cpu().numpy().astype(np.float32)


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


def build_filter(chain: dict, effort: str, prof: dict, use_df: bool) -> str:
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
        if rn is not None and "arnndn" in has:
            mix = 0.75 if den == "light" else 1.0
            f.append(f"arnndn=m='{escape_filter_path(rn)}':mix={mix}")
        if "afftdn" in has and (den == "strong" or effort in ("high", "max")):
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
        tmp = out_wav.with_name(out_wav.stem + ".part.wav")
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
        run(["-i", src, "-vn", "-map", "0:a:0", "-af", flt, "-ac", "1", "-ar", "48000", "-c:a", "pcm_f32le",
             str(tmp)], duration=dur)
        os.replace(tmp, out_wav)
        if src != str(media_path):
            try:
                os.remove(src)
            except OSError:
                pass
        side.write_text(json.dumps({"key": key, "filter": flt, "profile": prof}, ensure_ascii=False), encoding="utf-8")
    return out_wav

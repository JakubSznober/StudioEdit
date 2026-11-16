"""Lektor (TTS): Piper (polskie głosy ONNX, CPU) oraz klonowanie głosu z próbki (Chatterbox Multilingual, GPU).

Tekst dzielony jest na zdania/frazy; między nimi wstawiane są naturalne pauzy zależne od interpunkcji.
Wynik: WAV 48 kHz mono float32 znormalizowany do -18 LUFS (true peak <= -1 dBTP) + plik `.json` obok
z czasami zdań i (szacowanymi) słów - używany do napisów lektora.
"""
from __future__ import annotations

import functools
import json
import re
import threading
from pathlib import Path

import numpy as np

from .. import paths
from ..config import get_config
from ..events import log
from ..vram import vram
from . import SR, resample, write_wav
from . import loudness as ldn

PAUSES = {".": 0.42, "!": 0.45, "?": 0.45, "…": 0.55, ";": 0.3, ":": 0.28, ",": 0.0, "\n": 0.7}
TARGET_LUFS = -18.0
_lock = threading.Lock()
_voices: dict[str, object] = {}


def split_phrases(text: str) -> list[tuple[str, float]]:
    """[(fraza, pauza_po_s)] - podział po końcach zdań, średnikach, dwukropkach i akapitach."""
    text = re.sub(r"[ \t]+", " ", text.strip())
    parts: list[tuple[str, float]] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        chunks = re.findall(r"[^.!?…;:]+(?:[.!?…;:]+|$)", para)
        for ch in chunks:
            ch = ch.strip()
            if not ch:
                continue
            end = ch.rstrip("\"'”»)")[-1:]
            parts.append((ch, PAUSES.get(end, 0.25)))
        if parts:
            parts[-1] = (parts[-1][0], max(parts[-1][1], PAUSES["\n"]))
    if parts:
        parts[-1] = (parts[-1][0], 0.0)
    return parts


def _piper_voice(voice: str):
    from piper import PiperVoice  # type: ignore

    with _lock:
        if voice not in _voices:
            p = paths.PIPER_DIR / f"{voice}.onnx"
            if not p.exists():
                from .models import _download_piper

                _download_piper(voice)
            _voices[voice] = PiperVoice.load(str(p))
        return _voices[voice]


def _piper_phrase(v, text: str, length_scale: float | None) -> tuple[np.ndarray, int]:
    from piper.config import SynthesisConfig  # type: ignore

    cfg = SynthesisConfig(length_scale=length_scale, normalize_audio=False, noise_scale=0.6, noise_w_scale=0.75)
    chunks = list(v.synthesize(text, syn_config=cfg))
    if not chunks:
        return np.zeros(0, np.float32), 22050
    sr = chunks[0].sample_rate
    out = []
    for i, c in enumerate(chunks):
        out.append(c.audio_float_array.astype(np.float32))
        if i < len(chunks) - 1:
            out.append(np.zeros(int(0.3 * sr), np.float32))
    return np.concatenate(out), sr


def _trim(a: np.ndarray, sr: int, thr_db: float = -50.0) -> np.ndarray:
    if len(a) == 0:
        return a
    thr = 10 ** (thr_db / 20) * max(1e-6, float(np.max(np.abs(a))))
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a[:0]
    pad = int(0.03 * sr)
    return a[max(0, idx[0] - pad):min(len(a), idx[-1] + pad)]


@functools.lru_cache(maxsize=1)
def chatterbox_available() -> bool:
    try:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def _load_chatterbox():
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # type: ignore

    return ChatterboxMultilingualTTS.from_pretrained(device="cuda" if torch.cuda.is_available() else "cpu")


def _chatterbox_phrase(model, text: str, ref_audio: str, language: str) -> tuple[np.ndarray, int]:
    import torch

    with torch.inference_mode():
        wav = model.generate(text, language_id=language, audio_prompt_path=ref_audio, exaggeration=0.5,
                             cfg_weight=0.5, temperature=0.8)
    a = wav.squeeze().detach().float().cpu().numpy()
    return a.astype(np.float32), int(model.sr)


def _estimate_words(phrase: str, s: float, e: float) -> list[dict]:
    toks = phrase.split()
    if not toks:
        return []
    weights = np.array([max(1, len(re.sub(r"\W", "", t))) + 1.5 for t in toks], float)
    cum = np.concatenate([[0], np.cumsum(weights)]) / weights.sum()
    d = e - s
    return [{"w": t, "s": round(s + d * cum[i], 3), "e": round(s + d * cum[i + 1] - 0.02, 3)}
            for i, t in enumerate(toks)]


def synthesize(text: str, out_path: Path, *, voice: str | None = None, ref_audio: str | None = None,
               language: str = "pl", speed: float = 1.0) -> Path:
    out_path = Path(out_path)
    cfg = get_config()
    voice = voice or cfg.get("models.tts.voice", "pl_PL-gosia-medium")
    phrases = split_phrases(text)
    if not phrases:
        raise ValueError("Pusty tekst do syntezy")
    engine = "piper"
    use_cb = bool(ref_audio) and Path(str(ref_audio)).exists() and chatterbox_available()
    if ref_audio and not use_cb:
        log("sound", "Klonowanie głosu niedostępne (brak Chatterbox) - używam głosu Piper", path="Lektor (TTS)",
            level="warn")
    pieces: list[np.ndarray] = []
    timings: list[dict] = []
    t = 0.15
    lead = np.zeros(int(0.15 * SR), np.float32)
    pieces.append(lead)

    def add(a: np.ndarray, sr: int, phrase: str, pause: float):
        nonlocal t
        a = _trim(resample(a, sr, SR), SR)
        if len(a) == 0:
            return
        d = len(a) / SR
        timings.append({"text": phrase, "s": round(t, 3), "e": round(t + d, 3), "words": _estimate_words(phrase, t, t + d)})
        pieces.append(a)
        t += d
        if pause > 0:
            pieces.append(np.zeros(int(pause * SR), np.float32))
            t += pause

    if use_cb:
        try:
            with vram.lease("torch:tts", "sound", "Lektor (klon głosu)"):
                model = vram.cached("torch:tts", "chatterbox-mtl", _load_chatterbox)
                for ph, pause in phrases:
                    a, sr = _chatterbox_phrase(model, ph, str(ref_audio), language)
                    add(a, sr, ph, pause)
            engine = "chatterbox-multilingual"
        except Exception as e:
            log("sound", f"Chatterbox nie zadziałał ({e}) - używam Piper", path="Lektor (TTS)", level="warn")
            pieces, timings, t = [lead], [], 0.15
            use_cb = False
    if not use_cb:
        v = _piper_voice(voice)
        ls = 1.0 / max(0.5, min(2.0, speed))
        for ph, pause in phrases:
            a, sr = _piper_phrase(v, ph, ls)
            add(a, sr, ph, pause)
    pieces.append(np.zeros(int(0.2 * SR), np.float32))
    y = np.concatenate(pieces).astype(np.float32)
    # delikatna obróbka: highpass 70 Hz, normalizacja LUFS, limiter
    from scipy.signal import butter, sosfilt

    y = sosfilt(butter(2, 70, btype="high", fs=SR, output="sos"), y).astype(np.float32)
    lu = ldn.integrated(y, SR)
    if lu > -69:
        y = y * 10 ** ((TARGET_LUFS - lu) / 20)
    from .mix import limit_true_peak

    y = limit_true_peak(y.astype(np.float32), -1.0)
    write_wav(out_path, y, SR)
    out_path.with_suffix(".json").write_text(json.dumps(
        {"text": text, "voice": voice if engine == "piper" else "klon", "engine": engine,
         "duration": round(len(y) / SR, 3), "phrases": timings,
         "words": [w for ph in timings for w in ph["words"]]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return out_path

"""Transkrypcja mowy (STT) z czasami słów, podziałem na zdania i detekcją wypełniaczy/zająknięć.

Silniki (w kolejności):
  1. faster-whisper `large-v3-turbo` (CTranslate2, CUDA float16) - domyślny, działa na RTX 50xx (sm_120)
  2. transformers `openai/whisper-large-v3-turbo` na torch CUDA fp16 (gdy CTranslate2 nie ruszy na GPU)
  3. faster-whisper na CPU int8 (gdy GPU niedostępne)
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import get_config
from ..events import log
from ..project.model import Sentence, Transcript, Word
from ..vram import vram
from . import SPEECH_OWNER, extract_wav, load_wav

BEAM = {"low": 1, "medium": 3, "high": 5, "max": 5}
# Podpowiedź dla Whispera z wypełniaczami - bez niej model "wygładza" mowę i je pomija.
INITIAL_PROMPT = {
    "pl": "Eee, no więc... yyy, znaczy, to to jest tak, że, hmm, eem, no, wiesz. Mmm, dobra.",
    "en": "Umm, so, uh, I mean, like, you know, hmm, the the thing is...",
}
SOFT_FILLERS = {"pl": {"no", "znaczy", "jakby", "wiesz", "tego", "jakoś", "generalnie", "sumie"},
                "en": {"like", "so", "well", "basically", "actually"}}
# uwaga: pojedyncze "a"/"i"/"o"/"u" to polskie spójniki/przyimki - wypełniaczem jest dopiero "aaa", "uuu"
_FILLER_RE = re.compile(r"^(e+|y+|m+|h+m+|e+m+|y+m+|y+h+m+|a{2,}h*|u{2,}|u+m+|e+h+|m+h+m+)$")
_PUNCT = ".,!?;:…\"'„”«»()[]-–—"
_END_RE = re.compile(r"[.!?…]+[\"'”»)]*$")

_broken: set[str] = set()
_lock = threading.Lock()


def _norm(w: str) -> str:
    return w.strip().strip(_PUNCT).lower()


# ------------------------------------------------------------------------------------------
# Silniki
# ------------------------------------------------------------------------------------------
def _fw_path() -> str:
    from .models import entry, hf_local

    e = entry("faster-whisper-large-v3-turbo")
    p = hf_local(e["repo"], e["allow"])
    if p is not None:
        return str(p)
    name = get_config().get("models.stt.name", "large-v3-turbo")
    return name  # faster-whisper pobierze model (alias) do HF_HOME


def _load_fw(device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(_fw_path(), device=device, compute_type=compute_type,
                        cpu_threads=8 if device == "cpu" else 0)


def _fw_transcribe(model, audio: np.ndarray, language: str, effort: str, duration: float,
                   progress: Callable[[float], None] | None) -> list[dict]:
    segs, _info = model.transcribe(
        audio, language=language or None, task="transcribe", beam_size=BEAM.get(effort, 5),
        best_of=BEAM.get(effort, 5), word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 250, "threshold": 0.35},
        condition_on_previous_text=False, initial_prompt=INITIAL_PROMPT.get(language, None),
        temperature=(0.0, 0.2, 0.4, 0.6) if effort in ("high", "max") else (0.0, 0.4),
        no_speech_threshold=0.6, compression_ratio_threshold=2.4,
        hallucination_silence_threshold=2.0, suppress_blank=True,
        suppress_tokens=[-1],
    )
    words: list[dict] = []
    for s in segs:
        for w in s.words or []:
            words.append({"w": w.word, "s": float(w.start), "e": float(w.end), "p": float(w.probability)})
        if progress and duration:
            progress(min(0.99, float(s.end) / duration))
    return words


def _load_hf():
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    from .models import entry, hf_local

    e = entry("whisper-large-v3-turbo")
    src = hf_local(e["repo"], e["allow"])
    if src is None:
        from .models import download

        download("whisper-large-v3-turbo")
        src = hf_local(e["repo"], e["allow"])
    model = AutoModelForSpeechSeq2Seq.from_pretrained(str(src), torch_dtype=torch.float16,
                                                      low_cpu_mem_usage=True).to("cuda")
    proc = AutoProcessor.from_pretrained(str(src))
    return pipeline("automatic-speech-recognition", model=model, tokenizer=proc.tokenizer,
                    feature_extractor=proc.feature_extractor, torch_dtype=torch.float16, device="cuda")


def _hf_transcribe(pipe, audio: np.ndarray, language: str, progress) -> list[dict]:
    out: list[dict] = []
    chunk = 30 * 16000 * 10  # paczki po 5 min, żeby raportować postęp
    n = len(audio)
    for off in range(0, max(1, n), chunk):
        part = audio[off:off + chunk]
        if len(part) < 1600:
            continue
        r = pipe({"raw": part, "sampling_rate": 16000}, chunk_length_s=30, batch_size=8,
                 return_timestamps="word", generate_kwargs={"language": language, "task": "transcribe"})
        base = off / 16000
        for c in r.get("chunks", []):
            ts = c.get("timestamp") or (None, None)
            if ts[0] is None:
                continue
            s = float(ts[0]) + base
            e = float(ts[1]) + base if ts[1] is not None else s + 0.3
            out.append({"w": c["text"], "s": s, "e": max(e, s + 0.02), "p": 0.9})
        if progress:
            progress(min(0.99, (off + chunk) / max(1, n)))
    return out


# ------------------------------------------------------------------------------------------
# Wypełniacze, zdania, płynność
# ------------------------------------------------------------------------------------------
def filler_set(language: str = "pl") -> set[str]:
    base = {str(x).lower() for x in (get_config().get("edit.filler_words") or [])}
    if language == "en":
        base |= {"um", "uh", "umm", "uhm", "er", "erm", "hmm", "mm"}
    return base


def mark_fillers(words: list[Word], language: str = "pl") -> None:
    """Ustawia Word.filler: wypełniacze z configu, wydłużone dźwięki (eeee), powtórzenia i zająknięcia."""
    fs = filler_set(language)
    soft = SOFT_FILLERS.get(language, set())
    n = len(words)
    norms = [_norm(w.w) for w in words]
    for i, w in enumerate(words):
        t = norms[i]
        if not t:
            continue
        if t in fs or _FILLER_RE.match(t):
            w.filler = True
            continue
        # zająknięcie: "prz- przyszedłem", "s-sam"
        raw = w.w.strip()
        if i + 1 < n and (raw.endswith("-") or raw.endswith("—")) and norms[i + 1].startswith(t) and len(t) < len(norms[i + 1]):
            w.filler = True
            continue
        # powtórzenia: "to to to" -> wszystkie poza ostatnim
        if i + 1 < n and norms[i + 1] == t and words[i + 1].s - w.e < 0.6:
            w.filler = True
            continue
        # powtórzony bigram: "w tym w tym" -> pierwszy
        if i + 3 < n and norms[i] == norms[i + 2] and norms[i + 1] == norms[i + 3] and \
                words[i + 2].s - words[i + 1].e < 0.6 and len(t) <= 6:
            w.filler = True
            words[i + 1].filler = True
            continue
        # miękkie wypełniacze ("no", "znaczy") tylko gdy wyizolowane przecinkiem/pauzą z obu stron
        if t in soft:
            prev_break = i == 0 or words[i - 1].w.strip()[-1:] in ",.!?…" or w.s - words[i - 1].e > 0.25
            next_break = raw[-1:] in ",…" or (i + 1 < n and words[i + 1].s - w.e > 0.25) or i + 1 == n
            if prev_break and next_break:
                w.filler = True


def fluency(words: list[Word]) -> float:
    if not words:
        return 1.0
    n = len(words)
    fill = sum(1 for w in words if w.filler)
    lowp = float(np.mean([max(0.0, 0.75 - w.p) for w in words]))
    gaps = [b.s - a.e for a, b in zip(words, words[1:])]
    long_gaps = sum(1 for g in gaps if g > 1.2)
    pen = 1.6 * fill / max(5, n) + 0.8 * lowp + 0.05 * long_gaps
    return round(float(np.clip(1.0 - pen, 0.0, 1.0)), 3)


def build_sentences(raw: list[dict], media_short: str, language: str = "pl",
                    max_len: float = 20.0, pause: float = 0.6) -> list[Sentence]:
    words: list[Word] = []
    for r in raw:
        txt = str(r["w"]).strip()
        if not txt:
            continue
        s, e = float(r["s"]), float(r["e"])
        if words and s < words[-1].s:
            s = words[-1].s
        words.append(Word(w=txt, s=round(s, 3), e=round(max(e, s + 0.01), 3), p=round(float(r.get("p", 1.0)), 3)))
    mark_fillers(words, language)
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        dur = w.e - cur[0].s
        split = nxt is None
        if not split:
            gap = nxt.s - w.e
            if _END_RE.search(w.w) and not re.match(r"^(np|tzw|itd|itp|dr|mgr|prof|ok|godz|św|ul|nr)\.$", w.w.lower()):
                split = True
            elif gap > pause:
                split = True
            elif dur > max_len * 0.7 and (w.w.endswith(",") or gap > 0.25):
                split = True
            elif dur >= max_len:
                split = True
        if split:
            groups.append(cur)
            cur = []
    out = []
    for i, g in enumerate(groups):
        text = " ".join(w.w for w in g)
        text = re.sub(r"\s+([,.!?;:…])", r"\1", text)
        out.append(Sentence(id=f"{media_short}_{i:04d}", text=text, s=g[0].s, e=g[-1].e, words=g,
                            fluency=fluency(g)))
    return out


def media_short_id(media_path: str, media_id: str | None = None) -> str:
    if media_id:
        return re.sub(r"[^A-Za-z0-9]", "", media_id)[-8:] or "m"
    from . import param_hash

    stem = re.sub(r"[^A-Za-z0-9]", "", Path(media_path).stem)[:10] or "m"
    return f"{stem}{param_hash(str(Path(media_path).resolve()), n=4)}"


# ------------------------------------------------------------------------------------------
# API
# ------------------------------------------------------------------------------------------
def transcribe(media_path: str, *, language: str = "pl", effort: str = "high", progress=None,
               agent: str = "sound", media_id: str | None = None) -> Transcript:
    """Transkrypcja z czasami słów. `media_id` (opcjonalnie) daje stabilne ID zdań `<media>_<nnnn>`."""
    t_start = time.time()
    language = language or get_config().get("models.stt.language", "pl")
    p = progress or (lambda x: None)
    wav = extract_wav(media_path, 16000, True, progress=lambda x: p(0.05 * x))
    audio, _sr = load_wav(wav)
    duration = len(audio) / 16000.0
    short = media_short_id(media_path, media_id)
    if duration < 0.2 or float(np.max(np.abs(audio))) < 1e-4:
        return Transcript(language=language, engine="brak mowy", sentences=[], speakers=[])
    sub = lambda x: p(0.05 + 0.9 * x)  # noqa: E731
    raw: list[dict] | None = None
    engine = ""
    cfg = get_config()
    compute = cfg.get("models.stt.compute_type", "float16")
    gpu = _cuda_ok()
    if gpu and "fw-cuda" not in _broken:
        try:
            with vram.lease(SPEECH_OWNER, agent, "Transkrypcja"):
                model = vram.cached(SPEECH_OWNER, f"fw:cuda:{compute}", lambda: _load_fw("cuda", compute))
                raw = _fw_transcribe(model, audio, language, effort, duration, sub)
            engine = f"faster-whisper large-v3-turbo (CUDA {compute})"
        except Exception as e:  # np. brak kerneli sm_120 / DLL cuBLAS
            with _lock:
                _broken.add("fw-cuda")
            log(agent, f"faster-whisper na GPU nie działa ({e}) - przechodzę na transformers", path="Transkrypcja (STT)",
                level="warn")
            raw = None
    if raw is None and gpu and "hf-cuda" not in _broken:
        try:
            with vram.lease(SPEECH_OWNER, agent, "Transkrypcja (transformers)"):
                pipe = vram.cached(SPEECH_OWNER, "hf:whisper", _load_hf)
                raw = _hf_transcribe(pipe, audio, language, sub)
            engine = "transformers whisper-large-v3-turbo (CUDA fp16)"
        except Exception as e:
            with _lock:
                _broken.add("hf-cuda")
            log(agent, f"Whisper (transformers) na GPU nie działa ({e}) - przechodzę na CPU", path="Transkrypcja (STT)",
                level="warn")
            raw = None
    if raw is None:
        model = _cpu_model()
        raw = _fw_transcribe(model, audio, language, "low" if effort == "low" else "medium", duration, sub)
        engine = "faster-whisper large-v3-turbo (CPU int8)"
    sentences = build_sentences(raw, short, language)
    p(1.0)
    tr = Transcript(language=language, engine=engine, sentences=sentences, speakers=[])
    log(agent, f"Transkrypcja {Path(media_path).name}: {len(sentences)} zdań, {sum(len(s.words) for s in sentences)} słów, "
               f"{duration:.0f} s audio w {time.time() - t_start:.1f} s ({engine})", path="Transkrypcja (STT)")
    return tr


_cpu_cache: dict[str, object] = {}


def _cpu_model():
    with _lock:
        if "m" not in _cpu_cache:
            _cpu_cache["m"] = _load_fw("cpu", "int8")
        return _cpu_cache["m"]


def _cuda_ok() -> bool:
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return True
    except Exception:
        pass
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


def speech_ratio(tr: Transcript | None, duration: float) -> float:
    if not tr or duration <= 0:
        return 0.0
    cov = sum(max(0.0, w.e - w.s) for w in tr.words())
    return round(float(min(1.0, cov / duration)), 3)

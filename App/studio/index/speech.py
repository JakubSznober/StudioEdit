"""Transkrypcja faster-whisper ze znacznikami słów. Model ładowany raz na całą partię."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config import SETTINGS
from ..gpu import free_torch

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self) -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(SETTINGS.whisper_model_dir, device="cuda", compute_type=SETTINGS.whisper_compute)

    def transcribe(self, audio: Path) -> tuple[list[dict[str, Any]], str | None]:
        segments, info = self.model.transcribe(
            str(audio),
            language=SETTINGS.whisper_language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
        )
        out: list[dict[str, Any]] = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            out.append({
                "start": round(seg.start, 3), "end": round(seg.end, 3), "text": text,
                "words": [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 3)}
                          for w in (seg.words or [])],
                "avg_logprob": seg.avg_logprob, "no_speech_prob": seg.no_speech_prob,
            })
        return out, info.language

    def close(self) -> None:
        del self.model
        free_torch()


def speech_ratio(segments: list[dict[str, Any]], start: float, end: float) -> float:
    length = max(end - start, 1e-6)
    covered = 0.0
    for s in segments:
        a, b = max(start, s["start"]), min(end, s["end"])
        if b > a:
            covered += b - a
    return min(1.0, covered / length)

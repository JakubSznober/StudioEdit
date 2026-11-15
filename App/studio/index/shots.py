"""Granice ujęć (PySceneDetect) + podział długich ujęć na jednostki analizy."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from scenedetect import AdaptiveDetector, detect

from ..config import SETTINGS


def detect_shots(video: Path, duration: float) -> list[dict[str, Any]]:
    scenes = detect(str(video), AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=15), show_progress=False)
    cuts: list[tuple[float, float]] = [(s.get_seconds(), e.get_seconds()) for s, e in scenes]
    if not cuts:
        cuts = [(0.0, duration)]
    # ostatni fragment do końca pliku (detektor czasem kończy wcześniej)
    s0, e0 = cuts[-1]
    if duration and e0 < duration - 0.05:
        cuts[-1] = (s0, duration)

    units: list[dict[str, Any]] = []
    max_len, min_len = SETTINGS.max_shot_len, SETTINGS.min_shot_len
    for start, end in cuts:
        length = end - start
        if length < min_len and units:
            # bardzo krótki fragment (flash, błąd detekcji): doklejamy do poprzedniego
            units[-1]["end"] = end
            continue
        n = max(1, int(length // max_len) + (1 if length % max_len > min_len else 0))
        step = length / n
        for k in range(n):
            units.append({"start": round(start + k * step, 3), "end": round(start + (k + 1) * step, 3), "cut_start": k == 0})
    return units

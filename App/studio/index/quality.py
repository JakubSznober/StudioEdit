"""Metryki jakości ujęcia (OpenCV na proxy) i klatki kluczowe."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import SETTINGS

log = logging.getLogger(__name__)

ANALYSIS_WIDTH = 640


def _read_at(cap: cv2.VideoCapture, t: float) -> np.ndarray | None:
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t * 1000.0))
    ok, frame = cap.read()
    return frame if ok else None


def _small_gray(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = ANALYSIS_WIDTH / max(w, 1)
    small = cv2.resize(frame, (ANALYSIS_WIDTH, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def _sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _exposure(gray: np.ndarray) -> tuple[float, float, float]:
    return float(gray.mean() / 255.0), float((gray >= 250).mean()), float((gray <= 5).mean())


def _motion(g0: np.ndarray, g1: np.ndarray) -> tuple[float, float]:
    """(ruch w kadrze: średnia różnica, ruch kamery: przesunięcie globalne w px)."""
    diff = float(np.abs(g1.astype(np.int16) - g0.astype(np.int16)).mean() / 255.0)
    f0 = np.float32(g0)
    f1 = np.float32(g1)
    win = cv2.createHanningWindow(f0.shape[::-1], cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(f0, f1, win)
    return diff, float(np.hypot(dx, dy))


def analyze_shot(cap: cv2.VideoCapture, fps: float, start: float, end: float, keyframe_prefix: Path) -> dict[str, Any]:
    n = SETTINGS.frames_per_shot
    length = max(end - start, 1e-3)
    times = [start + length * (k + 0.5) / n for k in range(n)]
    sharp, lum, hi, lo, motions, cams = [], [], [], [], [], []
    keyframes: list[str] = []
    frame_dt = 1.0 / (fps or 25.0)
    keyframe_prefix.parent.mkdir(parents=True, exist_ok=True)

    for k, t in enumerate(times):
        f0 = _read_at(cap, t)
        if f0 is None:
            continue
        g0 = _small_gray(f0)
        sharp.append(_sharpness(g0))
        m, h_, l_ = _exposure(g0)
        lum.append(m), hi.append(h_), lo.append(l_)
        ok, f1 = cap.read()  # następna klatka
        if ok and f1 is not None:
            d, c = _motion(g0, _small_gray(f1))
            motions.append(d), cams.append(c)
        out = keyframe_prefix.with_name(f"{keyframe_prefix.name}_{k}.jpg")
        hmax = SETTINGS.vlm_image_max_side
        hh, ww = f0.shape[:2]
        if max(hh, ww) > hmax:
            s = hmax / max(hh, ww)
            f0 = cv2.resize(f0, (int(ww * s), int(hh * s)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(out), f0, [cv2.IMWRITE_JPEG_QUALITY, 88])
        keyframes.append(str(out))

    if not sharp:
        return {"keyframes": keyframes}
    cams_arr = np.array(cams) if cams else np.zeros(1)
    return {
        "keyframes": keyframes,
        "sharpness": float(np.median(sharp)),
        "luma_mean": float(np.mean(lum)),
        "clip_hi": float(np.mean(hi)),
        "clip_lo": float(np.mean(lo)),
        "motion": float(np.mean(motions)) if motions else None,
        "cam_motion": float(cams_arr.mean()),
        "shake": float(cams_arr.std()),
        "_frame_dt": frame_dt,
    }


def analyze_media(video: Path, fps: float, shots: list[dict[str, Any]], keyframes_dir: Path) -> list[dict[str, Any]]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        log.warning("nie można otworzyć %s", video)
        return [{} for _ in shots]
    results = []
    for s in shots:
        prefix = keyframes_dir / f"shot_{s['idx']:04d}"
        results.append(analyze_shot(cap, fps, s["start"], s["end"], prefix))
    cap.release()
    return results


def analyze_image(image: Path, keyframe_path: Path) -> dict[str, Any]:
    frame = cv2.imread(str(image))
    if frame is None:
        return {}
    g = _small_gray(frame)
    m, h_, l_ = _exposure(g)
    keyframe_path.parent.mkdir(parents=True, exist_ok=True)
    hh, ww = frame.shape[:2]
    hmax = SETTINGS.vlm_image_max_side
    if max(hh, ww) > hmax:
        s = hmax / max(hh, ww)
        frame = cv2.resize(frame, (int(ww * s), int(hh * s)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(keyframe_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return {"keyframes": [str(keyframe_path)], "sharpness": _sharpness(g), "luma_mean": m, "clip_hi": h_, "clip_lo": l_,
            "motion": 0.0, "cam_motion": 0.0, "shake": 0.0}

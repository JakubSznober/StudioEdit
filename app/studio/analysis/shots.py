"""Detekcja ujęć (PySceneDetect) oraz analiza jakości i ruchu (OpenCV) w jednym przebiegu po proxy."""
from __future__ import annotations

import math
import threading
from pathlib import Path

import cv2
import numpy as np

from ..media import ffmpeg as ff
from ..project.model import Shot


def detect_shots(path: str, duration: float, *, frame_skip: int = 0, min_shot: float = 0.6) -> list[tuple[float, float]]:
    """Zwraca listę (start, koniec) ujęć w sekundach."""
    try:
        from scenedetect import AdaptiveDetector, ContentDetector, SceneManager, open_video

        video = open_video(path, backend="opencv")
        sm = SceneManager()
        sm.add_detector(AdaptiveDetector(min_scene_len=int(max(8, min_shot * (video.frame_rate or 30)))))
        sm.add_detector(ContentDetector(threshold=32.0, min_scene_len=int(max(8, min_shot * (video.frame_rate or 30)))))
        sm.auto_downscale = True
        sm.detect_scenes(video, frame_skip=frame_skip, show_progress=False)
        scenes = sm.get_scene_list()
        cuts = sorted({round(s[0].get_seconds(), 3) for s in scenes} | {round(s[1].get_seconds(), 3) for s in scenes})
    except Exception:
        cuts = []
    cuts = [c for c in cuts if 0.0 < c < duration - 0.05]
    bounds = [0.0, *cuts, duration]
    shots: list[tuple[float, float]] = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a < min_shot and shots:
            shots[-1] = (shots[-1][0], b)  # połącz zbyt krótkie z poprzednim
        elif b - a > 0.05:
            shots.append((a, b))
    if not shots:
        shots = [(0.0, duration)]
    # bardzo długie ujęcia dziel na segmenty ~12 s (lepsze opisy i wybór fragmentów)
    out: list[tuple[float, float]] = []
    for a, b in shots:
        n = max(1, int(math.ceil((b - a) / 12.0))) if b - a > 16 else 1
        step = (b - a) / n
        for i in range(n):
            out.append((round(a + i * step, 3), round(a + (i + 1) * step, 3)))
    return out


# Detektor jest osobny dla każdego wątku: Archiwista analizuje kilka plików równolegle, a wspólny
# FaceDetectorYN zmieniał rozmiar wejścia (setInputSize) w trakcie detect() innego wątku → asercja
# OpenCV „buf.shape() == m.shape()” i utrata analizy całego pliku. CascadeClassifier też nie jest
# bezpieczny wątkowo.
_face_tls = threading.local()


def _init_face():
    """YuNet (OpenCV DNN, model w AI-Models\\weights\\face) lub kaskada Haara (OpenCV < 5)."""
    if getattr(_face_tls, "kind", ""):
        return
    from .. import paths

    cands = sorted((paths.WEIGHTS / "face").glob("face_detection_yunet*.onnx")) if (paths.WEIGHTS / "face").exists() else []
    if cands and hasattr(cv2, "FaceDetectorYN"):
        try:
            _face_tls.det = cv2.FaceDetectorYN.create(str(cands[-1]), "", (320, 320), 0.7, 0.3, 50)
            _face_tls.kind = "yunet"
            return
        except Exception:
            pass
    if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
        _face_tls.det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        _face_tls.kind = "haar"
        return
    _face_tls.det = None
    _face_tls.kind = "none"


def _faces(gray: np.ndarray, rgb: np.ndarray | None = None) -> list[tuple[int, int, int, int]]:
    _init_face()
    det, kind = _face_tls.det, _face_tls.kind
    if kind == "yunet" and rgb is not None:
        h, w = rgb.shape[:2]
        det.setInputSize((w, h))
        _ok, res = det.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if res is None:
            return []
        return [tuple(int(v) for v in r[:4]) for r in res]
    if kind == "haar":
        found = det.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(24, 24))
        return [tuple(map(int, f)) for f in found] if len(found) else []
    return []


def analyze_frames(path: str, duration: float, shots: list[tuple[float, float]], *, fps: float = 4.0,
                   progress=None, cancel=None) -> dict:
    """Jeden przebieg po klatkach: luminancja, przepalenia, ostrość, ruch (optical flow), drgania, twarze."""
    width = 320
    times, luma, clipped, crushed, sharp, motion, gx, gy = [], [], [], [], [], [], [], []
    face_hits: dict[int, list] = {}
    prev_small = None
    n_expected = max(1, int(duration * fps))
    for i, frame in enumerate(ff.read_frames(path, fps=fps, width=width)):
        if cancel is not None and cancel.is_set():
            raise ff.Cancelled("Przerwano")
        t = i / fps
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        times.append(t)
        luma.append(float(gray.mean()) / 255.0)
        clipped.append(float((gray >= 250).mean()) * 100)
        crushed.append(float((gray <= 5).mean()) * 100)
        sharp.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        small = cv2.resize(gray, (160, int(gray.shape[0] * 160 / gray.shape[1])))
        if prev_small is not None and prev_small.shape == small.shape:
            flow = cv2.calcOpticalFlowFarneback(prev_small, small, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            mag = np.linalg.norm(flow, axis=2)
            motion.append(float(mag.mean()))
            gx.append(float(np.median(flow[..., 0])))
            gy.append(float(np.median(flow[..., 1])))
        else:
            motion.append(0.0)
            gx.append(0.0)
            gy.append(0.0)
        prev_small = small
        # twarze: co ~1 s
        if i % max(1, int(fps)) == 0:
            si = _shot_index(shots, t)
            fs = _faces(gray, frame)
            if fs:
                h, w = gray.shape
                x, y, fw, fh = max(fs, key=lambda f: f[2] * f[3])
                face_hits.setdefault(si, []).append([(x + fw / 2) / w, (y + fh / 2) / h, fw / w, fh / h])
        if progress and i % 10 == 0:
            progress(min(1.0, i / n_expected))
    times_a = np.array(times)
    res_shots = []
    # drgania: składowa wysokoczęstotliwościowa ruchu globalnego
    gxa, gya = np.array(gx), np.array(gy)
    if len(gxa) > 5:
        k = np.ones(5) / 5
        hx = gxa - np.convolve(gxa, k, mode="same")
        hy = gya - np.convolve(gya, k, mode="same")
        jitter = np.sqrt(hx ** 2 + hy ** 2)
    else:
        jitter = np.zeros_like(gxa)
    for si, (a, b) in enumerate(shots):
        m = (times_a >= a) & (times_a < b) if len(times_a) else np.array([], bool)
        if not m.any():
            # bardzo krótkie ujęcie - najbliższa klatka
            idx = [int(np.argmin(np.abs(times_a - (a + b) / 2)))] if len(times_a) else []
            m = np.zeros(len(times_a), bool)
            for j in idx:
                m[j] = True

        def agg(arr, fn=np.mean):
            arr = np.array(arr)
            return float(fn(arr[m])) if len(arr) and m.any() else 0.0

        faces = face_hits.get(si)
        face_box = [float(v) for v in np.median(np.array(faces), axis=0)] if faces else None
        res_shots.append({
            "s": a, "e": b,
            "luma_mean": round(agg(luma), 4),
            "clipped_pct": round(agg(clipped), 3),
            "clipped_max": round(agg(clipped, np.max), 3),
            "crushed_pct": round(agg(crushed), 3),
            "sharpness": round(agg(sharp, np.median), 2),
            "motion": round(agg(motion), 4),
            "shake": round(min(1.0, agg(jitter) / 1.5), 4) if len(jitter) else 0.0,
            "face_box": face_box,
            "people_hint": len(faces or []),
        })
    return {"shots": res_shots, "motion_curve": [round(x, 4) for x in motion], "motion_fps": fps,
            "luma_curve": [round(x, 4) for x in luma]}


def _shot_index(shots, t) -> int:
    for i, (a, b) in enumerate(shots):
        if a <= t < b:
            return i
    return len(shots) - 1


def shot_keyframes(path: str, shot: Shot, out_dir: Path, n: int = 3, width: int = 640) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = shot.e - shot.s
    ts = [shot.s + d * (i + 1) / (n + 1) for i in range(n)] if d > 0.3 else [shot.s + d / 2]
    paths = []
    for j, t in enumerate(ts):
        p = out_dir / f"{shot.id}_{j}.jpg"
        if not p.exists():
            try:
                ff.extract_frame(path, t, p, width=width, quality=4)
            except ff.FFmpegError:
                continue
        paths.append(str(p))
    return paths


def quality_flags(shots: list[dict], duration: float) -> tuple[list[str], dict]:
    """Flagi klipu wg polityki „Zero usuwania prześwietleń”: oznacz do ratowania, nie odrzucaj."""
    flags, details = [], {}
    if not shots:
        return flags, details
    clipped = max(s["clipped_pct"] for s in shots)
    luma = float(np.mean([s["luma_mean"] for s in shots]))
    crushed = float(np.mean([s["crushed_pct"] for s in shots]))
    sharp = float(np.median([s["sharpness"] for s in shots]))
    shake = float(np.mean([s["shake"] for s in shots]))
    if clipped > 1.5 or luma > 0.72:
        flags.append("NEEDS_EXPOSURE_RECOVERY")
        details["clipped_pct"] = round(clipped, 2)
        details["luma_mean"] = round(luma, 3)
    if luma < 0.16 or crushed > 25:
        flags.append("NEEDS_EXPOSURE_LIFT")
        details["crushed_pct"] = round(crushed, 2)
    if sharp < 25:
        flags.append("BLURRY")
        details["sharpness"] = round(sharp, 1)
    if shake > 0.35:
        flags.append("SHAKY")
        details["shake"] = round(shake, 3)
    # totalne śmieci: prawie czarne i nieostre (kieszeń/podłoga) albo ułamek sekundy
    junk = (luma < 0.05 and sharp < 15) or duration < 0.5
    if junk:
        flags.append("JUNK")
        details["junk_reason"] = "ciemne i nieostre (kieszeń/podłoga)" if duration >= 0.5 else "zbyt krótki klip"
    return flags, details

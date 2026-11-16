"""Śledzenie twarzy/osoby do Smart Reframingu (np. 16:9 → 9:16) i anonimizacji.

Detekcja: YuNet (OpenCV Zoo, ONNX - CPU, bardzo szybki) dla twarzy; YOLO11m (klasa „person”) gdy twarzy
nie ma w klatce. Wybór głównego obiektu z ciągłością w czasie, interpolacja luk i wygładzanie
zero-fazowe filtrem One-Euro (brak skoków i opóźnienia kadru).
"""
from __future__ import annotations

import math
import threading
from pathlib import Path

import cv2
import numpy as np

from ..media import ffmpeg
from . import models

_lock = threading.Lock()
_yunet_cache: dict[str, object] = {}
_yolo_cpu: dict[str, object] = {}


def _yunet(w: int, h: int):
    p = models.path_of("yunet")
    if not p.exists():
        return None
    key = f"{threading.get_ident()}"
    det = _yunet_cache.get(key)
    if det is None:
        det = cv2.FaceDetectorYN.create(str(p), "", (w, h), 0.6, 0.3, 50)
        _yunet_cache[key] = det
    det.setInputSize((w, h))
    return det


def detect_faces(rgb: np.ndarray) -> list[list[float]]:
    """Twarze [x, y, w, h, score] w pikselach (YuNet)."""
    h, w = rgb.shape[:2]
    det = _yunet(w, h)
    if det is None:
        return []
    _n, faces = det.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if faces is None:
        return []
    return [[float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])] for f in faces]


def yolo_model(device: str = "cpu"):
    """YOLO11m z AI-Models\\weights\\yolo (nigdy nie pobiera do cwd)."""
    p = models.path_of("yolo11m")
    if not p.exists():
        raise RuntimeError("Brak wag YOLO11m - pobierz model „YOLO11m” w Ustawieniach → Modele")
    from ultralytics import YOLO

    with _lock:
        m = _yolo_cpu.get(device)
        if m is None:
            m = YOLO(str(p))
            _yolo_cpu[device] = m
    return m


def detect_objects(rgb: np.ndarray, classes: list[int] | None = None, *, model=None, device: str = "cpu",
                   conf: float = 0.3) -> list[list[float]]:
    """Obiekty COCO [x, y, w, h, score, cls] w pikselach."""
    m = model or yolo_model(device)
    res = m.predict(rgb[..., ::-1], classes=classes, conf=conf, verbose=False, device=device, imgsz=640)
    out = []
    for r in res:
        if r.boxes is None:
            continue
        for b, s, c in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy(), r.boxes.cls.cpu().numpy()):
            out.append([float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1]), float(s), int(c)])
    return out


class OneEuro:
    """Filtr One-Euro (Casiez i in.) - mało drgań w spoczynku, małe opóźnienie przy szybkim ruchu."""

    def __init__(self, freq: float, min_cutoff: float = 0.6, beta: float = 0.8, d_cutoff: float = 1.0):
        self.freq, self.min_cutoff, self.beta, self.d_cutoff = freq, min_cutoff, beta, d_cutoff
        self.x_prev: float | None = None
        self.dx_prev = 0.0

    @staticmethod
    def _alpha(cutoff: float, freq: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau * freq)

    def __call__(self, x: float) -> float:
        if self.x_prev is None:
            self.x_prev = x
            return x
        dx = (x - self.x_prev) * self.freq
        a_d = self._alpha(self.d_cutoff, self.freq)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, self.freq)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev = x_hat, dx_hat
        return x_hat


def smooth_series(v: np.ndarray, freq: float, *, min_cutoff: float = 0.5, beta: float = 4.0) -> np.ndarray:
    """Zero-fazowe wygładzanie: One-Euro w przód i w tył, uśrednione."""
    if len(v) < 3:
        return v.copy()
    f = OneEuro(freq, min_cutoff, beta)
    fw = np.array([f(float(x)) for x in v])
    b = OneEuro(freq, min_cutoff, beta)
    bw = np.array([b(float(x)) for x in v[::-1]])[::-1]
    return (fw + bw) / 2


def _pick(cands: list[list[float]], prev: tuple[float, float] | None, W: int, H: int) -> list[float] | None:
    """Wybór głównego obiektu: rozmiar × pewność, z premią za bliskość poprzedniej pozycji."""
    if not cands:
        return None
    best, best_s = None, -1e9
    for c in cands:
        x, y, w, h, s = c[:5]
        cx, cy = (x + w / 2) / W, (y + h / 2) / H
        area = (w * h) / (W * H)
        score = math.sqrt(area) * 3 + 0.3 * s
        if prev is not None:
            score -= 6.0 * math.hypot(cx - prev[0], cy - prev[1])  # ciągłość: bez przeskoków między osobami
        if score > best_s:
            best, best_s = c, score
    return best


def track_subject(media_path: str, t0: float, t1: float, *, fps: float = 5) -> list[dict]:
    """[{t, cx, cy, w, h}] - t w sekundach źródła, reszta znormalizowana 0..1; twarz (lub głowa osoby)."""
    info = ffmpeg.media_info(media_path)
    dur = max(0.05, float(t1) - float(t0))
    fps = max(0.5, float(fps))
    sw, sh = int(info.get("width") or 1920), int(info.get("height") or 1080)
    width = 640 if sw >= sh else 480
    frames = list(ffmpeg.read_frames(media_path, fps=fps, width=width, start=t0, duration=dur))
    if not frames:
        return []
    H, W = frames[0].shape[:2]
    yolo = None
    raw: list[tuple[float, list[float] | None]] = []
    prev = None
    for i, fr in enumerate(frames):
        t = float(t0) + i / fps
        faces = [f for f in detect_faces(fr) if f[4] >= 0.6]
        box = None
        f = _pick(faces, prev, W, H)
        if f is not None:
            x, y, w, h = f[:4]
            box = [(x + w / 2) / W, (y + h / 2) / H, w / W, h / H]
        else:
            try:
                yolo = yolo or yolo_model("cpu")
                persons = detect_objects(fr, [0], model=yolo)
                pbox = _pick(persons, prev, W, H)
            except Exception:
                pbox = None
            if pbox is not None:
                x, y, w, h = pbox[:4]
                # głowa ~ górne 18% sylwetki
                box = [(x + w / 2) / W, (y + h * 0.16) / H, min(1.0, w * 0.6 / W), min(1.0, h * 0.2 / H)]
        if box is not None:
            prev = (box[0], box[1])
        raw.append((t, box))
    known = [i for i, (_t, b) in enumerate(raw) if b is not None]
    if not known:
        return []
    ts = np.array([t for t, _ in raw])
    arr = np.full((len(raw), 4), np.nan)
    for i in known:
        arr[i] = raw[i][1]
    for c in range(4):  # interpolacja luk (krawędzie - wartość najbliższa)
        col = arr[:, c]
        ok = ~np.isnan(col)
        arr[:, c] = np.interp(ts, ts[ok], col[ok])
    # odrzucenie pojedynczych skoków (mediana 3)
    if len(arr) >= 3:
        med = arr.copy()
        for i in range(1, len(arr) - 1):
            med[i] = np.median(arr[i - 1:i + 2], axis=0)
        arr = med
    out_arr = np.stack([smooth_series(arr[:, c], fps, min_cutoff=0.4 if c < 2 else 0.2, beta=3.0)
                        for c in range(4)], axis=1)
    out_arr = np.clip(out_arr, 0.0, 1.0)
    return [{"t": round(float(t), 3), "cx": round(float(r[0]), 4), "cy": round(float(r[1]), 4),
             "w": round(float(r[2]), 4), "h": round(float(r[3]), 4)} for t, r in zip(ts, out_arr)]


def face_boxes_per_frame(frames: list[np.ndarray], *, min_score: float = 0.55, pad: float = 0.25) -> list[list[list[int]]]:
    """Wszystkie twarze w każdej klatce [x0,y0,x1,y1] (piksele, z marginesem) - do anonimizacji."""
    out = []
    for fr in frames:
        H, W = fr.shape[:2]
        boxes = []
        for x, y, w, h, s in detect_faces(fr):
            if s < min_score:
                continue
            px, py = w * pad, h * pad
            boxes.append([max(0, int(x - px)), max(0, int(y - py * 1.4)), min(W, int(x + w + px)),
                          min(H, int(y + h + py))])
        out.append(boxes)
    return out


def ensure_weights_dir() -> Path:
    return models.path_of("yolo11m").parent

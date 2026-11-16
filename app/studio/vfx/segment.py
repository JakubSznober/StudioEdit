"""Maskowanie obiektu w ruchu: SAM 2.1 (Ultralytics SAM2VideoPredictor) z punktu / prostokąta / opisu.

Opis tekstowy (po polsku) → klasa COCO → prostokąt z detektora YOLO11m na pierwszej klatce fragmentu.
Współrzędne wejściowe: punkty [[x, y], ...] i prostokąt [x, y, w, h] - znormalizowane 0..1 (lub piksele
źródła, gdy wartości > 1). Całość GPU w `vram.lease("torch:sam2", "vfx", ...)`.
"""
from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Callable

import numpy as np

from ..media import ffmpeg
from ..vram import vram
from . import models

OWNER = "torch:sam2"

# polskie nazwy → klasy COCO (YOLO); None = brak klasy, potrzebny punkt/prostokąt
COCO_PL: list[tuple[tuple[str, ...], int | None, str]] = [
    (("osob", "człowiek", "czlowiek", "ludzi", "przechodni", "mężczyzn", "kobiet", "dziecko", "facet", "postać"),
     0, "osoba"),
    (("rower",), 1, "rower"), (("samochód", "samochod", "auto ", "auta", "aut "), 2, "samochód"),
    (("motocykl", "motor"), 3, "motocykl"), (("samolot",), 4, "samolot"), (("autobus",), 5, "autobus"),
    (("pociąg", "pociag"), 6, "pociąg"), (("ciężarów", "ciezarow"), 7, "ciężarówka"), (("łódź", "lodz", "łódk"), 8, "łódź"),
    (("sygnaliz", "światła drog"), 9, "sygnalizacja"), (("hydrant",), 10, "hydrant"), (("znak stop",), 11, "znak stop"),
    (("ławk", "lawk"), 13, "ławka"), (("ptak", "gołąb", "golab", "mewa"), 14, "ptak"), (("kot",), 15, "kot"),
    (("pies", "psa", "psem"), 16, "pies"), (("koń", "konia"), 17, "koń"), (("plecak",), 24, "plecak"),
    (("parasol",), 25, "parasol"), (("torebk", "torba", "torbę"), 26, "torba"), (("krawat",), 27, "krawat"),
    (("walizk",), 28, "walizka"), (("piłk", "pilk"), 32, "piłka"), (("butelk",), 39, "butelka"),
    (("kieliszek", "kielisz"), 40, "kieliszek"), (("kubek", "filiżank", "kubka"), 41, "kubek"),
    (("krzesł", "krzesl", "krzesło"), 56, "krzesło"), (("kanap", "sofa"), 57, "kanapa"),
    (("roślin", "doniczk", "kwiat"), 58, "roślina"), (("stół", "stol", "biurk"), 60, "stół"),
    (("telewizor", "monitor", "ekran"), 62, "monitor"), (("laptop",), 63, "laptop"), (("myszk",), 64, "mysz"),
    (("pilot",), 65, "pilot"), (("klawiatur",), 66, "klawiatura"), (("telefon", "komórk", "smartfon"), 67, "telefon"),
    (("książk", "ksiazk"), 73, "książka"), (("zegar",), 74, "zegar"), (("wazon",), 75, "wazon"),
    (("nożyczk",), 76, "nożyczki"), (("miś", "pluszak"), 77, "miś"),
    (("kabel", "przewód", "przewod", "mikrofon", "statyw", "logo", "napis", "tyczk", "lamp", "drut"), None, ""),
]


def prompt_class(prompt: str | None) -> tuple[int | None, str, bool]:
    """(klasa COCO | None, nazwa, rozpoznano_słowo). Słowa bez klasy (kabel, statyw...) → (None, '', True)."""
    t = f" {(prompt or '').lower()} "
    for keys, cls, name in COCO_PL:
        for k in keys:
            if k in t:
                return cls, name or k.strip(), True
    m = re.search(r"\b(person|car|dog|cat|bottle|chair|bicycle|bird|cup|phone|laptop)\b", t)
    if m:
        en = {"person": 0, "car": 2, "dog": 16, "cat": 15, "bottle": 39, "chair": 56, "bicycle": 1, "bird": 14,
              "cup": 41, "phone": 67, "laptop": 63}
        return en[m.group(1)], m.group(1), True
    return None, "", False


def _to_px(vals: list[float], W: int, H: int, src_w: int, src_h: int) -> list[float]:
    """Znormalizowane (≤1) lub piksele źródła → piksele fragmentu."""
    if all(0.0 <= float(v) <= 1.0 for v in vals):
        return [float(v) * (W if i % 2 == 0 else H) for i, v in enumerate(vals)]
    return [float(v) * (W / max(1, src_w) if i % 2 == 0 else H / max(1, src_h)) for i, v in enumerate(vals)]


def cut_fragment(media_path: str, t0: float, t1: float, out: Path, *, max_side: int = 1024,
                 cancel: threading.Event | None = None) -> Path:
    """Fragment [t0,t1] w zmniejszonej rozdzielczości (do SAM 2.1), ta sama liczba klatek co źródło."""
    info = ffmpeg.media_info(media_path)
    sw, sh = int(info.get("width") or 1920), int(info.get("height") or 1080)
    k = min(1.0, max_side / max(sw, sh))
    w, h = int(round(sw * k / 2) * 2), int(round(sh * k / 2) * 2)
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(["-ss", f"{t0:.4f}", "-t", f"{max(0.05, t1 - t0):.4f}", "-i", str(media_path), "-an",
                "-vf", f"scale={w}:{h}:flags=bicubic", "-fps_mode", "passthrough",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", str(out)],
               cancel=cancel)
    return out


def _yolo_gpu():
    from ultralytics import YOLO

    p = models.path_of("yolo11m")
    if not p.exists():
        raise RuntimeError("Brak wag YOLO11m - pobierz model „YOLO11m” w Ustawieniach → Modele")
    return vram.cached(OWNER, "yolo11m", lambda: YOLO(str(p)))


def find_box(rgb: np.ndarray, prompt: str | None) -> tuple[list[float] | None, str]:
    """Prostokąt [x0,y0,x1,y1] (piksele) obiektu z opisu - największy pewny obiekt danej klasy."""
    cls, name, known = prompt_class(prompt)
    if cls is None:
        return None, name
    import torch

    dev = 0 if torch.cuda.is_available() else "cpu"
    res = _yolo_gpu().predict(rgb[..., ::-1], classes=[cls], conf=0.25, verbose=False, device=dev, imgsz=640)
    best, best_s = None, -1.0
    for r in res:
        if r.boxes is None:
            continue
        for b, s in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy()):
            area = float((b[2] - b[0]) * (b[3] - b[1]))
            sc = float(s) * np.sqrt(area)
            if sc > best_s:
                best, best_s = [float(v) for v in b], sc
    return best, name


def segment_video(video_path: str | Path, *, points: list[list[float]] | None = None,
                  box_xyxy: list[float] | None = None, progress: Callable[[float], None] | None = None,
                  cancel: threading.Event | None = None, n_frames: int | None = None) -> np.ndarray:
    """Maski (N, H, W) bool dla całego (krótkiego) pliku wideo - SAM 2.1 propagacja w czasie.

    Wołać wewnątrz vram.lease(OWNER, ...)."""
    import torch
    from ultralytics.models.sam import SAM2VideoPredictor

    w = models.path_of("sam2.1_l")
    if not w.exists():
        raise RuntimeError("Brak wag SAM 2.1 - pobierz model „SAM 2.1 Large” w Ustawieniach → Modele")
    over = dict(conf=0.25, task="segment", mode="predict", imgsz=1024, model=str(w), verbose=False,
                device=0 if torch.cuda.is_available() else "cpu", save=False)
    pred = SAM2VideoPredictor(overrides=over)
    kw: dict = {}
    if box_xyxy is not None:
        kw["bboxes"] = [box_xyxy]
    elif points:
        kw["points"] = [[list(map(float, p)) for p in points]]
        kw["labels"] = [[1] * len(points)]
    else:
        raise ValueError("Brak promptu dla SAM 2.1 (punkt lub prostokąt)")
    masks = []
    for i, r in enumerate(pred(source=str(video_path), stream=True, **kw)):
        if cancel is not None and cancel.is_set():
            raise ffmpeg.Cancelled("Przerwano")
        h, w_ = r.orig_shape
        if r.masks is None or r.masks.data is None or len(r.masks.data) == 0:
            masks.append(np.zeros((h, w_), bool))
        else:
            m = r.masks.data.cpu().numpy() > 0.5
            mm = m.any(axis=0)
            if mm.shape != (h, w_):
                import cv2

                mm = cv2.resize(mm.astype(np.uint8), (w_, h), interpolation=cv2.INTER_NEAREST) > 0
            masks.append(mm)
        if progress and n_frames:
            progress(min(1.0, (i + 1) / n_frames))
    del pred
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return np.stack(masks) if masks else np.zeros((0, 1, 1), bool)


def segment_object(media_path: str, t0: float, t1: float, *, points: list[list[float]] | None = None,
                   box: list[float] | None = None, prompt: str | None = None, work_dir: Path | None = None,
                   max_side: int = 1024, progress: Callable[[float], None] | None = None,
                   cancel: threading.Event | None = None) -> dict:
    """Maski obiektu na fragmencie [t0,t1]. Zwraca {"masks": (N,H,W) bool, "fragment": Path, "fps", "label"}."""
    from . import track

    info = ffmpeg.media_info(media_path)
    sw, sh = int(info.get("width") or 1920), int(info.get("height") or 1080)
    work = Path(work_dir or (Path(media_path).parent / "_vfx_tmp"))
    frag = cut_fragment(media_path, t0, t1, work / f"frag_{uuid.uuid4().hex[:8]}.mp4", max_side=max_side,
                        cancel=cancel)
    finfo = ffmpeg.media_info(frag)
    W, H = int(finfo["width"]), int(finfo["height"])
    p = progress or (lambda x: None)
    first = next(iter(ffmpeg.read_frames(frag, fps=float(finfo.get("fps") or 30), width=W, duration=0.2)), None)
    if first is None:
        raise RuntimeError("Nie udało się odczytać fragmentu do maskowania")
    n_frames = int(round(float(finfo.get("duration") or (t1 - t0)) * float(finfo.get("fps") or 30)))
    label = ""
    with vram.lease(OWNER, "vfx", "SAM 2.1 maska"):
        box_xyxy = None
        pts = None
        if box and len(box) == 4:
            x, y, bw, bh = _to_px(list(box), W, H, sw, sh)
            box_xyxy = [x, y, x + bw, y + bh]
            label = "zaznaczony obszar"
        elif points:
            pts = [_to_px(list(pt[:2]), W, H, sw, sh) for pt in points]
            label = "wskazany obiekt"
        elif prompt:
            box_xyxy, label = find_box(first, prompt)
            if box_xyxy is None:
                cls, name, known = prompt_class(prompt)
                what = name or prompt
                if cls is None:
                    raise RuntimeError(f"Nie rozpoznaję automatycznie obiektu „{what}” - zaznacz go prostokątem "
                                       f"lub kliknij punkt w Region Editorze")
                raise RuntimeError(f"Nie znalazłem obiektu „{what}” na początku fragmentu - zaznacz go "
                                   f"prostokątem lub punktem")
        else:
            # brak promptu - twarz/osoba w kadrze
            faces = track.detect_faces(first)
            if faces:
                x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]
                box_xyxy = [x, y, x + fw, y + fh]
                label = "twarz"
            else:
                raise RuntimeError("Wskaż obiekt (punkt, prostokąt lub opis)")
        p(0.1)
        masks = segment_video(frag, points=pts, box_xyxy=box_xyxy, progress=lambda x: p(0.1 + 0.9 * x),
                              cancel=cancel, n_frames=n_frames)
    p(1.0)
    return {"masks": masks, "fragment": frag, "fps": float(finfo.get("fps") or 30), "label": label,
            "size": (W, H), "source_size": (sw, sh)}

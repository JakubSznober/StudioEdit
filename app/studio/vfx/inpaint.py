"""Usuwanie obiektu z fragmentu wideo: maska SAM 2.1 → propagacja tła z sąsiednich klatek (wyrównanie
homografią) → LaMa (big-lama TorchScript) tylko dla pikseli nigdy niewidocznych → wygładzanie w czasie
→ miękkie wklejenie w oryginał. Fallback bez LaMa: cv2.inpaint (Telea).

Przetwarzany jest wyłącznie wskazany fragment [t0, t1] i tylko wycinek kadru wokół obiektu (pełna
rozdzielczość, 8 lub 16 bit zależnie od źródła). Wynik: plik o długości t1-t0 z dźwiękiem źródła.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import uuid
import warnings
from pathlib import Path
from typing import Callable, Iterator

import cv2
import numpy as np

from ..events import log
from ..media import ffmpeg
from ..vram import vram
from . import models
from .stabilize import encode_args

LAMA_OWNER = "torch:lama"
_DEBUG_DIR = Path(os.environ["STUDIOEDIT_VFX_DEBUG"]) if os.environ.get("STUDIOEDIT_VFX_DEBUG") else None


# ---------------------------------------------------------------------------------------------
# wejście/wyjście klatek (pełna rozdzielczość, spójna konwersja YUV↔RGB)
# ---------------------------------------------------------------------------------------------
def _color_args(info: dict) -> tuple[str, str]:
    cs = str(info.get("color_space") or "").lower()
    m = "bt2020" if cs.startswith("bt2020") else "bt601" if cs in ("smpte170m", "bt470bg") else "bt709"
    r = "pc" if str(info.get("color_range", "")).lower() in ("pc", "full", "jpeg") else "tv"
    return m, r


def iter_frames(path: str, t0: float, dur: float, info: dict, hi: bool) -> Iterator[np.ndarray]:
    w, h = int(info["width"]), int(info["height"])
    m, r = _color_args(info)
    pix = "rgb48le" if hi else "rgb24"
    cmd = [ffmpeg.ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{t0:.4f}", "-t", f"{dur:.4f}",
           "-i", str(path), "-vf", f"scale=in_color_matrix={m}:in_range={r},format={pix}", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", pix, "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=ffmpeg.CREATE_NO_WINDOW)
    dt = np.uint16 if hi else np.uint8
    fb = w * h * 3 * (2 if hi else 1)
    assert proc.stdout
    try:
        while True:
            buf = proc.stdout.read(fb)
            if len(buf) < fb:
                break
            yield np.frombuffer(buf, dt).reshape(h, w, 3)
    finally:
        proc.kill()
        proc.wait()


class FrameWriter:
    """Koduje klatki RGB (8/16 bit) do pliku + dźwięk z fragmentu źródła."""

    def __init__(self, out: Path, info: dict, src: str, t0: float, dur: float, hi: bool):
        w, h = int(info["width"]), int(info["height"])
        fps = float(info.get("fps") or 30)
        m, r = _color_args(info)
        pix = "rgb48le" if hi else "rgb24"
        cmd = [ffmpeg.ffmpeg_exe(), "-hide_banner", "-v", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", pix, "-s", f"{w}x{h}", "-r", f"{fps:.5f}", "-i", "-",
               "-ss", f"{t0:.4f}", "-t", f"{dur:.4f}", "-i", str(src),
               "-map", "0:v:0", "-map", "1:a?", "-vf", f"scale=out_color_matrix={m}:out_range={r}",
               *encode_args(info), "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=ffmpeg.CREATE_NO_WINDOW)
        self._err: list[bytes] = []
        self._t = threading.Thread(target=lambda: self._err.append(self.proc.stderr.read()), daemon=True)
        self._t.start()

    def write(self, frame: np.ndarray) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self) -> None:
        assert self.proc.stdin
        self.proc.stdin.close()
        self.proc.wait()
        self._t.join(timeout=5)
        if self.proc.returncode != 0:
            raise ffmpeg.FFmpegError("Kodowanie wyniku VFX nie powiodło się",
                                     b"".join(self._err).decode("utf-8", "ignore")[-1500:])

    def abort(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


# ---------------------------------------------------------------------------------------------
# LaMa
# ---------------------------------------------------------------------------------------------
def _load_lama():
    import torch

    p = models.path_of("big-lama")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    m = torch.jit.load(str(p), map_location=dev)
    m.eval()
    return m, dev


def lama_available() -> bool:
    return models.installed("big-lama")


def lama_inpaint(img: np.ndarray, mask: np.ndarray, *, max_side: int = 1024) -> np.ndarray:
    """img uint8 RGB (H,W,3), mask bool → uint8 RGB. Wołać wewnątrz vram.lease(LAMA_OWNER)."""
    import torch

    model, dev = vram.cached(LAMA_OWNER, "big-lama", _load_lama)
    H, W = img.shape[:2]
    k = min(1.0, max_side / max(H, W))
    im = cv2.resize(img, (int(W * k), int(H * k)), interpolation=cv2.INTER_AREA) if k < 1 else img
    mk = cv2.resize(mask.astype(np.uint8), (im.shape[1], im.shape[0]), interpolation=cv2.INTER_NEAREST) \
        if k < 1 else mask.astype(np.uint8)
    h, w = im.shape[:2]
    ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
    im_p = np.pad(im, ((0, ph), (0, pw), (0, 0)), mode="reflect")
    mk_p = np.pad(mk, ((0, ph), (0, pw)), mode="constant")
    t_im = torch.from_numpy(im_p).permute(2, 0, 1)[None].float().div(255).to(dev)
    t_mk = torch.from_numpy((mk_p > 0).astype(np.float32))[None, None].to(dev)
    with torch.inference_mode():
        out = model(t_im, t_mk)
    res = (out[0].permute(1, 2, 0).clamp(0, 1).mul(255).byte().cpu().numpy())[:h, :w]
    if k < 1:
        res = cv2.resize(res, (W, H), interpolation=cv2.INTER_CUBIC)
    return res


# ---------------------------------------------------------------------------------------------
# propagacja tła
# ---------------------------------------------------------------------------------------------
def _to8(a: np.ndarray) -> np.ndarray:
    return a if a.dtype == np.uint8 else (a >> 8).astype(np.uint8)


def _homography(src_g: np.ndarray, dst_g: np.ndarray, src_mask: np.ndarray, dst_mask: np.ndarray):
    """Homografia src→dst z punktów tła (poza maskami)."""
    feat_mask = (~src_mask).astype(np.uint8) * 255
    pts = cv2.goodFeaturesToTrack(src_g, maxCorners=400, qualityLevel=0.005, minDistance=6, mask=feat_mask)
    if pts is None or len(pts) < 12:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(src_g, dst_g, pts, None, winSize=(21, 21), maxLevel=3)
    ok = st.ravel() == 1
    if ok.sum() < 12:
        return None
    a, b = pts[ok].reshape(-1, 2), nxt[ok].reshape(-1, 2)
    inside = np.array([0 <= y < dst_mask.shape[0] and 0 <= x < dst_mask.shape[1] and not dst_mask[int(y), int(x)]
                       for x, y in b])
    if inside.sum() < 12:
        return None
    Hm, inl = cv2.findHomography(a[inside], b[inside], cv2.RANSAC, 2.0)
    if Hm is None or inl is None or inl.sum() < 10 or inl.mean() < 0.5:
        return None
    # rozsądność: narożniki przesunięte o < 15% przekątnej (sąsiednie klatki, brak „szalonych” homografii)
    hh, ww = src_g.shape[:2]
    corners = np.float32([[0, 0], [ww, 0], [ww, hh], [0, hh]]).reshape(-1, 1, 2)
    moved = cv2.perspectiveTransform(corners, Hm).reshape(-1, 2) - corners.reshape(-1, 2)
    if np.abs(moved).max() > 0.15 * np.hypot(ww, hh):
        return None
    return Hm


def _crop_box(masks: np.ndarray, W: int, H: int) -> tuple[int, int, int, int]:
    union = masks.any(axis=0)
    ys, xs = np.nonzero(union)
    if len(xs) == 0:
        return 0, 0, W, H
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    mx = max(192, int((x1 - x0) * 1.5))  # szeroki kontekst - LaMa potrzebuje otoczenia
    my = max(192, int((y1 - y0) * 0.6))
    x0, y0 = max(0, x0 - mx), max(0, y0 - my)
    x1, y1 = min(W, x1 + mx), min(H, y1 + my)
    return int(x0) & ~1, int(y0) & ~1, int(x1), int(y1)


def remove_object(media_path: str, t0: float, t1: float, out_path: Path, *, points: list[list[float]] | None = None,
                  box: list[float] | None = None, prompt: str | None = None,
                  progress: Callable[[float], None] | None = None, cancel: threading.Event | None = None) -> Path:
    """Usuwa obiekt z fragmentu [t0, t1] (SAM 2.1 + propagacja + LaMa). Zwraca ścieżkę wyniku."""
    from .segment import segment_object

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    p = progress or (lambda x: None)
    info = ffmpeg.media_info(media_path)
    W, H = int(info["width"]), int(info["height"])
    hi = int(info.get("bit_depth") or 8) > 8
    maxv = 65535.0 if hi else 255.0
    dur = max(0.05, float(t1) - float(t0))
    work = out_path.parent / f"_rm_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        seg = segment_object(media_path, t0, t1, points=points, box=box, prompt=prompt, work_dir=work,
                             progress=lambda x: p(0.35 * x), cancel=cancel)
        small = seg["masks"]
        if small.size == 0 or not small.any():
            raise RuntimeError("SAM 2.1 nie znalazł obiektu w zaznaczonym fragmencie")
        n = len(small)
        # maski w pełnej rozdzielczości + dylatacja (krawędzie, rozmycie ruchu) + suma z sąsiednimi klatkami
        dil = max(5, int(min(W, H) * 0.012)) | 1
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dil, dil))
        full = np.zeros((n, H, W), bool)
        for i in range(n):
            mm = cv2.resize(small[i].astype(np.uint8), (W, H), interpolation=cv2.INTER_LINEAR)
            full[i] = cv2.dilate(mm, kern) > 0
        temporal = full.copy()
        temporal[1:] |= full[:-1]
        temporal[:-1] |= full[1:]
        x0, y0, x1, y1 = _crop_box(temporal, W, H)
        cw, ch = x1 - x0, y1 - y0
        # przebieg 1: wycinki klatek
        crops: list[np.ndarray] = []
        for i, fr in enumerate(iter_frames(media_path, t0, dur, info, hi)):
            if cancel is not None and cancel.is_set():
                raise ffmpeg.Cancelled("Przerwano")
            crops.append(fr[y0:y1, x0:x1].copy())
        if not crops:
            raise RuntimeError("Nie udało się odczytać klatek fragmentu")
        nf = len(crops)
        idx_mask = [min(n - 1, int(round(i * (n - 1) / max(1, nf - 1)))) for i in range(nf)]
        cm = [temporal[j][y0:y1, x0:x1] for j in idx_mask]
        grays = [cv2.cvtColor(_to8(c), cv2.COLOR_RGB2GRAY) for c in crops]
        p(0.4)
        fills: list[np.ndarray] = []
        holes: list[np.ndarray] = []
        ring_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
        # maska „bezpieczna” źródeł: suma masek j-2..j+2 + dodatkowa dylatacja
        wide_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
        cmw = []
        for j in range(nf):
            u = np.zeros_like(cm[j])
            for jj in range(max(0, j - 2), min(nf, j + 3)):
                u |= cm[jj]
            cmw.append(cv2.dilate(u.astype(np.uint8), wide_k) > 0)
        offsets = sorted({o for k in (1, 2, 3, 5, 8, 12, 18, 26) for o in (k, -k)}, key=abs)
        prop_px = 0
        total_px = 0
        for i in range(nf):
            if cancel is not None and cancel.is_set():
                raise ffmpeg.Cancelled("Przerwano")
            fill = crops[i].copy()
            remain = cm[i].copy()
            total_px += int(remain.sum())
            ring_i = (cv2.dilate(cm[i].astype(np.uint8), ring_k) > 0) & ~cm[i]
            hy, hx = np.nonzero(remain)
            vals: list[np.ndarray] = []
            for o in offsets:
                j = i + o
                if j < 0 or j >= nf or len(hy) == 0:
                    continue
                Hm = _homography(grays[j], grays[i], cmw[j], cm[i])
                if Hm is None:
                    continue
                # kontrola fotometryczna na pierścieniu wokół maski: źle wyrównana klatka (albo inny ruchomy
                # obiekt w tle) nie może być źródłem łaty
                valid = cv2.warpPerspective((~cmw[j]).astype(np.uint8), Hm, (cw, ch), flags=cv2.INTER_NEAREST,
                                            borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0
                ring = ring_i & valid
                if ring.sum() < 150:
                    continue
                wg = cv2.warpPerspective(grays[j], Hm, (cw, ch), flags=cv2.INTER_LINEAR)
                if float(np.abs(wg[ring].astype(np.float32) - grays[i][ring].astype(np.float32)).mean()) > 9.0:
                    continue
                warped = cv2.warpPerspective(crops[j], Hm, (cw, ch), flags=cv2.INTER_LINEAR,
                                             borderMode=cv2.BORDER_CONSTANT)
                v = warped[hy, hx].astype(np.float32)
                v[~valid[hy, hx]] = np.nan
                vals.append(v)
            if len(vals) >= 2:
                # konsensus źródeł: mediana z co najmniej 2 zgodnych klatek (pojedyncza błędna maska SAM
                # w sąsiedniej klatce nie przeniesie fragmentu obiektu)
                st = np.stack(vals)
                cnt = np.sum(~np.isnan(st[..., 0]), axis=0)
                with np.errstate(all="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    med = np.nanmedian(st, axis=0)
                    mad = np.nanmedian(np.abs(st - med[None]), axis=0).max(axis=1)
                ok = (cnt >= 2) & (mad < 0.05 * maxv) & ~np.isnan(med[:, 0])
                if ok.any():
                    fill[hy[ok], hx[ok]] = med[ok].astype(fill.dtype)
                    remain[hy[ok], hx[ok]] = False
            # łata z propagacji tylko, gdy pokrywa prawie całą maskę - mieszanie częściowej propagacji z LaMa
            # daje „ducha” obiektu na granicy (LaMa przedłuża krawędź łaty do środka)
            area = max(1, int(cm[i].sum()))
            if 1.0 - remain.sum() / area < 0.85:
                fill = crops[i].copy()
                remain = cm[i].copy()
            prop_px += int(cm[i].sum() - remain.sum())
            fills.append(fill)
            if _DEBUG_DIR and i == nf // 2:
                _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                vis = np.hstack([_to8(crops[i]), _to8(fill), np.repeat(cm[i][..., None].astype(np.uint8) * 255, 3, 2),
                                 np.repeat(remain[..., None].astype(np.uint8) * 255, 3, 2)])
                cv2.imwrite(str(_DEBUG_DIR / "propagation.jpg"), cv2.resize(vis[..., ::-1], None, fx=0.4, fy=0.4))
            holes.append(remain)
            p(0.4 + 0.3 * (i + 1) / nf)
        # LaMa dla pikseli nigdy niewidocznych
        need = [i for i in range(nf) if holes[i].any()]
        method = "propagacja tła"
        if need:
            use_lama = lama_available()
            if use_lama:
                try:
                    with vram.lease(LAMA_OWNER, "vfx", "LaMa inpainting"):
                        prev_out: np.ndarray | None = None
                        for c, i in enumerate(need):
                            if cancel is not None and cancel.is_set():
                                raise ffmpeg.Cancelled("Przerwano")
                            res = lama_inpaint(_to8(fills[i]), holes[i])
                            res_t = (res.astype(np.uint16) * 257) if hi else res
                            # wygładzanie w czasie: mieszanie z poprzednim wynikiem w otworze
                            if prev_out is not None and i - 1 in need:
                                res_t = (res_t.astype(np.float32) * 0.6 + prev_out.astype(np.float32) * 0.4) \
                                    .astype(res_t.dtype)
                            fills[i][holes[i]] = res_t[holes[i]]
                            prev_out = fills[i].copy()
                            p(0.7 + 0.2 * (c + 1) / len(need))
                    method = "propagacja tła + LaMa"
                except ffmpeg.Cancelled:
                    raise
                except Exception as e:
                    log("vfx", f"LaMa niedostępna ({e}) - używam cv2.inpaint (Telea)", path="Usuwanie obiektów",
                        level="warn")
                    use_lama = False
            if not use_lama:
                for i in need:
                    f8 = _to8(fills[i])
                    res = cv2.inpaint(f8, holes[i].astype(np.uint8) * 255, 5, cv2.INPAINT_TELEA)
                    res_t = (res.astype(np.uint16) * 257) if hi else res
                    fills[i][holes[i]] = res_t[holes[i]]
                method = "propagacja tła + Telea"
        # przebieg 2: wklejenie z miękką krawędzią i zapis
        writer = FrameWriter(out_path, info, media_path, t0, dur, hi)
        try:
            for i, fr in enumerate(iter_frames(media_path, t0, dur, info, hi)):
                if cancel is not None and cancel.is_set():
                    raise ffmpeg.Cancelled("Przerwano")
                k = min(i, nf - 1)
                a = cv2.GaussianBlur(cm[k].astype(np.float32), (0, 0), sigmaX=max(1.5, dil / 4))[..., None]
                a = np.clip(a * 1.6, 0, 1)
                out = fr.copy()
                reg = out[y0:y1, x0:x1].astype(np.float32)
                out[y0:y1, x0:x1] = np.clip(reg * (1 - a) + fills[k].astype(np.float32) * a, 0, maxv) \
                    .astype(fr.dtype)
                writer.write(out)
                p(0.9 + 0.1 * (i + 1) / nf)
            writer.close()
        except BaseException:
            writer.abort()
            raise
        share = prop_px / max(1, total_px) * 100
        log("vfx", f"Usunięto „{seg['label'] or prompt or 'obiekt'}” z {nf} klatek ({method}; "
                   f"{share:.0f}% maski z tła sąsiednich klatek)",
            path="Usuwanie obiektów")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    p(1.0)
    return out_path


# ---------------------------------------------------------------------------------------------
# rozmycie / anonimizacja obszaru
# ---------------------------------------------------------------------------------------------
def _norm_box(box: list[float], W: int, H: int) -> tuple[int, int, int, int]:
    vals = [float(v) for v in box[:4]]
    if all(0.0 <= v <= 1.0 for v in vals):
        x, y, w, h = vals[0] * W, vals[1] * H, vals[2] * W, vals[3] * H
    else:
        x, y, w, h = vals
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x + w)), min(H, int(y + h))
    return x0, y0, x1, y1


def _anonymize(region: np.ndarray, strength: float) -> np.ndarray:
    """Mozaika + rozmycie (nieodwracalne, czytelne jako „zamazane”)."""
    h, w = region.shape[:2]
    if h < 2 or w < 2:
        return region
    blocks = max(4, int(18 - 12 * strength))
    small = cv2.resize(region, (max(1, w // blocks), max(1, h // blocks)), interpolation=cv2.INTER_AREA)
    mosaic = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    k = max(3, int(min(w, h) * 0.15) | 1)
    return cv2.GaussianBlur(mosaic, (k, k), 0)


def blur_region(media_path: str, t0: float, t1: float, out_path: Path, *, box: list[float] | None = None,
                prompt: str | None = None, strength: float = 0.8, progress: Callable[[float], None] | None = None,
                cancel: threading.Event | None = None) -> tuple[Path, str]:
    """Rozmycie obszaru na fragmencie. Prostokąt [x,y,w,h] (0..1) → stały obszar; bez prostokąta: twarze
    (YuNet) lub obiekty z opisu (YOLO, np. „samochód”, „osoba”) śledzone w każdej klatce.
    Zwraca (ścieżka, opis_trybu)."""
    from .segment import prompt_class
    from .track import detect_faces, detect_objects

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    p = progress or (lambda x: None)
    info = ffmpeg.media_info(media_path)
    W, H = int(info["width"]), int(info["height"])
    hi = int(info.get("bit_depth") or 8) > 8
    dur = max(0.05, float(t1) - float(t0))
    fps = float(info.get("fps") or 30)
    n_exp = max(1, int(dur * fps))
    static = _norm_box(box, W, H) if box and len(box) >= 4 else None
    cls, name, _known = prompt_class(prompt)
    t = (prompt or "").lower()
    faces_mode = static is None and (cls is None or any(k in t for k in ("twarz", "anonim", "face")))
    mode = "stały obszar" if static else ("twarze" if faces_mode else f"obiekty: {name}")
    writer = FrameWriter(out_path, info, media_path, t0, dur, hi)
    held: list[tuple[list[int], int]] = []  # (box, ważność w klatkach) - bez migotania przy chwilowej utracie
    det_w = 640
    try:
        for i, fr in enumerate(iter_frames(media_path, t0, dur, info, hi)):
            if cancel is not None and cancel.is_set():
                raise ffmpeg.Cancelled("Przerwano")
            out = fr.copy()
            boxes: list[list[int]] = []
            if static:
                boxes = [list(static)]
            else:
                k = det_w / W
                small = cv2.resize(_to8(fr), (det_w, int(H * k)), interpolation=cv2.INTER_AREA)
                if faces_mode:
                    dets = [d for d in detect_faces(small) if d[4] >= 0.5]
                    pad_x, pad_y = 0.25, 0.35
                elif i % 2 == 0 or not held:
                    dets = detect_objects(small, [cls], conf=0.3)
                    pad_x, pad_y = 0.05, 0.05
                else:
                    dets = []
                    pad_x = pad_y = 0.0
                fresh = []
                for d in dets:
                    x, y, w, h = (v / k for v in d[:4])
                    fresh.append([max(0, int(x - w * pad_x)), max(0, int(y - h * pad_y)),
                                  min(W, int(x + w * (1 + pad_x))), min(H, int(y + h * (1 + pad_y)))])
                if fresh:
                    held = [(b, int(fps * 0.25)) for b in fresh]
                else:
                    held = [(b, ttl - 1) for b, ttl in held if ttl > 1]
                boxes = [b for b, _ in held]
            for x0, y0, x1, y1 in boxes:
                if x1 - x0 > 2 and y1 - y0 > 2:
                    out[y0:y1, x0:x1] = _anonymize(out[y0:y1, x0:x1], strength)
            writer.write(out)
            p(min(1.0, (i + 1) / n_exp))
        writer.close()
    except BaseException:
        writer.abort()
        raise
    return out_path, mode

"""Stabilizacja fragmentu (vid.stab 2 przebiegi) i ocena drgań kamery (optical flow, OpenCV)."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ..media import ffmpeg


def _codec_args(info: dict, fps: float | None) -> list[str]:
    """H.264 NVENC CQ16 dla 8-bit, HEVC 10-bit dla materiału 10/12-bit (LOG/HDR nie traci precyzji)."""
    if int(info.get("bit_depth") or 8) > 8:
        args = ffmpeg.video_codec_args("hevc_nvenc", cq=16, fps=fps, ten_bit=True)
    else:
        args = ffmpeg.video_codec_args("h264_nvenc", cq=16, fps=fps)
    for opt, key in (("-color_primaries", "color_primaries"), ("-color_trc", "color_transfer"),
                     ("-colorspace", "color_space"), ("-color_range", "color_range")):
        v = info.get(key)
        if v:
            args += [opt, str(v)]
    return args


def encode_args(info: dict) -> list[str]:
    return _codec_args(info, float(info.get("fps") or 30))


def stabilize(media_path: str, t0: float, t1: float, out_path: Path, *, strength: float = 0.5,
              progress: Callable[[float], None] | None = None, cancel: threading.Event | None = None) -> Path:
    """Stabilizuje fragment [t0, t1] źródła (vidstabdetect + vidstabtransform, optzoom=1 - minimalny stały
    crop bez czarnych krawędzi). Wynik: plik o długości t1-t0 z dźwiękiem źródła."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    info = ffmpeg.media_info(media_path)
    fps = float(info.get("fps") or 30)
    dur = max(0.1, float(t1) - float(t0))
    s = float(np.clip(strength, 0.0, 1.0))
    work = out_path.parent / f"_vs_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    trf = work / "transforms.trf"
    p = progress or (lambda x: None)
    shakiness = int(round(4 + 6 * s))
    smoothing = int(round((8 + 40 * s) * fps / 30.0))
    seek = ["-ss", f"{max(0.0, t0):.4f}", "-t", f"{dur:.4f}", "-i", str(media_path)]
    try:
        ffmpeg.run([*seek, "-vf",
                    f"vidstabdetect=shakiness={shakiness}:accuracy=15:stepsize=6:mincontrast=0.2:"
                    f"result='{ffmpeg.escape_filter_path(trf)}'", "-an", "-f", "null", "-"],
                   duration=dur, progress=lambda x: p(0.45 * x), cancel=cancel)
        vf = (f"vidstabtransform=input='{ffmpeg.escape_filter_path(trf)}':smoothing={smoothing}:optzoom=1:"
              f"zoomspeed=0.25:interpol=bicubic:crop=black:relative=1,unsharp=5:5:0.5:3:3:0.2")
        ffmpeg.run([*seek, "-map", "0:v:0", "-map", "0:a?", "-vf", vf, *_codec_args(info, fps),
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_path)],
                   duration=dur, progress=lambda x: p(0.45 + 0.55 * x), cancel=cancel)
    finally:
        for f in work.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass
    p(1.0)
    return out_path


def camera_trajectory(media_path: str, t0: float, t1: float, *, fps: float = 12.0, width: int = 320) -> np.ndarray:
    """Globalny ruch kamery między kolejnymi klatkami: tablica (N-1, 3) [dx, dy, da] w pikselach/radianach."""
    dur = max(0.2, float(t1) - float(t0))
    prev = None
    moves = []
    for fr in ffmpeg.read_frames(media_path, fps=fps, width=width, start=t0, duration=dur, gray=True):
        g = np.ascontiguousarray(fr)
        if prev is not None:
            pts = cv2.goodFeaturesToTrack(prev, maxCorners=200, qualityLevel=0.01, minDistance=8, blockSize=7)
            m = None
            if pts is not None and len(pts) >= 8:
                nxt, st, _err = cv2.calcOpticalFlowPyrLK(prev, g, pts, None, winSize=(21, 21), maxLevel=3)
                ok = st.ravel() == 1
                if ok.sum() >= 8:
                    m, _inl = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC,
                                                          ransacReprojThreshold=2.0)
            if m is None:
                moves.append([0.0, 0.0, 0.0])
            else:
                moves.append([float(m[0, 2]), float(m[1, 2]), float(np.arctan2(m[1, 0], m[0, 0]))])
        prev = g
    return np.asarray(moves, dtype=np.float64).reshape(-1, 3)


def estimate_shake(media_path: str, t0: float, t1: float) -> float:
    """Drgania kamery 0..1 (0 = statyw/gimbal, ~0.5 = wyraźnie „z ręki”, 1 = bardzo trzęsące).

    Trajektoria kamery (optical flow LK + afiniczna RANSAC) minus jej wygładzona wersja (zamierzony ruch:
    panorama, jazda) = składowa wysokoczęstotliwościowa; RMS w % szerokości kadru.
    """
    width = 320
    fps = 12.0
    mv = camera_trajectory(media_path, t0, t1, fps=fps, width=width)
    if len(mv) < 4:
        return 0.0
    traj = np.cumsum(mv, axis=0)
    k = max(3, int(fps * 0.5) | 1)  # okno ~0.5 s
    pad = k // 2
    kern = np.ones(k) / k
    smooth = np.stack([np.convolve(np.pad(traj[:, i], pad, mode="edge"), kern, mode="valid")
                       for i in range(3)], axis=1)
    hf = traj - smooth
    rms_px = float(np.sqrt(np.mean(hf[:, 0] ** 2 + hf[:, 1] ** 2)))
    rms_rot = float(np.sqrt(np.mean(hf[:, 2] ** 2)))
    rel = rms_px / width + rms_rot * 0.5
    return float(np.clip(1.0 - np.exp(-rel / 0.008), 0.0, 1.0))

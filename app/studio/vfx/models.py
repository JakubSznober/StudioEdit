"""Katalog modeli obszaru VFX: SAM 2.1, YOLO11, LaMa, YuNet, Wan 2.2 TI2V 5B (ComfyUI).

Wagi plikowe trafiają do `AI-Models\\weights\\<obszar>\\`, modele ComfyUI do `ComfyUI\\ComfyUI\\models\\...`.
Pobieranie strumieniowe z wznawianiem (HTTP Range), zapis do *.part i atomowa zamiana.
"""
from __future__ import annotations

import os
import ssl
import time
from pathlib import Path
from typing import Callable

import httpx

from .. import paths

COMFY_MODELS = paths.COMFY_ROOT / "ComfyUI" / "models"
HF_WAN_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"


def _hf_url(repo: str, file: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{file}"


CATALOG: list[dict] = [
    {"key": "sam2.1_l", "name": "SAM 2.1 Large (Ultralytics)", "kind": "segmentation", "provider": "ultralytics",
     "size_gb": 0.45, "description": "Segmentacja i śledzenie maski obiektu w wideo (usuwanie obiektów, maski).",
     "used_by": ["vfx"],
     "files": [{"url": "https://github.com/ultralytics/assets/releases/download/v8.3.0/sam2.1_l.pt",
                "dest": paths.WEIGHTS / "sam" / "sam2.1_l.pt", "min_bytes": 150_000_000}]},
    {"key": "yolo11m", "name": "YOLO11m (COCO)", "kind": "detection", "provider": "ultralytics",
     "size_gb": 0.04, "description": "Detekcja osób i obiektów (prompt tekstowy → box, śledzenie postaci).",
     "used_by": ["vfx", "archivist"],
     "files": [{"url": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11m.pt",
                "dest": paths.WEIGHTS / "yolo" / "yolo11m.pt", "min_bytes": 30_000_000}]},
    {"key": "big-lama", "name": "LaMa (big-lama, TorchScript)", "kind": "inpainting", "provider": "Sanster/models",
     "size_gb": 0.20, "description": "Inpainting tła po usunięciu obiektu.", "used_by": ["vfx"],
     "files": [{"url": "https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt",
                "dest": paths.WEIGHTS / "lama" / "big-lama.pt", "min_bytes": 150_000_000}]},
    {"key": "yunet", "name": "YuNet (detekcja twarzy, OpenCV Zoo)", "kind": "face", "provider": "opencv_zoo",
     "size_gb": 0.0003, "description": "Szybka detekcja twarzy do Smart Reframingu 9:16.", "used_by": ["vfx"],
     "files": [{"url": "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
                       "face_detection_yunet/face_detection_yunet_2023mar.onnx",
                "dest": paths.WEIGHTS / "face" / "face_detection_yunet_2023mar.onnx", "min_bytes": 200_000}]},
    {"key": "wan22_ti2v_5b", "name": "Wan 2.2 TI2V 5B (ComfyUI)", "kind": "video_generation",
     "provider": f"huggingface:{HF_WAN_REPO}", "size_gb": 18.1,
     "description": "Generator B-Roll tekst→wideo 720p/480p (natywne węzły ComfyUI).", "used_by": ["vfx"],
     "files": [
         {"url": _hf_url(HF_WAN_REPO, "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"),
          "dest": COMFY_MODELS / "diffusion_models" / "wan2.2_ti2v_5B_fp16.safetensors", "min_bytes": 9_000_000_000},
         {"url": _hf_url(HF_WAN_REPO, "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
          "dest": COMFY_MODELS / "text_encoders" / "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
          "min_bytes": 6_000_000_000},
         {"url": _hf_url(HF_WAN_REPO, "split_files/vae/wan2.2_vae.safetensors"),
          "dest": COMFY_MODELS / "vae" / "wan2.2_vae.safetensors", "min_bytes": 1_300_000_000},
     ]},
]

_BY_KEY = {m["key"]: m for m in CATALOG}


def entry(key: str) -> dict:
    if key not in _BY_KEY:
        raise KeyError(f"Nieznany model VFX: {key}")
    return _BY_KEY[key]


def path_of(key: str, index: int = 0) -> Path:
    return Path(entry(key)["files"][index]["dest"])


def installed(key: str) -> bool:
    try:
        files = entry(key)["files"]
    except KeyError:
        return False
    for f in files:
        p = Path(f["dest"])
        if not p.exists() or p.stat().st_size < f.get("min_bytes", 1):
            return False
    return True


def _download_file(url: str, dest: Path, progress: Callable[[float], None] | None = None,
                   retries: int = 6) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    headers = {"User-Agent": "StudioEdit-model-downloader"}
    token = os.environ.get("HF_TOKEN")
    if token and "huggingface.co" in url:
        headers["Authorization"] = f"Bearer {token}"
    last_err: Exception | None = None
    for attempt in range(retries):
        have = part.stat().st_size if part.exists() else 0
        h = dict(headers)
        if have:
            h["Range"] = f"bytes={have}-"
        try:
            # magazyn certyfikatów systemu (Windows) - działa też przy inspekcji TLS przez antywirus
            with httpx.stream("GET", url, headers=h, follow_redirects=True, verify=ssl.create_default_context(),
                              timeout=httpx.Timeout(60.0, read=120.0)) as r:
                if r.status_code == 416:  # już całość
                    break
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code} dla {url}")
                if r.status_code == 200 and have:
                    have = 0  # serwer zignorował Range - od nowa
                total = int(r.headers.get("content-length") or 0) + have
                mode = "ab" if have else "wb"
                done = have
                last = 0.0
                with open(part, mode) as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if progress and total and time.time() - last > 0.5:
                            last = time.time()
                            progress(done / total)
            break
        except (httpx.HTTPError, OSError, RuntimeError) as e:
            last_err = e
            time.sleep(min(30, 2 ** attempt))
    else:
        raise RuntimeError(f"Nie udało się pobrać {dest.name}: {last_err}")
    os.replace(part, dest)
    if progress:
        progress(1.0)


def download(key: str, progress: Callable[[float], None] | None = None) -> None:
    """Pobiera wszystkie pliki modelu (pomija już kompletne). progress(0..1) dla całego modelu."""
    m = entry(key)
    files = m["files"]
    sizes = [max(1, f.get("min_bytes", 1)) for f in files]
    total = sum(sizes)
    acc = 0
    for f, sz in zip(files, sizes):
        dest = Path(f["dest"])
        if not (dest.exists() and dest.stat().st_size >= f.get("min_bytes", 1)):
            base = acc
            _download_file(f["url"], dest,
                           (lambda x, b=base, s=sz: progress((b + x * s) / total)) if progress else None)
            if dest.stat().st_size < f.get("min_bytes", 1):
                raise RuntimeError(f"Pobrany plik {dest.name} jest za mały ({dest.stat().st_size} B) - "
                                   f"prawdopodobnie błąd serwera")
        acc += sz
        if progress:
            progress(acc / total)


if __name__ == "__main__":  # python -m studio.vfx.models <klucz> [...]
    import sys

    paths.setup_env()
    keys = sys.argv[1:] or [m["key"] for m in CATALOG if m["key"] != "wan22_ti2v_5b"]
    for k in keys:
        t0 = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] Pobieram {k}…", flush=True)
        state = {"last": -1}

        def _p(x: float, k=k):
            pct = int(x * 100)
            if pct != state["last"] and pct % 2 == 0:
                state["last"] = pct
                print(f"[{time.strftime('%H:%M:%S')}] {k}: {pct}%", flush=True)

        download(k, _p)
        print(f"[{time.strftime('%H:%M:%S')}] {k}: gotowe ({time.time() - t0:.0f} s), installed={installed(k)}",
              flush=True)

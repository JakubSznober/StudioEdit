"""Katalog modeli obszaru Kolor.

Grading (CST, LUT, looki, ochrona skóry, klasyczny relighting) działa bez sieci neuronowych.
Opcjonalny relighting AI: IC-Light (SD1.5) w ComfyUI - wymaga węzłów ComfyUI-IC-Light (kijai) oraz dowolnego
checkpointu SD1.5 w ComfyUI\\ComfyUI\\models\\checkpoints. Bez nich kolorysta używa relightingu klasycznego.
"""
from __future__ import annotations

from typing import Callable

from ..vfx.models import COMFY_MODELS, _download_file, _hf_url

CATALOG: list[dict] = [
    {"key": "iclight_sd15_fc", "name": "IC-Light SD1.5 (foreground conditioned)", "kind": "relight",
     "provider": "huggingface:lllyasviel/ic-light", "size_gb": 1.72,
     "description": "Relighting AI klatek (opcjonalny; wymaga węzłów ComfyUI-IC-Light i checkpointu SD1.5).",
     "used_by": ["colorist"],
     "files": [{"url": _hf_url("lllyasviel/ic-light", "iclight_sd15_fc.safetensors"),
                "dest": COMFY_MODELS / "unet" / "iclight_sd15_fc.safetensors", "min_bytes": 1_600_000_000}]},
]
_BY_KEY = {m["key"]: m for m in CATALOG}


def installed(key: str) -> bool:
    m = _BY_KEY.get(key)
    if not m:
        return False
    return all(f["dest"].exists() and f["dest"].stat().st_size >= f.get("min_bytes", 1) for f in m["files"])


def download(key: str, progress: Callable[[float], None] | None = None) -> None:
    if key not in _BY_KEY:
        raise KeyError(f"Nieznany model koloru: {key}")
    files = _BY_KEY[key]["files"]
    for i, f in enumerate(files):
        if f["dest"].exists() and f["dest"].stat().st_size >= f.get("min_bytes", 1):
            continue
        _download_file(f["url"], f["dest"],
                       (lambda x, i=i: progress((i + x) / len(files))) if progress else None)
    if progress:
        progress(1.0)

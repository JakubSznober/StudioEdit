"""Dyscyplina VRAM: 16 GB nie pomieści naraz wszystkich modeli, więc każdy etap zwalnia po sobie."""
from __future__ import annotations

import gc
import logging

log = logging.getLogger(__name__)


def free_torch() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except Exception as e:  # noqa: BLE001
        log.debug("free_torch: %s", e)


def vram_used_mb() -> float:
    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            return (total - free) / 2**20
    except Exception:  # noqa: BLE001
        pass
    return -1.0


def ollama_unload(model: str, host: str | None = None) -> None:
    """Wyładowuje model z Ollamy (keep_alive=0), żeby zwolnić VRAM dla PyTorch/ComfyUI."""
    try:
        import ollama

        client = ollama.Client(host=host) if host else ollama
        client.generate(model=model, prompt="", keep_alive=0)
    except Exception as e:  # noqa: BLE001
        log.debug("ollama_unload(%s): %s", model, e)

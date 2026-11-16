"""Klient API ComfyUI: wysyłanie workflow (format API), śledzenie postępu, pobieranie wyników."""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import httpx

from . import paths
from .events import log
from .services import services
from .vram import vram


class ComfyError(RuntimeError):
    pass


def base_url() -> str:
    return services["comfyui"].url


def object_info() -> dict:
    services.ensure("comfyui", "vfx")
    return httpx.get(base_url() + "/object_info", timeout=60).json()


def has_nodes(*names: str) -> bool:
    try:
        info = object_info()
    except Exception:
        return False
    return all(n in info for n in names)


def models_in(folder: str) -> list[str]:
    try:
        return httpx.get(base_url() + f"/models/{folder}", timeout=10).json()
    except Exception:
        return []


def upload_image(path: str | Path, subfolder: str = "studioedit") -> str:
    """Kopiuje plik do katalogu input ComfyUI i zwraca nazwę do użycia w LoadImage/LoadVideo."""
    dst_dir = paths.CACHE / "comfy_in" / subfolder
    dst_dir.mkdir(parents=True, exist_ok=True)
    p = Path(path)
    name = f"{uuid.uuid4().hex[:8]}_{p.name}"
    shutil.copy2(p, dst_dir / name)
    return f"{subfolder}/{name}"


def run_workflow(workflow: dict, *, agent: str = "vfx", label: str = "ComfyUI",
                 progress: Callable[[float, str], None] | None = None, timeout: float = 3600,
                 cancel: threading.Event | None = None, project_id: str | None = None) -> list[Path]:
    """Wykonuje workflow (słownik w formacie API) i zwraca listę plików wynikowych."""
    services.ensure("comfyui", agent)
    services["comfyui"].last_used = time.time()
    client_id = uuid.uuid4().hex
    with vram.lease("comfy", agent, label):
        r = httpx.post(base_url() + "/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=60)
        if r.status_code != 200:
            raise ComfyError(f"ComfyUI odrzucił workflow: {r.text[:800]}")
        pid = r.json()["prompt_id"]
        log(agent, f"Wysłano workflow do ComfyUI ({label})", path="ComfyUI", project_id=project_id)
        t0 = time.time()
        last_pct = -1.0
        while True:
            if cancel is not None and cancel.is_set():
                httpx.post(base_url() + "/interrupt", timeout=10)
                raise ComfyError("Przerwano")
            if time.time() - t0 > timeout:
                httpx.post(base_url() + "/interrupt", timeout=10)
                raise ComfyError("Przekroczono limit czasu ComfyUI")
            h = httpx.get(base_url() + f"/history/{pid}", timeout=30).json()
            if pid in h:
                entry = h[pid]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = json.dumps(status.get("messages", []), ensure_ascii=False)[:1200]
                    raise ComfyError(f"Błąd w workflow ComfyUI: {msgs}")
                if status.get("completed", True):
                    break
            # przybliżony postęp z kolejki
            if progress:
                try:
                    q = httpx.get(base_url() + "/queue", timeout=10).json()
                    running = q.get("queue_running", [])
                    pct = min(0.95, (time.time() - t0) / max(60.0, timeout / 6))
                    if running and pct - last_pct > 0.02:
                        progress(pct, "generowanie")
                        last_pct = pct
                except Exception:
                    pass
            time.sleep(1.5)
        services["comfyui"].last_used = time.time()
    outputs: list[Path] = []
    for node_out in entry.get("outputs", {}).values():
        for key in ("images", "gifs", "videos", "animated", "audio"):
            for f in node_out.get(key, []) or []:
                if not isinstance(f, dict) or "filename" not in f:
                    continue
                local = paths.CACHE / "comfy_out" / f.get("subfolder", "") / f["filename"]
                if not local.exists():
                    resp = httpx.get(base_url() + "/view", params={"filename": f["filename"],
                                                                     "subfolder": f.get("subfolder", ""),
                                                                     "type": f.get("type", "output")}, timeout=300)
                    local.parent.mkdir(parents=True, exist_ok=True)
                    local.write_bytes(resp.content)
                outputs.append(local)
    if progress:
        progress(1.0, "gotowe")
    return outputs

"""Kontrola zajętości dysku (limit 500 GB), czyszczenie cache, ochrona oryginałów."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from . import paths
from .config import get_config

FOLDERS = [
    ("models", "Modele AI", paths.MODELS),
    ("comfyui", "ComfyUI i efekty", paths.COMFY_ROOT),
    ("projects", "Projekty", paths.PROJECTS),
    ("exports", "Eksporty", paths.EXPORTS),
    ("cache", "Cache i render", paths.CACHE),
    ("backups", "Kopie zapasowe", paths.BACKUPS),
    ("tools", "Narzędzia", paths.TOOLS),
    ("venv", "Środowisko Python", paths.ROOT / ".venv"),
    ("library", "Biblioteka audio", paths.AUDIO_LIBRARY),
    ("downloads", "Pobrane instalatory", paths.DOWNLOADS),
]


def dir_size(p: Path) -> int:
    total = 0
    if not p.exists():
        return 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def usage() -> dict:
    folders = []
    used = 0
    for key, label, p in FOLDERS:
        s = dir_size(p)
        used += s
        folders.append({"key": key, "label": label, "path": str(p), "size_gb": round(s / 1024**3, 2)})
    du = shutil.disk_usage(paths.ROOT)
    return {"limit_gb": get_config().get("storage.limit_gb", 500), "used_gb": round(used / 1024**3, 2),
            "free_disk_gb": round(du.free / 1024**3, 1), "total_disk_gb": round(du.total / 1024**3, 1),
            "folders": folders}


def _rm(p: Path) -> int:
    if not p.exists():
        return 0
    size = dir_size(p) if p.is_dir() else p.stat().st_size

    def onerr(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass
    if p.is_dir():
        shutil.rmtree(p, onerror=onerr)
    else:
        p.unlink(missing_ok=True)
    return size


def clean(target: str, project_id: str | None = None) -> float:
    """Czyści WYŁĄCZNIE pliki odtwarzalne (cache). Oryginały, projekty i eksporty nie są ruszane."""
    freed = 0
    proj_dirs = [paths.PROJECTS / project_id] if project_id else [d for d in paths.PROJECTS.iterdir() if d.is_dir()] \
        if paths.PROJECTS.exists() else []
    if target in ("renders", "all_cache"):
        for d in proj_dirs:
            freed += _rm(d / "cache" / "slices")
            for f in (d / "renders").glob("*.mp4") if (d / "renders").exists() else []:
                freed += _rm(f)
    if target in ("proxies", "all_cache"):
        for d in proj_dirs:
            freed += _rm(d / "cache" / "proxies")
    if target in ("cache", "all_cache"):
        for d in proj_dirs:
            for sub in ("frames", "audio", "tmp"):
                freed += _rm(d / "cache" / sub)
        if paths.CACHE.exists():
            for child in paths.CACHE.iterdir():
                if child.name.startswith("dev_"):
                    continue
                freed += _rm(child)
    if target in ("comfy", "all_cache"):
        freed += _rm(paths.CACHE / "comfy_out")
        freed += _rm(paths.CACHE / "comfy_in")
    if target == "downloads":
        freed += _rm(paths.DOWNLOADS)
        paths.DOWNLOADS.mkdir(exist_ok=True)
    return round(freed / 1024**3, 2)

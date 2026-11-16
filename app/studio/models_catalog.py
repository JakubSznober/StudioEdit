"""Katalog wszystkich modeli AI systemu (Ollama + pliki/HF z modułów) z instalacją i usuwaniem."""
from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import httpx

from . import paths
from .config import get_config
from .events import log
from .services import services

OLLAMA_ROLES = [
    ("director", "llm", "Mózg reżyserski (plan narracji, czat)", ["director", "producer"]),
    ("director_max", "llm", "Reżyser – tryb MAX (rozumowanie)", ["director"]),
    ("director_fast", "llm", "Szybki model (tryb LOW, tłumaczenia promptów)", ["producer", "vfx", "sound"]),
    ("vision", "vlm", "Video-VLM – analiza wizyjna ujęć", ["archivist"]),
]

MODULE_CATALOGS = ["studio.audio.models", "studio.vfx.models", "studio.color.models"]


def _ollama_manifest(name: str) -> Path:
    model, _, tag = name.partition(":")
    tag = tag or "latest"
    ns = "library"
    if "/" in model:
        ns, model = model.split("/", 1)
    return paths.OLLAMA_MODELS / "manifests" / "registry.ollama.ai" / ns / model / tag


def ollama_installed(name: str) -> bool:
    return _ollama_manifest(name).exists()


def ollama_size_gb(name: str) -> float:
    f = _ollama_manifest(name)
    if not f.exists():
        return 0.0
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return round(sum(l.get("size", 0) for l in d.get("layers", [])) / 1024**3, 1)
    except Exception:
        return 0.0


def catalog() -> list[dict]:
    cfg = get_config()
    out: list[dict] = []
    seen = set()
    for role, kind, desc, used in OLLAMA_ROLES:
        name = cfg.get(f"models.{role}.name", "")
        if not name:
            continue
        key = f"ollama:{name}"
        if key in seen:
            for e in out:
                if e["key"] == key:
                    e["used_by"] = sorted(set(e["used_by"]) | set(used))
                    e["role"] += f", {role}"
            continue
        seen.add(key)
        out.append({"key": key, "name": name, "kind": kind, "provider": "ollama", "installed": ollama_installed(name),
                    "size_gb": ollama_size_gb(name) or None, "used_by": used, "description": desc, "role": role})
    for modname in MODULE_CATALOGS:
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        for e in getattr(mod, "CATALOG", []):
            try:
                inst = bool(mod.installed(e["key"]))
            except Exception:
                inst = False
            out.append({"key": f"{modname.split('.')[1]}:{e['key']}", "name": e.get("name", e["key"]),
                        "kind": e.get("kind", "other"), "provider": e.get("provider", "hf"), "installed": inst,
                        "size_gb": e.get("size_gb"), "used_by": e.get("used_by", []),
                        "description": e.get("description", ""), "role": e.get("role", "")})
    return out


def install(key: str, progress=None) -> None:
    if key.startswith("ollama:"):
        name = key.split(":", 1)[1]
        services.ensure("ollama", "system")
        url = services["ollama"].url + "/api/pull"
        with httpx.stream("POST", url, json={"model": name, "stream": True}, timeout=None) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                if progress and d.get("total"):
                    progress(d.get("completed", 0) / d["total"])
        log("system", f"Pobrano model {name}", path="Modele")
        return
    area, _, sub = key.partition(":")
    mod = importlib.import_module(f"studio.{area}.models")
    mod.download(sub, progress=progress)


def remove(key: str) -> None:
    if key.startswith("ollama:"):
        name = key.split(":", 1)[1]
        services.ensure("ollama", "system")
        r = httpx.request("DELETE", services["ollama"].url + "/api/delete", json={"model": name}, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(r.text)
        return
    area, _, sub = key.partition(":")
    mod = importlib.import_module(f"studio.{area}.models")
    if hasattr(mod, "remove"):
        mod.remove(sub)
        return
    for e in getattr(mod, "CATALOG", []):
        if e["key"] == sub and e.get("path"):
            p = Path(e["path"])
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
            return
    raise RuntimeError("Ten model nie obsługuje usuwania z poziomu aplikacji")

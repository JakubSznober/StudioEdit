"""Status systemu: 15 kafelków modułów, wymagania, agenci - dla panelu „Status modeli, usług i modułów”."""
from __future__ import annotations

import functools
import importlib
import time

from . import gpu, paths
from .agents.registry import AGENTS, MODULES
from .config import get_config
from .media import ffmpeg as ff
from .models_catalog import ollama_installed
from .services import services
from .tasks import orchestrator
from .vram import vram

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fn):
    now = time.time()
    if key in _cache and now - _cache[key][0] < ttl:
        return _cache[key][1]
    v = fn()
    _cache[key] = (now, v)
    return v


def _module_installed(modname: str, key: str) -> bool:
    try:
        mod = importlib.import_module(modname)
        return bool(mod.installed(key))
    except Exception:
        return False


def requirements() -> dict[str, tuple[bool, str]]:
    cfg = get_config()
    o = services["ollama"]
    o_state = o.refresh()
    ollama_ok = o_state in ("running", "external") or (o.installed() and o.cfg.get("auto_wake", True))
    o_detail = {"running": "działa", "external": "działa (zewnętrznie)"}.get(o_state, "uśpiony — Auto-Wake na żądanie")
    director = cfg.get("models.director.name", "")
    vision = cfg.get("models.vision.name", "")
    llm_ok = any(ollama_installed(n) for n in [director] + list(cfg.get("models.director.fallbacks", [])))
    vlm_ok = ollama_installed(vision) or any(ollama_installed(n) for n in cfg.get("models.vision.fallbacks", []))
    stt_ok = _cached("stt", 30, lambda: _module_installed("studio.audio.models", "whisper-large-v3-turbo")
                     or _module_installed("studio.audio.models", "faster-whisper-large-v3-turbo"))
    sam_ok = _cached("sam", 30, lambda: _module_installed("studio.vfx.models", "sam2.1_l"))
    adobe = _cached("adobe", 120, _find_adobe)
    return {
        "ffmpeg": (paths.FFMPEG.exists(), "FFmpeg 9 (NVENC, libass, vidstab)" if paths.FFMPEG.exists() else "brak FFmpeg"),
        "nvenc": (_cached("nvenc", 600, ff.nvenc_ok), "NVENC/NVDEC" if _cached("nvenc", 600, ff.nvenc_ok) else "brak NVENC — render CPU"),
        "ollama": (ollama_ok, o_detail if o.installed() else "Ollama nie zainstalowana"),
        "llm": (llm_ok, director if llm_ok else f"brak modelu {director}"),
        "vlm": (vlm_ok, vision if vlm_ok else f"brak modelu {vision}"),
        "stt": (stt_ok, "Whisper large-v3-turbo" if stt_ok else "brak modelu STT"),
        "sam": (sam_ok, "SAM 2.1 Hiera-L" if sam_ok else "brak SAM 2.1 — tryb klasyczny"),
        "aftereffects": (bool(adobe.get("aftereffects")), adobe.get("aftereffects") or "After Effects nie znaleziony"),
    }


def _find_adobe() -> dict:
    try:
        from .exports.adobe import find_adobe

        return find_adobe()
    except Exception:
        return {}


@functools.lru_cache(maxsize=1)
def _module_code_ok() -> dict[str, bool]:
    checks = {"ai_colorist": "studio.agents.colorist", "ai_audio": "studio.agents.sound",
              "ai_subtitles": "studio.subtitles.build", "ai_effects": "studio.agents.vfx",
              "region_editor": "studio.agents.vfx", "export_studio": "studio.agents.render",
              "premiere_xml": "studio.exports.premiere", "ae_jsx": "studio.exports.aftereffects",
              "ae_runtime": "studio.exports.aftereffects", "ai_qc": "studio.agents.qc",
              "ai_director": "studio.agents.director", "ai_montage": "studio.agents.director",
              "ai_chat": "studio.agents.producer", "ai_analysis": "studio.agents.archivist",
              "ai_developer": "studio.devtools"}
    out = {}
    for k, mod in checks.items():
        try:
            importlib.import_module(mod)
            out[k] = True
        except Exception:
            out[k] = False
    return out


def modules() -> list[dict]:
    req = requirements()
    code = _module_code_ok()
    busy_agents = {p["agent"] for p in orchestrator.agents_state() if p["busy"]}
    out = []
    for m in MODULES:
        if m["key"] == "project_manager":
            continue
        missing = [r for r in m["requires"] if not req.get(r, (True, ""))[0]]
        soft = {"sam", "aftereffects", "vlm", "nvenc"}
        if not code.get(m["key"], True):
            state, detail = "error", "moduł nie załadował się (zobacz AI Developer → diagnostyka)"
        elif missing and all(x in soft for x in missing):
            state = "off" if m["key"] == "ae_runtime" else "warn"
            detail = "; ".join(req[x][1] for x in missing)
        elif missing:
            state, detail = "error", "; ".join(req[x][1] for x in missing)
        else:
            state = "busy" if m["agent"] in busy_agents else "ok"
            detail = "; ".join(req[x][1] for x in m["requires"]) or "gotowy"
        out.append({"key": m["key"], "name": m["name"], "agent": m["agent"], "state": state, "detail": detail,
                    "requires": m["requires"]})
    return out


def agents() -> list[dict]:
    cfg = get_config()
    states = {p["agent"]: p for p in orchestrator.agents_state()}
    out = []
    for a in AGENTS:
        st = states.get(a["key"], {"busy": 0, "queued": 0, "running": []})
        out.append({**a, "effort": cfg.effort(a["key"]), "workers": int(cfg.get(f"agents.{a['key']}.workers", 2)),
                    "state": {"busy": st.get("busy", 0), "queued": st.get("queued", 0), "running": st.get("running", [])}})
    return out


def system_status() -> dict:
    from .project.manager import projects
    from .storage import usage

    act = projects.active()
    return {
        "gpu": gpu.last() or gpu.stats(),
        "vram": vram.state(),
        "services": services.all_info(),
        "modules": modules(),
        "agents": agents(),
        "storage": _cached("storage", 60, usage),
        "active_project": act.summary() if act else None,
        "version": __import__("studio").__version__,
    }

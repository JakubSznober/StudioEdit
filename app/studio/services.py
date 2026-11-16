"""Menedżer usług lokalnych: Ollama i ComfyUI.

* Adresy i parametry wyłącznie z config.json (services.*).
* Auto-Wake: agent woła ensure("ollama") / ensure("comfyui") - usługa startuje sama w tle.
* Auto-Sleep: ComfyUI usypiany po bezczynności; Ollama zwalnia modele przez keep_alive=0.
* Procesy uruchamiane bez okna konsoli, logi w C:\\StudioEdit\\Logs\\<usługa>.log.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import paths
from .config import get_config
from .events import bus, log

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Service:
    name = ""
    label = ""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.state = "unknown"  # stopped | starting | running | stopping | error | external | disabled
        self.error = ""
        self.started_at: float | None = None
        self.last_used = time.time()
        self._lock = threading.RLock()

    # konfiguracja
    @property
    def cfg(self) -> dict:
        return get_config().get(f"services.{self.name}", {}) or {}

    @property
    def url(self) -> str:
        return self.cfg.get("url", "").rstrip("/")

    @property
    def host_port(self) -> tuple[str, int]:
        u = urlparse(self.url)
        return u.hostname or "127.0.0.1", u.port or 80

    # do nadpisania
    def health_path(self) -> str:
        return "/"

    def command(self) -> tuple[list[str], dict, Path | None] | None:
        return None

    def installed(self) -> bool:
        return self.command() is not None

    # API
    def healthy(self, timeout: float = 1.5) -> bool:
        try:
            r = httpx.get(self.url + self.health_path(), timeout=timeout)
            return r.status_code < 500
        except Exception:
            return False

    def refresh(self) -> str:
        with self._lock:
            if not self.cfg.get("enabled", True):
                self.state = "disabled"
            elif self.healthy():
                self.state = "running" if self.proc and self.proc.poll() is None else "external"
                self.error = ""
            elif self.proc and self.proc.poll() is None:
                self.state = "starting" if self.state == "starting" else "error"
            elif self.state not in ("starting",):
                self.state = "stopped"
            return self.state

    def start(self, wait: bool = True, timeout: float = 180) -> bool:
        with self._lock:
            if self.healthy():
                self.refresh()
                return True
            cmd = self.command()
            if cmd is None:
                self.state, self.error = "error", f"{self.label}: nie znaleziono instalacji"
                self._publish()
                return False
            argv, env, cwd = cmd
            paths.LOGS.mkdir(parents=True, exist_ok=True)
            logf = open(paths.LOGS / f"{self.name}.log", "ab")
            full_env = {**os.environ, **env}
            self.proc = subprocess.Popen(argv, cwd=str(cwd) if cwd else None, env=full_env, stdout=logf,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         creationflags=CREATE_NO_WINDOW)
            self.state, self.error, self.started_at = "starting", "", time.time()
            self._publish()
            log("system", f"Uruchamiam {self.label} ({self.url})", path="Usługi")
        if not wait:
            threading.Thread(target=self._wait_ready, args=(timeout,), daemon=True).start()
            return True
        return self._wait_ready(timeout)

    def _wait_ready(self, timeout: float) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.healthy():
                with self._lock:
                    self.state = "running"
                    self._publish()
                log("system", f"{self.label} gotowy po {time.time() - t0:.1f} s", path="Usługi")
                return True
            if self.proc and self.proc.poll() is not None:
                # proces padł - często dlatego, że usługa już działa (np. aplikacja Ollama w zasobniku)
                time.sleep(1.0)
                if self.healthy():
                    with self._lock:
                        self.proc = None
                        self.refresh()
                        self._publish()
                    return True
                break
            time.sleep(0.7)
        with self._lock:
            self.state = "error"
            self.error = f"{self.label} nie wystartował (zobacz Logs\\{self.name}.log)"
            self._publish()
        log("system", self.error, path="Usługi", level="error")
        return False

    def stop(self) -> bool:
        with self._lock:
            self.before_stop()
            if self.proc and self.proc.poll() is None:
                self.state = "stopping"
                self._publish()
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                self.proc = None
                log("system", f"Zatrzymano {self.label}", path="Usługi")
            self.refresh()
            self._publish()
            return True

    def before_stop(self) -> None:
        pass

    def ensure(self, agent: str = "system") -> bool:
        """Auto-Wake: gwarantuje działanie usługi (start w razie potrzeby)."""
        self.last_used = time.time()
        if self.healthy():
            if self.state not in ("running", "external"):
                self.refresh()
                self._publish()
            return True
        if not self.cfg.get("enabled", True):
            raise RuntimeError(f"{self.label} jest wyłączony w konfiguracji")
        if not self.cfg.get("auto_wake", True):
            raise RuntimeError(f"{self.label} nie działa, a Auto-Wake jest wyłączony")
        log(agent, f"Budzę usługę {self.label}…", path="Auto-Wake")
        if not self.start(wait=True):
            raise RuntimeError(self.error or f"{self.label} nie wystartował")
        return True

    def info(self) -> dict:
        self.refresh()
        return {"name": self.name, "label": self.label, "url": self.url, "state": self.state, "error": self.error,
                "installed": self.installed(), "managed": bool(self.proc and self.proc.poll() is None),
                "auto_wake": self.cfg.get("auto_wake", True), "auto_sleep": self.cfg.get("auto_sleep", True),
                "enabled": self.cfg.get("enabled", True), "pid": self.proc.pid if self.proc else None,
                "uptime_s": round(time.time() - self.started_at, 1) if self.started_at and self.state == "running"
                else None, **self.extra_info()}

    def extra_info(self) -> dict:
        return {}

    def _publish(self) -> None:
        bus.publish("services.update", {"name": self.name, "state": self.state, "error": self.error},
                    sticky_key=f"services.{self.name}")


class OllamaService(Service):
    name = "ollama"
    label = "Ollama"

    def health_path(self) -> str:
        return "/api/version"

    def exe(self) -> str | None:
        e = self.cfg.get("exe", "auto")
        if e and e != "auto" and Path(e).exists():
            return e
        cands = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                 Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Ollama" / "ollama.exe"]
        for c in cands:
            if c.exists():
                return str(c)
        return shutil.which("ollama")

    def command(self):
        exe = self.exe()
        if not exe:
            return None
        host, port = self.host_port
        env = {"OLLAMA_HOST": f"{host}:{port}", "OLLAMA_MODELS": str(paths.OLLAMA_MODELS),
               **{k: str(v) for k, v in (self.cfg.get("env") or {}).items()}}
        return [exe, "serve"], env, None

    def before_stop(self) -> None:
        for m in self.loaded_models():
            self.unload(m["name"])

    def models(self) -> list[dict]:
        try:
            r = httpx.get(self.url + "/api/tags", timeout=5)
            return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 1024**3, 1),
                     "family": m.get("details", {}).get("family", ""),
                     "params": m.get("details", {}).get("parameter_size", ""),
                     "quant": m.get("details", {}).get("quantization_level", "")}
                    for m in r.json().get("models", [])]
        except Exception:
            return []

    def loaded_models(self) -> list[dict]:
        try:
            r = httpx.get(self.url + "/api/ps", timeout=3)
            return [{"name": m["name"], "vram_gb": round(m.get("size_vram", 0) / 1024**3, 1),
                     "size_gb": round(m.get("size", 0) / 1024**3, 1), "expires_at": m.get("expires_at")}
                    for m in r.json().get("models", [])]
        except Exception:
            return []

    def unload(self, model: str) -> None:
        try:
            httpx.post(self.url + "/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
        except Exception:
            pass

    def unload_all(self) -> None:
        for m in self.loaded_models():
            self.unload(m["name"])

    def extra_info(self) -> dict:
        if self.state in ("running", "external"):
            return {"models": self.models(), "loaded": self.loaded_models()}
        return {"models": [], "loaded": []}


class ComfyService(Service):
    name = "comfyui"
    label = "ComfyUI"

    def health_path(self) -> str:
        return "/system_stats"

    def root(self) -> Path:
        return Path(self.cfg.get("root") or paths.COMFY_ROOT)

    def command(self):
        root = self.root()
        py = root / "python_embeded" / "python.exe"
        main = root / "ComfyUI" / "main.py"
        if not (py.exists() and main.exists()):
            return None
        host, port = self.host_port
        args = [str(py), "-s", str(main), "--listen", host, "--port", str(port),
                "--output-directory", str(paths.CACHE / "comfy_out"),
                "--input-directory", str(paths.CACHE / "comfy_in"), *self.cfg.get("args", [])]
        (paths.CACHE / "comfy_out").mkdir(parents=True, exist_ok=True)
        (paths.CACHE / "comfy_in").mkdir(parents=True, exist_ok=True)
        return args, {"PYTHONIOENCODING": "utf-8"}, root

    def free(self) -> None:
        try:
            httpx.post(self.url + "/free", json={"unload_models": True, "free_memory": True}, timeout=20)
        except Exception:
            pass

    def before_stop(self) -> None:
        self.free()

    def extra_info(self) -> dict:
        if self.state in ("running", "external"):
            try:
                return {"system": httpx.get(self.url + "/system_stats", timeout=3).json()}
            except Exception:
                return {}
        return {}


class ServiceManager:
    def __init__(self):
        self.services: dict[str, Service] = {"ollama": OllamaService(), "comfyui": ComfyService()}
        self._sleeper = threading.Thread(target=self._sleep_loop, daemon=True, name="service-sleeper")
        self._sleeper.start()

    def __getitem__(self, name: str) -> Service:
        return self.services[name]

    def ensure(self, name: str, agent: str = "system") -> bool:
        return self.services[name].ensure(agent)

    def all_info(self) -> list[dict]:
        return [s.info() for s in self.services.values()]

    def stop_all(self) -> None:
        for s in self.services.values():
            if s.proc and s.proc.poll() is None:
                s.stop()

    def _sleep_loop(self) -> None:
        """Auto-Sleep: ComfyUI zatrzymywany po 10 min bezczynności (tylko jeśli to my go uruchomiliśmy)."""
        while True:
            time.sleep(30)
            c = self.services["comfyui"]
            if c.cfg.get("auto_sleep", True) and c.proc and c.proc.poll() is None:
                if time.time() - c.last_used > 600:
                    from .vram import vram

                    if not (vram.owner or "").startswith("comfy"):
                        log("system", "ComfyUI bezczynny - usypiam", path="Auto-Sleep")
                        c.stop()


services = ServiceManager()


def _release_ollama(owner: str) -> None:
    model = owner.split(":", 1)[1] if ":" in owner else ""
    o: OllamaService = services["ollama"]  # type: ignore[assignment]
    if model:
        o.unload(model)
    else:
        o.unload_all()


def _release_comfy(owner: str) -> None:
    services["comfyui"].free()  # type: ignore[attr-defined]


def register_vram_releasers() -> None:
    from .vram import vram

    vram.register_releaser("ollama:", _release_ollama)
    vram.register_releaser("comfy", _release_comfy)

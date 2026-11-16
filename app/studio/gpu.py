"""Telemetria GPU (NVML): użycie, VRAM, temperatura, moc, enkoder/dekoder NVENC/NVDEC."""
from __future__ import annotations

import threading
import time

from .events import bus

_lock = threading.Lock()
_nvml_ok: bool | None = None
_last: dict = {}


def _init() -> bool:
    global _nvml_ok
    if _nvml_ok is not None:
        return _nvml_ok
    try:
        import pynvml

        pynvml.nvmlInit()
        _nvml_ok = True
    except Exception:
        _nvml_ok = False
    return _nvml_ok


def stats() -> dict:
    """Aktualny stan GPU 0. Zwraca {'available': False} gdy brak NVML."""
    with _lock:
        if not _init():
            return {"available": False}
        import pynvml

        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            out = {
                "available": True,
                "name": name,
                "util": util.gpu,
                "mem_util": util.memory,
                "mem_used_gb": round(mem.used / 1024**3, 2),
                "mem_total_gb": round(mem.total / 1024**3, 2),
                "mem_free_gb": round(mem.free / 1024**3, 2),
            }
            for key, fn in (("temp", lambda: pynvml.nvmlDeviceGetTemperature(h, 0)),
                            ("power_w", lambda: round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000, 1)),
                            ("power_limit_w", lambda: round(pynvml.nvmlDeviceGetEnforcedPowerLimit(h) / 1000, 1)),
                            ("enc_util", lambda: pynvml.nvmlDeviceGetEncoderUtilization(h)[0]),
                            ("dec_util", lambda: pynvml.nvmlDeviceGetDecoderUtilization(h)[0]),
                            ("driver", lambda: _dec(pynvml.nvmlSystemGetDriverVersion()))):
                try:
                    out[key] = fn()
                except Exception:
                    pass
            procs = []
            try:
                for p in pynvml.nvmlDeviceGetComputeRunningProcesses(h):
                    mem = round((p.usedGpuMemory or 0) / 1024**3, 2)
                    if mem > 0.05:
                        procs.append({"pid": p.pid, "mem_gb": mem})
            except Exception:
                pass
            out["processes"] = procs
            _last.update(out)
            return out
        except Exception as e:  # pragma: no cover
            return {"available": False, "error": str(e)}


def _dec(x):
    return x.decode() if isinstance(x, bytes) else x


def last() -> dict:
    return dict(_last) if _last else stats()


class GPUMonitor(threading.Thread):
    def __init__(self, interval: float = 1.0):
        super().__init__(daemon=True, name="gpu-monitor")
        self.interval = interval
        self._stop = threading.Event()

    def run(self) -> None:
        from .vram import vram

        while not self._stop.is_set():
            s = stats()
            s["owner"] = vram.owner
            s["owner_agent"] = vram.owner_agent
            bus.publish("gpu.stats", s, sticky_key="gpu.stats")
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


def wait_for_free(min_free_gb: float, timeout: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = stats()
        if not s.get("available") or s.get("mem_free_gb", 0) >= min_free_gb:
            return True
        time.sleep(0.5)
    return False

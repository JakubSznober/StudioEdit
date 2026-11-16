"""Konfiguracja StudioEdit (config/config.json) z domyślnymi wartościami i głębokim scalaniem.

Wszystkie adresy usług, modele, poziomy wysiłku agentów i profile skali montażu są tutaj.
UI może je zmieniać przez /api/config (PATCH) - zmiany zapisywane są atomowo.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

from . import paths

EFFORTS = ("low", "medium", "high", "max")

DEFAULTS: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8765, "open_browser": True},
    "services": {
        "ollama": {
            "enabled": True,
            "url": "http://127.0.0.1:11434",
            "exe": "auto",
            "auto_wake": True,
            "auto_sleep": True,
            "keep_alive": "10m",
            "env": {
                "OLLAMA_FLASH_ATTENTION": "1",
                "OLLAMA_KV_CACHE_TYPE": "q8_0",
                "OLLAMA_MAX_LOADED_MODELS": "1",
                "OLLAMA_NUM_PARALLEL": "1",
            },
        },
        "comfyui": {
            "enabled": True,
            "url": "http://127.0.0.1:8188",
            "root": str(paths.COMFY_ROOT),
            "auto_wake": True,
            "auto_sleep": True,
            "args": ["--disable-auto-launch", "--preview-method", "none"],
        },
    },
    "models": {
        # Mózg reżyserski: MoE 35B (3B aktywnych) - szybki mimo częściowego offloadu do RAM
        "director": {"provider": "ollama", "name": "qwen3.6:35b-a3b", "think": False, "num_ctx": 32768,
                     "fallbacks": ["qwen3.8:27b", "qwen3-vl:8b"]},
        # Tryb "max": najnowszy gęsty 27B z rozumowaniem (wolniejszy, dokładniejszy)
        "director_max": {"provider": "ollama", "name": "qwen3.8:27b", "think": True, "num_ctx": 32768,
                         "fallbacks": ["qwen3.6:35b-a3b", "qwen3-vl:8b"]},
        # Tryb "low": mały, w pełni w VRAM
        "director_fast": {"provider": "ollama", "name": "qwen3-vl:8b", "think": False, "num_ctx": 16384,
                          "fallbacks": []},
        # Analiza wizyjna ujęć
        "vision": {"provider": "ollama", "name": "qwen3-vl:8b", "num_ctx": 16384, "fallbacks": ["qwen3.6:35b-a3b"]},
        "stt": {"engine": "faster-whisper", "name": "large-v3-turbo", "compute_type": "float16",
                "language": "pl", "fallback_engine": "transformers", "fallback_name": "openai/whisper-large-v3-turbo"},
        "diarization": {"name": "microsoft/wavlm-base-plus-sv", "max_speakers": 6, "threshold": 0.72},
        "tts": {"engine": "piper", "voice": "pl_PL-gosia-medium", "voices": ["pl_PL-gosia-medium", "pl_PL-darkman-medium"]},
        "music": {"name": "facebook/musicgen-stereo-medium"},
        "sam": {"name": "sam2.1_l.pt"},
        "detector": {"name": "yolo11m.pt"},
        "inpaint": {"name": "big-lama.pt"},
        "video_gen": {"workflow": "wan22_ti2v_5b", "width": 832, "height": 480, "fps": 24, "steps": 24},
    },
    # Poziomy wysiłku (low/medium/high/max) i liczba wątków dla każdego agenta
    "agents": {
        "producer": {"effort": "high", "workers": 6},
        "archivist": {"effort": "high", "workers": 4},
        "director": {"effort": "high", "workers": 2},
        "colorist": {"effort": "high", "workers": 3},
        "sound": {"effort": "high", "workers": 3},
        "vfx": {"effort": "medium", "workers": 1},
        "qc": {"effort": "high", "workers": 2},
        "render": {"effort": "high", "workers": 3},
    },
    # Profile skali montażu
    "scale_profiles": {
        "small": {"label": "Mały (do ~15 min materiału)", "vlm_frames_per_shot": 4, "motion_fps": 6,
                  "max_shots_for_vlm": 400, "director_mode": "single", "proxy": True},
        "medium": {"label": "Średni (do ~2 h materiału)", "vlm_frames_per_shot": 3, "motion_fps": 4,
                   "max_shots_for_vlm": 1500, "director_mode": "single", "proxy": True},
        "massive": {"label": "Masywny (2 h+ materiału)", "vlm_frames_per_shot": 2, "motion_fps": 2,
                    "max_shots_for_vlm": 6000, "director_mode": "hierarchical", "proxy": True},
    },
    "render": {
        "video_codec": "hevc_nvenc",
        "ten_bit": True,
        "preset": "p6",
        "cq": 19,
        "audio_codec": "aac",
        "audio_bitrate": "320k",
        "chunk_codec": "h264_nvenc",
        "chunk_cq": 16,
        "preview_height": 720,
        "nvenc_sessions": 3,
    },
    "audio": {
        "target_lufs": -14.0,
        "true_peak": -1.0,
        "duck_db": -15.0,
        "micro_fade_ms": 80,
        "breath_ms": 200,
        "jl_frames": 12,
        "denoise": "auto",
    },
    "edit": {
        "min_shot_s": 0.8,
        "max_broll_s": 5.0,
        "punch_in_scale": 1.15,
        "slow_zoom_scale": 1.08,
        "filler_words": ["eee", "yyy", "eem", "yhm", "mmm", "hmm", "eh", "ee", "yy", "em", "ym"],
    },
    "storage": {"limit_gb": 500},
    "adobe": {"premiere_exe": "auto", "afterfx_exe": "auto"},
    "ui": {"language": "pl", "theme": "system"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    """Wątkowo-bezpieczny dostęp do konfiguracji."""

    def __init__(self, path: Path = paths.CONFIG_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            user: dict = {}
            if self.path.exists():
                try:
                    user = json.loads(self.path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    user = {}
            self._data = _deep_merge(DEFAULTS, user)
            if not self.path.exists():
                self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    @property
    def data(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            cur: Any = self._data
            for part in dotted.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return default
                cur = cur[part]
            return copy.deepcopy(cur)

    def update(self, patch: dict) -> dict:
        with self._lock:
            self._data = _deep_merge(self._data, patch)
            self.save()
            return self.data

    def effort(self, agent: str) -> str:
        e = self.get(f"agents.{agent}.effort", "high")
        return e if e in EFFORTS else "high"

    def effort_level(self, agent: str) -> int:
        return EFFORTS.index(self.effort(agent))


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config

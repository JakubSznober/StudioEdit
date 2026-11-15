"""StudioEdit — silnik lokalnego agenta do montażu.

Import tego pakietu rejestruje biblioteki CUDA 12 z pakietów `nvidia-*` w venv,
bo ctranslate2 (faster-whisper) jest zbudowany pod CUDA 12, a torch w tym venv używa CUDA 13.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(r"C:\StudioEdit")
APP_DIR = ROOT / "App"
MODELS_DIR = ROOT / "AI-Models"
TOOLS_DIR = ROOT / "Tools"
CACHE_DIR = ROOT / "Cache"
FOOTAGE_DIR = ROOT / "Footage"
PROXIES_DIR = ROOT / "Proxies"
EXPORTS_DIR = ROOT / "Exports"
PROJECTS_DIR = ROOT / "Projects"

FFMPEG = TOOLS_DIR / "FFmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = TOOLS_DIR / "FFmpeg" / "bin" / "ffprobe.exe"
WHISPER_MODEL_DIR = MODELS_DIR / "Whisper" / "large-v3-turbo"

_DEFAULT_ENV = {
    "OLLAMA_MODELS": MODELS_DIR / "Ollama",
    "HF_HOME": MODELS_DIR / "huggingface",
    "TORCH_HOME": MODELS_DIR / "torch",
    "PIP_CACHE_DIR": CACHE_DIR / "pip",
}


def _register_cuda_dlls() -> list[str]:
    """Dodaje katalogi bin pakietów nvidia-*-cu12 do ścieżki wyszukiwania DLL (tylko Windows)."""
    added: list[str] = []
    if sys.platform != "win32":
        return added
    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not nvidia_root.exists():
        return added
    for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
        bin_dir = nvidia_root / sub / "bin"
        if bin_dir.exists():
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            added.append(str(bin_dir))
    return added


def _ensure_env_defaults() -> None:
    """Gwarantuje, że cache modeli trafia do C:\\StudioEdit nawet w procesie bez zmiennych użytkownika."""
    for key, path in _DEFAULT_ENV.items():
        os.environ.setdefault(key, str(path))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "0")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")


CUDA_DLL_DIRS = _register_cuda_dlls()
_ensure_env_defaults()

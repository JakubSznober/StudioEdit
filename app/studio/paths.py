"""Centralne ścieżki StudioEdit. Wszystko żyje w katalogu głównym (domyślnie C:\\StudioEdit)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("STUDIOEDIT_ROOT", Path(__file__).resolve().parents[2]))

APP = ROOT / "app"
WEB_DIST = APP / "web"  # zbudowany frontend (vite build -> app/web)
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "config.json"
TOOLS = ROOT / "tools"
MODELS = ROOT / "AI-Models"
HF_HOME = MODELS / "huggingface"
OLLAMA_MODELS = MODELS / "Ollama"
PIPER_DIR = MODELS / "piper"
WEIGHTS = MODELS / "weights"
COMFY_ROOT = ROOT / "ComfyUI"
PROJECTS = ROOT / "Projects"
EXPORTS = ROOT / "Exports"
CACHE = ROOT / "Cache"
BACKUPS = ROOT / "Backups"
AUDIO_LIBRARY = ROOT / "Audio-Library"
LUTS = ROOT / "LUTs"
LOGS = ROOT / "Logs"
DOWNLOADS = ROOT / "Downloads"

FFMPEG_BIN = TOOLS / "ffmpeg" / "bin"
FFMPEG = FFMPEG_BIN / "ffmpeg.exe"
FFPROBE = FFMPEG_BIN / "ffprobe.exe"
RNNOISE_DIR = TOOLS / "rnnoise"


def ensure_dirs() -> None:
    for p in (CONFIG_DIR, TOOLS, MODELS, HF_HOME, OLLAMA_MODELS, PIPER_DIR, WEIGHTS, PROJECTS,
              EXPORTS, CACHE, BACKUPS, AUDIO_LIBRARY / "Music", AUDIO_LIBRARY / "SFX", LUTS, LOGS):
        p.mkdir(parents=True, exist_ok=True)


def setup_env() -> None:
    """Ustawia zmienne środowiskowe procesu (nie systemowe), żeby modele trafiały do AI-Models."""
    os.environ.setdefault("HF_HOME", str(HF_HOME))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TORCH_HOME", str(MODELS / "torch"))
    os.environ.setdefault("YOLO_CONFIG_DIR", str(MODELS / "ultralytics"))
    os.environ["PATH"] = str(FFMPEG_BIN) + os.pathsep + os.environ.get("PATH", "")
    if os.environ.get("STUDIOEDIT_OFFLINE", "0") == "1":
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    _add_cuda_dll_dirs()


def _add_cuda_dll_dirs() -> None:
    """CTranslate2 (faster-whisper) potrzebuje cuBLAS/cuDNN 12 - bierzemy je z pakietów pip nvidia-*."""
    if os.name != "nt":
        return
    import site
    import sys

    cands: list[Path] = []
    for sp in [*site.getsitepackages(), str(Path(sys.prefix) / "Lib" / "site-packages")]:
        base = Path(sp)
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin", "torch/lib"):
            d = base / sub
            if d.is_dir():
                cands.append(d)
    seen = set()
    for d in cands:
        s = str(d)
        if s in seen:
            continue
        seen.add(s)
        try:
            os.add_dll_directory(s)
        except (OSError, AttributeError):
            pass
        os.environ["PATH"] = s + os.pathsep + os.environ.get("PATH", "")

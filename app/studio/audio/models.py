"""Katalog modeli obszaru audio (STT, diaryzacja, TTS, muzyka, odszumianie) + pobieranie i sprawdzanie.

Modele HF trafiają do `AI-Models/huggingface` (HF_HOME), głosy Piper do `AI-Models/piper/`.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

from .. import paths

PIPER_REPO = "rhasspy/piper-voices"
PIPER_VOICES = {
    "pl_PL-gosia-medium": "pl/pl_PL/gosia/medium",
    "pl_PL-darkman-medium": "pl/pl_PL/darkman/medium",
}

CATALOG: list[dict] = [
    {"key": "faster-whisper-large-v3-turbo", "name": "Whisper large-v3-turbo (CTranslate2)", "kind": "stt", "provider": "hf",
     "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "size_gb": 1.62,
     "allow": ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
     "description": "Transkrypcja mowy z czasami słów (faster-whisper, GPU float16).", "used_by": ["sound"]},
    {"key": "whisper-large-v3-turbo", "name": "Whisper large-v3-turbo (transformers, zapas)", "kind": "stt",
     "provider": "hf", "repo": "openai/whisper-large-v3-turbo", "size_gb": 1.62, "optional": True,
     "allow": ["*.json", "model.safetensors", "merges.txt", "vocab.json"],
     "description": "Zapasowy silnik STT (torch), gdy CTranslate2 nie działa na GPU.", "used_by": ["sound"]},
    {"key": "wavlm_sv", "name": "WavLM Base+ SV (diaryzacja)", "kind": "diarization", "provider": "hf",
     "repo": "microsoft/wavlm-base-plus-sv", "size_gb": 0.40, "allow": None,
     "description": "Embeddingi głosu do rozpoznawania mówców (bez modeli z ograniczonym dostępem).",
     "used_by": ["sound"]},
    {"key": "piper_pl_gosia", "name": "Piper: pl_PL-gosia-medium", "kind": "tts", "provider": "piper",
     "voice": "pl_PL-gosia-medium", "size_gb": 0.06, "description": "Polski głos żeński lektora (CPU, ONNX).",
     "used_by": ["sound"]},
    {"key": "piper_pl_darkman", "name": "Piper: pl_PL-darkman-medium", "kind": "tts", "provider": "piper",
     "voice": "pl_PL-darkman-medium", "size_gb": 0.06, "description": "Polski głos męski lektora (CPU, ONNX).",
     "used_by": ["sound"]},
    {"key": "musicgen_stereo_medium", "name": "MusicGen stereo medium", "kind": "music", "provider": "hf",
     "repo": "facebook/musicgen-stereo-medium", "size_gb": 4.08,
     "allow": ["*.json", "model.safetensors", "spiece.model"],
     "description": "Generowanie muzyki w tle z opisu (GPU fp16).", "used_by": ["sound"]},
    {"key": "deepfilternet3", "name": "DeepFilterNet 3", "kind": "denoise", "provider": "pip",
     "package": "deepfilternet", "size_gb": 0.01, "optional": True,
     "description": "Neuronowe odszumianie mowy 48 kHz (gdy niedostępne: RNNoise arnndn + afftdn).",
     "used_by": ["sound"]},
    {"key": "chatterbox_mtl", "name": "Chatterbox Multilingual (klonowanie głosu)", "kind": "tts",
     "provider": "hf", "repo": "ResembleAI/chatterbox", "size_gb": 3.3, "optional": True,
     "allow": ["ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json",
               "conds.pt", "Cangjie5_TC.json"],
     "description": "Klonowanie głosu z próbki (wymaga pakietu chatterbox-tts).", "used_by": ["sound"]},
]


def entry(key: str) -> dict:
    for e in CATALOG:
        if e["key"] == key:
            return e
    raise KeyError(f"Nieznany model audio: {key}")


def _hf_env() -> None:
    os.environ.setdefault("HF_HOME", str(paths.HF_HOME))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def hf_local(repo: str, allow: list[str] | None = None) -> Path | None:
    """Ścieżka lokalnej migawki repo HF (bez sieci) lub None."""
    _hf_env()
    try:
        from huggingface_hub import snapshot_download

        p = Path(snapshot_download(repo, allow_patterns=allow, local_files_only=True))
        if any(p.iterdir()):
            return p
    except Exception:
        return None
    return None


def piper_voice_path(voice: str) -> Path:
    return paths.PIPER_DIR / f"{voice}.onnx"


def _pip_ok(pkg: str) -> bool:
    import importlib.util

    mod = {"deepfilternet": "df", "chatterbox-tts": "chatterbox"}.get(pkg, pkg)
    return importlib.util.find_spec(mod) is not None


def installed(key: str) -> bool:
    e = entry(key)
    if e["provider"] == "piper":
        p = piper_voice_path(e["voice"])
        return p.exists() and p.with_suffix(".onnx.json").exists()
    if e["provider"] == "pip":
        if key == "deepfilternet3":
            from . import clean

            return clean.deepfilter_available()
        return _pip_ok(e["package"])
    if key == "chatterbox_mtl" and not _pip_ok("chatterbox-tts"):
        return False
    p = hf_local(e["repo"], e.get("allow"))
    if p is None:
        return False
    if key == "faster-whisper-large-v3-turbo":
        return (p / "model.bin").exists()
    if key in ("whisper-large-v3-turbo", "musicgen_stereo_medium"):
        return (p / "model.safetensors").exists()
    return True


def download(key: str, progress: Callable[[float], None] | None = None) -> None:
    e = entry(key)
    if progress:
        progress(0.0)
    if e["provider"] == "piper":
        _download_piper(e["voice"])
    elif e["provider"] == "pip":
        if key == "deepfilternet3":
            from . import clean

            if not clean.deepfilter_available():
                raise RuntimeError("DeepFilterNet nie działa z bieżącym torch/torchaudio - używany jest RNNoise")
            clean.load_deepfilter()  # pobiera wagi przy pierwszym użyciu
    else:
        _hf_env()
        from huggingface_hub import snapshot_download

        snapshot_download(e["repo"], allow_patterns=e.get("allow"))
    if progress:
        progress(1.0)


def _download_piper(voice: str) -> None:
    _hf_env()
    from huggingface_hub import hf_hub_download

    sub = PIPER_VOICES.get(voice)
    if not sub:
        raise KeyError(f"Nieznany głos Piper: {voice}")
    paths.PIPER_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in (".onnx", ".onnx.json"):
        dst = paths.PIPER_DIR / f"{voice}{suffix}"
        if dst.exists() and dst.stat().st_size > 0:
            continue
        src = hf_hub_download(PIPER_REPO, f"{sub}/{voice}{suffix}")
        shutil.copyfile(src, dst)


def status() -> list[dict]:
    return [{**{k: v for k, v in e.items() if k not in ("allow",)}, "installed": installed(e["key"])}
            for e in CATALOG]

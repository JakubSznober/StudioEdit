"""Sprawdzenie środowiska StudioEdit: CUDA, Whisper, OTIO, scenedetect, FFmpeg, Ollama."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio  # noqa: E402  — rejestruje DLL CUDA 12 i domyślne zmienne środowiskowe
from studio import FFMPEG, ROOT, WHISPER_MODEL_DIR  # noqa: E402


def ok(label: str, detail: str = "") -> None:
    print(f"  OK   {label}" + (f"  ({detail})" if detail else ""))


def fail(label: str, detail: str = "") -> None:
    print(f"  FAIL {label}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    failures = 0
    print(f"Python {sys.version.split()[0]}  venv={sys.prefix}")

    import torch

    if torch.cuda.is_available():
        ok("torch CUDA", f"torch {torch.__version__}, cuda {torch.version.cuda}, {torch.cuda.get_device_name(0)}")
        arch = torch.cuda.get_arch_list()
        if "sm_120" in arch:
            ok("Blackwell sm_120 w buildzie torch")
        else:
            failures += 1
            fail("Blackwell sm_120", f"arch list: {arch}")
        x = torch.randn(1024, 1024, device="cuda")
        y = (x @ x).sum().item()
        ok("matmul na GPU", f"{y:.1f}")
    else:
        failures += 1
        fail("torch CUDA niedostępne")

    import ctranslate2

    cuda_types = ctranslate2.get_supported_compute_types("cuda") if ctranslate2.get_cuda_device_count() else set()
    if cuda_types:
        ok("ctranslate2 CUDA (faster-whisper)", ", ".join(sorted(cuda_types)))
    else:
        failures += 1
        fail("ctranslate2 nie widzi CUDA")

    if WHISPER_MODEL_DIR.exists():
        import numpy as np
        from faster_whisper import WhisperModel

        model = WhisperModel(str(WHISPER_MODEL_DIR), device="cuda", compute_type="float16")
        silence = np.zeros(16000, dtype=np.float32)
        segments, _ = model.transcribe(silence, language="pl")
        list(segments)
        ok("whisper large-v3-turbo na CUDA", f"DLL: {len(studio.CUDA_DLL_DIRS)} katalogi nvidia-*")
        del model
    else:
        failures += 1
        fail("brak wag Whisper", str(WHISPER_MODEL_DIR))

    import opentimelineio as otio

    tl = otio.schema.Timeline(name="smoke")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="c1",
            source_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(0, 25), otio.opentime.RationalTime(50, 25)
            ),
        )
    )
    adapters = otio.adapters.available_adapter_names()
    ok("opentimelineio", f"{otio.__version__}, adaptery: {', '.join(adapters)}")

    import scenedetect, cv2, librosa, ollama  # noqa: F401

    ok("scenedetect / opencv / librosa", f"{scenedetect.__version__} / {cv2.__version__} / {librosa.__version__}")

    if FFMPEG.exists():
        out = subprocess.run([str(FFMPEG), "-version"], capture_output=True, text=True).stdout.splitlines()[0]
        enc = subprocess.run([str(FFMPEG), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        have = [e for e in ("h264_nvenc", "hevc_nvenc", "av1_nvenc", "prores_ks", "dnxhd") if e in enc]
        ok("ffmpeg", f"{out.split()[2]}, enkodery: {', '.join(have)}")
    else:
        failures += 1
        fail("ffmpeg", str(FFMPEG))

    try:
        models = [m.model for m in ollama.list().models]
        ok("ollama", ", ".join(models))
    except Exception as e:  # noqa: BLE001
        failures += 1
        fail("ollama", str(e))

    for var in ("OLLAMA_MODELS", "HF_HOME", "TORCH_HOME", "PIP_CACHE_DIR"):
        val = os.environ.get(var, "")
        (ok if val.startswith(str(ROOT)) else fail)(var, val or "nieustawione")
        if not val.startswith(str(ROOT)):
            failures += 1

    print("WYNIK:", "wszystko OK" if failures == 0 else f"{failures} problem(y)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

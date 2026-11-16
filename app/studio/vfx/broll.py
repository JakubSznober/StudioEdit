"""Generator B-Roll: Wan 2.2 TI2V 5B w ComfyUI (natywne węzły), prompt PL → EN przez lokalny LLM.

Szablon workflow (format API): vfx/workflows/wan22_ti2v_5b.json - parametry wypełniane w kodzie.
Długość w klatkach = 4k+1 przy 24 fps (wymóg VAE Wan 2.2). Wynik: MP4 H.264 (NVENC) w docelowym rozmiarze.
"""
from __future__ import annotations

import copy
import json
import random
import threading
from pathlib import Path
from typing import Callable

from .. import comfy, paths
from ..events import log
from ..media import ffmpeg
from . import models

WORKFLOW = Path(__file__).parent / "workflows" / "wan22_ti2v_5b.json"
FPS = 24
MAX_FRAMES = 121  # ~5 s - zalecany limit modelu 5B
NEGATIVE = ("blurry, low quality, worst quality, jpeg artifacts, static frame, still image, overexposed, "
            "deformed, distorted, disfigured, bad anatomy, extra fingers, extra limbs, watermark, text, logo, "
            "subtitles, cartoon, painting, oversaturated, flickering, messy background")
REQUIRED_NODES = ("UNETLoader", "CLIPLoader", "VAELoader", "ModelSamplingSD3", "CLIPTextEncode",
                  "Wan22ImageToVideoLatent", "KSampler", "VAEDecode")


def frames_for(duration: float) -> int:
    """Liczba klatek 4k+1 przy 24 fps (min 17, maks. 121)."""
    n = int(round(max(0.5, duration) * FPS / 4.0)) * 4 + 1
    return max(17, min(MAX_FRAMES, n))


def load_template() -> dict:
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    return {k: v for k, v in wf.items() if not k.startswith("_")}


def build_workflow(prompt_en: str, *, width: int, height: int, frames: int, seed: int | None = None,
                   steps: int = 24, cfg: float = 5.0, negative: str = NEGATIVE, save_node: str = "SaveWEBM") -> dict:
    wf = copy.deepcopy(load_template())
    wf["6"]["inputs"]["text"] = prompt_en
    wf["7"]["inputs"]["text"] = negative
    w = max(32, int(round(width / 32.0)) * 32)
    h = max(32, int(round(height / 32.0)) * 32)
    wf["55"]["inputs"].update({"width": w, "height": h, "length": int(frames)})
    wf["3"]["inputs"].update({"seed": int(seed if seed is not None else random.randint(0, 2**48)),
                              "steps": int(steps), "cfg": float(cfg)})
    if save_node == "SaveAnimatedWEBP":
        wf["58"] = {"class_type": "SaveAnimatedWEBP",
                    "inputs": {"images": ["8", 0], "filename_prefix": "studioedit/broll", "fps": float(FPS),
                               "lossless": False, "quality": 95, "method": "default"}}
    else:
        wf["58"]["inputs"]["fps"] = float(FPS)
    return wf


def translate_prompt(prompt: str, *, project_id: str | None = None) -> str:
    """Polski opis → angielski prompt dla Wan 2.2 (lokalny LLM). Bez LLM - zwraca oryginał."""
    from .. import llm

    sys_msg = ("You write prompts for the Wan 2.2 text-to-video model. Translate the user's Polish description to "
               "English and expand it into ONE vivid paragraph (40-80 words): main subject, action, environment, "
               "camera movement (e.g. slow dolly, handheld, drone), lens and lighting, realistic cinematic B-roll "
               "style. No on-screen text, no logos, no people talking to camera unless asked. Output only the prompt.")
    try:
        out = llm.chat([{"role": "system", "content": sys_msg}, {"role": "user", "content": prompt}],
                       role="director_fast", agent="vfx", temperature=0.5, think=False, label="Prompt B-Roll",
                       project_id=project_id, timeout=180)
        out = out.strip().strip('"').strip()
        if len(out) > 15:
            return out
    except Exception as e:
        log("vfx", f"Tłumaczenie promptu przez LLM niedostępne ({e}) - używam opisu oryginalnego",
            path="Generowanie B-Roll", level="warn", project_id=project_id)
    return prompt


def _check_ready() -> str:
    if not models.installed("wan22_ti2v_5b"):
        raise RuntimeError("Brak modelu Wan 2.2 TI2V 5B w ComfyUI (diffusion_models/text_encoders/vae) - "
                           "pobierz go w Ustawieniach → Modele (VFX: „Wan 2.2 TI2V 5B”)")
    try:
        info = comfy.object_info()
    except Exception as e:
        raise RuntimeError(f"ComfyUI nie działa lub nie odpowiada: {e}") from e
    missing = [n for n in REQUIRED_NODES if n not in info]
    if missing:
        raise RuntimeError(f"ComfyUI nie ma węzłów wymaganych przez Wan 2.2: {', '.join(missing)} - "
                           f"zaktualizuj ComfyUI")
    clip_types = (info.get("CLIPLoader", {}).get("input", {}).get("required", {}).get("type") or [[]])[0]
    if clip_types and "wan" not in clip_types:
        raise RuntimeError("CLIPLoader w ComfyUI nie obsługuje typu „wan” - zaktualizuj ComfyUI")
    return "SaveWEBM" if "SaveWEBM" in info else "SaveAnimatedWEBP"


def generate_broll(prompt: str, duration: float, out_path: Path, *, width: int = 832, height: int = 480,
                   progress: Callable[[float], None] | None = None, cancel: threading.Event | None = None,
                   steps: int | None = None, seed: int | None = None, translate: bool = True,
                   project_id: str | None = None) -> Path:
    """Generuje klip B-Roll (MP4, 24 fps, bez dźwięku). Rzuca RuntimeError po polsku, gdy brak modelu/ComfyUI."""
    out_path = Path(out_path)
    save_node = _check_ready()
    p = progress or (lambda x: None)
    p(0.02)
    en = translate_prompt(prompt, project_id=project_id) if translate else prompt
    log("vfx", f"Prompt Wan 2.2: {en}", path="Generowanie B-Roll", project_id=project_id)
    frames = frames_for(duration)
    p(0.05)
    wf = build_workflow(en, width=width, height=height, frames=frames, seed=seed, steps=steps or 24,
                        save_node=save_node)
    outs = comfy.run_workflow(wf, agent="vfx", label="Wan 2.2 B-Roll", cancel=cancel, timeout=3600,
                              progress=lambda x, _m="": p(0.05 + 0.85 * x), project_id=project_id)
    vids = [o for o in outs if o.suffix.lower() in (".webm", ".webp", ".mp4", ".mov", ".gif")]
    if not vids:
        raise RuntimeError("ComfyUI nie zwrócił pliku wideo z Wan 2.2")
    gen = vids[0]
    gen_len = (frames - 1) / FPS
    # dociągnięcie do żądanej długości: łagodne spowolnienie (maks. 1.6x) z interpolacją klatek
    stretch = min(1.6, max(1.0, float(duration) / max(0.1, gen_len)))
    vf = [f"scale={width}:{height}:flags=lanczos", "setsar=1"]
    if stretch > 1.01:
        vf.insert(0, f"setpts={stretch:.4f}*PTS")
        vf.insert(1, f"minterpolate=fps={FPS}:mi_mode=blend")
    vf.append(f"fps={FPS}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(["-i", str(gen), "-vf", ",".join(vf), *ffmpeg.video_codec_args("h264_nvenc", cq=16, fps=FPS),
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-an",
                "-movflags", "+faststart", str(out_path)],
               duration=gen_len * stretch, progress=lambda x: p(0.9 + 0.1 * x), cancel=cancel)
    p(1.0)
    return out_path


def comfy_output_dir() -> Path:
    return paths.CACHE / "comfy_out"

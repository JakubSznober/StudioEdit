"""Relighting „kinowy” bez sieci + lokalne przyciemnienie nieba - jako jeden filtr ffmpeg `geq`.

Klasyczny model: kierunkowe światło (left/right/top/bottom/front) jako gładka maska gradientowa +
opcjonalne doświetlenie twarzy (face_box z analizy ujęć, [cx, cy, w, h] znormalizowane). Działa w
YUV 4:2:0 10-bit (zakres TV): luminancja przez miękkie „soft light” (bez przepaleń), kolor światła przez
przesunięcie Cb/Cr proporcjonalne do maski. Niebo: gradient od góry kadru z selekcją niebieskich lub
jasnych pikseli (efekt filtra połówkowego / polaryzacyjnego).

Wyrażenia są w apostrofach (przecinki chronione na poziomie filtergraph) i nie używają
wplecione w sumę z mnożnikiem 0, więc łańcuch bezpiecznie wkleja się do -vf i -filter_complex.

Opcjonalnie: IC-Light w ComfyUI (relight_ai) - tylko gdy zainstalowane są odpowiednie węzły i modele.
"""
from __future__ import annotations

import random
from pathlib import Path

DIRECTIONS = ("left", "right", "top", "bottom", "front")
COLORS = {"warm": (-1.0, 1.0), "cool": (1.0, -0.8), "neutral": (0.0, 0.0),
          "ciepłe": (-1.0, 1.0), "zimne": (1.0, -0.8), "neutralne": (0.0, 0.0)}
_DIR_PL = {"lewa": "left", "lewo": "left", "prawa": "right", "prawo": "right", "góra": "top", "gora": "top",
           "dół": "bottom", "dol": "bottom", "przód": "front", "przod": "front", "przodu": "front"}


def _f(v: float) -> str:
    return f"{v:.5f}".rstrip("0").rstrip(".") if v != 0 else "0"


def normalize_relight(relight: dict | None) -> dict | None:
    if not relight or not isinstance(relight, dict):
        return None
    if relight.get("enabled") is False:
        return None
    d = str(relight.get("direction", "left")).lower()
    d = _DIR_PL.get(d, d)
    if d not in DIRECTIONS:
        d = "left"
    c = str(relight.get("color", "neutral")).lower()
    if c not in COLORS:
        c = "neutral"
    s = float(relight.get("strength", 0.5))
    s = max(0.0, min(1.0, s))
    if s <= 0.001:
        return None
    return {"direction": d, "color": c, "strength": s, "face": bool(relight.get("face", True)),
            "softness": max(0.2, min(2.0, float(relight.get("softness", 1.0))))}


def _light_terms(direction: str, s: float) -> str:
    """Zmiana światła D(X,Y) jako tanie wyrażenie liniowe/kwadratowe (strona oświetlona +, przeciwna -)."""
    up, down = 0.32 * s, 0.14 * s
    span = up + down
    if direction == "left":
        return f"({_f(up)}-{_f(span)}*X/W)"
    if direction == "right":
        return f"({_f(span)}*X/W-{_f(down)})"
    if direction == "top":
        return f"({_f(up)}-{_f(span)}*Y/H)"
    if direction == "bottom":
        return f"({_f(span)}*Y/H-{_f(down)})"
    # front: parabola wokół środka kadru
    up, down = 0.28 * s, 0.08 * s
    return (f"({_f(up + down)}*max(1-((X/W-0.5)*(X/W-0.5)+(Y/H-0.45)*(Y/H-0.45))*2.2,0)"
            f"-{_f(down)})")


def _face_expr(face_box: list[float] | None, s: float) -> str | None:
    if not face_box or len(face_box) < 4:
        return None
    cx, cy, w, h = (float(v) for v in face_box[:4])
    if w <= 0 or h <= 0:
        return None
    rx, ry = max(0.04, w * 1.6), max(0.04, h * 1.6)
    d2 = (f"((X/W-{_f(cx)})*(X/W-{_f(cx)})*{_f(1 / (rx * rx))}"
          f"+(Y/H-{_f(cy)})*(Y/H-{_f(cy)})*{_f(1 / (ry * ry))})")
    return f"{_f(0.22 * s)}*max(1-{d2},0)"


def light_expr(relight: dict | None, face_box: list[float] | None) -> str | None:
    """Wyrażenie D(X,Y) - zmiana światła ok. -0.15..+0.5 (0 = bez zmian), w znormalizowanych X/W, Y/H."""
    r = normalize_relight(relight)
    if r is None:
        return None
    s = r["strength"]
    expr = _light_terms(r["direction"], s)
    fe = _face_expr(face_box, s) if r["face"] else None
    if fe:
        expr = f"({expr}+{fe})"
    return expr


def sky_expr(sky_darken: float, horizon: float = 0.5) -> str | None:
    """Współczynnik przyciemnienia nieba (płaszczyzna luminancji; używa ld(0) = znormalizowana luminancja).

    Gradient od góry kadru do linii horyzontu, ważony jasnością piksela (niebo to zwykle najjaśniejszy
    obszar górnej części kadru) - ciemne obiekty przy górnej krawędzi prawie nie są przyciemniane."""
    s = max(0.0, min(1.0, float(sky_darken or 0)))
    if s <= 0.001:
        return None
    hz = max(0.2, min(0.8, horizon))
    t = f"max(1-Y*{_f(1 / hz)}/H,0)"
    return f"({_f(0.5 * s)}*{t}*{t}*clip(ld(0)*3-1.2,0,1))"


def geq_filter(relight: dict | None = None, sky_darken: float = 0.0, *, face_box: list[float] | None = None,
               horizon: float = 0.5) -> str:
    """Filtr geq (wejście: yuv420p10le, zakres TV) albo "" gdy nic do zrobienia.

    Wyrażenia są celowo krótkie (koszt geq rośnie z liczbą węzłów wyrażenia na piksel)."""
    L = light_expr(relight, face_box)
    S = sky_expr(sky_darken, horizon)
    if L is None and S is None:
        return ""
    # ld(0) = y (0..1), ld(1) = D (zmiana światła)
    pre = "st(0,(lum(X,Y)-64)*0.0011416)*0"
    if S:
        pre += f"+st(0,ld(0)*(1-{S}))*0"
    if L:
        pre += f"+st(1,{L})*0"
        out = "(ld(0)+if(gt(ld(1),0),2*ld(1)*ld(0)*(1-ld(0)),ld(1)*ld(0)))"
    else:
        out = "ld(0)"
    lum = f"{pre}+clip(64+876*{out},64,940)"
    cb, cr = "cb(X,Y)", "cr(X,Y)"
    r = normalize_relight(relight)
    if L and r and r["color"] not in ("neutral", "neutralne"):
        kb, kr = COLORS[r["color"]]
        amt = 90.0  # jednostki 10-bit na pełne światło
        cb = f"clip(cb(X,Y)+{_f(kb * amt)}*max({L},0),64,960)"
        cr = f"clip(cr(X,Y)+{_f(kr * amt)}*max({L},0),64,960)"
    return f"geq=lum='{lum}':cb='{cb}':cr='{cr}':interpolation=nearest"


# ---------------------------------------------------------------------------------------------
# IC-Light przez ComfyUI (opcjonalnie)
# ---------------------------------------------------------------------------------------------
IC_LIGHT_NODES = ("LoadAndApplyICLightUnet", "ICLightConditioning")


def ai_available() -> bool:
    """True, gdy ComfyUI ma węzły IC-Light, model IC-Light i checkpoint SD1.5."""
    from .. import comfy

    try:
        if not comfy.has_nodes(*IC_LIGHT_NODES):
            return False
        unets = [u for u in comfy.models_in("unet") + comfy.models_in("diffusion_models") if "iclight" in u.lower()]
        ckpts = comfy.models_in("checkpoints")
        return bool(unets and ckpts)
    except Exception:
        return False


def relight_ai(image_path: str | Path, out_path: str | Path, *, direction: str = "left", color: str = "warm",
               prompt: str = "", strength: float = 0.6, cancel=None, progress=None) -> Path:
    """Relight pojedynczej klatki przez IC-Light (ComfyUI). Rzuca RuntimeError, gdy IC-Light niedostępny."""
    from .. import comfy

    if not ai_available():
        raise RuntimeError("IC-Light niedostępny w ComfyUI (brak węzłów ComfyUI-IC-Light lub modeli) - "
                           "użyj klasycznego relightingu")
    unet = next(u for u in comfy.models_in("unet") + comfy.models_in("diffusion_models") if "iclight" in u.lower()
                and "fc" in u.lower()) if any("fc" in u.lower() for u in comfy.models_in("unet")) else \
        next(u for u in comfy.models_in("unet") + comfy.models_in("diffusion_models") if "iclight" in u.lower())
    ckpt = next((c for c in comfy.models_in("checkpoints") if "1.5" in c or "sd15" in c.lower() or "v1-5" in c),
                comfy.models_in("checkpoints")[0])
    light = {"left": "light from the left side", "right": "light from the right side", "top": "light from above",
             "bottom": "light from below", "front": "soft frontal light"}.get(direction, "soft light")
    tone = {"warm": "warm golden sunlight", "cool": "cool blue moonlight"}.get(color, "neutral soft daylight")
    pos = f"{prompt + ', ' if prompt else ''}cinematic lighting, {tone}, {light}, high detail, photo"
    img = comfy.upload_image(image_path)
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "LoadAndApplyICLightUnet", "inputs": {"model": ["1", 0], "model_path": unet}},
        "3": {"class_type": "LoadImage", "inputs": {"image": img}},
        "4": {"class_type": "VAEEncode", "inputs": {"pixels": ["3", 0], "vae": ["1", 2]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": ["1", 1]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "lowres, bad anatomy, worst quality", "clip": ["1", 1]}},
        "7": {"class_type": "ICLightConditioning", "inputs": {"positive": ["5", 0], "negative": ["6", 0],
                                                              "vae": ["1", 2], "foreground": ["4", 0],
                                                              "multiplier": 0.18215}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["7", 0], "negative": ["7", 1],
                                                   "latent_image": ["4", 0], "seed": random.randint(0, 2**31),
                                                   "steps": 25, "cfg": 2.0, "sampler_name": "dpmpp_2m",
                                                   "scheduler": "karras",
                                                   "denoise": max(0.3, min(1.0, 0.4 + 0.5 * strength))}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "studioedit/relight"}},
    }
    outs = comfy.run_workflow(wf, agent="colorist", label="IC-Light relight", cancel=cancel,
                              progress=progress)
    imgs = [o for o in outs if o.suffix.lower() in (".png", ".jpg", ".webp")]
    if not imgs:
        raise RuntimeError("IC-Light nie zwrócił obrazu")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(imgs[0].read_bytes())
    return out_path

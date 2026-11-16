"""Kompilator gradu do LUT 3D (.cube): CST LOG→Rec.709, ekspozycja, WB, dopasowanie ujęć, ratowanie
prześwietleń, kontrast, saturacja, look kreatywny i ochrona odcieni skóry - wszystko w jednym LUT.

Model przetwarzania (na każdym węźle siatki LUT, float64):
  kod wejściowy ─┬─ LOG/HLG/PQ: OETF⁻¹ → liniowe sceniczne (gamut kamery) → macierz → Rec.709 liniowe
                 └─ Rec.709/flat: EOTF (γ2.4) → odwrotny tone-map → „liniowe sceniczne” (tożsamość przy zerowym gradzie)
  → balans bieli (temperatura/tint + auto WB z analizy) → ekspozycja (EV ręczne + auto + ratowanie świateł)
  → miękkie kolano sceniczne (highlight roll-off) → filmowy tone-map (ACES-fit) z mieszanką „ratio-preserving”
    (odzysk koloru w światłach) → ochrona przepalonych (bez szarych plam) → γ1/2.4
  → podniesienie cieni → kontrast (S wokół pivotu) → saturacja z ochroną skóry → dopasowanie Lab do referencji
  → look (siła look_strength) → ochrona skóry (chroma z wersji bez looku + dociągnięcie do linii skóry).
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..project.model import Grade
from . import looks as _looks
from .looks import LUMA, apply_look, is_monochrome, normalize_look, pivot_curve, saturate, smoothstep

looks = _looks.looks  # kontrakt ARCHITECTURE.md: lut.looks()

LOG_PROFILES = ("slog3", "clog3", "dlog", "vlog", "hlg", "pq")
PROFILES = ("rec709", "flat", *LOG_PROFILES)
PROFILE_NAMES = {"rec709": "Rec.709", "flat": "płaski profil", "slog3": "S-Log3/S-Gamut3.Cine",
                 "clog3": "Canon Log 3/Cinema Gamut", "dlog": "DJI D-Log/D-Gamut", "vlog": "Panasonic V-Log/V-Gamut",
                 "hlg": "HLG (BT.2100)", "pq": "PQ HDR10 (BT.2100)"}

# ---------------------------------------------------------------------------------------------
# Gamuty → Rec.709 (macierze wyliczane z chromatyczności, biel D65)
# ---------------------------------------------------------------------------------------------
_D65 = (0.3127, 0.3290)
_PRIMARIES = {
    "rec709": ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060)),
    "rec2020": ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)),
    "sgamut3cine": ((0.766, 0.275), (0.225, 0.800), (0.089, -0.087)),
    "cinemagamut": ((0.740, 0.270), (0.170, 1.140), (0.080, -0.100)),
    "dgamut": ((0.710, 0.310), (0.210, 0.880), (0.090, -0.080)),
    "vgamut": ((0.730, 0.280), (0.165, 0.840), (0.100, -0.030)),
}
_PROFILE_GAMUT = {"slog3": "sgamut3cine", "clog3": "cinemagamut", "dlog": "dgamut", "vlog": "vgamut",
                  "hlg": "rec2020", "pq": "rec2020"}


def _rgb_to_xyz(prims, white=_D65) -> np.ndarray:
    xyz = np.array([[x / y, 1.0, (1 - x - y) / y] for x, y in prims]).T
    wx, wy = white
    w = np.array([wx / wy, 1.0, (1 - wx - wy) / wy])
    s = np.linalg.solve(xyz, w)
    return xyz * s


def gamut_matrix(src: str, dst: str = "rec709") -> np.ndarray:
    return np.linalg.inv(_rgb_to_xyz(_PRIMARIES[dst])) @ _rgb_to_xyz(_PRIMARIES[src])


# ---------------------------------------------------------------------------------------------
# Krzywe transferu: kod → liniowe sceniczne (szarość 18% = 0.18)
# ---------------------------------------------------------------------------------------------
def _to_cv(x: np.ndarray, full_range: bool) -> np.ndarray:
    """Wejście LUT (ffmpeg rozciąga zakres TV do 0..1 = IRE) → kod 10-bit/1023 (dziedzina wzorów Sony/Pana/DJI)."""
    return x if full_range else (x * 876.0 + 64.0) / 1023.0


def _to_ire(x: np.ndarray, full_range: bool) -> np.ndarray:
    return (x * 1023.0 - 64.0) / 876.0 if full_range else x


def decode_slog3(cv: np.ndarray) -> np.ndarray:
    cut = 171.2102946929 / 1023.0
    return np.where(cv >= cut, np.power(10.0, (cv * 1023.0 - 420.0) / 261.5) * 0.19 - 0.01,
                    (cv * 1023.0 - 95.0) * 0.01125 / (171.2102946929 - 95.0))


def decode_clog3(ire: np.ndarray) -> np.ndarray:
    a = np.where(ire < 0.097465473, -(np.power(10.0, (0.12783901 - ire) / 0.36726845) - 1.0) / 14.98325,
                 np.where(ire <= 0.15277891, (ire - 0.12512219) / 1.9754798,
                          (np.power(10.0, (ire - 0.12240537) / 0.36726845) - 1.0) / 14.98325))
    return a * 0.9


def decode_vlog(cv: np.ndarray) -> np.ndarray:
    return np.where(cv < 0.181, (cv - 0.125) / 5.6, np.power(10.0, (cv - 0.598206) / 0.241514) - 0.00873)


def decode_dlog(cv: np.ndarray) -> np.ndarray:
    return np.where(cv <= 0.14, (cv - 0.0929) / 6.025, (np.power(10.0, 3.89616 * cv - 2.27752) - 0.0108) / 0.9892)


def decode_hlg(e: np.ndarray) -> np.ndarray:
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    e = np.clip(e, 0.0, 1.0)
    lin = np.where(e <= 0.5, e * e / 3.0, (np.exp((e - c) / a) + b) / 12.0)
    return lin / 0.2647  # biel referencyjna HLG 75% → 1.0 (szarość 18% → 0.18)


def decode_pq(e: np.ndarray) -> np.ndarray:
    m1, m2, c1, c2, c3 = 0.1593017578125, 78.84375, 0.8359375, 18.8515625, 18.6875
    ep = np.power(np.clip(e, 0.0, 1.0), 1.0 / m2)
    nits = np.power(np.maximum(ep - c1, 0.0) / (c2 - c3 * ep), 1.0 / m1) * 10000.0
    return nits / 203.0  # biel referencyjna 203 nit → 1.0


_DECODERS = {"slog3": (decode_slog3, "cv"), "clog3": (decode_clog3, "ire"), "vlog": (decode_vlog, "cv"),
             "dlog": (decode_dlog, "cv"), "hlg": (decode_hlg, "raw"), "pq": (decode_pq, "raw")}

# ---------------------------------------------------------------------------------------------
# Filmowy tone-map (ACES fit Narkowicza ze skalą 0.6 → szarość 0.18 ≈ kod 0.44)
# ---------------------------------------------------------------------------------------------
_TM_A, _TM_B, _TM_C, _TM_D, _TM_E, _TM_S = 2.51, 0.03, 2.43, 0.59, 0.14, 0.6


def tonemap(s: np.ndarray) -> np.ndarray:
    x = np.maximum(s, 0.0) * _TM_S
    return np.clip(x * (_TM_A * x + _TM_B) / (x * (_TM_C * x + _TM_D) + _TM_E), 0.0, 1.0)


def tonemap_inv(d: np.ndarray) -> np.ndarray:
    d = np.clip(d, 0.0, 1.0)
    a = _TM_A - _TM_C * d
    b = _TM_B - _TM_D * d
    c = -_TM_E * d
    x = (-b + np.sqrt(np.maximum(b * b - 4 * a * c, 0.0))) / (2 * a)
    return x / _TM_S


def _encode(d: np.ndarray) -> np.ndarray:
    return np.power(np.clip(d, 0.0, 1.0), 1.0 / 2.4)


def _decode709(c: np.ndarray) -> np.ndarray:
    return np.power(np.clip(c, 0.0, 1.0), 2.4)


def _gamut_compress(rgb: np.ndarray) -> np.ndarray:
    """Ujemne składowe (poza gamutem Rec.709) → przesunięcie w stronę luminancji z zachowaniem odcienia."""
    y = np.maximum(rgb @ LUMA, 0.0)[:, None]
    mn = rgb.min(axis=1, keepdims=True)
    t = np.where(mn < 0, y / np.maximum(y - mn, 1e-9), 1.0)
    return y + (rgb - y) * np.clip(t, 0.0, 1.0)


# ---------------------------------------------------------------------------------------------
# Plan gradu (wszystkie wartości pochodne liczone raz)
# ---------------------------------------------------------------------------------------------
def _profile(grade: Grade, stats: dict) -> str:
    p = (grade.input_profile or "rec709").lower().replace("-", "").replace("_", "")
    p = {"slog": "slog3", "clog": "clog3", "canonlog3": "clog3", "vlog3": "vlog", "dlogm": "dlog",
         "hdr10": "pq", "": "rec709", "709": "rec709", "log": "flat"}.get(p, p)
    return p if p in PROFILES else "rec709"


def _full_range(stats: dict) -> bool:
    return str(stats.get("color_range", "tv")).lower() in ("pc", "full", "jpeg")


def _to_scene(rgb: np.ndarray, plan: dict) -> np.ndarray:
    """Kod wejściowy → liniowe sceniczne Rec.709."""
    prof = plan["profile"]
    if prof in _DECODERS:
        fn, dom = _DECODERS[prof]
        fr = plan["full_range"]
        x = _to_cv(rgb, fr) if dom == "cv" else _to_ire(rgb, fr) if dom == "ire" else rgb
        lin = fn(x)
        lin = lin @ plan["gamut"].T
        return _gamut_compress(lin)
    x = rgb
    if prof == "flat":
        lo, hi = plan["flat_lo"], plan["flat_hi"]
        x = np.clip((x - lo) / max(1e-6, hi - lo), 0.0, 1.0) * 0.94 + 0.02
    return tonemap_inv(_decode709(x))


def _wb_gains(grade: Grade, stats: dict, plan: dict) -> np.ndarray:
    t, tint = float(grade.temperature), float(grade.tint)
    g = np.array([2.0 ** (0.32 * t), 2.0 ** (-0.18 * tint), 2.0 ** (-0.32 * t)])
    auto = float(grade.match.get("auto_wb", 0.6) if isinstance(grade.match, dict) else 0.6)
    wb = stats.get("wb_rgb")
    if auto > 0 and wb and len(wb) == 3 and min(wb) > 0.02:
        lin = _to_scene(np.array([wb], dtype=np.float64), plan)[0]
        lin = np.maximum(lin, 1e-4)
        y = float(lin @ LUMA)
        corr = y / lin
        corr = np.clip(corr, 0.78, 1.28) ** auto
        # bez korekty, gdy dominanta jest pomijalna (< ~2%)
        if np.max(np.abs(np.log(corr))) > 0.02:
            g = g * corr
    return g / float(g @ LUMA)


def _scene_knee(s: np.ndarray, knee_s: float, c: float) -> np.ndarray:
    """Miękkie kolano w dziedzinie scenicznej (kompresja powyżej knee_s, monotoniczne)."""
    if c <= 0:
        return s
    over = np.maximum(s - knee_s, 0.0)
    return np.where(s > knee_s, knee_s + over / (1.0 + c * over / max(knee_s, 1e-6)), s)


def _base(rgb_in: np.ndarray, plan: dict, ev: float) -> np.ndarray:
    """Korekcja techniczna (bez dopasowania, looku i skóry) dla danego całkowitego EV."""
    s = _to_scene(rgb_in, plan)
    s = s * plan["wb"] * (2.0 ** ev)
    s = _scene_knee(s, plan["knee_s"], plan["knee_c"])
    # tone-map: mieszanka per-kanał (filmowa) i zachowująca proporcje (kolor w światłach)
    d_pc = tonemap(s)
    ys = np.maximum(s @ LUMA, 1e-9)
    yd = tonemap(ys)
    d_rp = s * (yd / ys)[:, None]
    mx = d_rp.max(axis=1)
    over = mx > 1.0
    if np.any(over):
        k = ((1.0 - yd) / np.maximum(mx - yd, 1e-9))[:, None]
        d_rp = np.where(over[:, None], yd[:, None] + (d_rp - yd[:, None]) * k, d_rp)
    m = plan["tm_blend"]
    d = d_pc * (1 - m) + d_rp * m if m > 0 else d_pc
    c = _encode(d)
    # ochrona przepalonych: obszary przepalone w źródle zostają prawie białe (bez szarych plam)
    if plan["clip_protect"] > 0:
        y_in = rgb_in @ LUMA
        w = (smoothstep(0.90, 0.995, y_in) * plan["clip_protect"])[:, None]
        c = c * (1 - w) + np.maximum(c, rgb_in * plan["clip_white"]) * w
    # podniesienie cieni
    if plan["lift"] > 0:
        c = c + plan["lift"] * 0.10 * np.power(np.clip(1.0 - c, 0, 1), 4)
    # kontrast
    if abs(plan["contrast_k"] - 1.0) > 1e-4:
        c = pivot_curve(c, plan["contrast_k"], 0.435)
    return np.clip(c, 0.0, 1.0)


def skin_weight(rgb: np.ndarray) -> np.ndarray:
    """Waga 0..1 przynależności do odcieni skóry (HSV ~10-50°, oś ~123° wektoroskopu)."""
    h, s, v = _looks.hsv(np.clip(rgb, 0, 1))
    hd = h * 360.0
    dist = ((hd - 25.0 + 180.0) % 360.0) - 180.0
    wh = np.exp(-(dist / 15.0) ** 2)
    ws = smoothstep(0.08, 0.2, s) * (1.0 - smoothstep(0.62, 0.85, s))
    wv = smoothstep(0.1, 0.28, v)
    return wh * ws * wv


def _to_lab(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(rgb, 0, 1).astype(np.float32).reshape(-1, 1, 3), cv2.COLOR_RGB2Lab).reshape(-1, 3) \
        .astype(np.float64)


def _from_lab(lab: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(lab.astype(np.float32).reshape(-1, 1, 3), cv2.COLOR_Lab2RGB).reshape(-1, 3) \
        .astype(np.float64)


def _ref_from_grade(grade: Grade) -> dict | None:
    m = grade.match if isinstance(grade.match, dict) else {}
    ref = m.get("ref")
    if isinstance(ref, dict) and ref.get("lab_mean") and ref.get("lab_std"):
        return ref
    return None


def plan_grade(grade: Grade, stats: dict, ref_stats: dict | None = None) -> dict:
    """Wylicza wszystkie parametry pochodne gradu (używane przez kompilator i logi kolorysty)."""
    stats = stats or {}
    prof = _profile(grade, stats)
    hr = float(np.clip(grade.highlight_recovery, 0.0, 1.0))
    m = grade.match if isinstance(grade.match, dict) else {}
    knee_code = float(m.get("knee", 0.80 - 0.06 * hr))
    plan: dict[str, Any] = {
        "profile": prof, "full_range": _full_range(stats),
        "gamut": gamut_matrix(_PROFILE_GAMUT[prof]) if prof in _PROFILE_GAMUT else np.eye(3),
        "flat_lo": float(stats.get("luma_p01", 0.06)) * 0.8,
        "flat_hi": min(1.0, float(stats.get("luma_p99", 0.9)) + (1 - float(stats.get("luma_p99", 0.9))) * 0.3),
        "hr": hr, "knee": knee_code,
        "knee_s": float(tonemap_inv(np.array([knee_code ** 2.4]))[0]),
        "knee_c": 2.2 * hr,
        "tm_blend": (0.3 if prof in LOG_PROFILES else 0.0) + 0.5 * hr,
        "clip_protect": 1.0 if prof in ("rec709", "flat") else 0.0,
        "clip_white": 0.965,
        "lift": float(np.clip(grade.shadow_lift, 0.0, 1.0)),
        "contrast_k": float(np.clip(1.0 + 0.5 * float(grade.contrast) + (0.18 if prof == "flat" else 0.0)
                                    + (0.08 if prof in LOG_PROFILES else 0.0), 0.4, 2.0)),
        "saturation": float(max(0.0, grade.saturation)) * (1.2 if prof == "flat" else 1.0)
                      * (1.08 if prof in LOG_PROFILES else 1.0),
        "skin_protect": float(np.clip(grade.skin_protect, 0.0, 1.0)),
        "look": normalize_look(grade.look), "look_strength": float(np.clip(grade.look_strength, 0.0, 1.0)),
        "hl_ev": -0.75 * hr,
    }
    plan["wb"] = _wb_gains(grade, stats, plan)
    # auto ekspozycja: dobór EV tak, by średnia luminancja wyniku (z histogramu) trafiła w cel
    night = bool(stats.get("night")) or (float(stats.get("luma_mean", 0.4)) < 0.2
                                         and float(stats.get("luma_p99", 1)) < 0.75)
    target = float(m.get("target_luma", 0.30 if night else 0.42))
    lm = float(stats.get("luma_mean", 0.42))
    if lm > 0.6 and not night and "target_luma" not in m:
        target = 0.5  # high-key (śnieg, biel) - nie ściemniaj na siłę
    target -= 0.04 * hr
    plan["target_luma"] = target
    auto_on = bool(m.get("auto_exposure", True))
    hist = np.asarray(stats.get("hist") or [], dtype=np.float64)
    auto_ev = 0.0
    if auto_on and hist.size >= 4 and hist.sum() > 0:
        centers = (np.arange(hist.size) + 0.5) / hist.size
        grays = np.repeat(centers[:, None], 3, axis=1)
        w = hist / hist.sum()

        def mean_out(ev: float) -> float:
            return float(((_base(grays, plan, ev) @ LUMA) * w).sum())

        base_ev = plan["hl_ev"]  # auto EV liczone bez ręcznej ekspozycji (ta dodawana na końcu)
        # pasmo tolerancji: poprawnie naświetlone ujęcia zostają, korekta tylko do krawędzi pasma
        band = float(m.get("target_band", 0.0 if "target_luma" in m else 0.06))
        cur = mean_out(base_ev)
        plan["luma_before"] = cur
        if target - band <= cur <= target + band * 1.3:
            sol = 0.0
            tgt = cur
        else:
            tgt = target - band if cur < target - band else target + band * 1.3
            lo, hi = -3.0, 3.0
            if mean_out(base_ev + lo) > tgt:
                sol = lo
            elif mean_out(base_ev + hi) < tgt:
                sol = hi
            else:
                for _ in range(32):
                    mid = (lo + hi) / 2
                    if mean_out(base_ev + mid) < tgt:
                        lo = mid
                    else:
                        hi = mid
                sol = (lo + hi) / 2
        plan["target_luma"] = tgt
        strength = float(m.get("auto_exposure_strength", 0.85))
        auto_ev = float(np.clip(sol * strength, -1.5, 1.8))
        if hr > 0:  # przy ratowaniu świateł nie rozjaśniaj z powrotem ponad poziom wejścia
            auto_ev = min(auto_ev, 0.25)
    plan["auto_ev"] = auto_ev
    plan["total_ev"] = float(grade.exposure) + plan["hl_ev"] + auto_ev
    # dopasowanie do referencji (Lab)
    ref = ref_stats or _ref_from_grade(grade)
    plan["match"] = None
    if ref and stats.get("lab_mean") and stats.get("lab_std"):
        st = float(m.get("strength", 0.6))
        sm, ss = np.array(stats["lab_mean"], float), np.maximum(np.array(stats["lab_std"], float), 1e-3)
        rm, rs = np.array(ref["lab_mean"], float), np.maximum(np.array(ref["lab_std"], float), 1e-3)
        plan["match"] = {
            "strength": st,
            "dL": float(np.clip((rm[0] - sm[0]) * st * 0.5, -8, 8)),
            "da": float(np.clip((rm[1] - sm[1]) * st, -6, 6)),
            "db": float(np.clip((rm[2] - sm[2]) * st, -6, 6)),
            "rL": float(np.clip((rs[0] / ss[0]) ** st, 0.85, 1.18)),
            "rab": float(np.clip(((rs[1] / ss[1]) * (rs[2] / ss[2])) ** (0.5 * st), 0.8, 1.25)),
        }
    if plan["profile"] in ("rec709", "flat") and plan["total_ev"] < 0:
        plan["clip_white"] = 0.965
    return plan


def apply_grade_rgb(rgb: np.ndarray, grade: Grade, stats: dict, ref_stats: dict | None = None,
                    plan: dict | None = None) -> np.ndarray:
    """Pełny grad na tablicy RGB (N,3) lub obrazie (H,W,3) float 0..1 - to samo, co wypala LUT."""
    shape = rgb.shape
    x = np.asarray(rgb, dtype=np.float64).reshape(-1, 3)
    plan = plan or plan_grade(grade, stats, ref_stats)
    base = _base(x, plan, plan["total_ev"])
    # saturacja z ochroną skóry
    skin = skin_weight(base)
    sp = plan["skin_protect"]
    sat = plan["saturation"]
    if abs(sat - 1.0) > 1e-4:
        s_eff = 1.0 + (sat - 1.0) * (1.0 - 0.6 * skin * sp)
        base = np.clip(saturate(base, s_eff), 0.0, 1.0)
    # dopasowanie do ujęcia referencyjnego
    mt = plan["match"]
    if mt:
        lab = _to_lab(base)
        L = lab[:, 0] / 100.0
        wmid = 4.0 * L * (1.0 - L)
        lab[:, 0] = np.clip(50.0 + (lab[:, 0] - 50.0) * mt["rL"] + mt["dL"] * wmid, 0, 100)
        lab[:, 1] = lab[:, 1] * mt["rab"] + mt["da"] * wmid
        lab[:, 2] = lab[:, 2] * mt["rab"] + mt["db"] * wmid
        base = np.clip(_from_lab(lab), 0.0, 1.0)
    out = base
    lk = plan["look"]
    if lk and plan["look_strength"] > 0:
        looked = np.clip(apply_look(base, lk), 0.0, 1.0)
        out = base + (looked - base) * plan["look_strength"]
        if sp > 0 and not is_monochrome(lk):
            # ochrona skóry: chroma skóry z wersji bez looku, delikatnie dociągnięta do linii skóry
            w = skin_weight(base) * sp
            if np.any(w > 1e-3):
                lab_o = _to_lab(out)
                lab_b = _to_lab(base)
                hue_b = np.arctan2(lab_b[:, 2], lab_b[:, 1])
                chroma_b = np.hypot(lab_b[:, 1], lab_b[:, 2])
                line = math.radians(58.0)  # linia skóry w Lab (~123° na wektoroskopie YUV)
                dh = np.angle(np.exp(1j * (line - hue_b)))
                hue_t = hue_b + dh * 0.3
                a_t, b_t = chroma_b * np.cos(hue_t), chroma_b * np.sin(hue_t)
                lab_o[:, 1] = lab_o[:, 1] * (1 - w) + a_t * w
                lab_o[:, 2] = lab_o[:, 2] * (1 - w) + b_t * w
                out = np.clip(_from_lab(lab_o), 0.0, 1.0)
    return np.clip(out, 0.0, 1.0).reshape(shape)


# ---------------------------------------------------------------------------------------------
# Zapis .cube
# ---------------------------------------------------------------------------------------------
def lattice(size: int) -> np.ndarray:
    """Węzły siatki w kolejności .cube (R zmienia się najszybciej)."""
    idx = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(idx, idx, idx, indexing="ij")
    return np.stack([r, g, b], axis=-1).reshape(-1, 3)


def write_cube(table: np.ndarray, size: int, out_path: Path, title: str = "StudioEdit",
               comments: list[str] | None = None) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".cube.tmp")
    head = [f'TITLE "{title}"', *[f"# {c}" for c in (comments or [])], f"LUT_3D_SIZE {size}",
            "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    with open(tmp, "w", encoding="ascii", errors="replace", newline="\n") as f:
        f.write("\n".join(head) + "\n")
        np.savetxt(f, table.astype(np.float32), fmt="%.6f")
    tmp.replace(out_path)
    return out_path


def grade_hash(grade: Grade, stats: dict, ref_stats: dict | None = None, size: int = 33) -> str:
    g = grade.model_dump(mode="json")
    g.pop("lut", None)
    g.pop("relight", None)
    g.pop("sky_darken", None)
    key = {k: stats.get(k) for k in ("wb_rgb", "hist", "luma_mean", "luma_p01", "luma_p99", "lab_mean", "lab_std",
                                     "color_range", "night")}
    payload = json.dumps({"g": g, "s": key, "r": ref_stats and {k: ref_stats.get(k) for k in ("lab_mean", "lab_std")},
                          "n": size, "v": 3}, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def compile_grade_lut(grade: Grade, stats: dict, out_path: Path, size: int = 33,
                      ref_stats: dict | None = None) -> Path:
    """Kompiluje Grade do pliku .cube (LUT 3D, float32). Zwraca ścieżkę."""
    t0 = time.time()
    size = int(max(2, min(129, size)))
    plan = plan_grade(grade, stats or {}, ref_stats)
    grid = lattice(size)
    out = apply_grade_rgb(grid, grade, stats or {}, ref_stats, plan=plan)
    comments = [
        f"profil: {plan['profile']}  EV: {plan['total_ev']:+.2f} (auto {plan['auto_ev']:+.2f}, światła "
        f"{plan['hl_ev']:+.2f})  cel luminancji: {plan['target_luma']:.2f}",
        f"WB: {' '.join(f'{g:.3f}' for g in plan['wb'])}  kolano: {plan['knee']:.2f}  kontrast k: "
        f"{plan['contrast_k']:.2f}  saturacja: {plan['saturation']:.2f}",
        f"look: {plan['look'] or '-'} ({plan['look_strength']:.2f})  ochrona skóry: {plan['skin_protect']:.2f}  "
        f"dopasowanie: {'tak' if plan['match'] else 'nie'}  czas: {time.time() - t0:.2f}s",
    ]
    return write_cube(out, size, Path(out_path), title="StudioEdit grade", comments=comments)


def describe_plan(plan: dict) -> str:
    """Krótki opis decyzji gradu po polsku (do logów)."""
    parts = [PROFILE_NAMES.get(plan["profile"], plan["profile"])]
    parts.append(f"EV {plan['total_ev']:+.2f}".replace(".", ","))
    if plan["hr"] > 0:
        parts.append(f"ratowanie świateł {plan['hr']:.2f}, kolano {plan['knee']:.2f}".replace(".", ","))
    wb = plan["wb"]
    if abs(wb[0] - wb[2]) > 0.03:
        parts.append("ociepla" if wb[0] > wb[2] else "ochładza")
    if plan["look"]:
        parts.append(f"look {plan['look']} {int(plan['look_strength'] * 100)}%")
    if plan["match"]:
        parts.append("dopasowanie do referencji")
    return ", ".join(parts)

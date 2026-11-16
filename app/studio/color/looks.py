"""Looki kreatywne: wbudowane (funkcje na RGB w kodowaniu Rec.709) + pliki .cube z C:\\StudioEdit\\LUTs.

Wszystkie funkcje operują na tablicach float (N, 3) w zakresie 0..1 (wartości kodowe po korekcji
technicznej) i są gładkie/monotoniczne w luminancji, więc nadają się do wypalenia w LUT 3D.
"""
from __future__ import annotations

import functools
from pathlib import Path

import numpy as np

from .. import paths

LUMA = np.array([0.2126, 0.7152, 0.0722])

BUILTIN: list[dict] = [
    {"id": "teal_orange", "name": "Teal & Orange (kinowy)",
     "description": "Chłodne, morskie cienie i ciepłe światła - klasyczny blockbuster, skóra wyraźnie odcięta od tła."},
    {"id": "warm_film", "name": "Ciepły film",
     "description": "Ciepła taśma: podniesiona czerń, miękkie światła, lekko stonowane kolory."},
    {"id": "clean_doc", "name": "Czysty dokument",
     "description": "Neutralny, naturalny obraz z delikatnym kontrastem - wierne kolory do wywiadów i reportażu."},
    {"id": "moody", "name": "Nastrojowy (moody)",
     "description": "Ciemniej, chłodne cienie, przygaszone kolory i stłumione światła - klimat dramatu."},
    {"id": "bw_film", "name": "Czarno-biały film",
     "description": "Monochrom z filtrem czerwonym (ciemniejsze niebo), mocny kontrast i miękka czerń."},
    {"id": "vibrant_social", "name": "Żywy (social media)",
     "description": "Soczyste kolory (vibrance), jaśniej i bardziej kontrastowo - pod telefon i rolki."},
    {"id": "pastel", "name": "Pastelowy",
     "description": "Jasny, miękki obraz o niskim kontraście i delikatnych, pudrowych barwach."},
]
BUILTIN_IDS = {b["id"] for b in BUILTIN}
MONOCHROME = {"bw_film", "bw"}
ALIASES = {"bw": "bw_film", "teal-orange": "teal_orange", "tealorange": "teal_orange", "film": "warm_film",
           "doc": "clean_doc", "natural": "clean_doc", "vibrant": "vibrant_social", "social": "vibrant_social"}


def luts_dir() -> Path:
    return paths.LUTS


def looks() -> list[dict]:
    """[{"id","name","description","builtin"}] - wbudowane + *.cube z katalogu LUTs."""
    out = [{**b, "builtin": True} for b in BUILTIN]
    d = luts_dir()
    if d.exists():
        for f in sorted(d.glob("*.cube")):
            out.append({"id": f.name, "name": f.stem.replace("_", " "), "builtin": False,
                        "description": f"Plik LUT użytkownika ({f.name})"})
    return out


def normalize_look(look: str | None) -> str | None:
    if not look:
        return None
    s = str(look).strip()
    if s.lower() in ("none", "brak", "off", ""):
        return None
    key = s.lower().replace(" ", "_")
    key = ALIASES.get(key, key)
    if key in BUILTIN_IDS:
        return key
    return s


def resolve_cube(look: str) -> Path | None:
    """Ścieżka pliku .cube dla looku nie-wbudowanego (pełna ścieżka, nazwa pliku lub sam rdzeń)."""
    p = Path(look)
    if p.suffix.lower() == ".cube" and p.is_absolute() and p.exists():
        return p
    d = luts_dir()
    for cand in (d / p.name, d / f"{p.stem}.cube", d / f"{look}.cube"):
        if cand.exists() and cand.suffix.lower() == ".cube":
            return cand
    return None


def is_monochrome(look: str | None) -> bool:
    return normalize_look(look) in MONOCHROME


# ---------------------------------------------------------------------------------------------
# pomocnicze operacje na RGB
# ---------------------------------------------------------------------------------------------
def luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ LUMA


def saturate(rgb: np.ndarray, s: float | np.ndarray) -> np.ndarray:
    y = luma(rgb)[:, None]
    s = np.asarray(s)
    if s.ndim == 1:
        s = s[:, None]
    return y + (rgb - y) * s


def pivot_curve(x: np.ndarray, k: float, p: float = 0.435) -> np.ndarray:
    """Krzywa S wokół pivotu: nachylenie k w pivocie, 0→0 i 1→1, monotoniczna dla k>0."""
    x = np.clip(x, 0.0, 1.0)
    lo = p * np.power(x / p, k)
    hi = 1.0 - (1.0 - p) * np.power(np.clip((1.0 - x) / (1.0 - p), 0, None), k)
    return np.where(x < p, lo, hi)


def smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / max(1e-9, e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def split_tone(rgb: np.ndarray, shadow: tuple[float, float, float], high: tuple[float, float, float]) -> np.ndarray:
    """Dodaje odcień w cieniach i światłach (zachowując luminancję)."""
    y = luma(rgb)[:, None]
    ws = (1.0 - y) ** 2
    wh = y ** 2
    sh = np.array(shadow) - np.dot(np.array(shadow), LUMA)
    hi = np.array(high) - np.dot(np.array(high), LUMA)
    return rgb + ws * sh + wh * hi


def _rgb2hsv(rgb: np.ndarray):
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    mx = rgb.max(axis=1)
    mn = rgb.min(axis=1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-9
    rc = np.where(nz, (mx - r) / np.where(nz, d, 1), 0)
    gc = np.where(nz, (mx - g) / np.where(nz, d, 1), 0)
    bc = np.where(nz, (mx - b) / np.where(nz, d, 1), 0)
    h = np.where(r == mx, bc - gc, np.where(g == mx, 2.0 + rc - bc, 4.0 + gc - rc))
    h = np.where(nz, (h / 6.0) % 1.0, 0.0)
    s = np.where(mx > 1e-9, d / np.where(mx > 1e-9, mx, 1), 0.0)
    return h, s, mx


def hue_deg(rgb: np.ndarray) -> np.ndarray:
    return _rgb2hsv(rgb)[0] * 360.0


def hsv(rgb: np.ndarray):
    return _rgb2hsv(rgb)


# ---------------------------------------------------------------------------------------------
# looki wbudowane
# ---------------------------------------------------------------------------------------------
def _teal_orange(rgb):
    x = split_tone(rgb, (-0.05, 0.015, 0.06), (0.06, 0.015, -0.06))
    # komplementarność: zielenie/błękity → teal, pomarańcze lekko mocniej nasycone
    h, s, v = _rgb2hsv(np.clip(x, 0, 1))
    hd = h * 360
    warm = np.exp(-((((hd - 30 + 180) % 360) - 180) / 25.0) ** 2)
    x = saturate(x, 1.08 + 0.12 * warm)
    return pivot_curve(x, 1.12)


def _warm_film(rgb):
    x = rgb * np.array([1.035, 1.0, 0.93])
    x = x / max(1e-6, float(np.dot(np.array([1.035, 1.0, 0.93]), LUMA)))
    x = saturate(x, 0.9)
    x = pivot_curve(x, 1.08)
    # podniesiona czerń i miękkie światła (ciepła czerń)
    x = np.array([0.035, 0.03, 0.025]) + x * (0.955 - np.array([0.035, 0.03, 0.025]))
    return x


def _clean_doc(rgb):
    x = saturate(rgb, 1.06)
    return pivot_curve(x, 1.06)


def _moody(rgb):
    x = np.power(np.clip(rgb, 0, 1), 1.12)
    x = saturate(x, 0.74)
    x = split_tone(x, (-0.03, 0.01, 0.04), (0.02, 0.005, -0.015))
    x = pivot_curve(x, 1.12)
    return 0.012 + x * 0.91


def _bw_film(rgb):
    y = rgb @ np.array([0.33, 0.55, 0.12])  # filtr czerwono-pomarańczowy: ciemniejsze niebo
    y = pivot_curve(y, 1.22)
    y = 0.018 + y * 0.962
    return np.repeat(y[:, None], 3, axis=1)


def _vibrant_social(rgb):
    h, s, v = _rgb2hsv(np.clip(rgb, 0, 1))
    vib = 1.0 + 0.45 * (1.0 - s) ** 1.5  # vibrance: słabo nasycone mocniej
    x = saturate(rgb, vib * 1.06)
    x = np.power(np.clip(x, 0, 1), 0.95)
    return pivot_curve(x, 1.1)


def _pastel(rgb):
    x = saturate(rgb, 0.72)
    x = pivot_curve(x, 0.85)
    x = split_tone(x, (0.0, 0.01, 0.02), (0.02, 0.0, 0.012))
    return 0.07 + x * 0.905


_FUNCS = {"teal_orange": _teal_orange, "warm_film": _warm_film, "clean_doc": _clean_doc, "moody": _moody,
          "bw_film": _bw_film, "vibrant_social": _vibrant_social, "pastel": _pastel}


# ---------------------------------------------------------------------------------------------
# pliki .cube
# ---------------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=16)
def _read_cube_cached(path: str, mtime: float) -> tuple:
    size3 = size1 = 0
    dmin = np.zeros(3)
    dmax = np.ones(3)
    rows: list[list[float]] = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            up = s.upper()
            if up.startswith("TITLE"):
                continue
            if up.startswith("LUT_3D_SIZE"):
                size3 = int(s.split()[1])
                continue
            if up.startswith("LUT_1D_SIZE"):
                size1 = int(s.split()[1])
                continue
            if up.startswith("DOMAIN_MIN"):
                dmin = np.array([float(v) for v in s.split()[1:4]])
                continue
            if up.startswith("DOMAIN_MAX"):
                dmax = np.array([float(v) for v in s.split()[1:4]])
                continue
            if up.startswith("LUT_1D_INPUT_RANGE") or up.startswith("LUT_3D_INPUT_RANGE"):
                parts = s.split()
                dmin = np.full(3, float(parts[1]))
                dmax = np.full(3, float(parts[2]))
                continue
            parts = s.split()
            if len(parts) >= 3:
                try:
                    rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
                except ValueError:
                    continue
    data = np.asarray(rows, dtype=np.float64)
    if size3:
        if len(data) < size3 ** 3:
            raise ValueError(f"Uszkodzony LUT {Path(path).name}: {len(data)} < {size3 ** 3} wierszy")
        # .cube: R zmienia się najszybciej → indeksy [b, g, r]
        return ("3d", data[: size3 ** 3].reshape(size3, size3, size3, 3), dmin, dmax)
    if size1:
        return ("1d", data[:size1], dmin, dmax)
    raise ValueError(f"Plik {Path(path).name} nie zawiera LUT_3D_SIZE ani LUT_1D_SIZE")


def read_cube(path: str | Path) -> tuple:
    p = Path(path)
    return _read_cube_cached(str(p), p.stat().st_mtime)


def apply_cube(rgb: np.ndarray, path: str | Path) -> np.ndarray:
    """Zastosowanie pliku .cube (trilinearnie dla 3D, liniowo dla 1D)."""
    kind, table, dmin, dmax = read_cube(path)
    x = np.clip((rgb - dmin) / np.maximum(1e-9, dmax - dmin), 0.0, 1.0)
    if kind == "1d":
        n = len(table)
        pos = x * (n - 1)
        i0 = np.clip(np.floor(pos).astype(int), 0, n - 2)
        f = pos - i0
        out = np.empty_like(x)
        for c in range(3):
            out[:, c] = table[i0[:, c], c] * (1 - f[:, c]) + table[i0[:, c] + 1, c] * f[:, c]
        return out
    n = table.shape[0]
    pos = x * (n - 1)
    i0 = np.clip(np.floor(pos).astype(int), 0, n - 2)
    f = pos - i0
    r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
    fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
    out = np.zeros_like(x)
    for db in (0, 1):
        wb = fb if db else (1 - fb)
        for dg in (0, 1):
            wg = fg if dg else (1 - fg)
            for dr in (0, 1):
                wr = fr if dr else (1 - fr)
                out += table[b0 + db, g0 + dg, r0 + dr] * (wb * wg * wr)
    return out


def apply_look(rgb: np.ndarray, look: str | None) -> np.ndarray:
    """Look w pełnej sile (mieszanie z siłą robi lut.py). Nieznany look → bez zmian."""
    lk = normalize_look(look)
    if lk is None:
        return rgb
    fn = _FUNCS.get(lk)
    if fn is not None:
        return fn(np.clip(rgb, 0.0, 1.0))
    cube = resolve_cube(lk)
    if cube is None:
        return rgb
    return apply_cube(np.clip(rgb, 0.0, 1.0), cube)


def look_exists(look: str | None) -> bool:
    lk = normalize_look(look)
    return lk is None or lk in _FUNCS or resolve_cube(lk) is not None

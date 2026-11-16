"""Pomiar tekstu (Pillow + czcionki Windows) i łamanie linii napisów w bezpiecznym obszarze kadru.

Rozmiar czcionki w ASS (libass) odpowiada wysokości linii (ascent+descent z metryk fontu), a nie
wielkości em jak w Pillow - `font_em_ratio()` przelicza jedno na drugie, żeby pomiar był zgodny z libass.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Callable, Sequence

FONTS_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
FONT_FILES = {
    "segoe ui black": "seguibl.ttf", "segoe ui": "segoeui.ttf", "segoe ui bold": "segoeuib.ttf",
    "segoe ui semibold": "seguisb.ttf", "segoe ui light": "segoeuil.ttf", "segoe ui semilight": "segoeuisl.ttf",
    "arial": "arial.ttf", "arial bold": "arialbd.ttf", "arial black": "ariblk.ttf", "impact": "impact.ttf",
    "georgia": "georgia.ttf", "georgia bold": "georgiab.ttf", "verdana": "verdana.ttf", "verdana bold": "verdanab.ttf",
    "tahoma": "tahoma.ttf", "tahoma bold": "tahomabd.ttf", "calibri": "calibri.ttf", "calibri bold": "calibrib.ttf",
    "bahnschrift": "bahnschrift.ttf", "trebuchet ms": "trebuc.ttf", "consolas": "consola.ttf",
}
FALLBACKS = ("arialbd.ttf", "arial.ttf", "segoeui.ttf")


def font_file(name: str, bold: bool = False) -> Path | None:
    n = (name or "").strip().lower()
    cands = []
    if bold and f"{n} bold" in FONT_FILES:
        cands.append(FONT_FILES[f"{n} bold"])
    if n in FONT_FILES:
        cands.append(FONT_FILES[n])
    cands += list(FALLBACKS)
    for c in cands:
        p = FONTS_DIR / c
        if p.exists():
            return p
    return None


@functools.lru_cache(maxsize=64)
def _font(path: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(path, size)


@functools.lru_cache(maxsize=32)
def font_em_ratio(path: str) -> float:
    """em / wysokość linii libass (winAscent+winDescent). Np. Segoe UI ~0.75, Arial ~0.89."""
    try:
        from fontTools.ttLib import TTFont  # type: ignore

        f = TTFont(path, lazy=True)
        upm = f["head"].unitsPerEm
        os2 = f["OS/2"]
        h = os2.usWinAscent + os2.usWinDescent
        if h > 0:
            return upm / h
    except Exception:
        pass
    f = _font(path, 1000)
    asc, desc = f.getmetrics()
    return 1000.0 / max(1, asc + desc)


class Measurer:
    """Mierzy szerokość tekstu w pikselach ASS (PlayRes) dla danego Fontsize."""

    def __init__(self, font_name: str, ass_size: float, *, bold: bool = False, spacing: float = 0.0,
                 outline: float = 0.0, scale_x: float = 1.0):
        self.path = font_file(font_name, bold)
        self.size = ass_size
        self.spacing = spacing
        self.outline = outline
        self.scale_x = scale_x
        self.em = ass_size * (font_em_ratio(str(self.path)) if self.path else 0.85)

    def width(self, text: str) -> float:
        if not text:
            return 0.0
        if self.path is None:
            w = len(text) * self.em * 0.6
        else:
            f = _font(str(self.path), 200)
            w = f.getlength(text) * self.em / 200.0
        return (w + self.spacing * max(0, len(text) - 1)) * self.scale_x + 2 * self.outline


def balance_lines(words: Sequence[str], n_lines: int, measure: Callable[[str], float]) -> list[list[str]]:
    """Dzieli słowa na n_lines linii minimalizując najszerszą linię (preferuje krótszą górną)."""
    words = list(words)
    if n_lines <= 1 or len(words) <= 1:
        return [words]
    if n_lines == 2:
        best = None
        for k in range(1, len(words)):
            a, b = " ".join(words[:k]), " ".join(words[k:])
            wa, wb = measure(a), measure(b)
            cost = max(wa, wb) + (0.04 * (wa - wb) if wa > wb else 0.0)  # lekka preferencja "piramidy"
            if best is None or cost < best[0]:
                best = (cost, k)
        k = best[1]
        return [words[:k], words[k:]]
    # >2 linie: zachłannie po równej szerokości
    total = measure(" ".join(words))
    target = total / n_lines
    lines, cur = [], []
    for w in words:
        cand = " ".join(cur + [w])
        if cur and measure(cand) > target * 1.08 and len(lines) < n_lines - 1:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    lines.append(cur)
    return lines


def fits(words: Sequence[str], max_width: float, max_chars: int, max_lines: int,
         measure: Callable[[str], float]) -> bool:
    if not words:
        return True
    for n in range(1, max_lines + 1):
        lines = balance_lines(words, n, measure)
        if all(len(" ".join(l)) <= max_chars and measure(" ".join(l)) <= max_width for l in lines):
            return True
    return False


def layout_cue(words: Sequence[str], max_width: float, max_chars: int, max_lines: int,
               measure: Callable[[str], float]) -> tuple[list[list[int]], float]:
    """Linie (jako indeksy słów) + skala (<=1) gdy pojedyncze słowo nie mieści się w szerokości."""
    words = list(words)
    for n in range(1, max_lines + 1):
        lines = balance_lines(words, n, measure)
        if all(len(" ".join(l)) <= max_chars and measure(" ".join(l)) <= max_width for l in lines):
            return _to_idx(lines), 1.0
    lines = balance_lines(words, max_lines, measure)
    widest = max(measure(" ".join(l)) for l in lines)
    return _to_idx(lines), min(1.0, max_width / max(1.0, widest))


def _to_idx(lines: list[list[str]]) -> list[list[int]]:
    out, i = [], 0
    for l in lines:
        out.append(list(range(i, i + len(l))))
        i += len(l)
    return out


def split_cues(words: Sequence[str], max_width: float, max_chars: int, max_lines: int,
               measure: Callable[[str], float], breaks: Sequence[bool] | None = None) -> list[list[int]]:
    """Dzieli ciąg słów na plansze (cue) mieszczące się w max_lines liniach.

    `breaks[i]` = True wymusza koniec planszy po słowie i (koniec zdania, pauza, zmiana mówcy).
    """
    words = list(words)
    cues: list[list[int]] = []
    cur: list[int] = []
    for i, w in enumerate(words):
        cand = [words[j] for j in cur] + [w]
        if cur and not fits(cand, max_width, max_chars, max_lines, measure):
            cues.append(cur)
            cur = []
        cur.append(i)
        if breaks is not None and i < len(breaks) and breaks[i]:
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    # nie zostawiaj pojedynczego krótkiego słowa na końcu planszy, jeśli można je dokleić do poprzedniej
    merged: list[list[int]] = []
    for c in cues:
        if merged and len(c) == 1 and len(words[c[0]]) <= 3 and \
                not (breaks and breaks[merged[-1][-1]]) and \
                fits([words[j] for j in merged[-1] + c], max_width, max_chars, max_lines, measure):
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    return merged

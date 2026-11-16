"""Planowanie renderu: podział osi na plastry o stałym zestawie warstw + hash parametrów (render przyrostowy).

Każdy plaster to słownik JSON (spec) opisujący WSZYSTKO, co wpływa na obraz: źródła (ścieżka, mtime, rozmiar),
czasy w źródle, prędkość, transformacje, reframe, kolor, efekty, tytuły, rozdzielczość, fps, jakość.
`hash = sha1(spec)` -> nazwa pliku w cache; plik istnieje = plaster pominięty.

Pozycja plastra na osi NIE wchodzi do hasha (liczy się treść), więc przesunięcie całej reszty osi
(ripple) nie unieważnia gotowych plastrów.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from ..events import log
from ..media.ffmpeg import media_info
from ..project.model import Effect, MediaItem, TitleClip, Timeline, VClip, Variant

RENDER_VERSION = 3


def _code_version() -> str:
    """Wersja kodu renderu (zmiana filtrów/planera unieważnia plastry w cache)."""
    h = hashlib.sha1(str(RENDER_VERSION).encode())
    for name in ("filters.py", "slices.py"):
        try:
            h.update((Path(__file__).parent / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:10]


CODE_VERSION = _code_version()
TRANSITION_KINDS = {"dissolve", "dip_black", "dip_white", "whip", "light_leak", "zoom", "slide"}
FILTER_EFFECTS = {"vignette", "film_grain", "sharpen", "denoise_video", "blur_region"}
ALPHA_PIX = ("yuva", "rgba", "argb", "bgra", "abgr", "gbrap", "ya8", "ya16", "pal8")
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


# ---------------------------------------------------------------------------------------------
# Odwzorowanie czasu osi -> czasu w źródle (prędkość stała lub krzywa speed_ramp)
# ---------------------------------------------------------------------------------------------
def _smooth(u: np.ndarray) -> np.ndarray:
    return u * u * (3 - 2 * u)


class ClipTime:
    """tau = sekundy od początku klipu na osi; src(tau) = czas w pliku źródłowym.

    speed_ramp: punkty (t 0..1 względnie w czasie osi, speed) łączone krzywą ease (smoothstep).
    Krzywa jest skalowana tak, by całka = długość materiału źródłowego przy długości klipu z modelu
    (VClip.duration), więc oś i render zawsze się zgadzają.
    """

    def __init__(self, clip: VClip):
        self.clip = clip
        self.D = max(1e-6, float(clip.duration))
        self.src_in = float(clip.src_in)
        self.src_dur = max(0.0, float(clip.src_out) - float(clip.src_in))
        self.ramp = bool(clip.speed_ramp)
        if self.ramp:
            pts = sorted(clip.speed_ramp, key=lambda p: p.t)
            ts = np.array([min(1.0, max(0.0, p.t)) for p in pts])
            sp = np.array([max(0.05, p.speed) for p in pts])
            self.u = np.linspace(0.0, 1.0, 2001)
            s = np.empty_like(self.u)
            for i, x in enumerate(self.u):
                if x <= ts[0]:
                    s[i] = sp[0]
                elif x >= ts[-1]:
                    s[i] = sp[-1]
                else:
                    j = int(np.searchsorted(ts, x, side="right") - 1)
                    span = max(1e-9, ts[j + 1] - ts[j])
                    w = _smooth(np.array((x - ts[j]) / span))
                    s[i] = sp[j] + (sp[j + 1] - sp[j]) * float(w)
            cum = np.concatenate([[0.0], np.cumsum((s[1:] + s[:-1]) / 2 * np.diff(self.u))])
            self.k = self.src_dur / max(1e-9, self.D * cum[-1])
            self.cum = cum  # F(u)
            self.s = s
            self.v0 = float(s[0] * self.k)
            self.v1 = float(s[-1] * self.k)
        else:
            self.v0 = self.v1 = max(0.05, float(clip.speed))

    def src(self, tau: float) -> float:
        if not self.ramp:
            return self.src_in + tau * self.v0
        if tau <= 0:
            return self.src_in + tau * self.v0
        if tau >= self.D:
            return self.src_in + self.src_dur + (tau - self.D) * self.v1
        f = float(np.interp(tau / self.D, self.u, self.cum))
        return self.src_in + self.D * self.k * f

    def speed_at(self, tau: float) -> float:
        if not self.ramp:
            return self.v0
        return float(np.interp(min(1.0, max(0.0, tau / self.D)), self.u, self.s)) * self.k

    def tau_of(self, s: float) -> float:
        """Odwrotność src(): czas klipu (oś), w którym źródło osiąga s."""
        if not self.ramp:
            return (s - self.src_in) / self.v0
        rel = s - self.src_in
        if rel <= 0:
            return rel / self.v0
        if rel >= self.src_dur:
            return self.D + (rel - self.src_dur) / self.v1
        f = rel / (self.D * self.k)
        return float(np.interp(f, self.cum, self.u)) * self.D

    def ramp_taus(self, steps: int) -> list[float]:
        """Granice mikro-plastrów o stałej prędkości - gęściej tam, gdzie prędkość się zmienia."""
        if not self.ramp:
            return []
        # rozkład kroków proporcjonalnie do |ds/du| + stała baza
        ds = np.abs(np.gradient(self.s)) + 1e-3 * (self.s.max() - self.s.min() + 1e-6)
        c = np.cumsum(ds)
        c = (c - c[0]) / max(1e-9, c[-1] - c[0])
        marks = [float(np.interp(i / steps, c, self.u)) * self.D for i in range(1, steps)]
        return marks


# ---------------------------------------------------------------------------------------------
# Struktury planu
# ---------------------------------------------------------------------------------------------
@dataclass
class SliceJob:
    p: int  # pierwsza klatka (na siatce wyjściowej, numer klatki osi)
    q: int  # klatka końcowa (wyłącznie)
    spec: dict
    hash: str = ""
    path: Path | None = None
    cached: bool = False

    @property
    def n(self) -> int:
        return self.q - self.p


@dataclass
class RenderPlan:
    W: int
    H: int
    fps: Fraction
    FA: int
    FB: int
    quality: str
    variant: Variant
    slices: list[SliceJob] = field(default_factory=list)
    titles: list[TitleClip] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    windows: list[dict] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return self.FB - self.FA

    @property
    def duration(self) -> float:
        return float(Fraction(self.frames) / self.fps)


def spec_hash(spec: dict) -> str:
    def strip(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if not str(k).startswith("_")}
        if isinstance(o, list):
            return [strip(x) for x in o]
        if isinstance(o, float):
            return round(o, 5)
        return o

    blob = json.dumps(strip(spec), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:24]


def file_sig(path: str | Path) -> dict:
    p = Path(path)
    try:
        st = p.stat()
        return {"path": str(p).replace("\\", "/"), "mtime": int(st.st_mtime), "size": int(st.st_size)}
    except OSError:
        return {"path": str(p).replace("\\", "/"), "mtime": 0, "size": 0}


# ---------------------------------------------------------------------------------------------
# Planer
# ---------------------------------------------------------------------------------------------
class Planner:
    def __init__(self, project, tl: Timeline, variant: Variant, *, W: int, H: int, fps: Fraction, quality: str,
                 before: bool = False, color: bool = True, ramp_steps: int = 8, max_slice_s: float = 6.0,
                 use_proxy: bool | None = None):
        self.project = project
        self.tl = tl
        self.variant = variant
        self.W, self.H, self.fps = W, H, fps
        self.F = float(fps)
        self.quality = quality
        self.before = before
        self.color = color and not before
        self.ramp_steps = ramp_steps
        self.max_slice_s = max_slice_s
        self.use_proxy = (quality == "preview") if use_proxy is None else use_proxy
        self.warnings: list[str] = []
        self._ct: dict[str, ClipTime] = {}
        self._media: dict[str, MediaItem | None] = {}
        self._info: dict[str, dict] = {}
        self._color_cache: dict[str, str] = {}
        self._color_fn = None
        self._color_failed = False
        self._paths_cache: dict[str, list[list[float]]] = {}
        self.windows: list[dict] = []
        self.v1 = [c for c in tl.tracks.get("V1", []) if isinstance(c, VClip)]
        self.v2 = [c for c in tl.tracks.get("V2", []) if isinstance(c, VClip)]
        self.v3 = [c for c in tl.tracks.get("V3", []) if isinstance(c, VClip)]
        self.titles = [c for c in tl.tracks.get("V3", []) if isinstance(c, TitleClip)]
        for k, items in tl.tracks.items():
            if k.startswith("V") and k not in ("V1", "V2", "V3"):
                self.v3.extend(c for c in items if isinstance(c, VClip))
                self.titles.extend(c for c in items if isinstance(c, TitleClip))

    # -- pomocnicze -----------------------------------------------------------------------
    def fr(self, t: float) -> int:
        return int(math.floor(t * self.F + 0.5 + 1e-9))

    def ct(self, clip: VClip) -> ClipTime:
        if clip.id not in self._ct:
            self._ct[clip.id] = ClipTime(clip)
        return self._ct[clip.id]

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)

    def media(self, mid: str) -> MediaItem | None:
        if mid not in self._media:
            try:
                self._media[mid] = self.project.get_media(mid)
            except Exception:
                self._media[mid] = None
        return self._media[mid]

    def info(self, path: str) -> dict:
        if path not in self._info:
            try:
                self._info[path] = media_info(path)
            except Exception as e:
                self.warn(f"Nie można odczytać pliku {Path(path).name}: {e}")
                self._info[path] = {}
        return self._info[path]

    def color_filter(self, clip: VClip, media: MediaItem) -> str:
        if not self.color or self._color_failed:
            return ""
        if self._color_fn is None:
            try:
                from ..color.filters import video_filter  # type: ignore

                self._color_fn = video_filter
            except Exception as e:
                self._color_failed = True
                self.warn(f"Moduł koloru niedostępny ({type(e).__name__}) - render bez korekcji koloru")
                return ""
        key = clip.id + "|" + clip.grade.model_dump_json()
        if key not in self._color_cache:
            try:
                self._color_cache[key] = str(self._color_fn(clip, media, self.project) or "")
            except Exception as e:
                self.warn(f"Korekcja koloru klipu {clip.label or clip.id} nieudana ({e}) - pominięta")
                self._color_cache[key] = ""
        return self._color_cache[key]

    # -- źródła -----------------------------------------------------------------------------
    def effect_output(self, clip: VClip, s_a: float, s_b: float) -> tuple[Effect | None, float, float]:
        """Efekt z gotowym plikiem `output` pokrywający zakres źródła [s_a, s_b]."""
        if self.before:
            return None, 0.0, 0.0
        best = None
        for e in clip.effects:
            if not e.enabled or e.status != "done" or not e.output or not Path(e.output).exists():
                continue
            e0, e1 = effect_range(clip, e)
            if e0 - 1e-3 <= s_a and s_b <= e1 + 1e-3:
                best = (e, e0, e1)
        return best if best else (None, 0.0, 0.0)

    def source_for(self, clip: VClip, media: MediaItem, s_a: float, s_b: float) -> tuple[dict, float, dict | None]:
        """Zwraca (src_sig, seek_offset_correction, effect_meta)."""
        eff, e0, _e1 = self.effect_output(clip, s_a, s_b)
        if eff is not None:
            path = str(eff.output)
            info = self.info(path)
            sig = {**file_sig(path), "w": info.get("width", 0), "h": info.get("height", 0),
                   "fps": info.get("fps", 0), "dur": round(float(info.get("duration", 0)), 3),
                   "image": False, "alpha": _has_alpha(info), "kind": "vfx"}
            off = float(eff.params.get("out_offset", 0.0) or 0.0)
            return sig, -e0 + off, {"type": eff.type, "graded": bool(eff.params.get("graded", False))}
        path = media.path
        if self.use_proxy and media.proxy and Path(media.proxy).exists():
            path = media.proxy
        info = self.info(path)
        if not info and path != media.path:
            path = media.path
            info = self.info(path)
        is_img = media.kind == "image" or clip.role == "image" or Path(path).suffix.lower() in IMAGE_EXT \
            or info.get("kind") == "image"
        orig = self.info(media.path) if path != media.path else info
        sig = {**file_sig(path), "w": info.get("width", 0), "h": info.get("height", 0),
               "fps": info.get("fps", 0), "dur": round(float(info.get("duration", 0)), 3),
               "image": bool(is_img), "alpha": _has_alpha(info),
               "ow": orig.get("width", info.get("width", 0)), "oh": orig.get("height", info.get("height", 0))}
        return sig, 0.0, None

    # -- warstwy ---------------------------------------------------------------------------
    def layer(self, clip: VClip, track: str, f0: int, nfr: int, *, handles: bool = False) -> dict | None:
        """Warstwa klipu dla klatek osi [f0, f0+nfr)."""
        media = self.media(clip.media_id)
        if media is None:
            self.warn(f"Klip {clip.label or clip.id}: brak mediów {clip.media_id} - pominięty")
            return None
        ct = self.ct(clip)
        tau0 = f0 / self.F - clip.start
        L = nfr / self.F
        s_a = ct.src(tau0)
        s_b = ct.src(tau0 + L)
        speed = max(0.01, (s_b - s_a) / L) if L > 0 else ct.v0
        src, off, eff = self.source_for(clip, media, max(s_a, clip.src_in) if handles else s_a,
                                        min(s_b, clip.src_out) if handles else s_b)
        if eff is not None:
            src_seek = s_a + off
        else:
            src_seek = s_a
        pre = 0.0
        if not src["image"]:
            if src_seek < 0:
                pre = -src_seek / speed
                src_seek = 0.0
        lay: dict[str, Any] = {
            "track": track, "src": src, "s_a": round(src_seek, 4), "speed": round(speed, 5),
            "pre": round(pre, 4), "n": nfr, "tau0": round(tau0, 4), "opacity": round(float(clip.opacity), 4),
            "_clip": clip.id, "_label": clip.label,
        }
        # interpolacja spowolnień poniżej natywnego klatkażu
        if not src["image"]:
            src_fps = float(src.get("fps") or 0) or self.F
            eff_fps = src_fps * speed  # klatki źródła na sekundę osi
            if eff_fps < self.F * 0.95 and clip.interpolate != "none":
                mode = clip.interpolate
                if mode == "optical" and (self.quality == "preview" or L > 8.0):
                    if self.quality != "preview":
                        self.warn(f"Klip {clip.label or clip.id}: interpolacja optyczna tylko dla krótkich "
                                  f"fragmentów - użyto przenikania klatek")
                    mode = "blend"
                lay["interp"] = mode
        # dopasowanie do kadru
        lay.update(self.fit(clip, media, src, track, tau0, L))
        # kolor
        if not (eff and eff.get("graded")):
            cf = self.color_filter(clip, media)
            if cf:
                lay["color"] = cf
                lay["luts"] = _lut_sigs(cf, clip)
        # efekty-filtry
        if not self.before:
            fx = []
            for e in clip.effects:
                if e.enabled and e.type in FILTER_EFFECTS and e.status != "failed" and not e.output:
                    fx.append({"type": e.type, "params": e.params})
            if fx:
                lay["fx"] = fx
        return lay

    def fit(self, clip: VClip, media: MediaItem, src: dict, track: str, tau0: float, L: float) -> dict:
        W, H = self.W, self.H
        ow = float(src.get("ow") or src.get("w") or W)
        oh = float(src.get("oh") or src.get("h") or H)
        src_ar = ow / max(1.0, oh)
        dst_ar = W / H
        out: dict[str, Any] = {}
        kf = sorted(clip.transform, key=lambda k: k.t)
        if src["image"] and not kf and track != "V3":
            # Ken Burns: łagodny zoom 100 -> 108% z lekkim dryfem
            d = max(0.5, clip.duration)
            kf_list = [[0.0, 1.0, -0.01, 0.0, 0.0, "linear"], [round(d, 4), 1.08, 0.01, 0.0, 0.0, "linear"]]
        else:
            kf_list = [[round(k.t, 4), round(k.scale, 5), round(k.x, 5), round(k.y, 5), round(k.rot, 4), k.ease]
                       for k in kf]
        if kf_list and all(abs(k[1] - 1) < 1e-4 and abs(k[2]) < 1e-5 and abs(k[3]) < 1e-5 and abs(k[4]) < 1e-4
                           for k in kf_list):
            kf_list = []
        if kf_list:
            out["kf"] = _restrict_kf(kf_list, tau0, tau0 + L)
        path = clip.reframe.get(self.variant.name) if isinstance(clip.reframe, dict) else None
        ratio = src_ar / dst_ar
        if track == "V3":
            # grafika/VFX: rozmiar natywny względem wariantu (zmniejszana, gdy nie mieści się w kadrze)
            out["fit"] = "contain_alpha"
            out["gscale"] = round(min(W / ow, H / oh, W / max(1, self.variant.width)), 5)
        elif path and 1 / 3.5 <= ratio <= 3.5:
            # ścieżka reframe (np. 16:9 -> 9:16) - kadr podąża za punktem
            out["fit"] = "reframe"
            out["path"] = self.reframe_path(clip, path, tau0, tau0 + L)
        elif abs(math.log(ratio)) < 0.03:
            out["fit"] = "cover"
        elif ratio < 0.7 or ratio > 1.4:
            # duża różnica proporcji (pion w 16:9, poziom w 9:16) -> contain z rozmytym tłem
            out["fit"] = "blur"
        else:
            # umiarkowana różnica (4:3 w 16:9, 9:16 w 4:5) -> wypełnienie z centrowaniem na twarzy
            out["fit"] = "cover"
            c = _face_center(media, self.ct(clip).src(tau0 + L / 2))
            if c:
                out["center"] = [round(c[0], 4), round(c[1], 4)]
        return out

    def reframe_path(self, clip: VClip, path: list[dict], a: float, b: float) -> list[list[float]]:
        """Wygładzona ścieżka reframe (10 Hz, gauss 0,35 s), zawężona do [a, b] (+ sąsiedzi)."""
        key = clip.id + self.variant.name
        if key not in self._paths_cache:
            pts = sorted(([float(p.get("t", 0)), float(p.get("cx", 0.5)), float(p.get("cy", 0.5))]
                          for p in path if isinstance(p, dict)), key=lambda x: x[0])
            if not pts:
                pts = [[0.0, 0.5, 0.5]]
            if len(pts) == 1:
                self._paths_cache[key] = [[0.0] + pts[0][1:]]
            else:
                arr = np.array(pts)
                t_lo, t_hi = arr[0, 0], arr[-1, 0]
                ts = np.arange(t_lo, t_hi + 1e-9, 0.1)
                if len(ts) < 2:
                    ts = np.array([t_lo, t_hi])
                cx = np.interp(ts, arr[:, 0], arr[:, 1])
                cy = np.interp(ts, arr[:, 0], arr[:, 2])
                sig = 3.5  # 0,35 s przy 10 Hz
                r = int(sig * 3)
                ker = np.exp(-0.5 * (np.arange(-r, r + 1) / sig) ** 2)
                ker /= ker.sum()
                cxs = np.convolve(np.pad(cx, r, mode="edge"), ker, mode="valid")
                cys = np.convolve(np.pad(cy, r, mode="edge"), ker, mode="valid")
                self._paths_cache[key] = [[round(float(t), 3), round(float(x), 4), round(float(y), 4)]
                                          for t, x, y in zip(ts, cxs, cys)]
        full = self._paths_cache[key]
        if len(full) <= 2:
            return full
        i0 = max(0, int(np.searchsorted([p[0] for p in full], a, side="right")) - 2)
        i1 = min(len(full), int(np.searchsorted([p[0] for p in full], b, side="left")) + 2)
        sub = full[i0:i1]
        return sub if sub else [full[-1]]

    # -- plan ------------------------------------------------------------------------------
    def transition_windows(self, FA: int, FB: int) -> list[dict]:
        wins: list[dict] = []
        tol = 1.5 / self.F + 0.02
        for tr in sorted(self.tl.transitions, key=lambda x: x.at):
            if tr.kind not in TRANSITION_KINDS or tr.duration <= 0:
                continue
            A = next((c for c in self.v1 if abs(c.end - tr.at) <= tol), None)
            B = next((c for c in self.v1 if abs(c.start - tr.at) <= tol), None)
            if A is None and B is None:
                self.warn(f"Przejście {tr.kind} w {tr.at:.2f} s nie leży na cięciu V1 - pominięte")
                continue
            d = float(tr.duration)
            wa, wb = self.fr(tr.at - d / 2), self.fr(tr.at + d / 2)
            if A is not None:
                wa = max(wa, self.fr(A.start) + 1)
            if B is not None:
                wb = min(wb, self.fr(B.end) - 1)
            if wins and wa < wins[-1]["wb"]:
                wa = wins[-1]["wb"]
            if wb - wa < 2:
                continue
            wins.append({"wa": wa, "wb": wb, "kind": tr.kind, "A": A, "B": B, "tr": tr})
        return [w for w in wins if w["wb"] > FA and w["wa"] < FB]

    def plan(self, FA: int, FB: int) -> list[SliceJob]:
        wins = self.transition_windows(FA, FB)
        cuts: set[int] = {FA, FB}

        def add(t: float):
            f = self.fr(t)
            if FA < f < FB:
                cuts.add(f)

        for c in self.v1:
            add(c.start)
            add(c.end)
            # długie klipy dzielimy względem ich początku (stabilne hashe przy ripple)
            k = 1
            while self.max_slice_s > 0 and k * self.max_slice_s < c.duration - 0.5:
                add(c.start + k * self.max_slice_s)
                k += 1
        for c in self.v2 + self.v3:
            add(c.start)
            add(c.end)
        for t in self.titles:
            add(t.start)
            add(t.end)
        for c in self.v1 + self.v2 + self.v3:
            ct = self.ct(c)
            for tau in ct.ramp_taus(self.ramp_steps):
                add(c.start + tau)
            for e in c.effects:
                if e.enabled and e.status == "done" and e.output and not self.before:
                    e0, e1 = effect_range(c, e)
                    for s in (e0, e1):
                        tau = ct.tau_of(s)
                        if 0 < tau < c.duration:
                            add(c.start + tau)
        # przejścia są atomowe: brak granic wewnątrz okna
        for w in wins:
            for f in list(cuts):
                if w["wa"] < f < w["wb"] and f not in (FA, FB):
                    cuts.discard(f)
            for f in (w["wa"], w["wb"]):
                if FA < f < FB:
                    cuts.add(f)
        bounds = sorted(cuts)
        jobs = []
        for p, q in zip(bounds[:-1], bounds[1:]):
            if q <= p:
                continue
            win = next((w for w in wins if w["wa"] <= p and q <= w["wb"]), None)
            spec = self.slice_spec(p, q, win)
            jobs.append(SliceJob(p=p, q=q, spec=spec, hash=spec_hash(spec)))
        self.windows = wins
        return jobs

    def _active(self, clips: list, f_mid: float) -> list:
        t = f_mid / self.F
        return [c for c in clips if c.start <= t < c.end]

    def slice_spec(self, p: int, q: int, win: dict | None) -> dict:
        n = q - p
        spec: dict[str, Any] = {"v": CODE_VERSION, "W": self.W, "H": self.H, "fps": str(self.fps), "n": n,
                                "quality": self.quality, "variant": self.variant.name,
                                "_p": p, "_q": q}
        if self.before:
            spec["before"] = True
        mid = (p + q) / 2.0
        if win is not None:
            nw = win["wb"] - win["wa"]
            base: dict[str, Any] = {"type": "transition", "kind": win["kind"], "nw": nw,
                                    "trim": [p - win["wa"], q - win["wa"]]}
            for key in ("A", "B"):
                c = win[key]
                base[key] = self.layer(c, "V1", win["wa"], nw + 2, handles=True) if c is not None else None
            spec["base"] = base
        else:
            act = self._active(self.v1, mid)
            if act:
                c = max(act, key=lambda x: x.start)
                lay = self.layer(c, "V1", p, n)
                spec["base"] = {"type": "clip", **lay} if lay else {"type": "black"}
            else:
                spec["base"] = {"type": "black"}
        overlays = []
        for track, clips in (("V2", self.v2), ("V3", self.v3)):
            for c in sorted(clips, key=lambda x: x.start):
                fs, fe = max(p, self.fr(c.start)), min(q, self.fr(c.end))
                if fe - fs < 1:
                    continue
                lay = self.layer(c, track, fs, fe - fs)
                if lay:
                    lay["rel0"] = fs - p
                    overlays.append(lay)
        if self.before:
            overlays = [o for o in overlays if o["track"] == "V2"]
        # V2 pełnoekranowy i nieprzezroczysty na całej długości przykrywa wszystko pod sobą -> nie dekodujemy V1
        cover_idx = None
        for i, o in enumerate(overlays):
            if o["track"] == "V2" and o.get("rel0", 0) == 0 and o["n"] == n and o["opacity"] >= 0.999                     and o.get("fit") in ("cover", "reframe") and _min_scale(o) >= 0.999:
                cover_idx = i
        if cover_idx is not None:
            o_base = dict(overlays[cover_idx])
            o_base.pop("rel0", None)
            spec["base"] = {"type": "clip", **o_base}
            overlays = overlays[cover_idx + 1:]
        spec["overlays"] = overlays
        if not self.before:
            titles = []
            for t in self.titles:
                if t.start < q / self.F and t.end > p / self.F and t.text.strip():
                    titles.append({"text": t.text, "style": t.style, "position": t.position,
                                   "params": t.params, "rs": round(t.start - p / self.F, 4),
                                   "dur": round(t.duration, 4)})
            if titles:
                spec["titles"] = titles
                spec["vw"], spec["vh"] = self.variant.width, self.variant.height
        return spec


# ---------------------------------------------------------------------------------------------
def effect_range(clip: VClip, e) -> tuple[float, float]:
    """Zakres źródła [src_t0, src_t1], który pokrywa plik `output` efektu (domyślnie cały klip)."""
    p = e.params or {}
    e0 = p.get("src_t0", clip.src_in)
    e1 = p.get("src_t1", clip.src_out)
    try:
        return float(e0), float(e1)
    except (TypeError, ValueError):
        return float(clip.src_in), float(clip.src_out)


def _has_alpha(info: dict) -> bool:
    pix = str(info.get("pix_fmt", "") or "")
    return any(pix.startswith(a) for a in ALPHA_PIX)


def _min_scale(lay: dict) -> float:
    kf = lay.get("kf") or []
    return min([k[1] for k in kf] or [1.0])


def _restrict_kf(kf: list[list], a: float, b: float) -> list[list]:
    """Tylko klatki kluczowe wpływające na [a, b] (wartości poza zakresem są stałe)."""
    if len(kf) <= 2:
        return kf
    ts = [k[0] for k in kf]
    i0 = max(0, int(np.searchsorted(ts, a, side="right")) - 1)
    i1 = min(len(kf), int(np.searchsorted(ts, b, side="left")) + 1)
    return kf[i0:i1] or [kf[-1]]


def _face_center(media: MediaItem, src_t: float) -> tuple[float, float] | None:
    for s in media.analysis.shots:
        if s.s <= src_t <= s.e and s.face_box:
            return float(s.face_box[0]), float(s.face_box[1])
    return None


def _lut_sigs(color_filter: str, clip: VClip) -> dict:
    """mtime plików LUT użytych w łańcuchu koloru (zmiana LUT = nowy hash)."""
    import re

    out = {}
    cands = set(re.findall(r"(?:file|filename)=\\?'?([^:'\\,]+(?:\\:[^'\\,]+)?)", color_filter))
    if clip.grade.lut:
        cands.add(clip.grade.lut)
    for c in cands:
        p = c.replace("\\:", ":")
        if os.path.exists(p):
            out[p.lower()] = int(os.path.getmtime(p))
    return out


def plan_render(project, tl: Timeline, variant: Variant, *, W: int, H: int, fps: Fraction, quality: str,
                t0: float = 0.0, t1: float | None = None, before: bool = False, ramp_steps: int = 8,
                max_slice_s: float = 6.0, use_proxy: bool | None = None) -> tuple[RenderPlan, Planner]:
    pl = Planner(project, tl, variant, W=W, H=H, fps=fps, quality=quality, before=before,
                 ramp_steps=ramp_steps, max_slice_s=max_slice_s, use_proxy=use_proxy)
    end = tl.duration if t1 is None else float(t1)
    FA = pl.fr(max(0.0, t0))
    FB = max(FA + 1, pl.fr(end))
    jobs = pl.plan(FA, FB)
    plan = RenderPlan(W=W, H=H, fps=fps, FA=FA, FB=FB, quality=quality, variant=variant, slices=jobs,
                      titles=pl.titles, warnings=pl.warnings,
                      windows=[{"wa": w["wa"], "wb": w["wb"], "kind": w["kind"]} for w in pl.windows])
    return plan, pl


def log_warnings(plan: RenderPlan, project_id: str | None) -> None:
    for w in plan.warnings:
        log("render", w, path="Render", level="warn", project_id=project_id)

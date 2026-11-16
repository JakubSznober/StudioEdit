"""Budowa grafów filtrów ffmpeg dla plastrów: źródło, prędkość, kolor, efekty, kadrowanie (subpikselowe),
przejścia (xfade), nakładki V2/V3 i tytuły (ASS). Wszystko z jednej specyfikacji plastra (slices.py).

Kadrowanie/transformacje: okno w pikselach źródła (środek, rozmiar, obrót) liczone wyrażeniami czasu
i przekazywane do filtra `perspective` (interpolacja subpikselowa, eval=frame) -> brak „schodkowania”
i drgań typowych dla `crop`/`zoompan` z całkowitymi współrzędnymi.
"""
from __future__ import annotations

import functools
import json
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from .. import paths
from ..media.ffmpeg import escape_filter_path

FONTS_DIR = Path(r"C:\Windows\Fonts")


def _num(x: float) -> str:
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


# ---------------------------------------------------------------------------------------------
# Graf
# ---------------------------------------------------------------------------------------------
class Graph:
    def __init__(self):
        self.inputs: list[list[str]] = []
        self.lines: list[str] = []
        self._n = 0

    def label(self, base: str = "s") -> str:
        self._n += 1
        return f"{base}{self._n}"

    def add_input(self, args: list[str]) -> int:
        self.inputs.append([str(a) for a in args])
        return len(self.inputs) - 1

    def add(self, line: str) -> None:
        self.lines.append(line)

    def chain(self, src: str, filters: list[str], base: str = "s") -> str:
        fl = [f for f in filters if f]
        if not fl:
            return src
        out = self.label(base)
        self.add(f"[{src}]{','.join(fl)}[{out}]")
        return out

    def script(self) -> str:
        return ";\n".join(self.lines)

    def input_args(self) -> list[str]:
        out: list[str] = []
        for a in self.inputs:
            out += a
        return out


# ---------------------------------------------------------------------------------------------
# Wyrażenia czasu (klatki kluczowe, ścieżki reframe)
# ---------------------------------------------------------------------------------------------
def _ease(u: str, ease: str) -> str:
    if ease == "linear":
        return u
    if ease == "hold":
        return "0"
    return f"({u})*({u})*(3-2*({u}))"


def kf_expr(kf: list[list], idx: int, T: str) -> str:
    """Wyrażenie wartości parametru (idx: 1=scale, 2=x, 3=y, 4=rot) od czasu T (sek. od początku klipu)."""
    if not kf:
        return {1: "1", 2: "0", 3: "0", 4: "0"}[idx]
    vals = [k[idx] for k in kf]
    if len(kf) == 1 or max(vals) - min(vals) < 1e-9:
        return _num(vals[0])
    expr = _num(vals[-1])
    for i in range(len(kf) - 2, -1, -1):
        t0, t1 = kf[i][0], kf[i + 1][0]
        v0, v1 = kf[i][idx], kf[i + 1][idx]
        span = max(1e-6, t1 - t0)
        u = f"clip(({T}-{_num(t0)})/{_num(span)},0,1)"
        seg = _num(v0) if abs(v1 - v0) < 1e-9 else f"({_num(v0)}+{_num(v1 - v0)}*{_ease(u, kf[i][5])})"
        expr = f"if(lt({T},{_num(t1)}),{seg},{expr})"
    if kf[0][0] > 0:
        expr = f"if(lt({T},{_num(kf[0][0])}),{_num(vals[0])},{expr})"
    return expr


def path_expr(path: list[list], idx: int, T: str) -> str:
    """Liniowa interpolacja wygładzonej ścieżki reframe (idx 1=cx, 2=cy; wartości 0..1)."""
    if not path:
        return "0.5"
    if len(path) == 1:
        return _num(path[0][idx])
    expr = _num(path[-1][idx])
    for i in range(len(path) - 2, -1, -1):
        t0, t1 = path[i][0], path[i + 1][0]
        v0, v1 = path[i][idx], path[i + 1][idx]
        span = max(1e-6, t1 - t0)
        seg = _num(v0) if abs(v1 - v0) < 1e-7 else \
            f"({_num(v0)}+{_num(v1 - v0)}*clip(({T}-{_num(t0)})/{_num(span)},0,1))"
        expr = f"if(lt({T},{_num(t1)}),{seg},{expr})"
    return f"if(lt({T},{_num(path[0][0])}),{_num(path[0][idx])},{expr})"


def _kf_static(kf: list[list]) -> bool:
    if not kf:
        return True
    for idx in (1, 2, 3, 4):
        vals = [k[idx] for k in kf]
        if max(vals) - min(vals) > 1e-9:
            return False
    return True


# ---------------------------------------------------------------------------------------------
# Geometria warstwy
# ---------------------------------------------------------------------------------------------
def _perspective(cx: str, cy: str, hw: str, hh: str, rot: str | None, interp: str, dynamic: bool) -> str:
    if rot is None:
        xs = (f"{cx}-{hw}", f"{cx}+{hw}", f"{cx}-{hw}", f"{cx}+{hw}")
        ys = (f"{cy}-{hh}", f"{cy}-{hh}", f"{cy}+{hh}", f"{cy}+{hh}")
    else:
        a = f"(-({rot})*PI/180)"
        c, s = f"cos({a})", f"sin({a})"
        corners = ((-1, -1), (1, -1), (-1, 1), (1, 1))
        xs = tuple(f"{cx}+({sx}*{hw})*{c}-({sy}*{hh})*{s}" for sx, sy in corners)
        ys = tuple(f"{cy}+({sx}*{hw})*{s}+({sy}*{hh})*{c}" for sx, sy in corners)
    parts = [f"x{i}='{xs[i]}':y{i}='{ys[i]}'" for i in range(4)]
    return "perspective=" + ":".join(parts) + f":interpolation={interp}:sense=source:eval={'frame' if dynamic else 'init'}"


def geometry(lay: dict, W: int, H: int, F: Fraction, quality: str) -> tuple[list[str], list[str] | None]:
    """Zwraca (prescale_filters, filtry_geometrii). Dla trybu 'blur' drugi element to None (obsługa w build)."""
    src = lay["src"]
    w = float(src.get("w") or W)
    h = float(src.get("h") or H)
    fit = lay.get("fit", "cover")
    kf = lay.get("kf") or []
    smax = max([k[1] for k in kf] or [1.0])
    interp = "linear" if quality == "preview" else "cubic"
    T = f"({_num(lay['tau0'])}+in/{_num(float(F))})"
    flags = "bicubic" if quality != "preview" else "bilinear"
    if fit in ("cover", "reframe"):
        src_ar, dst_ar = w / h, W / H
        wn = min(1.0, dst_ar / src_ar)  # szerokość okna (część szerokości źródła) przy skali 1
        hn = min(1.0, src_ar / dst_ar)
        f = min(1.0, 1.1 * W * max(1.0, smax) / max(1.0, wn * w))
        pre: list[str] = []
        w2, h2 = int(w), int(h)
        if f < 0.98:
            w2, h2 = _even(w * f), _even(h * f)
            pre = [f"scale={w2}:{h2}:flags={flags}"]
        ww0, hh0 = wn * w2, hn * h2
        path = lay.get("path") if fit == "reframe" else None
        center = lay.get("center") or [0.5, 0.5]
        dynamic = not _kf_static(kf) or (path is not None and len(path) > 1)
        rot_vals = [k[4] for k in kf] if kf else [0.0]
        has_rot = any(abs(r) > 1e-6 for r in rot_vals)
        if not dynamic and not has_rot:
            s = kf[0][1] if kf else 1.0
            x = kf[0][2] if kf else 0.0
            y = kf[0][3] if kf else 0.0
            cxn = path[0][1] if path else center[0]
            cyn = path[0][2] if path else center[1]
            ww, hh = ww0 / s, hh0 / s
            cx = cxn * w2 - x * ww0 / s
            cy = cyn * h2 - y * hh0 / s
            if ww <= w2:
                cx = min(max(cx, ww / 2), w2 - ww / 2)
            if hh <= h2:
                cy = min(max(cy, hh / 2), h2 - hh / 2)
            if abs(ww - w2) < 1 and abs(hh - h2) < 1:
                return pre, [f"scale={W}:{H}:flags={flags}"]
            if s >= 1.0:
                cw, ch = max(2, int(round(ww))), max(2, int(round(hh)))
                cw, ch = min(cw, int(w2)), min(ch, int(h2))
                x0 = int(round(min(max(0, cx - cw / 2), w2 - cw)))
                y0 = int(round(min(max(0, cy - ch / 2), h2 - ch)))
                return pre, [f"crop={cw}:{ch}:{x0}:{y0}", f"scale={W}:{H}:flags={flags}"]
        s_e = kf_expr(kf, 1, T)
        x_e = kf_expr(kf, 2, T)
        y_e = kf_expr(kf, 3, T)
        r_e = kf_expr(kf, 4, T) if has_rot else None
        cxn_e = path_expr(path, 1, T) if path else _num(center[0])
        cyn_e = path_expr(path, 2, T) if path else _num(center[1])
        hw = f"({_num(ww0 / 2)}/({s_e}))"
        hh = f"({_num(hh0 / 2)}/({s_e}))"
        cx = f"(({cxn_e})*{w2}-({x_e})*{_num(ww0)}/({s_e}))"
        cy = f"(({cyn_e})*{h2}-({y_e})*{_num(hh0)}/({s_e}))"
        if not has_rot and min([k[1] for k in kf] or [1.0]) >= 1.0:
            cx = f"clip({cx},{hw},{w2}-{hw})"
            cy = f"clip({cy},{hh},{h2}-{hh})"
        return pre, [_perspective(cx, cy, hw, hh, r_e, interp, dynamic), f"scale={W}:{H}:flags={flags}"]
    # blur / contain_alpha: transformacja na płótnie
    return [], None


def canvas_transform(lay: dict, Wk: int, Hk: int, W: int, H: int, F: Fraction, quality: str) -> list[str]:
    kf = lay.get("kf") or []
    flags = "bicubic" if quality != "preview" else "bilinear"
    if not kf or (all(abs(k[1] - 1) < 1e-6 and abs(k[2]) < 1e-9 and abs(k[3]) < 1e-9 and abs(k[4]) < 1e-9
                      for k in kf)):
        return [f"scale={W}:{H}:flags={flags}"] if (Wk, Hk) != (W, H) else []
    T = f"({_num(lay['tau0'])}+in/{_num(float(F))})"
    s_e, x_e, y_e = kf_expr(kf, 1, T), kf_expr(kf, 2, T), kf_expr(kf, 3, T)
    has_rot = any(abs(k[4]) > 1e-6 for k in kf)
    r_e = kf_expr(kf, 4, T) if has_rot else None
    hw = f"({_num(Wk / 2)}/({s_e}))"
    hh = f"({_num(Hk / 2)}/({s_e}))"
    cx = f"({_num(Wk / 2)}-({x_e})*{Wk}/({s_e}))"
    cy = f"({_num(Hk / 2)}-({y_e})*{Hk}/({s_e}))"
    interp = "linear" if quality == "preview" else "cubic"
    out = [_perspective(cx, cy, hw, hh, r_e, interp, not _kf_static(kf))]
    if (Wk, Hk) != (W, H):
        out.append(f"scale={W}:{H}:flags={flags}")
    return out


# ---------------------------------------------------------------------------------------------
# Efekty-filtry
# ---------------------------------------------------------------------------------------------
def _fx_simple(fx: dict) -> str:
    t = fx.get("type")
    p = fx.get("params") or {}
    s = float(p.get("strength", p.get("amount", 0.5)) or 0.5)
    s = min(1.0, max(0.0, s))
    if t == "vignette":
        return f"vignette=angle={_num(0.25 + 0.55 * s)}"
    if t == "film_grain":
        return f"noise=alls={int(4 + 18 * s)}:allf=t"
    if t == "sharpen":
        return f"unsharp=5:5:{_num(0.3 + 1.2 * s)}:5:5:0"
    if t == "denoise_video":
        return f"hqdn3d={_num(2 + 6 * s)}:{_num(1.5 + 4 * s)}:{_num(3 + 6 * s)}:{_num(2.5 + 5 * s)}"
    return ""


def apply_fx(g: Graph, label: str, fxs: list[dict]) -> str:
    for fx in fxs:
        if fx.get("type") == "blur_region":
            p = fx.get("params") or {}
            box = p.get("box") or p.get("rect")
            if not box or len(box) != 4:
                continue
            x, y, bw, bh = (min(1.0, max(0.0, float(v))) for v in box)
            if bw <= 0.005 or bh <= 0.005:
                continue
            m, r, rb, o = g.label("m"), g.label("r"), g.label("rb"), g.label("o")
            g.add(f"[{label}]split=2[{m}][{r}]")
            g.add(f"[{r}]crop=w='trunc(iw*{_num(bw)}/2)*2':h='trunc(ih*{_num(bh)}/2)*2':"
                  f"x='trunc(iw*{_num(x)})':y='trunc(ih*{_num(y)})',"
                  f"boxblur=luma_radius='min(w,h)/5':luma_power=3:chroma_radius='min(cw,ch)/5':chroma_power=3[{rb}]")
            g.add(f"[{m}][{rb}]overlay=x='trunc(main_w*{_num(x)})':y='trunc(main_h*{_num(y)})'[{o}]")
            label = o
        else:
            f = _fx_simple(fx)
            if f:
                label = g.chain(label, [f])
    return label


# ---------------------------------------------------------------------------------------------
# Warstwa klipu -> strumień W×H o dokładnie n klatkach
# ---------------------------------------------------------------------------------------------
def layer_stream(g: Graph, lay: dict, W: int, H: int, F: Fraction, quality: str, *, hw_decode: bool,
                 alpha_out: bool = False, pix: str = "yuv420p") -> str:
    src = lay["src"]
    n = int(lay["n"])
    Fs = str(F)
    Ff = float(F)
    speed = float(lay.get("speed", 1.0))
    pre = float(lay.get("pre", 0.0))
    path = src["path"]
    if src.get("image"):
        idx = g.add_input(["-loop", "1", "-framerate", Fs, "-t", f"{n / Ff + 1.0:.4f}", "-i", path])
        filters = [f"fps=fps={Fs}"]
    else:
        s_a = float(lay["s_a"])
        dur = float(src.get("dur") or 0)
        if dur > 0 and s_a > dur - 0.05:
            s_a = max(0.0, dur - 0.05)
        src_len = max(0.2, (n / Ff - pre) * speed + 0.5)
        args = []
        if hw_decode and not src.get("alpha"):
            args += ["-hwaccel", "cuda"]
        args += ["-ss", f"{s_a:.4f}", "-t", f"{src_len:.4f}", "-i", path]
        idx = g.add_input(args)
        # NVDEC oddaje klatki NV12/P010 (semi-planar) - część filtrów CPU (boxblur, tmix...) psuje na nich
        # chrominancję, więc od razu normalizujemy do formatu planarnego
        filters = [f"format={'yuv420p10le' if pix.endswith('10le') else 'yuv420p'}"] if "-hwaccel" in args else []
    pre_f, geo = geometry(lay, W, H, F, quality)
    filters += pre_f
    if not src.get("image"):
        if abs(speed - 1.0) > 1e-4:
            filters.append(f"setpts=PTS/{_num(speed)}")
        interp = lay.get("interp")
        if interp == "optical":
            filters.append(f"minterpolate=fps={Fs}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
        elif interp == "blend":
            filters.append(f"framerate=fps={Fs}:interp_start=0:interp_end=255:scene=100")
        filters.append(f"fps=fps={Fs}:start_time=0")
        if pre > 1e-4:
            filters.append(f"tpad=start_duration={pre:.4f}:start_mode=clone")
    filters += [f"tpad=stop_mode=clone:stop_duration={n / Ff + 0.5:.4f}", f"trim=end_frame={n}",
                "setpts=PTS-STARTPTS"]
    if lay.get("color"):
        filters.append(lay["color"])
    label = g.chain(f"{idx}:v", filters, "c")
    if lay.get("fx"):
        label = apply_fx(g, label, lay["fx"])
    fit = lay.get("fit", "cover")
    if geo is not None:
        label = g.chain(label, geo, "g")
    elif fit == "blur":
        kf = lay.get("kf") or []
        smax = max([k[1] for k in kf] or [1.0])
        k = min(1.5, max(1.0, smax)) if kf else 1.0
        Wk, Hk = _even(W * k), _even(H * k)
        sw, sh = _even(W / 8), _even(H / 8)
        r = max(2, min(10, min(sw, sh) // 10))
        a, b, bg, fg, cv = g.label("a"), g.label("b"), g.label("bg"), g.label("fg"), g.label("cv")
        g.add(f"[{label}]split=2[{a}][{b}]")
        g.add(f"[{a}]scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=bilinear,crop={sw}:{sh},"
              f"boxblur=luma_radius={r}:luma_power=3:chroma_radius={max(1, r // 2)}:chroma_power=3,"
              f"scale={Wk}:{Hk}:flags=bicubic,eq=brightness=-0.07:saturation=0.85[{bg}]")
        g.add(f"[{b}]scale={Wk}:{Hk}:force_original_aspect_ratio=decrease:flags=bicubic,setsar=1[{fg}]")
        g.add(f"[{bg}][{fg}]overlay=x=(W-w)/2:y=(H-h)/2[{cv}]")
        label = g.chain(cv, canvas_transform(lay, Wk, Hk, W, H, F, quality), "g")
    else:  # contain_alpha (grafiki V3)
        kf = lay.get("kf") or []
        smax = max([k[1] for k in kf] or [1.0])
        k = min(1.5, max(1.0, smax)) if kf else 1.0
        Wk, Hk = _even(W * k), _even(H * k)
        gs = float(lay.get("gscale") or 0) * k
        sc = (f"scale=w='max(2,trunc(iw*{_num(gs)}/2)*2)':h='max(2,trunc(ih*{_num(gs)}/2)*2)':flags=bicubic"
              if gs > 0 else f"scale={Wk}:{Hk}:force_original_aspect_ratio=decrease")
        label = g.chain(label, ["format=rgba", sc, f"pad={Wk}:{Hk}:(ow-iw)/2:(oh-ih)/2:color=black@0"], "g")
        label = g.chain(label, canvas_transform(lay, Wk, Hk, W, H, F, quality), "g")
    fin = ["setsar=1"]
    op = float(lay.get("opacity", 1.0))
    if alpha_out or fit == "contain_alpha":
        fin.append("format=yuva444p" if pix.endswith("10le") else "format=yuva420p")
        if op < 0.999:
            fin.append(f"colorchannelmixer=aa={_num(max(0.0, op))}")
    else:
        fin.append(f"format={pix}")
    return g.chain(label, fin, "L")


def black_stream(g: Graph, W: int, H: int, F: Fraction, n: int, pix: str, color: str = "black") -> str:
    out = g.label("k")
    g.add(f"color=c={color}:s={W}x{H}:r={F}:d={n / float(F) + 0.2:.4f},format={pix},setsar=1,"
          f"trim=end_frame={n},setpts=PTS-STARTPTS[{out}]")
    return out


# ---------------------------------------------------------------------------------------------
# Przejścia
# ---------------------------------------------------------------------------------------------
XFADE = {"dissolve": "fade", "dip_black": "fadeblack", "dip_white": "fadewhite", "whip": "slideleft",
         "light_leak": "fade", "zoom": "zoomin", "slide": "slideleft"}


def transition_stream(g: Graph, base: dict, W: int, H: int, F: Fraction, quality: str, *, hw_decode: bool,
                      pix: str) -> str:
    nw = int(base["nw"])
    Ff = float(F)
    D = nw / Ff
    streams = []
    for key in ("A", "B"):
        lay = base.get(key)
        if lay:
            lay = dict(lay)
            lay["opacity"] = 1.0
            streams.append(layer_stream(g, lay, W, H, F, quality, hw_decode=hw_decode, pix=pix))
        else:
            streams.append(black_stream(g, W, H, F, nw + 2, pix))
    kind = base.get("kind", "dissolve")
    x = g.label("x")
    if kind in ("dip_black", "dip_white"):
        # symetryczne „przejście przez kolor”: A ściemnia się do połowy okna, B wyłania się w drugiej połowie
        col = "black" if kind == "dip_black" else "white"
        h = max(1, nw // 2)
        a1, b1 = g.label("da"), g.label("db")
        g.add(f"[{streams[0]}]trim=end_frame={h},setpts=PTS-STARTPTS,fade=t=out:s=0:n={h}:color={col}[{a1}]")
        g.add(f"[{streams[1]}]trim=start_frame={h}:end_frame={nw + 2},setpts=PTS-STARTPTS,"
              f"fade=t=in:s=0:n={max(1, nw - h)}:color={col}[{b1}]")
        g.add(f"[{a1}][{b1}]concat=n=2:v=1:a=0[{x}]")
    else:
        g.add(f"[{streams[0]}][{streams[1]}]xfade=transition={XFADE.get(kind, 'fade')}:duration={D:.6f}:offset=0[{x}]")
    if kind == "whip":
        # rozmycie ruchu w poziomie w środku przejścia (gblur kierunkowy; tmix z enable psuje chrominancję)
        x = g.chain(x, [f"gblur=sigma={max(2, W // 60)}:sigmaV=0.01:steps=2:"
                        f"enable='between(t,{_num(D * 0.2)},{_num(D * 0.8)})'"], "x")
    elif kind == "light_leak":
        lw, lh = 192, _even(192 * H / W)
        Dl = D + 3 / Ff
        env = f"pow(sin(PI*min(T/{_num(D)},1)),1.3)"
        blob = (f"exp(-(pow(X-W*(0.12+0.76*T/{_num(D)}),2)+pow(Y-H*0.32,2))/(2*pow(W*0.30,2)))")
        blob2 = (f"exp(-(pow(X-W*(0.85-0.5*T/{_num(D)}),2)+pow(Y-H*0.8,2))/(2*pow(W*0.22,2)))")
        lk, xg, bl = g.label("lk"), g.label("xg"), g.label("bl")
        g.add(f"color=c=black:s={lw}x{lh}:r={F}:d={Dl:.4f},format=gbrp,"
              f"geq=r='255*{env}*min(1,{blob}+0.6*{blob2})':g='175*{env}*min(1,{blob}+0.5*{blob2})':"
              f"b='70*{env}*{blob}',scale={W}:{H}:flags=bicubic,gblur=sigma={max(2, W // 160)},"
              f"trim=end_frame={nw + 2},setpts=PTS-STARTPTS[{lk}]")
        g.add(f"[{x}]format=gbrp[{xg}]")
        g.add(f"[{xg}][{lk}]blend=all_mode=screen:shortest=1,format={pix}[{bl}]")
        x = bl
    k0, k1 = base["trim"]
    return g.chain(x, [f"trim=start_frame={int(k0)}:end_frame={int(k1)}", "setpts=PTS-STARTPTS", "setsar=1",
                       f"format={pix}"], "t")


# ---------------------------------------------------------------------------------------------
# Plaster -> graf
# ---------------------------------------------------------------------------------------------
def build_slice_graph(spec: dict, *, hw_decode: bool, pix: str, titles_ass: Path | None,
                      subs_ass: Path | None = None, t_abs: float = 0.0) -> tuple[Graph, str]:
    W, H = int(spec["W"]), int(spec["H"])
    F = Fraction(spec["fps"])
    n = int(spec["n"])
    q = spec["quality"]
    g = Graph()
    base = spec["base"]
    if base["type"] == "clip":
        lay = {k: v for k, v in base.items() if k != "type"}
        op = float(lay.get("opacity", 1.0))
        if op < 0.999:
            bk = black_stream(g, W, H, F, n, pix)
            st = layer_stream(g, lay, W, H, F, q, hw_decode=hw_decode, alpha_out=True, pix=pix)
            cur = g.label("b")
            g.add(f"[{bk}][{st}]overlay=0:0:format={'yuv420p10' if pix.endswith('10le') else 'yuv420'}[{cur}]")
        else:
            cur = layer_stream(g, lay, W, H, F, q, hw_decode=hw_decode, pix=pix)
    elif base["type"] == "transition":
        cur = transition_stream(g, base, W, H, F, q, hw_decode=hw_decode, pix=pix)
    else:
        cur = black_stream(g, W, H, F, n, pix)
    for ov in spec.get("overlays", []):
        st = layer_stream(g, ov, W, H, F, q, hw_decode=hw_decode, alpha_out=True, pix=pix)
        rel0 = int(ov.get("rel0", 0))
        if rel0 > 0:
            st = g.chain(st, [f"setpts=PTS+{rel0}/({F})/TB"], "d")
        out = g.label("o")
        g.add(f"[{cur}][{st}]overlay=0:0:eof_action=pass:format={'yuv420p10' if pix.endswith('10le') else 'yuv420'}"
              f"[{out}]")
        cur = out
    p_abs = float(Fraction(int(spec.get("_p", 0))) / F) if "_p" in spec else t_abs
    for ass in (titles_ass if spec.get("titles") else None, subs_ass):
        if ass is None:
            continue
        cur = g.chain(cur, [f"setpts=PTS+{_num(p_abs)}/TB",
                            f"ass=filename='{escape_filter_path(ass)}':fontsdir='{escape_filter_path(FONTS_DIR)}'",
                            "setpts=PTS-STARTPTS"], "a")
    cur = g.chain(cur, [f"trim=end_frame={n}", f"format={pix}"], "out")
    return g, cur


# ---------------------------------------------------------------------------------------------
# Czcionki i pomiar tekstu (tytuły, QC napisów)
# ---------------------------------------------------------------------------------------------
_KNOWN_FONTS = {
    "segoe ui black": "seguibl.ttf", "segoe ui": "segoeui.ttf", "segoe ui bold": "segoeuib.ttf",
    "segoe ui semibold": "seguisb.ttf", "segoe ui light": "segoeuil.ttf", "segoe ui semilight": "segoeuisl.ttf",
    "arial": "arial.ttf", "arial bold": "arialbd.ttf", "arial black": "ariblk.ttf", "impact": "impact.ttf",
    "bahnschrift": "bahnschrift.ttf", "calibri": "calibri.ttf", "verdana": "verdana.ttf",
    "tahoma": "tahoma.ttf", "georgia": "georgia.ttf", "times new roman": "times.ttf",
}


@functools.lru_cache(maxsize=1)
def _font_index() -> dict[str, str]:
    cache = paths.CACHE / "fonts_index.json"
    try:
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        pass
    idx: dict[str, str] = {}
    try:
        from PIL import ImageFont

        for f in FONTS_DIR.glob("*.tt[fc]"):
            try:
                fam, style = ImageFont.truetype(str(f), 12).getname()
                key = fam.lower() if style.lower() in ("regular", "normal") else f"{fam} {style}".lower()
                idx.setdefault(key, str(f))
                idx.setdefault(fam.lower(), str(f))
            except Exception:
                continue
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(idx), encoding="utf-8")
    except Exception:
        pass
    return idx


def font_file(family: str, bold: bool = False) -> str | None:
    fam = (family or "Segoe UI").strip().lower()
    if bold and f"{fam} bold" in _KNOWN_FONTS:
        fam = f"{fam} bold"
    if fam in _KNOWN_FONTS and (FONTS_DIR / _KNOWN_FONTS[fam]).exists():
        return str(FONTS_DIR / _KNOWN_FONTS[fam])
    idx = _font_index()
    if bold and f"{fam} bold" in idx:
        return idx[f"{fam} bold"]
    if fam in idx:
        return idx[fam]
    fb = FONTS_DIR / "segoeui.ttf"
    return str(fb) if fb.exists() else None


@functools.lru_cache(maxsize=64)
def _font_obj(path: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(path, size)


@functools.lru_cache(maxsize=64)
def _em_ratio(path: str) -> float:
    """ASS: rozmiar czcionki = wysokość komórki (ascent+descent). Zwraca em/komórkę."""
    f = _font_obj(path, 200)
    asc, desc = f.getmetrics()
    return 200.0 / max(1, asc + desc)


def text_width(text: str, family: str, ass_size: float, bold: bool = False, spacing: float = 0.0) -> float:
    """Szerokość tekstu (piksele PlayRes) dla rozmiaru czcionki ASS."""
    path = font_file(family, bold)
    if not path:
        return len(text) * ass_size * 0.55
    try:
        em = max(4, int(round(ass_size * _em_ratio(path))))
        f = _font_obj(path, em)
        return float(f.getlength(text)) + spacing * max(0, len(text) - 1)
    except Exception:
        return len(text) * ass_size * 0.55


def wrap_text(text: str, family: str, size: float, max_w: float, max_lines: int = 3) -> tuple[list[str], float]:
    """Zawijanie słów do szerokości; zmniejsza rozmiar, gdy nie mieści się w max_lines."""
    words = text.replace("\n", " \n ").split(" ")
    for _ in range(12):
        lines: list[str] = []
        cur = ""
        for wd in words:
            if wd == "\n":
                lines.append(cur.strip())
                cur = ""
                continue
            if not wd:
                continue
            cand = (cur + " " + wd).strip()
            if cur and text_width(cand, family, size) > max_w:
                lines.append(cur.strip())
                cur = wd
            else:
                cur = cand
        if cur.strip():
            lines.append(cur.strip())
        if len(lines) <= max_lines and all(text_width(ln, family, size) <= max_w for ln in lines):
            return lines, size
        size *= 0.9
    return lines, size


# ---------------------------------------------------------------------------------------------
# Tytuły (TitleClip) -> ASS
# ---------------------------------------------------------------------------------------------
def _ass_color(hexcol: str | None, alpha: int = 0, default: str = "#FFFFFF") -> str:
    h = (hexcol or default).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = default.lstrip("#")
    r, g_, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g_}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    hh, rem = divmod(cs, 360000)
    mm, rem = divmod(rem, 6000)
    ss, cc = divmod(rem, 100)
    return f"{hh}:{mm:02d}:{ss:02d}.{cc:02d}"


def _ass_escape(s: str) -> str:
    return s.replace("\\", "/").replace("{", "(").replace("}", ")")


def title_events(t: dict, vw: int, vh: int) -> list[str]:
    """Zdarzenia ASS dla jednego tytułu (czasy absolutne osi: t['start'], t['end'])."""
    unit = min(vw, vh)
    vertical = vh > vw
    p = t.get("params") or {}
    style = t.get("style") or "lower_third"
    pos = t.get("position") or ("bottom" if style in ("lower_third", "caption") else "middle")
    st, en = _ass_time(t["start"]), _ass_time(t["end"])
    font = p.get("font") or ("Segoe UI Black" if style != "caption" else "Segoe UI Semibold")
    color = _ass_color(p.get("color"), 0, "#FFFFFF")
    accent = _ass_color(p.get("accent"), 0, "#FFD400")
    safe_w = vw * (0.80 if vertical else 0.86)
    ev = []
    text = str(t.get("text", "")).strip()
    if p.get("uppercase"):
        text = text.upper()
    if style == "lower_third":
        size = float(p.get("size") or unit * (0.050 if vertical else 0.056))
        sub = str(p.get("subtitle") or "").strip()
        sub_size = size * 0.62
        lines, size = wrap_text(text, font, size, safe_w * 0.8, 2)
        sub_lines, sub_size = wrap_text(sub, "Segoe UI Semibold", sub_size, safe_w * 0.8, 1) if sub else ([], sub_size)
        pad = unit * 0.022
        ml = vw * (0.07 if vertical else 0.06)
        mb = vh * (0.24 if vertical else 0.11) if pos == "bottom" else vh * 0.5 if pos == "middle" else vh * 0.22
        tw = max([text_width(ln, font, size) for ln in lines] +
                 [text_width(ln, "Segoe UI Semibold", sub_size) for ln in sub_lines] + [10])
        th = size * 1.05 * len(lines) + (sub_size * 1.1 * len(sub_lines))
        bw, bh = tw + 2 * pad + unit * 0.012, th + 2 * pad
        top = vh - mb - bh
        bar = unit * 0.010
        slide = unit * 0.04
        box = f"m 0 0 l {bw:.0f} 0 l {bw:.0f} {bh:.0f} l 0 {bh:.0f}"
        acc = f"m 0 0 l {bar:.0f} 0 l {bar:.0f} {bh:.0f} l 0 {bh:.0f}"
        ev.append(f"Dialogue: 0,{st},{en},Box,,0,0,0,,{{\\an7\\move({ml - slide:.0f},{top:.0f},{ml:.0f},{top:.0f},0,350)"
                  f"\\fad(250,250)\\1c&H000000&\\1a&H50&\\bord0\\shad0\\p1}}{box}")
        ev.append(f"Dialogue: 1,{st},{en},Box,,0,0,0,,{{\\an7\\move({ml - slide:.0f},{top:.0f},{ml:.0f},{top:.0f},0,350)"
                  f"\\fad(250,250)\\1c{accent}\\1a&H00&\\bord0\\shad0\\p1}}{acc}")
        tx = ml + bar + pad
        ty = top + pad
        main = "\\N".join(_ass_escape(x) for x in lines)
        ev.append(f"Dialogue: 2,{st},{en},Title,,0,0,0,,{{\\an7\\move({tx - slide:.0f},{ty:.0f},{tx:.0f},{ty:.0f},0,350)"
                  f"\\fad(250,250)\\fn{font}\\fs{size:.0f}\\1c{color}\\bord0\\shad0}}{main}")
        if sub_lines:
            sy = ty + size * 1.05 * len(lines)
            ev.append(f"Dialogue: 2,{st},{en},Title,,0,0,0,,{{\\an7\\move({tx - slide:.0f},{sy:.0f},{tx:.0f},{sy:.0f},50,400)"
                      f"\\fad(300,250)\\fnSegoe UI Semibold\\fs{sub_size:.0f}\\1c&HDDDDDD&\\bord0\\shad0}}"
                      f"{_ass_escape(sub_lines[0])}")
        return ev
    if style == "title":
        size = float(p.get("size") or unit * (0.085 if vertical else 0.095))
        lines, size = wrap_text(text, font, size, safe_w, 3)
        an = {"top": 8, "middle": 5, "bottom": 2}.get(pos, 5)
        x = vw / 2
        y = {8: vh * 0.16, 5: vh * 0.46, 2: vh * (0.76 if vertical else 0.84)}[an]
        if "x" in p and "y" in p:
            x, y = float(p["x"]) * vw, float(p["y"]) * vh
        body = "\\N".join(_ass_escape(s) for s in lines)
        ev.append(f"Dialogue: 2,{st},{en},Title,,0,0,0,,{{\\an{an}\\pos({x:.0f},{y:.0f})\\fad(300,300)"
                  f"\\fscx92\\fscy92\\t(0,450,\\fscx100\\fscy100)\\fn{font}\\fs{size:.0f}\\1c{color}"
                  f"\\3c&H000000&\\bord{unit * 0.004:.1f}\\shad{unit * 0.003:.1f}\\4a&H80&}}{body}")
        return ev
    if style == "callout":
        size = float(p.get("size") or unit * 0.05)
        lines, size = wrap_text(text, font, size, safe_w * 0.9, 2)
        an = {"top": 8, "middle": 5, "bottom": 2}.get(pos if pos != "bottom" else "top", 8)
        x, y = vw / 2, (vh * (0.14 if vertical else 0.1) if an == 8 else vh * 0.5)
        if "x" in p and "y" in p:
            x, y = float(p["x"]) * vw, float(p["y"]) * vh
        body = "\\N".join(_ass_escape(s) for s in lines)
        ev.append(f"Dialogue: 2,{st},{en},Callout,,0,0,0,,{{\\an{an}\\pos({x:.0f},{y:.0f})\\fad(200,200)"
                  f"\\fn{font}\\fs{size:.0f}\\1c&H101010&\\3c{accent}\\4c{accent}\\bord{unit * 0.012:.1f}"
                  f"\\shad0}}{body}")
        return ev
    # caption
    size = float(p.get("size") or unit * 0.045)
    lines, size = wrap_text(text, font, size, safe_w, 2)
    an = {"top": 8, "middle": 5, "bottom": 2}.get(pos, 2)
    x = vw / 2
    y = {8: vh * 0.12, 5: vh * 0.5, 2: vh * (0.72 if vertical else 0.9)}[an]
    body = "\\N".join(_ass_escape(s) for s in lines)
    ev.append(f"Dialogue: 2,{st},{en},Caption,,0,0,0,,{{\\an{an}\\pos({x:.0f},{y:.0f})\\fad(150,150)"
              f"\\fn{font}\\fs{size:.0f}\\1c{color}\\bord{unit * 0.010:.1f}\\shad0}}{body}")
    return ev


def titles_ass(titles: list, vw: int, vh: int, out_path: Path) -> Path:
    """Plik ASS z tytułami V3 w czasie absolutnym osi (PlayRes = rozmiar wariantu)."""
    unit = min(vw, vh)
    hdr = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {vw}", f"PlayResY: {vh}",
        "ScaledBorderAndShadow: yes", "WrapStyle: 2", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,Segoe UI Black,{unit * 0.06:.0f},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,"
        f"100,100,0,0,1,2,1,7,0,0,0,1",
        "Style: Box,Segoe UI,20,&H50000000,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Caption,Segoe UI Semibold,{unit * 0.045:.0f},&H00FFFFFF,&H00FFFFFF,&H60000000,&H60000000,0,0,0,0,"
        f"100,100,0,0,3,8,0,2,0,0,0,1",
        f"Style: Callout,Segoe UI Black,{unit * 0.05:.0f},&H00101010,&H00FFFFFF,&H0000D4FF,&H0000D4FF,0,0,0,0,"
        f"100,100,0,0,3,10,0,8,0,0,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines = list(hdr)
    for t in titles:
        d = t if isinstance(t, dict) else {"text": t.text, "style": t.style, "position": t.position,
                                           "params": t.params, "start": t.start, "end": t.end}
        if "end" not in d:
            d["end"] = d["start"] + d.get("duration", 0)
        if not str(d.get("text", "")).strip():
            continue
        lines += title_events(d, vw, vh)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return out_path

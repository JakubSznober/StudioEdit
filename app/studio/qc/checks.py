"""Agent Nadzorca Jakości: automatyczna kontrola renderu.

Sprawdza: czarne klatki, zamrożenia, przesterowania (true peak), głośność (integrated vs cel, dialog),
martwe cisze, jump cuty (SSIM ostatniej/pierwszej klatki), ucięte napisy (pomiar szerokości czcionką Windows,
bezpieczna strefa 9:16), synchronizację (audio vs wideo, A1 vs V1), zgodność techniczną i długość.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from ..events import log
from ..media import ffmpeg as ff
from ..project.model import AClip, Timeline, VClip, Variant
from ..render import presets
from ..render.filters import text_width

EFFORT_LEVEL = {"low": 0, "medium": 1, "high": 2, "max": 3}
CODEC_MAP = {"h264": "h264", "hevc": "hevc", "av1": "av1", "prores": "prores", "dnxhr": "dnxhd"}


# ---------------------------------------------------------------------------------------------
# Pomiary ffmpeg
# ---------------------------------------------------------------------------------------------
def video_scan(path: str, *, level: int, fps: float) -> dict:
    """blackdetect + freezedetect w jednym przebiegu (gęstość wg wysiłku)."""
    sample = {0: 5.0, 1: 15.0}.get(level)
    black_d = max(1.0 / fps, 1.0 / sample) if sample else 1.0 / fps
    freeze_d = {0: 2.0, 1: 1.5}.get(level, 1.0)
    chain = []
    if sample and sample < fps:
        chain.append(f"fps={sample}")
    chain += ["scale=320:-2", f"blackdetect=d={black_d * 0.99:.4f}:pix_th=0.10:pic_th=0.98",
              f"freezedetect=n=-60dB:d={freeze_d}"]
    _out, err = ff.run_capture(["-v", "info", "-i", path, "-an", "-sn", "-vf", ",".join(chain), "-f", "null", "-"],
                               timeout=3600)
    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", err)]
    fs = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", err)]
    fe = [float(x) for x in re.findall(r"freeze_end:\s*([\d.]+)", err)]
    freezes = []
    for i, a in enumerate(fs):
        b = fe[i] if i < len(fe) else None
        freezes.append((a, b))
    return {"black": blacks, "freeze": freezes}


def audio_scan(path: str, *, silence_db: float = -50.0, silence_d: float = 1.5) -> dict:
    """ebur128 (integrated, LRA, true peak, krzywa momentary) + silencedetect."""
    _out, err = ff.run_capture(["-v", "info", "-i", path, "-vn", "-sn", "-af",
                                f"ebur128=peak=true:framelog=info,silencedetect=n={silence_db}dB:d={silence_d}",
                                "-f", "null", "-"], timeout=3600)
    mom = [(float(t), float(m)) for t, m in re.findall(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+|-inf)", err)
           if m != "-inf"]
    summ = err[err.rfind("Summary:"):] if "Summary:" in err else ""

    def grab(pat: str) -> float | None:
        m = re.search(pat, summ, re.S)
        try:
            return float(m.group(1)) if m else None
        except ValueError:
            return None

    lufs = grab(r"I:\s*(-?[\d.]+)\s*LUFS")
    lra = grab(r"LRA:\s*(-?[\d.]+)\s*LU")
    tp = grab(r"True peak:\s*Peak:\s*(-?[\d.]+|-inf)")
    ss = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", err)]
    se = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", err)]
    sil = [(max(0.0, a), se[i] if i < len(se) else None) for i, a in enumerate(ss)]
    return {"lufs": lufs, "lra": lra, "true_peak": tp, "momentary": mom, "silence": sil}


def stream_durations(path: str) -> tuple[float | None, float | None]:
    try:
        import av  # type: ignore

        with av.open(path) as c:
            v = c.streams.video[0] if c.streams.video else None
            a = c.streams.audio[0] if c.streams.audio else None
            vd = float(v.duration * v.time_base) if v is not None and v.duration else None
            ad = float(a.duration * a.time_base) if a is not None and a.duration else None
            return vd, ad
    except Exception:
        return None, None


def grab_gray(path: str, t: float, width: int = 256) -> np.ndarray | None:
    try:
        out, _ = ff.run_capture(["-v", "error", "-ss", f"{max(0.0, t):.4f}", "-i", path, "-frames:v", "1",
                                 "-vf", f"scale={width}:-2,format=gray", "-f", "rawvideo", "-"], timeout=60)
    except Exception:
        return None
    if not out:
        return None
    h = len(out) // width
    if h <= 0:
        return None
    return np.frombuffer(out[: h * width], np.uint8).reshape(h, width).astype(np.float32)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    import cv2

    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(b, (11, 11), 1.5)
    saa = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a ** 2
    sbb = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b ** 2
    sab = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (saa + sbb + c2))
    return float(m.mean())


# ---------------------------------------------------------------------------------------------
# Napisy (ASS) - pomiar szerokości
# ---------------------------------------------------------------------------------------------
def _ass_styles(txt: str) -> tuple[dict, dict]:
    info = {"PlayResX": 384, "PlayResY": 288}
    for k in ("PlayResX", "PlayResY"):
        m = re.search(rf"^{k}:\s*(\d+)", txt, re.M)
        if m:
            info[k] = int(m.group(1))
    styles: dict[str, dict] = {}
    fmt = None
    for line in txt.splitlines():
        if line.startswith("Format:") and "Fontname" in line:
            fmt = [x.strip() for x in line[7:].split(",")]
        elif line.startswith("Style:") and fmt:
            vals = [x.strip() for x in line[6:].split(",")]
            d = dict(zip(fmt, vals))
            styles[d.get("Name", "Default")] = d
    return info, styles


def subtitle_issues(ass_path: str, *, kind: str = "subtitles", vertical: bool = False) -> list[dict]:
    """Linie wychodzące poza bezpieczny obszar (szerokość) i za nisko w 9:16 (interfejs aplikacji)."""
    try:
        txt = Path(ass_path).read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return []
    info, styles = _ass_styles(txt)
    W, H = info["PlayResX"], info["PlayResY"]
    out: list[dict] = []
    fmt = None
    for line in txt.splitlines():
        if line.startswith("Format:") and "Text" in line and "Start" in line:
            fmt = [x.strip() for x in line[7:].split(",")]
            continue
        if not line.startswith("Dialogue:") or not fmt:
            continue
        parts = line[9:].split(",", len(fmt) - 1)
        d = dict(zip(fmt, [p.strip() for p in parts]))
        text = d.get("Text", "")
        if "\\p1" in text or "\\p2" in text:
            continue
        st = styles.get(d.get("Style", ""), {})
        font = st.get("Fontname", "Segoe UI")
        try:
            size = float(st.get("Fontsize", 48))
        except ValueError:
            size = 48.0
        an = int(st.get("Alignment", 2) or 2)
        ml = int(d.get("MarginL") or 0) or int(st.get("MarginL", 10) or 10)
        mr = int(d.get("MarginR") or 0) or int(st.get("MarginR", 10) or 10)
        mv = int(d.get("MarginV") or 0) or int(st.get("MarginV", 10) or 10)
        tags = "".join(re.findall(r"\{([^}]*)\}", text))
        m = re.search(r"\\fn([^\\}]+)", tags)
        if m:
            font = m.group(1).strip()
        m = re.search(r"\\fs([\d.]+)", tags)
        if m:
            size = float(m.group(1))
        m = re.search(r"\\an(\d)", tags)
        if m:
            an = int(m.group(1))
        sx = 1.0
        m = re.search(r"\\fscx([\d.]+)", tags)
        if m:
            sx = float(m.group(1)) / 100
        pos = re.search(r"\\(?:pos|move)\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)", tags)
        clean = re.sub(r"\{[^}]*\}", "", text).replace("\\h", " ")
        lines = [x for x in re.split(r"\\[Nn]", clean) if x.strip()]
        if not lines:
            continue
        width = max(text_width(x, font, size, bold=st.get("Bold", "0") not in ("0", "")) for x in lines) * sx
        if pos:
            x = float(pos.group(1))
            col = an % 3
            left = x if col == 1 else (x - width / 2 if col == 2 else x - width)
            right = left + width
            y = float(pos.group(2))
        else:
            avail = W - ml - mr
            left = ml + (avail - width) / 2 if an % 3 == 2 else ml
            right = left + width
            y = (H - mv) if an <= 3 else (mv if an >= 7 else H / 2)
        t0, t1 = _ass_sec(d.get("Start", "0:00:00.00")), _ass_sec(d.get("End", "0:00:00.00"))
        safe_l, safe_r = W * 0.03, W * 0.97
        if left < safe_l - 1 or right > safe_r + 1:
            out.append({"kind": kind, "t0": t0, "t1": t1, "overflow": round(max(safe_l - left, right - safe_r), 1),
                        "text": clean[:60], "width": round(width, 1), "W": W})
        elif vertical and an <= 3 and y > H * 0.86:
            out.append({"kind": kind + "_unsafe", "t0": t0, "t1": t1, "y": round(y / H, 3), "text": clean[:60]})
    return out


def _ass_sec(s: str) -> float:
    try:
        h, m, rest = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------------------------
# Pomocnicze osi
# ---------------------------------------------------------------------------------------------
def _v(tl: Timeline, track: str) -> list[VClip]:
    return sorted([c for c in tl.tracks.get(track, []) if isinstance(c, VClip)], key=lambda c: c.start)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _scale_at(c: VClip, tau: float) -> float:
    from ..exports import kf_value

    return kf_value(c, tau, "scale")


def speech_ranges(project, tl: Timeline) -> list[tuple[float, float]]:
    out = []
    a1 = [c for c in tl.tracks.get("A1", []) if isinstance(c, AClip) and c.role == "dialog"]
    src: list[tuple[str | None, float, float, float, float]] = [(c.media_id, c.start, c.src_in, c.duration, 1.0)
                                                                for c in a1]
    if not a1:
        src = [(c.media_id, c.start, c.src_in, c.duration, max(0.01, (c.src_out - c.src_in) / max(1e-6, c.duration)))
               for c in _v(tl, "V1") if c.role == "a_roll"]
    for mid, start, s_in, dur, sp in src:
        try:
            m = project.get_media(mid) if mid else None
        except Exception:
            m = None
        if m is None or not m.transcript:
            continue
        s1 = s_in + dur * sp
        for s in m.transcript.sentences:
            if s.e <= s_in or s.s >= s1:
                continue
            out.append((start + (max(s.s, s_in) - s_in) / sp, start + (min(s.e, s1) - s_in) / sp))
    return out


def _audio_active(project, tl: Timeline) -> list[tuple[float, float]]:
    from ..exports import audio_items

    return sorted((it.start, it.start + it.duration) for it in audio_items(project, tl))


def _variant_for(meta: dict | None, tl: Timeline, w: int, h: int) -> Variant:
    if meta and meta.get("variant_obj"):
        try:
            return Variant(**meta["variant_obj"])
        except Exception:
            pass
    if meta and meta.get("variant"):
        return presets.resolve_variant(tl, meta["variant"])
    ar = w / max(1, h)
    for v in tl.variants:
        if abs(v.width / v.height - ar) < 0.02:
            return v
    return presets.variant_for("tiktok" if ar < 0.8 else "youtube")


# ---------------------------------------------------------------------------------------------
# Główna funkcja
# ---------------------------------------------------------------------------------------------
def run_qc(project, timeline: Timeline, render_path: Path, *, audio_report: dict | None = None,
           effort: str = "high") -> dict:
    from ..render.engine import render_meta

    level = EFFORT_LEVEL.get(effort, 2)
    path = str(render_path)
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    meta = render_meta(project, path) or {}
    info = ff.media_info(path)
    W, H = int(info.get("width") or 0), int(info.get("height") or 0)
    fps = float(info.get("fps") or tl.fps or 30)
    dur = float(info.get("duration") or 0)
    variant = _variant_for(meta, tl, W, H)
    T0 = float(meta.get("t0", 0.0) or 0.0)  # czas osi odpowiadający początkowi pliku
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {"duration": round(dur, 3), "width": W, "height": H, "fps": fps,
                               "vcodec": info.get("vcodec"), "acodec": info.get("acodec"),
                               "channels": info.get("channels"), "sample_rate": info.get("sample_rate"),
                               "variant": variant.name, "effort": effort}

    def add(kind: str, severity: str, t0: float, t1: float, message: str, fix: dict | None = None, **extra):
        issues.append({"id": f"q{len(issues) + 1}", "kind": kind, "severity": severity, "t0": round(t0, 3),
                       "t1": round(t1, 3), "message": message, "fix": fix, **extra})

    v1 = _v(tl, "V1")
    v2 = _v(tl, "V2")
    tol = 1.5 / fps + 0.02
    # okna przejść (zamierzona czerń / zamrożenia uchwytów)
    dips, windows = [], []
    for tr in tl.transitions:
        if tr.kind == "cut":
            continue
        windows.append((tr.at - tr.duration / 2 - 0.05, tr.at + tr.duration / 2 + 0.05))
        if tr.kind == "dip_black":
            dips.append(windows[-1])
    # --- obraz ------------------------------------------------------------------------------
    vs = video_scan(path, level=level, fps=fps)
    black_total = 0.0
    for a, b in vs["black"]:
        ta, tb = a + T0, b + T0
        black_total += b - a
        if any(_overlap(ta, tb, x0, x1) >= (tb - ta) * 0.6 for x0, x1 in dips):
            continue
        if a < 0.5 and (b - a) < 1.0 or b > dur - 0.5 and (b - a) < 1.0:
            continue
        gap = not any(c.start - tol <= ta and tb <= c.end + tol for c in v1)
        sev = "error" if (b - a) >= 0.1 else "warn"
        add("black_frames", sev, ta, tb, f"Niezamierzona czerń {b - a:.2f} s w {ta:.2f} s"
            + (" (luka na ścieżce V1)" if gap else ""),
            {"agent": "render", "action": "fill_gap", "params": {"t0": round(ta, 3), "t1": round(tb, 3)}})
    metrics["black_total"] = round(black_total, 3)
    freeze_total = 0.0
    images = [(c.start, c.end) for c in v1 if c.role == "image"]
    for a, b in vs["freeze"]:
        b = dur if b is None else b
        ta, tb = a + T0, b + T0
        freeze_total += b - a
        if any(_overlap(ta, tb, x0, x1) >= (tb - ta) * 0.5 for x0, x1 in images + windows):
            continue
        add("freeze", "warn", ta, tb, f"Zamrożony obraz {b - a:.2f} s w {ta:.2f} s",
            {"agent": "render", "action": "fix_freeze", "params": {"t0": round(ta, 3), "t1": round(tb, 3)}})
    metrics["freeze_total"] = round(freeze_total, 3)
    # --- dźwięk -----------------------------------------------------------------------------
    sil_d = {0: 3.0, 1: 2.0, 2: 1.5, 3: 1.0}[level]
    if info.get("has_audio"):
        au = audio_scan(path, silence_d=sil_d)
        lufs, tp, lra = au["lufs"], au["true_peak"], au["lra"]
        metrics.update({"lufs": lufs, "true_peak": tp, "lra": lra})
        target = float(tl.mix.target_lufs)
        if tp is not None and tp > -0.1:
            add("clipping", "error", T0, T0 + dur, f"Przesterowanie: true peak {tp:+.1f} dBFS (> −0,1 dBFS)",
                {"agent": "sound", "action": "true_peak", "params": {"true_peak": round(tl.mix.true_peak - 0.5, 2)}})
        elif tp is not None and tp > tl.mix.true_peak + 0.5:
            add("true_peak", "warn", T0, T0 + dur, f"True peak {tp:+.1f} dBFS powyżej celu {tl.mix.true_peak:+.1f}",
                {"agent": "sound", "action": "true_peak", "params": {"true_peak": round(tl.mix.true_peak - 0.5, 2)}})
        if lufs is not None and dur > 3 and abs(lufs - target) > 1.0:
            sev = "error" if abs(lufs - target) > 3 else "warn"
            add("loudness", sev, T0, T0 + dur, f"Głośność {lufs:.1f} LUFS zamiast {target:.1f} LUFS (±1 LU)",
                {"agent": "sound", "action": "loudness", "params": {"delta": round(target - lufs, 2)}})
        mom = au["momentary"]
        sp_r = speech_ranges(project, tl)
        if sp_r and mom:
            vals = [m for t, m in mom if any(a <= t + T0 - 0.2 <= b for a, b in sp_r)]
            if vals:
                speech = 10 * math.log10(np.mean([10 ** (v / 10) for v in vals]) + 1e-12)
                metrics["speech_lufs"] = round(speech, 2)
                if speech < target - 5:
                    add("dialog_quiet", "warn", T0, T0 + dur,
                        f"Dialog za cichy: {speech:.1f} LUFS przy celu {target:.1f}",
                        {"agent": "sound", "action": "dialog_gain",
                         "params": {"gain_db": round(min(9.0, target - 3 - speech), 1)}})
        active = _audio_active(project, tl)
        sil_total = 0.0
        for a, b in au["silence"]:
            b = dur if b is None else b
            ta, tb = a + T0, b + T0
            sil_total += b - a
            covered = sum(_overlap(ta, tb, x0, x1) for x0, x1 in active)
            if covered >= (tb - ta) * 0.8 and (tb - ta) >= sil_d:
                add("silence", "warn", ta, tb, f"Martwa cisza {b - a:.1f} s w {ta:.1f} s, choć oś przewiduje dźwięk",
                    None if tl.mix.room_tone else
                    {"agent": "sound", "action": "room_tone", "params": {"t0": round(ta, 3), "t1": round(tb, 3)}})
        metrics["silence_total"] = round(sil_total, 2)
        if audio_report:
            metrics["mix_report"] = {k: audio_report.get(k) for k in ("lufs", "true_peak", "lra") if k in audio_report}
    else:
        add("no_audio", "error", T0, T0 + dur, "Render nie zawiera ścieżki audio", None)
    # --- jump cuty ----------------------------------------------------------------------------
    jumps = 0
    for a, b in zip(v1[:-1], v1[1:]):
        if abs(a.end - b.start) > tol or a.media_id != b.media_id:
            continue
        cut = b.start
        if cut < T0 or cut > T0 + dur:
            continue
        if any(t.kind != "cut" and abs(t.at - cut) <= tol for t in tl.transitions):
            continue
        if any(c.start <= cut - 0.1 and c.end >= cut + 0.1 for c in v2):
            continue
        if abs(b.src_in - a.src_out) < 0.15:
            continue  # ciągłość źródła - brak przeskoku
        sa, sb = _scale_at(a, a.duration), _scale_at(b, 0.0)
        if abs(sa - sb) / max(sa, sb) >= 0.05:
            continue
        sim = None
        if level >= 1:
            f = round((cut - T0) * fps)
            ga = grab_gray(path, (f - 0.5) / fps)
            gb = grab_gray(path, (f + 0.5) / fps)
            if ga is not None and gb is not None:
                sim = ssim(ga, gb)
            if sim is not None and sim < 0.45:
                continue
        jumps += 1
        add("jump_cut", "warn", cut - 0.1, cut + 0.1,
            f"Jump cut w {cut:.2f} s (to samo ujęcie, ten sam kadr" + (f", SSIM {sim:.2f})" if sim else ")"),
            {"agent": "render", "action": "punch_in", "params": {"clip_id": b.id, "prev_clip_id": a.id,
                                                                  "scale": 1.12}}, ssim=sim)
    metrics["jump_cuts"] = jumps
    # --- napisy / tytuły -----------------------------------------------------------------------
    vertical = variant.height > variant.width
    subs = meta.get("subtitles") or {}
    for src, kind in ((subs.get("ass") if subs.get("burned") else None, "subtitles"),
                      (meta.get("titles_ass"), "titles")):
        if not src or not Path(src).exists():
            continue
        for s in subtitle_issues(src, kind=kind, vertical=vertical):
            if s["t1"] < T0 or s["t0"] > T0 + dur:
                continue
            if s["kind"].endswith("_unsafe"):
                add("subtitle_position", "warn", s["t0"], s["t1"],
                    f"Napis „{s['text']}” za nisko dla 9:16 (y={s['y']:.0%}) - zasłoni go interfejs aplikacji",
                    {"agent": "sound", "action": "subtitles", "params": {"position": "middle"}} if kind == "subtitles"
                    else None)
            else:
                add("subtitle_cut" if kind == "subtitles" else "title_cut", "warn", s["t0"], s["t1"],
                    f"{'Napis' if kind == 'subtitles' else 'Tytuł'} „{s['text']}” wychodzi poza kadr "
                    f"o {s['overflow']:.0f} px",
                    {"agent": "sound", "action": "subtitles",
                     "params": {"max_chars": max(14, tl.subtitles.max_chars - 6)}} if kind == "subtitles" else None)
    # --- synchronizacja -------------------------------------------------------------------------
    vd, ad = stream_durations(path)
    metrics["video_duration"], metrics["audio_duration"] = vd, ad
    if vd and ad and abs(vd - ad) > 1.0 / fps + 0.03:
        add("av_length", "error", T0, T0 + dur, f"Audio ({ad:.3f} s) i wideo ({vd:.3f} s) mają różną długość",
            {"agent": "render", "action": "rerender", "params": {}})
    for a in [c for c in tl.tracks.get("A1", []) if isinstance(c, AClip) and c.link]:
        v = next((c for c in v1 if c.id == a.link), None)
        if v is None or v.media_id != a.media_id or abs(v.speed - 1) > 1e-3 or v.speed_ramp:
            continue
        off = (a.start - a.src_in) - (v.start - v.src_in)
        if abs(off) > 1.5 / fps:
            add("sync", "error", a.start, a.end, f"Dialog przesunięty względem obrazu o {off * 1000:+.0f} ms",
                {"agent": "sound", "action": "resync", "params": {"clip_id": a.id,
                                                                   "start": round(v.start - v.src_in + a.src_in, 4)}})
        elif max(abs(a.start - v.start), abs(a.end - v.end)) > 2.0:
            add("jl_cut", "info", a.start, a.end, "Cięcie J/L dłuższe niż 2 s", None)
    # --- zgodność techniczna -------------------------------------------------------------------
    exp_w, exp_h = meta.get("width"), meta.get("height")
    if exp_w and exp_h and (W, H) != (int(exp_w), int(exp_h)):
        add("resolution", "error", T0, T0 + dur, f"Rozdzielczość {W}x{H} zamiast {exp_w}x{exp_h}", None)
    elif not exp_w and abs(W / max(1, H) - variant.width / variant.height) > 0.02:
        add("aspect", "error", T0, T0 + dur, f"Proporcje {W}x{H} niezgodne z wariantem {variant.name}", None)
    exp_fps = float(presets.fps_fraction(meta.get("fps"))) if meta.get("fps") else float(variant.fps)
    if abs(fps - exp_fps) > 0.02:
        add("fps", "error", T0, T0 + dur, f"Klatkaż {fps:.3f} zamiast {exp_fps:.3f}", None)
    st = meta.get("settings") or {}
    want = CODEC_MAP.get(str(st.get("codec") or ""))
    if want and info.get("vcodec") and want not in str(info.get("vcodec")):
        add("codec", "warn", T0, T0 + dur, f"Kodek {info.get('vcodec')} zamiast {want}", None)
    if info.get("has_audio"):
        if int(info.get("channels") or 0) != 2:
            add("channels", "warn", T0, T0 + dur, f"Kanały audio: {info.get('channels')} (oczekiwano 2)", None)
        if int(info.get("sample_rate") or 0) != 48000:
            add("sample_rate", "warn", T0, T0 + dur, f"Próbkowanie {info.get('sample_rate')} Hz (oczekiwano 48000)",
                None)
    # --- długość -----------------------------------------------------------------------------
    t1m = meta.get("t1")
    exp_len = (float(t1m) - T0) if t1m is not None else tl.duration
    if abs(dur - exp_len) > 1.5 / fps + 0.05:
        add("duration_mismatch", "error", T0, T0 + dur, f"Długość renderu {dur:.2f} s ≠ oś {exp_len:.2f} s",
            {"agent": "render", "action": "rerender", "params": {}})
    if variant.max_duration and tl.duration > variant.max_duration + 0.01:
        add("too_long", "error", 0, tl.duration,
            f"Film ma {tl.duration:.1f} s, a limit {variant.name} to {variant.max_duration:.0f} s",
            {"agent": "director", "action": "shorten", "params": {"target": variant.max_duration}})
    target_d = getattr(getattr(project, "meta", None), "target_duration", None)
    if target_d and abs(tl.duration - target_d) > max(3.0, 0.15 * target_d):
        add("target_duration", "info", 0, tl.duration,
            f"Długość {tl.duration:.0f} s odbiega od docelowej {target_d:.0f} s", None)
    # --- wynik -----------------------------------------------------------------------------------
    w = {"error": 15, "warn": 5, "info": 1}
    score = max(0, 100 - sum(w.get(i["severity"], 0) for i in issues))
    passed = not any(i["severity"] == "error" for i in issues)
    report = {"passed": passed, "score": score, "issues": issues, "metrics": metrics,
              "render_path": path, "variant": variant.name, "timeline_version": tl.version}
    report["summary"] = summarize(report, effort, getattr(project, "id", None))
    return report


def _template_summary(rep: dict) -> str:
    iss = rep["issues"]
    m = rep["metrics"]
    err = sum(1 for i in iss if i["severity"] == "error")
    warn = sum(1 for i in iss if i["severity"] == "warn")
    head = "Kontrola jakości zaliczona" if rep["passed"] else "Kontrola jakości NIEzaliczona"
    parts = [f"{head} — wynik {rep['score']}/100 ({err} błędów, {warn} ostrzeżeń)."]
    if m.get("lufs") is not None:
        parts.append(f"Głośność {m['lufs']:.1f} LUFS, true peak {m.get('true_peak') or 0:+.1f} dBFS.")
    kinds: dict[str, int] = {}
    for i in iss:
        if i["severity"] != "info":
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    names = {"black_frames": "czarne klatki", "freeze": "zamrożenia", "clipping": "przester", "loudness": "głośność",
             "dialog_quiet": "cichy dialog", "silence": "martwe cisze", "jump_cut": "jump cuty",
             "subtitle_cut": "ucięte napisy", "title_cut": "ucięte tytuły", "subtitle_position": "pozycja napisów",
             "sync": "synchronizacja", "av_length": "długość audio/wideo", "too_long": "za długi film",
             "true_peak": "true peak", "duration_mismatch": "długość renderu"}
    if kinds:
        parts.append("Do poprawy: " + ", ".join(f"{names.get(k, k)} ×{v}" for k, v in kinds.items()) + ".")
    fixable = sum(1 for i in iss if i.get("fix"))
    if fixable:
        parts.append(f"{fixable} problemów można poprawić automatycznie.")
    return " ".join(parts)


def summarize(rep: dict, effort: str, project_id: str | None = None) -> str:
    base = _template_summary(rep)
    if effort != "max" or not rep["issues"]:
        return base
    try:
        from ..services import services

        if not services["ollama"].healthy(timeout=0.5):
            return base
        from ..llm import chat

        brief = "\n".join(f"- [{i['severity']}] {i['t0']:.1f}s: {i['message']}" for i in rep["issues"][:20])
        txt = chat([{"role": "system", "content": "Jesteś nadzorcą jakości postprodukcji. Odpowiadasz po polsku, "
                                                   "2-3 zdania, konkretnie, bez list."},
                    {"role": "user", "content": f"Wynik QC {rep['score']}/100. Problemy:\n{brief}\n"
                                                f"Podsumuj stan filmu i najważniejszą poprawkę."}],
                   role="director_fast", agent="qc", temperature=0.3, think=False, timeout=90,
                   label="Podsumowanie QC", project_id=project_id)
        txt = (txt or "").strip()
        return txt if 20 < len(txt) < 900 else base
    except Exception as e:
        log("qc", f"Podsumowanie LLM niedostępne ({e}) - użyto szablonu", path="QC", level="debug",
            project_id=project_id)
        return base

"""Budowa napisów ASS + SRT z transkrypcji mediów odwzorowanej na czas osi.

Tryby: static (klasyczne plansze), karaoke (\\kf), word_pop (słowo po słowie: podświetlenie aktualnego
słowa kolorem `highlight` + delikatny "pop" skali). Style: modern, classic, bold_social, minimal, cinema.
Napisy nigdy nie wychodzą poza bezpieczny obszar (pomiar szerokości czcionką Windows, w razie potrzeby
zmniejszenie planszy przez \\fscx\\fscy).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..project.model import SubtitleSettings, Timeline, Variant
from .layout import Measurer, layout_cue, split_cues

STYLES = {
    #            font (gdy domyślny), bold, outline, shadow, size_mul, uppercase, spacing, mode_default
    "modern": dict(font=None, bold=True, outline=0.075, shadow=0.035, size=1.0, upper=False, spacing=0.0),
    "classic": dict(font="Arial", bold=True, outline=0.055, shadow=0.02, size=0.9, upper=False, spacing=0.0),
    "bold_social": dict(font=None, bold=True, outline=0.11, shadow=0.05, size=1.15, upper=True, spacing=0.02),
    "minimal": dict(font="Segoe UI Semibold", bold=False, outline=0.0, shadow=0.05, size=0.85, upper=False,
                    spacing=0.0),
    "cinema": dict(font="Segoe UI Semibold", bold=False, outline=0.04, shadow=0.0, size=0.82, upper=False,
                   spacing=0.04),
}
DEFAULT_FONT = "Segoe UI Black"


@dataclass
class TWord:
    w: str
    s: float
    e: float
    spk: str | None = None
    brk: bool = False  # koniec zdania po tym słowie


# ------------------------------------------------------------------------------------------
# Słowa na osi czasu
# ------------------------------------------------------------------------------------------
def timeline_words(project, tl: Timeline, max_t: float | None = None) -> list[TWord]:
    from ..audio.mix import dialog_clips

    out: list[TWord] = []
    for c, sp in dialog_clips(project, tl):
        if not c.media_id:
            continue
        try:
            m = project.get_media(c.media_id)
        except KeyError:
            continue
        if not m.transcript:
            continue
        clip_end = c.start + (c.src_out - c.src_in) / max(0.05, sp)
        for sent in m.transcript.sentences:
            if sent.e < c.src_in or sent.s > c.src_out:
                continue
            ws = [w for w in sent.words if not w.filler]
            for i, w in enumerate(ws):
                mid = (w.s + w.e) / 2
                if mid < c.src_in or mid > c.src_out:
                    continue
                s = c.start + (max(w.s, c.src_in) - c.src_in) / sp
                e = c.start + (min(w.e, c.src_out) - c.src_in) / sp
                s, e = max(s, c.start), min(e, clip_end)
                if e <= s:
                    continue
                txt = w.w.strip()
                if not re.sub(r"[\W_]+", "", txt):
                    continue
                last = i == len(ws) - 1
                out.append(TWord(txt, s, e, w.spk, brk=last or bool(re.search(r"[.!?…]$", txt))))
    # lektor (A5): czasy słów zapisane przy syntezie
    for c in tl.a("A5"):
        words = (c.processing or {}).get("words") or []
        if not words and c.path:
            side = Path(c.path).with_suffix(".json")
            if side.exists():
                try:
                    words = json.loads(side.read_text(encoding="utf-8")).get("words", [])
                except Exception:
                    words = []
        for w in words:
            s, e = c.start + float(w["s"]) - c.src_in, c.start + float(w["e"]) - c.src_in
            if e <= c.start or s >= c.end:
                continue
            out.append(TWord(str(w["w"]), max(s, c.start), min(e, c.end), "Lektor",
                             brk=bool(re.search(r"[.!?…]$", str(w["w"])))))
    out.sort(key=lambda w: w.s)
    # usuwanie dubli z nakładających się klipów (J/L cut, ten sam tekst w obu)
    ded: list[TWord] = []
    for w in out:
        if ded and w.s < ded[-1].e - 0.05:
            prev = ded[-1]
            if prev.w.lower().strip(".,!?") == w.w.lower().strip(".,!?") or (min(prev.e, w.e) - w.s) > 0.5 * (w.e - w.s):
                continue
            w.s = prev.e
            if w.e - w.s < 0.05:
                continue
        ded.append(w)
    if max_t is not None:
        ded = [w for w in ded if w.s < max_t]
    return ded


# ------------------------------------------------------------------------------------------
# Format
# ------------------------------------------------------------------------------------------
def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    h = (h + "000000")[:6]
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_escape(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


@dataclass
class Geometry:
    size: int
    margin_l: int
    margin_r: int
    margin_v: int
    alignment: int
    max_width: float


def geometry(st: SubtitleSettings, variant: Variant, style: dict) -> Geometry:
    W, H = int(variant.width), int(variant.height)
    portrait = H / max(1, W) > 1.2
    base = min(H * 0.055, W * 0.075)
    size = int(st.size) if st.size and st.size > 0 else int(round(base * style["size"]))
    ml = mr = int(round(W * (0.07 if portrait else 0.05)))
    if st.position == "top":
        alignment, mv = 8, int(round(H * (0.13 if portrait else 0.07)))
    elif st.position == "middle":
        alignment, mv = 5, 0
    else:
        alignment, mv = 2, int(round(H * (0.22 if portrait else 0.075)))
    return Geometry(size, ml, mr, mv, alignment, float(W - ml - mr))


def _style_line(name: str, font: str, size: int, primary: str, secondary: str, outline_c: str, back: str,
                bold: bool, outline: float, shadow: float, spacing: float, border_style: int, g: Geometry) -> str:
    return (f"Style: {name},{font},{size},{primary},{secondary},{outline_c},{back},{-1 if bold else 0},0,0,0,"
            f"100,100,{spacing:.2f},0,{border_style},{outline:.1f},{shadow:.1f},{g.alignment},"
            f"{g.margin_l},{g.margin_r},{g.margin_v},1")


def build_subtitles(project, timeline: Timeline, variant: Variant, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tl = timeline.model_copy(deep=True).normalize()
    st = tl.subtitles
    style_name = st.style if st.style in STYLES else "modern"
    sty = STYLES[style_name]
    font = st.font or DEFAULT_FONT
    if sty["font"] and font == DEFAULT_FONT:
        font = sty["font"]
    upper = st.uppercase or sty["upper"]
    mode = st.mode if st.enabled else "none"
    g = geometry(st, variant, sty)
    outline = round(g.size * sty["outline"], 1)
    shadow = round(g.size * sty["shadow"], 1)
    spacing = g.size * sty["spacing"]
    border_style = 3 if st.box else 1
    if st.box:
        outline = max(outline, round(g.size * 0.18, 1))  # padding tła
        shadow = 0.0
    meas = Measurer(font, g.size, bold=sty["bold"], spacing=spacing, outline=outline)
    pop = 1.10 if mode == "word_pop" else 1.0
    # zapas: pop jednego słowa poszerza linię, cień, zaokrąglenia libass
    safe_w = g.max_width / (1.0 + (pop - 1.0) * 0.6) * 0.97 - shadow
    measure = meas.width
    max_t = min(tl.duration, variant.max_duration) if variant.max_duration else tl.duration
    words = timeline_words(project, tl, max_t)
    texts = [w.w.upper() if upper else w.w for w in words]
    # wymuszone końce plansz: koniec zdania, pauza > 0.6 s, zmiana mówcy
    breaks = []
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        breaks.append(w.brk or nxt is None or (nxt.s - w.e) > 0.6 or (nxt.spk != w.spk and nxt.spk and w.spk))
    max_chars = max(8, int(st.max_chars))
    max_lines = max(1, int(st.max_lines))
    if mode == "word_pop" and variant.height > variant.width:
        max_chars = min(max_chars, 24)  # pionowe: krótsze, czytelniejsze plansze
    cues = split_cues(texts, safe_w, max_chars, max_lines, measure, breaks) if words else []

    primary = ass_color(st.primary)
    hl = ass_color(st.highlight)
    oc = ass_color(st.outline)
    back = ass_color("#000000", 0x60 if not st.box else 0x50)
    box_c = ass_color(st.outline if st.outline.upper() not in ("#FFFFFF",) else "#000000", 0x55)
    styles = [_style_line("Default", font, g.size, primary, primary, box_c if st.box else oc, back, sty["bold"],
                          outline, shadow, spacing, border_style, g)]
    if mode == "karaoke":
        # \k: SecondaryColour przed "zaśpiewaniem", PrimaryColour po
        styles = [_style_line("Default", font, g.size, hl, primary, box_c if st.box else oc, back, sty["bold"],
                              outline, shadow, spacing, border_style, g)]
    events: list[str] = []
    srt: list[str] = []
    timed = []
    for ci, idxs in enumerate(cues):
        s = words[idxs[0]].s
        e = words[idxs[-1]].e + 0.25
        nxt_s = words[cues[ci + 1][0]].s if ci + 1 < len(cues) else None
        if e - s < 0.8:
            e = s + 0.8
        if nxt_s is not None:
            e = min(e, nxt_s - 0.02)
        e = min(e, max_t)
        if e <= s:
            continue
        timed.append((idxs, s, e))
    for n, (idxs, s, e) in enumerate(timed, 1):
        cue_txt = [texts[i] for i in idxs]
        lines, scale = layout_cue(cue_txt, safe_w, max_chars, max_lines, measure)
        pre = ""
        if scale < 0.999:
            pc = int(scale * 100)
            pre = f"{{\\fscx{pc}\\fscy{pc}}}"
        base_scale = int(round(scale * 100))
        plain = "\n".join(" ".join(cue_txt[i] for i in ln) for ln in lines)
        srt.append(f"{n}\n{srt_time(s)} --> {srt_time(e)}\n{plain}\n")
        if mode == "none":
            continue
        if mode == "static":
            txt = "\\N".join(" ".join(ass_escape(cue_txt[i]) for i in ln) for ln in lines)
            events.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Default,,0,0,0,,{pre}{txt}")
        elif mode == "karaoke":
            parts = []
            t_prev = s
            for li, ln in enumerate(lines):
                seg = []
                for i in ln:
                    w = words[idxs[i]]
                    lead = max(0.0, w.s - t_prev)
                    if lead >= 0.01:
                        seg.append(f"{{\\k{int(round(lead * 100))}}}")
                    dur = max(0.05, w.e - max(w.s, t_prev))
                    seg.append(f"{{\\kf{int(round(dur * 100))}}}{ass_escape(cue_txt[i])} ")
                    t_prev = max(t_prev, w.e)
                parts.append("".join(seg).rstrip())
            events.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Default,,0,0,0,,{pre}" + "\\N".join(parts))
        else:  # word_pop
            for k, i_act in enumerate(range(len(idxs))):
                w = words[idxs[i_act]]
                ws = s if k == 0 else w.s
                we = words[idxs[i_act + 1]].s if i_act + 1 < len(idxs) else e
                if we - ws < 0.02:
                    continue
                pieces = []
                for ln in lines:
                    lp = []
                    for i in ln:
                        t = ass_escape(cue_txt[i])
                        if i == i_act:
                            up = int(round(base_scale * pop))
                            lp.append(f"{{\\c{hl}\\fscx{base_scale}\\fscy{base_scale}\\t(0,70,\\fscx{up}\\fscy{up})"
                                      f"\\t(70,170,\\fscx{base_scale}\\fscy{base_scale})}}{t}{{\\c{primary}}}")
                        else:
                            lp.append(t)
                    pieces.append(" ".join(lp))
                events.append(f"Dialogue: 0,{ass_time(ws)},{ass_time(we)},Default,,0,0,0,,{pre}" + "\\N".join(pieces))
    head = [
        "[Script Info]", "; StudioEdit - napisy wygenerowane automatycznie", "ScriptType: v4.00+",
        f"PlayResX: {int(variant.width)}", f"PlayResY: {int(variant.height)}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    ass_p = out_dir / f"subtitles_{variant.name}.ass"
    srt_p = out_dir / f"subtitles_{variant.name}.srt"
    ass_p.write_text("\n".join(head + events) + "\n", encoding="utf-8-sig")
    srt_p.write_text("\n".join(srt), encoding="utf-8")
    return {"ass": ass_p, "srt": srt_p, "cues": len(timed), "words": len(words), "mode": mode,
            "style": style_name, "font": font, "size": g.size}

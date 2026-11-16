"""Pojedyncza klatka osi (podgląd „Przed” / „Po”) - ten sam graf co plaster renderu, 1 klatka, cache po hashu."""
from __future__ import annotations

import hashlib
import json
import shutil
from fractions import Fraction
from pathlib import Path

from ..media import ffmpeg as ff
from ..project.model import Timeline, Variant
from . import presets
from .engine import build_subs, timeline_hash
from .filters import build_slice_graph, titles_ass
from .slices import plan_render


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def render_frame(project, timeline: Timeline, t: float, out_jpg: Path, *, variant: Variant | None = None,
                 before: bool = False, width: int = 960) -> Path:
    """Klatka w czasie osi `t`. before=True: źródło bez koloru/efektów/tytułów (to samo kadrowanie wariantu)."""
    out_jpg = Path(out_jpg)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    v = variant or presets.resolve_variant(tl, None)
    F = presets.output_fps(v, tl)
    W = _even(min(width, v.width))
    H = _even(W * v.height / v.width)
    fa = int(float(t) * float(F) + 1e-6)
    end_f = max(1, int(round(tl.duration * float(F))))
    fa = max(0, min(fa, end_f - 1))
    plan, _pl = plan_render(project, tl, v, W=W, H=H, fps=F, quality="final", t0=float(Fraction(fa) / F),
                            t1=float(Fraction(fa + 1) / F), before=before, use_proxy=True)
    job = plan.slices[0]
    subs_ass = None
    subs_sig = ""
    if not before and tl.subtitles.enabled and tl.subtitles.burn_in and tl.subtitles.mode != "none":
        th = timeline_hash(tl)
        sdir = project.cache / "frames" / f"subs_{v.name}_{th}"
        ass_done = sdir / ".done.json"
        if ass_done.exists():
            res = json.loads(ass_done.read_text(encoding="utf-8"))
        else:
            res, _w = build_subs(project, tl, v, sdir)
            res = res or {}
            sdir.mkdir(parents=True, exist_ok=True)
            ass_done.write_text(json.dumps(res), encoding="utf-8")
        if res.get("ass") and Path(res["ass"]).exists():
            subs_ass = Path(res["ass"])
            subs_sig = hashlib.sha1(subs_ass.read_bytes()).hexdigest()[:10]
    key = hashlib.sha1(f"{job.hash}|{before}|{W}|{subs_sig}|{fa}".encode()).hexdigest()[:24]
    cdir = project.cache / "frames"
    cdir.mkdir(parents=True, exist_ok=True)
    cached = cdir / f"{key}.jpg"
    if cached.exists():
        if cached.resolve() != out_jpg.resolve():
            shutil.copyfile(cached, out_jpg)
        return out_jpg
    titles_file = None
    if job.spec.get("titles"):
        th = hashlib.sha1(json.dumps([x.model_dump(mode="json") for x in plan.titles], sort_keys=True,
                                     default=str).encode()).hexdigest()[:12]
        titles_file = project.cache / "render" / "titles" / f"{v.name}_{th}.ass"
        if not titles_file.exists():
            titles_ass(plan.titles, v.width, v.height, titles_file)
    g, out = build_slice_graph(job.spec, hw_decode=False, pix="yuv420p", titles_ass=titles_file,
                               subs_ass=subs_ass)
    script = cdir / f"{key}.fg.txt"
    script.write_text(g.script(), encoding="utf-8")
    ff.run([*g.input_args(), "-/filter_complex", str(script), "-map", f"[{out}]", "-frames:v", "1",
            "-q:v", "3", "-update", "1", str(cached)], timeout=120)
    try:
        script.unlink()
    except OSError:
        pass
    if cached.resolve() != out_jpg.resolve():
        shutil.copyfile(cached, out_jpg)
    return out_jpg

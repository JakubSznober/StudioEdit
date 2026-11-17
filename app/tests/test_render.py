"""Testy obszaru render: mapowanie prędkości, plan plastrów i hashe, presety, wyrażenia, render z test.MP4.

Testy integracyjne (ffmpeg, C:\\StudioEdit\\test.MP4 tylko do odczytu) zapisują wyniki w Cache\\dev_render.
"""
from __future__ import annotations

import math
import shutil
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from studio import paths

paths.setup_env()

from studio.project.model import Keyframe, SpeedPoint, TitleClip, Timeline, Transition, VClip, Variant  # noqa: E402
from studio.render import filters, presets  # noqa: E402
from studio.render.slices import ClipTime, plan_render  # noqa: E402

TEST = Path(r"C:\StudioEdit\test.MP4")
OUT = paths.CACHE / "dev_render" / "pytest"
HAS_MEDIA = TEST.exists() and paths.FFMPEG.exists()


# ---------------------------------------------------------------------------------------------
# Szybkie (bez renderu)
# ---------------------------------------------------------------------------------------------
def test_cliptime_constant_speed():
    c = VClip(media_id="m", src_in=2.0, src_out=4.0, start=10.0, speed=0.5)
    ct = ClipTime(c)
    assert c.duration == pytest.approx(4.0)
    assert ct.src(0) == pytest.approx(2.0)
    assert ct.src(4.0) == pytest.approx(4.0)
    assert ct.tau_of(3.0) == pytest.approx(2.0)


def test_cliptime_ramp_consistent_with_model_duration():
    c = VClip(media_id="m", src_in=8.0, src_out=11.0, start=0.0,
              speed_ramp=[SpeedPoint(t=0, speed=1.0), SpeedPoint(t=0.5, speed=0.4), SpeedPoint(t=1, speed=1.0)])
    ct = ClipTime(c)
    D = c.duration
    assert ct.src(0) == pytest.approx(8.0)
    assert ct.src(D) == pytest.approx(11.0, abs=1e-6)  # całka krzywej = materiał źródłowy
    xs = [ct.src(t) for t in np.linspace(0, D, 50)]
    assert all(b > a for a, b in zip(xs, xs[1:]))  # monotonicznie
    assert ct.speed_at(D / 2) < ct.speed_at(0.05) * 0.6  # spowolnienie w środku
    assert ct.tau_of(ct.src(1.3)) == pytest.approx(1.3, abs=1e-3)
    marks = ct.ramp_taus(8)
    assert len(marks) == 7 and all(0 < m < D for m in marks)


def test_kf_expr_matches_python_interpolation():
    kf = [[0.0, 1.0, 0.0, 0.0, 0.0, "linear"], [3.0, 1.08, 0.0, 0.0, 0.0, "ease"], [4.0, 1.2, 0, 0, 0, "hold"]]
    expr = filters.kf_expr(kf, 1, "T")

    def ev(t):
        e = expr.replace("T", repr(t)).replace("if(", "IF(")
        env = {"IF": lambda c, a, b: a if c else b, "lt": lambda a, b: a < b,
               "clip": lambda x, lo, hi: min(max(x, lo), hi)}
        return eval(e, env)  # noqa: S307 - test wyrażenia ffmpeg w podzbiorze Pythona

    assert ev(0.0) == pytest.approx(1.0)
    assert ev(1.5) == pytest.approx(1.04)
    assert ev(3.5) == pytest.approx(1.08 + 0.12 * 0.5)  # smoothstep(0.5) = 0.5
    assert ev(10.0) == pytest.approx(1.2)


def test_presets_and_sizes():
    v = presets.variant_for("tiktok")
    assert (v.width, v.height, v.max_duration) == (1080, 1920, 60.0)
    assert presets.variant_for("instagram_feed").height == 1350
    # podgląd: krótszy bok 720 - pion 720x1280 (wcześniej 404x720, rozmyte), poziom 1280x720
    assert presets.render_size(v, {}, "preview") == (720, 1280)
    assert presets.render_size(presets.variant_for("youtube"), {}, "preview") == (1280, 720)
    assert presets.render_size(presets.variant_for("youtube"), {"resolution": "2160p"}, "final") == (3840, 2160)
    assert presets.fps_fraction(29.97) == Fraction(30000, 1001)
    n = presets.normalize_settings({"codec": "prores", "container": "mp4", "audio_codec": "wav", "quality": "max"})
    assert n["container"] == "mov" and n["audio_codec"] == "pcm"
    tl = Timeline(fps=25, width=1280, height=720)
    m = presets.resolve_variant(tl, "master")
    assert (m.width, m.height, m.fps) == (1280, 720, 25)


def test_wrap_text_and_titles_ass(tmp_path):
    lines, size = filters.wrap_text("Bardzo długi tytuł który na pewno nie zmieści się w jednej linii ekranu",
                                    "Segoe UI Black", 100, 900, 3)
    assert 1 < len(lines) <= 3
    assert all(filters.text_width(ln, "Segoe UI Black", size) <= 900 for ln in lines)
    t = TitleClip(text="Test", start=1.0, duration=2.0, style="lower_third", params={"subtitle": "Podpis"})
    p = filters.titles_ass([t], 1920, 1080, tmp_path / "t.ass")
    txt = p.read_text(encoding="utf-8-sig")
    assert "PlayResX: 1920" in txt and "Dialogue:" in txt and "Podpis" in txt


# ---------------------------------------------------------------------------------------------
# Projekt testowy
# ---------------------------------------------------------------------------------------------
def make_project(name: str):
    from studio.media.ffmpeg import media_info
    from studio.project.manager import ProjectManager

    root = OUT / "projects"
    if (root / name).exists():
        shutil.rmtree(root / name, ignore_errors=True)
    pm = ProjectManager(root=root)
    p = pm.create(name, ingest_mode="link")
    m = p.add_media_file(TEST)
    info = media_info(TEST)
    p.update_media(m.id, lambda x: x.info.update(info))
    return p, m.id


def build_timeline(mid: str) -> Timeline:
    tl = Timeline(fps=30.0, variants=[Variant(name="youtube"),
                                      Variant(name="tiktok", aspect="9:16", width=1080, height=1920)])
    tl.tracks["V1"] = [
        VClip(id="c1", media_id=mid, src_in=0.0, src_out=2.0, start=0.0,
              transform=[Keyframe(t=0, scale=1.0, ease="linear"), Keyframe(t=2.0, scale=1.08)]),
        VClip(id="c2", media_id=mid, src_in=3.5, src_out=5.0, start=2.0, transform=[Keyframe(t=0, scale=1.15,
                                                                                             ease="hold")]),
        VClip(id="c3", media_id=mid, src_in=6.0, src_out=7.0, start=3.5, speed=0.5),
        VClip(id="c4", media_id=mid, src_in=8.0, src_out=10.0, start=5.5,
              speed_ramp=[SpeedPoint(t=0, speed=1), SpeedPoint(t=0.5, speed=0.5), SpeedPoint(t=1, speed=1)]),
    ]
    tl.tracks["V2"] = [VClip(id="b1", media_id=mid, src_in=12.0, src_out=13.0, start=6.0, role="b_roll")]
    tl.tracks["V3"] = [TitleClip(id="t1", text="Test renderu", start=0.3, duration=1.5, style="title")]
    tl.transitions = [Transition(at=2.0, kind="dissolve", duration=0.4),
                      Transition(at=3.5, kind="dip_black", duration=0.4),
                      Transition(at=5.5, kind="whip", duration=0.3)]
    tl.subtitles.enabled = False
    return tl.normalize()


@pytest.mark.skipif(not HAS_MEDIA, reason="brak test.MP4 / ffmpeg")
def test_plan_ripple_keeps_hashes():
    p, mid = make_project("pytest-plan")
    tl = build_timeline(mid)
    v = presets.resolve_variant(tl, "youtube")
    plan1, _ = plan_render(p, tl, v, W=640, H=360, fps=Fraction(30), quality="preview")
    tl2 = tl.model_copy(deep=True)
    for c in tl2.tracks["V1"] + tl2.tracks["V2"] + tl2.tracks["V3"]:
        c.start += 1.0  # ripple całej osi o 1 s
    for t in tl2.transitions:
        t.at += 1.0
    plan2, _ = plan_render(p, tl2.normalize(), v, W=640, H=360, fps=Fraction(30), quality="preview")
    h1 = [j.hash for j in plan1.slices]
    h2 = [j.hash for j in plan2.slices if j.p >= 30]
    assert h1 == h2  # przesunięcie osi nie unieważnia plastrów
    kinds = [j.spec["base"]["type"] for j in plan1.slices]
    assert kinds.count("transition") == 3


@pytest.mark.skipif(not HAS_MEDIA, reason="brak test.MP4 / ffmpeg")
def test_render_preview_final_incremental_and_frames():
    import av

    from studio.render.engine import render_timeline
    from studio.render.frame import render_frame

    p, mid = make_project("pytest-render")
    tl = build_timeline(mid)
    OUT.mkdir(parents=True, exist_ok=True)
    for vn in ("youtube", "tiktok"):
        v = presets.resolve_variant(tl, vn)
        r = render_timeline(p, tl, v, OUT / f"prev_{vn}.mp4", quality="preview")
        exp = round(tl.duration * 30)
        assert abs(r["frames"] - exp) <= 1
        with av.open(r["path"]) as c:
            n = sum(1 for pk in c.demux(c.streams.video[0]) if pk.size)
            ad = float(c.streams.audio[0].duration * c.streams.audio[0].time_base)
        assert n == r["frames"]
        assert abs(ad - r["duration"]) <= 1 / 30 + 1e-3
    # final 1080p
    v = presets.resolve_variant(tl, "youtube")
    rf = render_timeline(p, tl, v, OUT / "final_youtube.mp4", quality="final", settings={"codec": "h264"})
    with av.open(rf["path"]) as c:
        assert (c.streams.video[0].width, c.streams.video[0].height) == (1920, 1080)
    # przyrostowo: zmiana jednego klipu -> tylko jego plastry
    r1 = render_timeline(p, tl, v, OUT / "prev_youtube.mp4", quality="preview")
    assert r1["slices_rendered"] == 0
    tl.tracks["V1"][2].transform = [Keyframe(t=0, scale=1.2, ease="hold")]  # c3
    r2 = render_timeline(p, tl, v, OUT / "prev_youtube.mp4", quality="preview")
    assert 0 < r2["slices_rendered"] <= 4 < r2["slices_total"]
    # klatki przed/po
    t0 = time.time()
    a = render_frame(p, tl, 0.6, OUT / "f_after.jpg", variant=v)
    b = render_frame(p, tl, 0.6, OUT / "f_before.jpg", variant=v, before=True)
    assert a.exists() and b.exists() and a.stat().st_size != b.stat().st_size
    assert time.time() - t0 < 10
    t1 = time.time()
    render_frame(p, tl, 0.6, OUT / "f_after2.jpg", variant=v)
    assert time.time() - t1 < 0.5  # cache

"""Testy QC: SSIM, pomiar napisów, automatyczne poprawki, QC renderu z celowym jump cutem i czarną luką."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from studio import paths

paths.setup_env()

from studio.project.model import Keyframe, Timeline, Transition, VClip, Variant  # noqa: E402
from studio.qc.checks import ssim, subtitle_issues  # noqa: E402
from studio.qc.fixes import apply_fixes, fixable  # noqa: E402

TEST = Path(r"C:\StudioEdit\test.MP4")
OUT = paths.CACHE / "dev_render" / "pytest_qc"


def test_ssim():
    rng = np.random.default_rng(0)
    a = rng.integers(0, 255, (90, 160)).astype(np.float32)
    assert ssim(a, a) == pytest.approx(1.0, abs=1e-4)
    assert ssim(a, rng.integers(0, 255, (90, 160)).astype(np.float32)) < 0.2


def test_subtitle_overflow_and_unsafe(tmp_path):
    ass = tmp_path / "s.ass"
    ass.write_text("\n".join([
        "[Script Info]", "PlayResX: 1080", "PlayResY: 1920", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        "Style: Default,Segoe UI Black,90,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,2,"
        "40,40,60,1", "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,TO JEST BARDZO DŁUGA LINIA NAPISÓW",
        "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,krótko",
    ]), encoding="utf-8")
    iss = subtitle_issues(str(ass), vertical=True)
    kinds = [(i["kind"], i["t0"]) for i in iss]
    assert ("subtitles", 1.0) in kinds  # za szeroka linia
    assert ("subtitles_unsafe", 3.0) in kinds  # MarginV 60 px w 9:16 -> pod interfejsem aplikacji


def test_apply_fixes_offline():
    tl = Timeline(fps=30)
    tl.tracks["V1"] = [VClip(id="a", media_id="m", src_in=0, src_out=2, start=0),
                       VClip(id="b", media_id="m", src_in=2.5, src_out=4.5, start=2),
                       VClip(id="c", media_id="m", src_in=6, src_out=8, start=4.4)]
    tl.normalize()
    issues = [
        {"kind": "jump_cut", "severity": "warn", "fix": {"agent": "render", "action": "punch_in",
                                                         "params": {"clip_id": "b", "prev_clip_id": "a"}}},
        {"kind": "black_frames", "severity": "error", "fix": {"agent": "render", "action": "fill_gap",
                                                              "params": {"t0": 4.0, "t1": 4.4}}},
        {"kind": "loudness", "severity": "warn", "fix": {"agent": "sound", "action": "loudness",
                                                         "params": {"delta": 2.0}}},
        {"kind": "subtitle_cut", "severity": "warn", "fix": {"agent": "sound", "action": "subtitles",
                                                             "params": {"max_chars": 24}}},
    ]

    class P:  # projekt bez mediów (długość źródła nieznana)
        def get_media(self, mid):
            raise KeyError(mid)

    tl2, notes = apply_fixes(P(), tl, fixable(issues))
    b = next(c for c in tl2.tracks["V1"] if c.id == "b")
    assert b.transform and b.transform[0].scale == pytest.approx(1.12)
    assert b.end == pytest.approx(4.4)  # luka wypełniona wydłużeniem poprzedniego klipu
    assert tl2.mix.target_lufs == pytest.approx(-14 + 1.6)
    assert tl2.subtitles.max_chars == 24
    assert len(notes) == 4 and all(isinstance(n, str) for n in notes)
    # naprzemienne skale, gdy poprzedni już powiększony
    tl.tracks["V1"][0].transform = [Keyframe(t=0, scale=1.12, ease="hold")]
    tl3, _ = apply_fixes(P(), tl, fixable(issues[:1]))
    assert next(c for c in tl3.tracks["V1"] if c.id == "b").transform[0].scale == pytest.approx(1.0)


@pytest.mark.skipif(not (TEST.exists() and paths.FFMPEG.exists()), reason="brak test.MP4 / ffmpeg")
def test_qc_detects_jump_cut_and_black():
    from studio.media.ffmpeg import media_info
    from studio.project.manager import ProjectManager
    from studio.qc.checks import run_qc
    from studio.render import presets
    from studio.render.engine import render_timeline

    root = OUT / "projects"
    shutil.rmtree(root, ignore_errors=True)
    p = ProjectManager(root=root).create("qc-test", ingest_mode="link")
    m = p.add_media_file(TEST)
    info = media_info(TEST)
    p.update_media(m.id, lambda x: x.info.update(info))
    tl = Timeline(fps=30, variants=[Variant(name="tiktok", aspect="9:16", width=1080, height=1920)])
    tl.tracks["V1"] = [VClip(id="j1", media_id=m.id, src_in=0, src_out=2, start=0),
                       VClip(id="j2", media_id=m.id, src_in=2.6, src_out=4.6, start=2),  # jump cut
                       VClip(id="j3", media_id=m.id, src_in=6, src_out=8, start=4.3),  # luka 0,3 s = czerń
                       VClip(id="j4", media_id=m.id, src_in=9, src_out=11, start=6.3)]
    tl.transitions = [Transition(at=6.3, kind="dip_black", duration=0.6)]  # zamierzona czerń
    tl.subtitles.enabled = False
    tl.normalize()
    v = presets.resolve_variant(tl, "tiktok")
    out = OUT / "qc.mp4"
    r = render_timeline(p, tl, v, out, quality="preview")
    rep = run_qc(p, tl, out, audio_report=r["audio"], effort="medium")
    kinds = {i["kind"]: i for i in rep["issues"]}
    assert "jump_cut" in kinds and kinds["jump_cut"]["t0"] == pytest.approx(1.9, abs=0.05)
    assert "black_frames" in kinds and 3.9 <= kinds["black_frames"]["t0"] <= 4.1
    assert not any(i["kind"] == "black_frames" and 5.9 < i["t0"] < 6.7 for i in rep["issues"])  # dip_black OK
    assert not rep["passed"] and rep["summary"]
    tl2, notes = apply_fixes(p, tl, fixable(rep["issues"]))
    r2 = render_timeline(p, tl2, v, out, quality="preview")
    assert r2["slices_rendered"] < r2["slices_total"]
    rep2 = run_qc(p, tl2, out, audio_report=r2["audio"], effort="medium")
    assert not any(i["kind"] in ("jump_cut", "black_frames") for i in rep2["issues"])

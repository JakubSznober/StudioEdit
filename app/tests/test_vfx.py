"""Szybkie testy obszaru VFX (bez GPU i bez ComfyUI)."""
from __future__ import annotations

import numpy as np
import pytest

from studio.agents import vfx as agent
from studio.project.model import Effect, Timeline, VClip
from studio.vfx import broll, models, segment, track
from studio.vfx.inpaint import _anonymize, _crop_box, _norm_box


def test_frames_4k_plus_1():
    for d in (0.5, 1.0, 2.0, 3.3, 5.0, 12.0):
        n = broll.frames_for(d)
        assert (n - 1) % 4 == 0 and 17 <= n <= broll.MAX_FRAMES
    assert broll.frames_for(2.0) == 49


def test_workflow_filled():
    wf = broll.build_workflow("a red car", width=830, height=470, frames=49, seed=7, steps=20)
    assert not any(k.startswith("_") for k in wf)
    assert wf["6"]["inputs"]["text"] == "a red car"
    assert wf["55"]["inputs"]["width"] % 32 == 0 and wf["55"]["inputs"]["height"] % 32 == 0
    assert wf["55"]["inputs"]["length"] == 49
    assert wf["3"]["inputs"]["seed"] == 7 and wf["3"]["inputs"]["sampler_name"] == "uni_pc"
    assert wf["38"]["inputs"]["type"] == "wan"
    assert wf["48"]["inputs"]["shift"] == 8.0
    assert broll.build_workflow("x", width=832, height=480, frames=17, save_node="SaveAnimatedWEBP")["58"][
        "class_type"] == "SaveAnimatedWEBP"


def test_prompt_classes_pl():
    assert segment.prompt_class("usuń tego przechodnia z tła")[0] == 0
    assert segment.prompt_class("usuń samochód")[0] == 2
    assert segment.prompt_class("wytnij psa")[0] == 16
    cls, _n, known = segment.prompt_class("usuń kabel z podłogi")
    assert cls is None and known
    assert segment.prompt_class("coś dziwnego")[2] is False


def test_timeline_to_source():
    c = VClip(media_id="m", src_in=10, src_out=20, start=5, speed=2.0)  # na osi 5..10
    assert agent.timeline_to_source(c, 6, 8) == pytest.approx((12, 16))
    assert agent.timeline_to_source(c, 0, 100) == pytest.approx((10, 20))
    assert agent.timeline_to_source(c, 30, 40) == pytest.approx((10, 20))  # brak części wspólnej → cały klip


def test_attach_effect_replaces_overlapping():
    tl = Timeline()
    c = VClip(media_id="m", src_in=0, src_out=10, start=0)
    tl.tracks["V1"] = [c]
    e1 = Effect(type="stabilize", output="a.mp4", status="done", params={"src_t0": 1, "src_t1": 4})
    e2 = Effect(type="stabilize", output="b.mp4", status="done", params={"src_t0": 3, "src_t1": 6})
    e3 = Effect(type="stabilize", output="c.mp4", status="done", params={"src_t0": 7, "src_t1": 9})
    for e in (e1, e2, e3):
        assert agent._attach_effect(tl, c.id, e)
    outs = [e.output for e in tl.v("V1")[0].effects]
    assert outs == ["b.mp4", "c.mp4"]
    assert not agent._attach_effect(tl, "brak", e1)


def test_clear_v2():
    tl = Timeline()
    tl.tracks["V2"] = [VClip(media_id="m", src_in=0, src_out=4, start=0),
                       VClip(media_id="m", src_in=0, src_out=2, start=5),
                       VClip(media_id="m", src_in=0, src_out=4, start=6.5)]
    n = agent._clear_v2(tl, 3.0, 7.0)
    v2 = tl.v("V2")
    assert n == 3 and len(v2) == 2
    assert v2[0].end == pytest.approx(3.0)
    assert v2[1].start == pytest.approx(7.0) and v2[1].src_in == pytest.approx(0.5)


def test_one_euro_smoothing():
    rng = np.random.default_rng(0)
    x = 0.5 + 0.02 * rng.standard_normal(100)
    y = track.smooth_series(x, 5.0)
    assert np.std(np.diff(y)) < np.std(np.diff(x)) * 0.6
    step = np.r_[np.zeros(30), np.ones(30)]
    s = track.smooth_series(step, 5.0, beta=3.0)
    assert s[-1] > 0.9 and s[0] < 0.1


def test_pick_continuity():
    cands = [[100, 100, 50, 50, 0.9], [400, 100, 52, 52, 0.95]]
    assert track._pick(cands, (125 / 640, 125 / 360), 640, 360)[0] == 100


def test_boxes_and_anonymize():
    assert _norm_box([0.1, 0.2, 0.5, 0.5], 100, 200) == (10, 40, 60, 140)
    assert _norm_box([10, 20, 30, 40], 100, 200) == (10, 20, 40, 60)
    m = np.zeros((3, 100, 200), bool)
    m[1, 40:60, 90:110] = True
    x0, y0, x1, y1 = _crop_box(m, 200, 100)
    assert x0 <= 90 and x1 >= 110 and y0 <= 40 and y1 >= 60
    img = (np.random.default_rng(1).random((40, 40, 3)) * 255).astype(np.uint8)
    out = _anonymize(img, 0.8)
    assert out.shape == img.shape and out.std() < img.std()


def test_models_catalog():
    keys = {m["key"] for m in models.CATALOG}
    assert {"sam2.1_l", "yolo11m", "big-lama", "yunet", "wan22_ti2v_5b"} <= keys
    for m in models.CATALOG:
        for k in ("key", "name", "kind", "provider", "size_gb", "description", "used_by"):
            assert k in m
    wan = models.entry("wan22_ti2v_5b")
    assert any("diffusion_models" in str(f["dest"]) for f in wan["files"])
    assert models.installed("nie-ma-takiego") is False
    with pytest.raises(KeyError):
        models.entry("nie-ma-takiego")


def test_vfx_task_validates_kind():
    class P:
        id = "p"
        cache = "."
    with pytest.raises(ValueError):
        agent.vfx_task(P(), Timeline(), "teleport", "x", 0, 1, {}, group="g")
    with pytest.raises(ValueError):
        agent.vfx_task(P(), Timeline(), "stabilize", "brak", 0, 1, {}, group="g")

"""Szybkie testy rdzenia: wersjonowanie osi, pacing, dublowanie, operacje ripple, parser czatu, VRAM."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from studio import paths

paths.setup_env()

from studio.agents.producer import rule_parse  # noqa: E402
from studio.edit import ops  # noqa: E402
from studio.edit.material import SentenceUnit  # noqa: E402
from studio.edit.takes import group_takes, keep_ranges  # noqa: E402
from studio.project.manager import ProjectManager  # noqa: E402
from studio.project.model import AClip, Keyframe, Sentence, Timeline, VClip, Word  # noqa: E402
from studio.vram import VRAMOrchestrator  # noqa: E402


def _tl() -> Timeline:
    tl = Timeline()
    tl.tracks["V1"] = [VClip(media_id="m1", src_in=0, src_out=4, start=0),
                       VClip(media_id="m1", src_in=10, src_out=14, start=4),
                       VClip(media_id="m2", src_in=0, src_out=2, start=8)]
    tl.tracks["A1"] = [AClip(media_id="m1", src_in=0, src_out=4, start=0),
                       AClip(media_id="m1", src_in=10, src_out=14, start=4)]
    return tl.normalize()


def test_versions_undo_redo(tmp_path: Path):
    pm = ProjectManager(root=tmp_path)
    p = pm.create("Test")
    tl = _tl()
    p.commit_timeline(tl, "v1")
    tl2 = p.timeline()
    tl2.tracks["V1"] = tl2.tracks["V1"][:1]
    p.commit_timeline(tl2, "v2")
    assert p.timeline().version == 2
    assert p.undo().version == 1
    assert len(p.timeline().tracks["V1"]) == 3
    assert p.redo().version == 2
    v3 = p.mutate_timeline(lambda t: ops.ripple_delete(t, 0, 1) and t, "v3")
    assert v3.version == 3 and p.history()["redo"] == []


def test_keep_ranges_removes_fillers_and_keeps_breath():
    words = [Word(w="To", s=0.0, e=0.2), Word(w="jest", s=0.25, e=0.5), Word(w="eee", s=0.6, e=1.4, filler=True),
             Word(w="świetne", s=1.5, e=2.0), Word(w="miejsce", s=3.2, e=3.7)]
    s = Sentence(id="x", text="To jest eee świetne miejsce", s=0, e=3.7, words=words)
    r = keep_ranges(s, breath=0.2, max_gap=0.45)
    assert len(r) >= 2
    # wypełniacz nie może być w żadnym zakresie
    assert all(not (a <= 0.9 <= b) for a, b in r)
    # długa pauza 2.0-3.2 wycięta
    assert all(not (a < 2.6 < b) for a, b in r)


def test_take_selection_picks_fluent():
    def unit(i, text, fl):
        s = Sentence(id=f"s{i}", text=text, s=i * 5, e=i * 5 + 3)
        return SentenceUnit(id=s.id, media_id="m", text=text, s=s.s, e=s.e, spk="Mówca 1", fluency=fl, sentence=s,
                            order=i)
    us = [unit(0, "Witajcie w moim nowym filmie o górach", 0.6),
          unit(1, "Witajcie w moim nowym filmie o górach!", 0.95),
          unit(2, "Dzisiaj pokażę wam piękną trasę", 0.9)]
    groups = group_takes(us)
    assert len(groups) == 1
    assert us[1].hero and not us[0].hero and us[2].hero


def test_ripple_delete_and_speed():
    tl = _tl()
    d = ops.ripple_delete(tl, 3.0, 5.0)
    assert d == pytest.approx(2.0)
    assert tl.duration == pytest.approx(8.0)
    tl.tracks["V2"] = [VClip(media_id="m3", src_in=0, src_out=3, start=1, role="b_roll")]
    tl.normalize()
    ops.set_speed(tl, {}, 1.0, 2.0, 0.5)
    v2 = tl.tracks["V2"][0]
    assert v2.duration == pytest.approx(3.0)  # długość na osi zachowana
    assert v2.src_out - v2.src_in == pytest.approx(1.5)


def test_punch_in_keyframes():
    tl = _tl()
    ops.punch_in(tl, 1.0, 2.0, 1.2)
    kf = tl.tracks["V1"][0].transform
    assert any(k.scale == 1.2 for k in kf) and kf[-1].scale == 1.0


def test_rule_parser_polish():
    r = rule_parse("Zmontuj dynamiczny film na YouTube, max 4 minuty, ciepłe filmowe kolory", None)
    a = r["actions"][0]
    assert a["type"] == "montage" and a["params"]["target_duration"] == 240 and a["params"]["look"] == "warm_film"
    r = rule_parse("Tutaj zrób slow-motion i przyciemnij niebo, lektor: Witajcie w Tatrach", {"in": 1, "out": 3})
    types = [x["type"] for x in r["actions"]]
    assert "edit" in types and "color" in types and "tts" in types


def test_vram_single_owner_and_batching():
    v = VRAMOrchestrator(idle_unload_s=999)
    released = []
    v.register_releaser("torch:", lambda o: released.append(o))
    order = []
    with v.lease("torch:a", "x"):
        def other():
            with v.lease("torch:b", "y"):
                order.append("b")
        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.3)
        assert order == []  # b czeka, aż a zwolni GPU
    t.join(timeout=90)  # pierwsze zwolnienie importuje torch (wolne)
    assert order == ["b"] and released == ["torch:a"]

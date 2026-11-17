"""Ręczna edycja osi: kolejność V1 (magnetycznie), przycinanie, usuwanie, swobodne przesuwanie."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from studio import paths

paths.setup_env()

from studio.edit import ops  # noqa: E402
from studio.project.model import AClip, Marker, Timeline, Transition, VClip  # noqa: E402


def _tl() -> Timeline:
    """V1: a[0-4) b[4-7) c[7-12); dialog A1 z J-cutem przy b; przebitka nad b; przejście po a."""
    a = VClip(id="a", media_id="m1", src_in=0, src_out=4, start=0)
    b = VClip(id="b", media_id="m2", src_in=10, src_out=13, start=4)
    c = VClip(id="c", media_id="m3", src_in=0, src_out=5, start=7)
    tl = Timeline(fps=30, width=1080, height=1920)
    tl.tracks = {"V1": [a, b, c], "V2": [VClip(id="br", media_id="m9", src_in=0, src_out=1, start=5, role="b_roll")],
                 "V3": [], "A1": [AClip(id="aa", media_id="m1", src_in=0, src_out=4, start=0, link="a"),
                                  AClip(id="ab", media_id="m2", src_in=9.6, src_out=13, start=3.6, link="b"),
                                  AClip(id="ac", media_id="m3", src_in=0, src_out=5, start=7, link="c")],
                 "A2": [], "A3": [AClip(id="mus", path="x.wav", src_in=0, src_out=12, start=0, role="music")],
                 "A4": [], "A5": []}
    tl.transitions = [Transition(at=4.0, kind="dissolve")]
    tl.markers = [Marker(t=7.5, label="X", kind="climax")]
    return tl.normalize()


def _starts(tl, k):
    return {c.id: round(c.start, 3) for c in tl.tracks[k]}


def test_reorder_moves_linked_dialog_broll_and_markers():
    tl = _tl()
    ops.move_v1(tl, "c", 0)  # c na początek
    assert [c.id for c in tl.tracks["V1"]] == ["c", "a", "b"]
    assert _starts(tl, "V1") == {"c": 0.0, "a": 5.0, "b": 9.0}
    a1 = _starts(tl, "A1")
    assert a1["ac"] == 0.0 and a1["aa"] == 5.0 and a1["ab"] == pytest.approx(8.6)  # J-cut zachowany
    assert _starts(tl, "V2")["br"] == 10.0  # przebitka jedzie z klipem b
    assert tl.markers[0].t == pytest.approx(0.5)  # marker był w c
    assert _starts(tl, "A3")["mus"] == 0.0  # muzyka zostaje
    assert [t.at for t in tl.transitions] == [9.0]  # a→b nadal sąsiadują, więc przejście zostaje


def test_reorder_drops_transition_when_neighbours_change():
    tl = _tl()
    ops.move_v1(tl, "b", 2)  # a, c, b
    assert [c.id for c in tl.tracks["V1"]] == ["a", "c", "b"]
    assert tl.transitions == []


def test_trim_out_on_v1_ripples_and_trims_linked_audio():
    tl = _tl()
    media = {"m1": SimpleNamespace(info={"duration": 20.0})}
    ops.trim_clip(tl, media, "a", "out", -1.0)
    assert tl.tracks["V1"][0].src_out == 3.0
    assert _starts(tl, "V1") == {"a": 0.0, "b": 3.0, "c": 6.0}
    assert next(c for c in tl.tracks["A1"] if c.id == "aa").src_out == 3.0


def test_trim_respects_media_length():
    tl = _tl()
    media = {"m3": SimpleNamespace(info={"duration": 5.5})}
    ops.trim_clip(tl, media, "c", "out", 10.0)
    assert tl.tracks["V1"][2].src_out == 5.5


def test_delete_v1_removes_linked_and_anchored_and_closes_gap():
    tl = _tl()
    ops.delete_clip(tl, "b")
    assert _starts(tl, "V1") == {"a": 0.0, "c": 4.0}
    assert "ab" not in _starts(tl, "A1") and tl.tracks["V2"] == []
    assert _starts(tl, "A1")["ac"] == 4.0


def test_move_free_track_and_reject_v1():
    tl = _tl()
    ops.move_clip(tl, "br", 1.25)
    assert _starts(tl, "V2")["br"] == 1.25
    with pytest.raises(ValueError):
        ops.move_clip(tl, "a", 3.0)


def test_track_state_hides_and_mutes():
    from studio.audio.mix import dialog_clips

    tl = _tl()
    assert not tl.track_off("A4")
    tl.track_state = {"A1": {"muted": True}, "V2": {"hidden": True}, "A3": {"muted": False}}
    assert tl.track_off("A1") and tl.track_off("V2") and not tl.track_off("A3")
    assert tl.a_active("A1") == [] and len(tl.a_active("A3")) == 1
    assert dialog_clips(None, tl) == []  # bez zapasowego dźwięku z V1
    # stan ścieżek przeżywa zapis/odczyt osi
    again = Timeline(**tl.to_json())
    assert again.track_off("V2")

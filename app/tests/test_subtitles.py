"""Szybkie testy napisów: łamanie linii, bezpieczny obszar, formaty ASS/SRT."""
from __future__ import annotations

import re

import pytest

from studio import paths

paths.setup_env()

from studio.project.model import MediaItem, Sentence, Timeline, Transcript, Variant, VClip, Word  # noqa: E402
from studio.subtitles.build import ass_color, ass_time, build_subtitles, srt_time  # noqa: E402
from studio.subtitles.layout import Measurer, balance_lines, layout_cue, split_cues  # noqa: E402


def test_time_formats():
    assert srt_time(3725.5) == "01:02:05,500"
    assert ass_time(65.257) == "0:01:05.26"
    assert ass_color("#FFD400") == "&H0000D4FF"
    assert ass_color("#000000", 0x80) == "&H80000000"


def test_measure_and_balance():
    m = Measurer("Segoe UI Black", 60)
    assert m.width("WWWW") > m.width("iiii") > 0
    lines = balance_lines("to jest całkiem długi tekst napisów do podziału".split(), 2, m.width)
    assert len(lines) == 2
    wa, wb = m.width(" ".join(lines[0])), m.width(" ".join(lines[1]))
    assert abs(wa - wb) < m.width("długi tekst")


def test_split_cues_respect_width():
    m = Measurer("Segoe UI Black", 80)
    words = ("Dzisiaj pokażę wam jak w prosty sposób zmontować film zupełnie automatycznie "
             "przy pomocy lokalnej sztucznej inteligencji").split()
    maxw = 900
    cues = split_cues(words, maxw, 32, 2, m.width)
    assert sum(len(c) for c in cues) == len(words)
    for c in cues:
        lines, scale = layout_cue([words[i] for i in c], maxw, 32, 2, m.width)
        assert len(lines) <= 2
        for ln in lines:
            assert m.width(" ".join(words[c[i]] for i in ln)) * scale <= maxw + 1


def test_single_long_word_scaled():
    m = Measurer("Segoe UI Black", 120)
    lines, scale = layout_cue(["Konstantynopolitańczykowianeczka"], 600, 40, 2, m.width)
    assert scale < 1.0


class _FakeProject:
    id = "test"

    def __init__(self, media):
        self._m = {m.id: m for m in media}

    def get_media(self, mid):
        return self._m[mid]


def _media():
    words = []
    t = 0.5
    text = ("Eee witajcie w moim nowym filmie. Dzisiaj pokażę wam jak zrobić świetny montaż "
            "bez wysiłku i bez drogiego sprzętu.").split()
    for w in text:
        words.append(Word(w=w, s=t, e=t + 0.3, filler=w.lower().startswith("eee")))
        t += 0.38
    s1 = Sentence(id="m1_0000", text=" ".join(text[:6]), s=words[0].s, e=words[5].e, words=words[:6])
    s2 = Sentence(id="m1_0001", text=" ".join(text[6:]), s=words[6].s, e=words[-1].e, words=words[6:])
    return MediaItem(id="m1", name="a.mp4", path="a.mp4", info={"has_audio": True, "duration": 20},
                     transcript=Transcript(sentences=[s1, s2]))


@pytest.mark.parametrize("mode", ["word_pop", "karaoke", "static"])
def test_build_subtitles_formats(tmp_path, mode):
    tl = Timeline()
    tl.tracks["V1"] = [VClip(media_id="m1", src_in=0.0, src_out=3.0, start=0.0),
                       VClip(media_id="m1", src_in=3.5, src_out=12.0, start=3.0)]
    tl.subtitles.mode = mode
    v = Variant(name="tiktok", aspect="9:16", width=1080, height=1920)
    res = build_subtitles(_FakeProject([_media()]), tl, v, tmp_path)
    ass = res["ass"].read_text(encoding="utf-8-sig")
    srt = res["srt"].read_text(encoding="utf-8")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Eee" not in ass and "Eee" not in srt  # wypełniacze usunięte
    blocks = [b for b in srt.strip().split("\n\n") if b.strip()]
    assert blocks
    for i, b in enumerate(blocks, 1):
        ls = b.splitlines()
        assert ls[0] == str(i)
        assert re.match(r"\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}$", ls[1])
        assert 1 <= len(ls) - 2 <= 2
    if mode == "word_pop":
        assert "\\t(" in ass and "\\c&H" in ass
    if mode == "karaoke":
        assert "\\kf" in ass
    # pierwsze słowo bez wypełniacza ("witajcie" @ 0.88 s); słowa ze źródła 3.5+ trafiają na oś od 3.0
    assert "0:00:00.88" in ass
    from studio.subtitles.build import timeline_words

    tw = timeline_words(_FakeProject([_media()]), tl)
    src_word = next(w for w in _media().transcript.words() if w.s >= 3.5)
    mapped = next(w for w in tw if w.w == src_word.w)
    assert mapped.s == pytest.approx(3.0 + (src_word.s - 3.5), abs=1e-6)
    assert all(b.s >= a.s for a, b in zip(tw, tw[1:]))

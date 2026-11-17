"""Regresje z projektu „Europa”: pionowe nagrania z telefonu, duble-parafrazy, zakładki, plan z modelu."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from studio import paths

paths.setup_env()

from studio.edit import assemble as asm  # noqa: E402
from studio.edit import planner  # noqa: E402
from studio.edit.takes import take_similarity  # noqa: E402
from studio.media import ffmpeg as ff  # noqa: E402


# ------------------------------------------------------------------ obrót (iPhone: 3840x2160 + macierz -90°)
@pytest.fixture(scope="module")
def portrait_clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("rot")
    land = d / "land.mp4"
    ff.run(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(land)])
    out = d / "portrait.mp4"
    ff.run(["-display_rotation", "90", "-i", str(land), "-c", "copy", str(out)])
    return out


def test_media_info_reads_display_rotation(portrait_clip):
    info = ff.media_info(portrait_clip)
    assert abs(info["rotation"]) == 90
    assert (info["width"], info["height"]) == (180, 320)


def test_read_frames_are_portrait(portrait_clip):
    frame = next(iter(ff.read_frames(portrait_clip, fps=5, width=90)))
    assert frame.shape[0] > frame.shape[1]


def test_stderr_fallback_reads_rotation(portrait_clip):
    v = next(s for s in ff._probe_ffmpeg_stderr(str(portrait_clip))["streams"] if s["codec_type"] == "video")
    assert abs(v["side_data_list"][0]["rotation"]) == 90


# ------------------------------------------------------------------ duble-parafrazy
@pytest.mark.parametrize("a,b", [
    ("Hmm, dokładnie, czyli ten wcześniejszy artykuł okazał się typowym przykładem dezinformacji, czyli celowego "
     "wprowadzenia nas w błąd.",
     "Dokładnie, czyli okazało się, że wcześniejszy artykuł był typowym przykładem dezinformacji, czyli cel tego "
     "rozpowszechniania błędnych informacji, żeby wprowadzić nas w błąd."),
    ("Stop, ale może najpierw sprawdzilibyśmy tę informację, bo przecież to, że ktoś coś napisał w artykule, nie musi "
     "wcale być zgodne z prawdą.",
     "Stop, ale może najpierw sprawdzilibyśmy w ogóle, czy to nie jest fake news, bo przecież to, że ktoś napisał coś "
     "w artykule, nie musi oznaczać, że informacja jest zgodna z prawdą."),
])
def test_paraphrased_takes_match(a, b):
    assert take_similarity(a, b) >= 78


@pytest.mark.parametrize("a,b", [
    ("Przecież nie każda informacja, którą czytamy, musi być zgodna z prawdą, co nie?",
     "Stop, ale może najpierw sprawdzilibyśmy tę informację, bo przecież to, że ktoś coś napisał w artykule, nie musi "
     "wcale być zgodne z prawdą."),
    ("Widziałeś, że na Ukrainie zaczęli strzelać do swoich.", "Tak więc, słuchajcie, zatrzymajmy się, sprawdźmy "
     "informacje, zanim wyślemy je dalej."),
])
def test_different_lines_do_not_match(a, b):
    assert take_similarity(a, b) < 78


# ------------------------------------------------------------------ zakładki i powtórzone kwestie
def _unit(mid, tg=None, uid="u"):
    return SimpleNamespace(media_id=mid, take_group=tg, id=uid)


def test_clamp_overlaps_same_source():
    u = _unit("m1")
    a = asm.Piece(unit=u, part="hook", s=0.33, e=5.51, first_of_unit=True, last_of_unit=True)
    b = asm.Piece(unit=u, part="hook", s=5.39, e=9.91, first_of_unit=True, last_of_unit=True)
    c = asm.Piece(unit=_unit("m2"), part="x", s=1.0, e=2.0, first_of_unit=True, last_of_unit=True)
    out = asm.clamp_overlaps([a, b, c])
    assert [round(p.s, 2) for p in out] == [0.33, 5.51, 1.0]


def test_resolve_skips_second_take_of_same_line():
    u1, u2, u3 = _unit("a", "take_x", "s1"), _unit("b", "take_x", "s2"), _unit("c", None, "s3")
    plan = {"structure": [{"part": "rozwinięcie", "items": [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]}]}
    seq, missing = asm._resolve(plan, [u1, u2, u3], [])
    assert [u.id for _p, u, _i in seq] == ["s1", "s3"] and not missing


# ------------------------------------------------------------------ plan z modelu: limit i ponowna próba
def test_llm_plan_retries_with_limits(monkeypatch):
    calls = []

    def fake_chat_json(messages, schema, **kw):
        calls.append(kw)
        if len(calls) == 1:
            raise planner.llm.LLMError("Błąd połączenia z Ollama: timed out")
        return {"title": "T", "pace": "dynamiczne", "music_mood": "x", "structure": [{"part": "hook", "items": [{"id": "s1"}]}]}

    monkeypatch.setattr(planner.llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(planner, "log", lambda *a, **k: None)
    plan = planner._llm_plan([{"role": "user", "content": "x"}], role="director", project_id=None, on_token=None)
    assert plan["structure"][0]["items"][0]["id"] == "s1"
    assert len(calls) == 2
    assert all(c["num_predict"] == planner.PLAN_MAX_TOKENS and c["timeout"] == planner.PLAN_IDLE_TIMEOUT_S
               and callable(c["on_token"]) for c in calls)
    assert calls[1]["temperature"] < calls[0]["temperature"]


def test_llm_plan_falls_back_to_faster_model_and_deadline(monkeypatch):
    roles = []

    def fake_chat_json(messages, schema, **kw):
        roles.append(kw["role"])
        if kw["role"] == "director_max":
            raise planner.llm.LLMError("timed out")
        return {"title": "T", "pace": "x", "music_mood": "x", "structure": [{"part": "hook", "items": [{"id": "a"}]}]}

    monkeypatch.setattr(planner.llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(planner, "log", lambda *a, **k: None)
    planner._llm_plan([], role="director_max", project_id=None, on_token=None)
    assert roles == ["director_max", "director"]
    cb = planner._deadline(-1.0, None)
    with pytest.raises(planner.llm.LLMError):
        cb("x")


def test_alternate_take_media_only_pure_alternates():
    S = lambda mid, tg, hero: SimpleNamespace(media_id=mid, take_group=tg, hero=hero)  # noqa: E731
    sents = [S("hero", "g1", True), S("alt", "g1", False), S("mixed", "g2", False), S("mixed", None, True),
             S("plain", None, True)]
    assert asm.alternate_take_media(sents) == {"alt"}


# ------------------------------------------------------------------ przejścia
@pytest.mark.parametrize("kind,style,speech,same,scene,expected", [
    ("whip", "reklama", True, False, True, "dissolve"),       # efektowne → spokojne w filmie z mową
    ("zoom", "reklama", True, False, False, None),            # bez zmiany sceny → czyste cięcie
    ("dip_black", "dokument", True, False, True, "dip_black"),
    ("whip", "teledysk", True, False, True, "whip"),          # teledysk może mieć efekty
    ("dissolve", "reklama", True, True, True, None),          # to samo nagranie → nigdy przejście
])
def test_natural_transition(kind, style, speech, same, scene, expected):
    assert asm.natural_transition(kind, style=style, speech=speech, same_media=same, scene_change=scene) == expected


def test_location_differs_uses_word_stems():
    assert not asm.location_differs("korytarz szkolny", "korytarz szkoły")
    assert asm.location_differs("korytarz szkolny", "wewnętrzne pomieszczenie")


def test_short_plan_gets_refined(monkeypatch):
    units = [SimpleNamespace(id=f"s{i}", dur=10.0) for i in range(6)]
    short = {"structure": [{"part": "hook", "items": [{"id": "s0"}, {"id": "s1"}]}]}          # 20 s
    longer = {"structure": [{"part": "hook", "items": [{"id": f"s{i}"} for i in range(4)]}]}  # 40 s
    seen = []
    monkeypatch.setattr(planner, "_llm_plan", lambda messages, **kw: (seen.append(messages), longer)[1])
    monkeypatch.setattr(planner, "log", lambda *a, **k: None)
    out = planner._refine_if_short(short, [{"role": "user", "content": "x"}], units, target=45, role="director",
                                   project_id=None)
    assert out is longer and "45 s" in seen[0][-1]["content"]
    # wystarczająco długi plan - bez dodatkowego zapytania
    assert planner._refine_if_short(longer, [], units, target=45, role="director", project_id=None) is longer


@pytest.mark.parametrize("brief,hit", [("dodaj muzykę i używaj przejść", True), ("ładne przejścia", True),
                                       ("z przenikaniem", True), ("bez efektów", False)])
def test_brief_asks_for_transitions(brief, hit):
    assert bool(asm.TRANSITION_WORDS.search(brief)) is hit

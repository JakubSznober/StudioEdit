"""Katalog stylów montażu: kompletność, spójność z muzyką i lookami, reguły przejść."""
from __future__ import annotations

import pytest

from studio import paths

paths.setup_env()

from studio.audio.music import MOODS  # noqa: E402
from studio.color.looks import look_exists  # noqa: E402
from studio.edit import assemble as asm  # noqa: E402
from studio.edit import styles  # noqa: E402

FIELDS = {"label", "description", "pace", "broll_gap", "transitions", "punch_in", "slow_zoom", "title_card",
          "subtitles", "music_mood", "look", "director"}


@pytest.mark.parametrize("key", list(styles.STYLES))
def test_style_complete_and_consistent(key):
    s = styles.STYLES[key]
    assert FIELDS <= set(s), FIELDS - set(s)
    assert s["pace"] in ("spokojne", "umiarkowane", "dynamiczne", "bardzo dynamiczne")
    assert s["transitions"] in ("none", "calm", "flashy")
    assert s["subtitles"][0] in ("static", "karaoke", "word_pop", "none")
    assert s["music_mood"] in MOODS, f"{key}: nieznany nastrój {s['music_mood']}"
    assert look_exists(s["look"]), f"{key}: nieznany look {s['look']}"
    assert len(s["description"]) > 30 and len(s["director"]) > 30


def test_aliases_and_default():
    assert styles.key("TikTok") == "rolka" and styles.key("reportaż") == "dokument"
    assert styles.key(None) == styles.DEFAULT and styles.key("nieznany") == styles.DEFAULT
    assert all(v in styles.STYLES for v in styles.ALIASES.values())


def test_public_catalog_for_ui():
    pub = styles.public()
    assert len(pub) == len(styles.STYLES) >= 15
    assert all(p["rules"] and p["description"] for p in pub)


def test_transition_policy_by_style():
    # podcast = czyste cięcia; chyba że brief wprost prosi o przejścia
    assert asm.natural_transition("dissolve", style="podcast", speech=True, same_media=False, scene_change=True) is None
    assert asm.natural_transition("dissolve", style="podcast", speech=True, same_media=False, scene_change=True,
                                  brief_asks=True) == "dissolve"
    # zwiastun i sport mogą mieć efektowne przejścia, dokument zamienia je na spokojne
    assert asm.natural_transition("whip", style="zwiastun", speech=True, same_media=False, scene_change=True) == "whip"
    assert asm.natural_transition("whip", style="dokument", speech=True, same_media=False, scene_change=True) == "dissolve"

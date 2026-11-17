"""Pliki podglądu: każdy render do nowego pliku, meta wskazuje aktualny, sprzątanie omija pliki w użyciu."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio import paths

paths.setup_env()

from studio.agents import render  # noqa: E402


@pytest.fixture()
def proj(tmp_path):
    d = tmp_path / "renders"
    d.mkdir()
    return SimpleNamespace(id="p1", dir=lambda name: tmp_path / name)


def test_preview_path_follows_meta_and_falls_back(proj):
    d = proj.dir("renders")
    legacy = d / "preview_tiktok.mp4"
    legacy.write_bytes(b"x")
    assert render.preview_path(proj, "tiktok") == legacy  # stary projekt bez pola „file”
    new = render._new_preview_path(proj, "tiktok")
    new.write_bytes(b"y")
    (d / "preview_tiktok.json").write_text(json.dumps({"file": new.name}), encoding="utf-8")
    assert render.preview_path(proj, "tiktok") == new
    assert new.name.startswith("preview_tiktok_2") and new != render._new_preview_path(proj, "tiktok")


def test_cleanup_keeps_current_and_other_variants(proj):
    d = proj.dir("renders")
    old = [d / "preview_tiktok.mp4", d / "preview_tiktok_20260101_000000_aaaa.mp4",
           d / "preview_tiktok_20260101_000000_aaaa.srt"]
    keep = d / "preview_tiktok_20270101_000000_bbbb.mp4"
    other = d / "preview_youtube_20260101_000000_cccc.mp4"
    for f in [*old, keep, other]:
        f.write_bytes(b"x")
    assert render._cleanup_previews(proj, "tiktok", keep) == 3
    assert keep.exists() and other.exists() and not any(f.exists() for f in old)


@pytest.mark.skipif(sys.platform != "win32", reason="blokada otwartego pliku dotyczy Windows")
def test_cleanup_skips_file_in_use(proj):
    d = proj.dir("renders")
    playing = d / "preview_tiktok_20260101_000000_aaaa.mp4"
    playing.write_bytes(b"x")
    keep = d / "preview_tiktok_20270101_000000_bbbb.mp4"
    keep.write_bytes(b"y")
    with open(playing, "rb"):  # jak przeglądarka odtwarzająca stary podgląd
        render._cleanup_previews(proj, "tiktok", keep)  # nie może rzucić wyjątku
    assert keep.exists()
    render._cleanup_previews(proj, "tiktok", keep)  # po zwolnieniu pliku sprząta przy kolejnym renderze
    assert not playing.exists()


def test_same_variant_shares_one_lock():
    a = render._lock_for(("p", "tiktok", "preview"))
    assert a is render._lock_for(("p", "tiktok", "preview"))
    assert a is not render._lock_for(("p", "youtube", "preview"))
    assert a is not render._lock_for(("p", "tiktok", "final"))

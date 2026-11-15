"""Szybkie testy mechaniki indeksu (bez modeli, bez GPU)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio.index.db import Database  # noqa: E402
from studio.index.probe import color_profile  # noqa: E402
from studio.index.query import ShotFilter, search  # noqa: E402
from studio.index.speech import speech_ratio  # noqa: E402


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "index.sqlite")
    yield d
    d.close()


def test_color_profile_heuristics(tmp_path: Path) -> None:
    p = tmp_path / "clip.mp4"
    assert color_profile({"color_transfer": "smpte2084"}, {}, p) == "hdr-pq"
    assert color_profile({"color_transfer": "arib-std-b67"}, {}, p) == "hdr-hlg"
    assert color_profile({"color_transfer": "bt709"}, {}, p) == "sdr"
    assert color_profile({}, {"comment": "S-Log3 / S-Gamut3.Cine"}, p) == "log"
    assert color_profile({}, {}, tmp_path / "A7S3_slog3_0001.mp4") == "log"
    assert color_profile({}, {}, p) == "unknown"


def test_speech_ratio() -> None:
    segs = [{"start": 1.0, "end": 3.0}, {"start": 4.0, "end": 5.0}]
    assert speech_ratio(segs, 0.0, 10.0) == pytest.approx(0.3)
    assert speech_ratio(segs, 2.0, 4.5) == pytest.approx(0.6)
    assert speech_ratio(segs, 6.0, 8.0) == 0.0


def test_db_roundtrip_and_fingerprint_change(db: Database) -> None:
    mid = db.upsert_media("C:/x/a.mp4", "fp1", 10, 1.0, "video")
    db.replace_shots(mid, [{"start": 0, "end": 5}, {"start": 5, "end": 9, "cut_start": False}])
    shots = db.list_shots(mid)
    assert [s["cut_start"] for s in shots] == [1, 0]
    db.update_shot(shots[0]["id"], sharpness=100.0, tags=["las", "jezioro"], embedding=np.ones(8, dtype=np.float32))
    db.mark_stage(mid, "shots")
    assert db.stages_done(mid) == {"shots"}
    ids, mat = db.shot_embeddings()
    assert ids == [shots[0]["id"]] and mat.shape == (1, 8)

    # ten sam plik, inny odcisk → wszystko wyczyszczone
    mid2 = db.upsert_media("C:/x/a.mp4", "fp2", 11, 2.0, "video")
    assert mid2 == mid
    assert db.list_shots(mid) == [] and db.stages_done(mid) == set()


def test_fts_and_search_filters(db: Database) -> None:
    mid = db.upsert_media("C:/x/b.mp4", "fp", 10, 1.0, "video")
    db.replace_shots(mid, [{"start": 0, "end": 4}, {"start": 4, "end": 8}, {"start": 8, "end": 12}])
    s = db.list_shots(mid)
    db.update_shot(s[0]["id"], sharpness=500, caption="Kobieta mówi do kamery w kuchni", people=1, speech_ratio=0.9)
    db.update_shot(s[1]["id"], sharpness=20, caption="Rozmyte ujęcie lasu", people=0, speech_ratio=0.0)
    db.update_shot(s[2]["id"], sharpness=400, caption="Góry o zachodzie słońca", people=0, speech_ratio=0.0)
    for row in db.list_shots(mid):
        db.set_shot_text(row["id"], mid, row["caption"])
    db.replace_transcript(mid, [{"start": 0.5, "end": 3.0, "text": "Dzień dobry, witam w mojej kuchni"}])

    # filtr: bez mowy i ostre → tylko góry
    res = search(db, None, ShotFilter(has_speech=False, min_sharpness=100))
    assert [r["idx"] for r in res] == [2]

    # FTS bez embeddera: "kuchnia" trafia w opis i w transkrypcję ujęcia 0 (odmiana przez prefiks "kuchni")
    res = search(db, "kuchni", ShotFilter(), embedder=None)
    assert res and res[0]["idx"] == 0 and res[0]["score"] >= 1.0

    # min_people
    res = search(db, None, ShotFilter(min_people=1))
    assert [r["idx"] for r in res] == [0]

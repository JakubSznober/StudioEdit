"""Szybkie testy obszaru Kolor (bez GPU, bez plików wideo)."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from studio.color import analyze, filters, looks, lut, relight
from studio.project.model import Grade, MediaItem, VClip

STATS = {"luma_mean": 0.45, "luma_p01": 0.03, "luma_p99": 0.95, "clipped_pct": 0.2, "crushed_pct": 0.1,
         "wb_rgb": [0.5, 0.5, 0.5], "sat_mean": 0.3, "lab_mean": [50.0, 2.0, 5.0], "lab_std": [20.0, 8.0, 10.0],
         "skin_pct": 5.0, "sky_pct": 10.0, "hist": [1 / 32] * 32, "color_range": "tv"}
PROFILES = ["rec709", "slog3", "clog3", "dlog", "vlog", "hlg", "pq", "flat"]
from studio.color.looks import BUILTIN  # noqa: E402

LOOKS = [None] + [b["id"] for b in BUILTIN]


def _gray_ramp(n=256):
    x = np.linspace(0, 1, n)
    return np.stack([x, x, x], axis=1)


def test_neutral_grade_is_identity():
    g = Grade(match={"auto_wb": 0, "auto_exposure": False})
    grid = lut.lattice(9)
    assert np.abs(lut.apply_grade_rgb(grid, g, STATS) - grid).max() < 1e-6


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("look", LOOKS)
def test_luminance_monotonic(profile, look):
    g = Grade(input_profile=profile, look=look, highlight_recovery=0.5, contrast=0.3, shadow_lift=0.3)
    y = lut.apply_grade_rgb(_gray_ramp(), g, STATS) @ lut.LUMA
    assert np.all(np.diff(y) >= -1e-6), f"{profile}/{look} nie jest monotoniczny"
    assert y[-1] > y[0] + 0.5


def test_log_decoders_mid_gray():
    # 18% szarości w specyfikacjach producentów (kod 10-bit / IRE)
    assert lut.decode_slog3(np.array([420 / 1023]))[0] == pytest.approx(0.18, abs=1e-3)
    assert lut.decode_vlog(np.array([0.423]))[0] == pytest.approx(0.18, abs=3e-3)
    assert lut.decode_dlog(np.array([0.3988]))[0] == pytest.approx(0.18, abs=3e-3)
    assert lut.decode_clog3(np.array([0.3434]))[0] == pytest.approx(0.18, abs=3e-3)
    assert lut.decode_hlg(np.array([0.75]))[0] == pytest.approx(1.0, abs=0.01)


@pytest.mark.parametrize("gamut", ["sgamut3cine", "cinemagamut", "dgamut", "vgamut", "rec2020"])
def test_gamut_matrix_preserves_white(gamut):
    m = lut.gamut_matrix(gamut)
    assert np.allclose(m @ np.ones(3), np.ones(3), atol=1e-6)


def test_tonemap_inverse():
    d = np.linspace(0, 0.999, 50)
    assert np.allclose(lut.tonemap(lut.tonemap_inv(d)), d, atol=1e-6)


def test_highlight_recovery_keeps_clipped_white_and_lowers_highlights():
    base = lut.apply_grade_rgb(np.array([[0.88, 0.88, 0.88], [1.0, 1.0, 1.0]]), Grade(), STATS)
    rec = lut.apply_grade_rgb(np.array([[0.88, 0.88, 0.88], [1.0, 1.0, 1.0]]), Grade(highlight_recovery=0.8), STATS)
    assert rec[0, 0] < base[0, 0] - 0.02  # jasne partie ściemnione (odzysk)
    assert rec[1].min() > 0.94  # przepalona biel nie robi się szarą plamą


def test_skin_hue_protected():
    skin = np.array([[0.85, 0.62, 0.50], [0.70, 0.48, 0.38]])
    h0 = looks.hue_deg(skin)
    for look in ("teal_orange", "moody", "pastel", "vibrant_social"):
        prot = lut.apply_grade_rgb(skin, Grade(look=look, look_strength=1.0, skin_protect=1.0), STATS)
        free = lut.apply_grade_rgb(skin, Grade(look=look, look_strength=1.0, skin_protect=0.0), STATS)
        dp = np.abs(((looks.hue_deg(prot) - h0 + 180) % 360) - 180).max()
        df = np.abs(((looks.hue_deg(free) - h0 + 180) % 360) - 180).max()
        assert dp <= max(df, 3.0), look  # ochrona nie zwiększa przesunięcia odcienia skóry
        assert dp < 12, f"{look}: odcień skóry przesunięty o {dp:.1f}°"


def test_auto_exposure_brightens_dark_clip():
    dark = dict(STATS, luma_mean=0.15, hist=[0.12] * 8 + [0.0] * 24)
    plan = lut.plan_grade(Grade(), dark)
    assert plan["auto_ev"] > 0.3
    ok = lut.plan_grade(Grade(), STATS)
    assert abs(ok["auto_ev"]) < 0.3


def test_manual_exposure_not_cancelled_by_auto():
    a = lut.plan_grade(Grade(), STATS)
    b = lut.plan_grade(Grade(exposure=0.5), STATS)
    assert b["total_ev"] == pytest.approx(a["total_ev"] + 0.5, abs=1e-6)


def test_match_moves_towards_reference():
    ref = {"lab_mean": [50.0, 2.0, 20.0], "lab_std": [20.0, 8.0, 10.0]}
    g = Grade(match={"auto_exposure": False, "auto_wb": 0})
    px = np.array([[0.5, 0.5, 0.5]])
    out = lut.apply_grade_rgb(px, g, STATS, ref_stats=ref)
    assert out[0, 0] > out[0, 2]  # cieplej (b* rośnie)


def test_cube_roundtrip(tmp_path: Path):
    p = lut.compile_grade_lut(Grade(look="warm_film"), STATS, tmp_path / "a.cube", size=17)
    kind, table, dmin, dmax = looks.read_cube(p)
    assert kind == "3d" and table.shape == (17, 17, 17, 3)
    grid = lut.lattice(17)
    direct = lut.apply_grade_rgb(grid, Grade(look="warm_film"), STATS)
    via = looks.apply_cube(grid, p)
    assert np.abs(direct - via).max() < 1e-5
    text = p.read_text()
    assert "LUT_3D_SIZE 17" in text


def test_file_look(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(looks, "luts_dir", lambda: tmp_path)
    inv = 1.0 - lut.lattice(5)
    lut.write_cube(inv, 5, tmp_path / "negatyw.cube")
    ids = [l["id"] for l in looks.looks()]
    assert "negatyw.cube" in ids and "teal_orange" in ids
    out = looks.apply_look(np.array([[0.2, 0.4, 0.6]]), "negatyw.cube")
    assert np.allclose(out, [[0.8, 0.6, 0.4]], atol=1e-6)


def test_detect_log_profile():
    assert analyze.detect_log_profile({"color_transfer": "arib-std-b67"}, {}) == "hlg"
    assert analyze.detect_log_profile({"color_transfer": "smpte2084"}, {}) == "pq"
    assert analyze.detect_log_profile({"tags": {"model": "Sony S-Log3"}}, {}) == "slog3"
    flat = {"luma_p01": 0.12, "luma_p99": 0.7, "sat_mean": 0.12, "luma_std": 0.1, "crushed_pct": 0, "clipped_pct": 0}
    assert analyze.detect_log_profile({}, flat) == "flat"
    assert analyze.detect_log_profile({}, STATS) == ""


def test_frame_stats_synthetic():
    img = np.zeros((100, 100, 3), np.uint8)
    img[:50] = 255
    st = analyze.frame_stats(img)
    assert st["clipped_pct"] == pytest.approx(50, abs=1)
    assert st["crushed_pct"] == pytest.approx(50, abs=1)
    assert len(st["hist"]) == 32 and math.isclose(sum(st["hist"]), 1.0, abs_tol=1e-3)


def test_filters_text(tmp_path: Path):
    cube = tmp_path / "x y.cube"
    lut.write_cube(lut.lattice(3), 3, cube)
    info = {"bit_depth": 10, "color_space": "bt709", "color_range": "tv"}
    g = Grade(sky_darken=0.5, relight={"direction": "left", "strength": 0.6, "color": "warm"})
    vf = filters.grade_chain(g, info, lut_path=cube, face_box=[0.5, 0.4, 0.1, 0.1])
    assert "lut3d=file='" in vf and "\\:" in vf and ":interp=tetrahedral" in vf
    assert "format=gbrp16le" in vf and "format=yuv420p10le" in vf
    assert ";" not in vf  # łańcuch liniowy - bezpieczny w -vf i -filter_complex
    assert vf.count("geq=") == 1 and "cb(X,Y)" in vf


def test_geq_empty_when_nothing():
    assert relight.geq_filter(None, 0.0) == ""
    assert relight.geq_filter({"strength": 0}, 0.0) == ""
    assert relight.normalize_relight({"direction": "lewa", "color": "zimne"})["direction"] == "left"


def test_video_filter_neutral_returns_empty():
    clip = VClip(media_id="m1", src_in=0, src_out=2, start=0)
    media = MediaItem(id="m1", name="a.mp4", path="nie_istnieje.mp4", info={"bit_depth": 8})
    assert filters.video_filter(clip, media, project=None) == ""
    assert filters.grade_is_neutral(Grade())
    assert not filters.grade_is_neutral(Grade(look="moody"))
    assert not filters.grade_is_neutral(Grade(input_profile="slog3"))


def test_apply_params():
    from studio.agents.colorist import _apply_params

    g = _apply_params(Grade(exposure=0.2), {"exposure_delta": 0.5, "look": "Teal Orange", "saturation": 9,
                                            "relight": {"direction": "right"}})
    assert g.exposure == pytest.approx(0.7)
    assert g.look == "teal_orange"
    assert g.saturation == 2.0
    assert g.relight == {"direction": "right"}
    assert _apply_params(g, {"relight": None, "look": "brak"}).relight is None

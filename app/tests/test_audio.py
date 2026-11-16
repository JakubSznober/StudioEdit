"""Szybkie testy obszaru audio (bez GPU): przenikania, ducking, limiter, LUFS, sync, wypełniacze, SFX, room tone."""
from __future__ import annotations

import numpy as np
import pytest

from studio import paths

paths.setup_env()

from studio.audio import SR, equal_power, write_wav  # noqa: E402
from studio.audio import loudness as ldn  # noqa: E402
from studio.audio.mix import (Seg, coverage_gain, duck_gain, envelope, limit_true_peak, plan_fades,  # noqa: E402
                              render_mix, speech_activity, tp_envelope)
from studio.project.model import AClip, Timeline  # noqa: E402


def _sine(dur, f=440.0, amp=0.3, phase=0.0, sr=SR):
    t = np.arange(int(dur * sr)) / sr
    return (amp * np.sin(2 * np.pi * f * t + phase)).astype(np.float32)


def test_equal_power_sums_to_one():
    fi, fo = equal_power(1000, True), equal_power(1000, False)
    assert np.allclose(fi ** 2 + fo ** 2, 1.0, atol=1e-6)


def test_plan_fades_butt_cut_uses_handles():
    a = Seg(src="a", src_in=2.0, src_out=4.0, start=0.0, file_dur=10.0)
    b = Seg(src="b", src_in=3.0, src_out=6.0, start=2.0, file_dur=10.0)
    plan_fades([a, b], 0.08)
    assert a.ext_tail == pytest.approx(0.04)
    assert b.ext_head == pytest.approx(0.04)
    assert a.fout == pytest.approx(0.08) and b.fin == pytest.approx(0.08)
    # obwiednie w obszarze przenikania: suma kwadratów = 1 (equal-power)
    n = int(0.08 * SR)
    total_a = int(round((a.dur + a.ext_head + a.ext_tail) * SR))
    ea = envelope(a, total_a - n, total_a)
    eb = envelope(b, 0, n)
    assert np.allclose(ea ** 2 + eb ** 2, 1.0, atol=2e-3)


def test_plan_fades_no_handles_and_contiguous_linear():
    a = Seg(src="x", src_in=0.0, src_out=2.0, start=0.0, file_dur=2.0)
    b = Seg(src="y", src_in=0.0, src_out=2.0, start=2.0, file_dur=2.0)
    plan_fades([a, b], 0.08)
    # a nie ma materiału po końcu -> całe przenikanie wchodzi w b (handle przed b brak) -> krótkie fade'y
    assert a.ext_tail == 0.0 and b.ext_head == 0.0
    assert a.fout > 0 and b.fin > 0
    c = Seg(src="z", src_in=1.0, src_out=3.0, start=0.0, file_dur=10.0)
    d = Seg(src="z", src_in=3.0, src_out=5.0, start=2.0, file_dur=10.0)
    plan_fades([c, d], 0.08)
    assert c.tail_curve == "linear" and d.head_curve == "linear"


def test_render_mix_no_clicks_and_loudness(tmp_path):
    # dwa źródła sinusoidy w przeciwnej fazie - cięcie na styk bez przenikania dałoby trzask
    fa = write_wav(tmp_path / "a.wav", _sine(6.0, phase=0.0), SR)
    fb = write_wav(tmp_path / "b.wav", _sine(6.0, phase=np.pi), SR)
    tl = Timeline()
    tl.mix.voice_chain = {"enabled": False}
    tl.mix.room_tone = False
    tl.tracks["A1"] = [AClip(path=str(fa), src_in=1.0, src_out=3.0, start=0.0),
                       AClip(path=str(fb), src_in=2.0, src_out=4.5, start=2.0)]
    res = render_mix(None, tl, tmp_path / "mix.wav")
    import soundfile as sf

    y, sr = sf.read(res["path"], dtype="float32")
    assert sr == SR and y.shape[1] == 2
    assert res["lufs"] == pytest.approx(-14.0, abs=0.6)
    assert res["true_peak"] <= -1.0 + 0.05
    d2 = np.abs(np.diff(y[:, 0], 2))
    amp = np.max(np.abs(y[:, 0]))
    normal = amp * (2 * np.pi * 440 / SR) ** 2
    assert d2.max() < 6 * normal, "trzask na cięciu"


def test_duck_gain_smooth():
    n_frames = 600  # 6 s
    act = np.zeros(n_frames, bool)
    act[200:400] = True
    g = duck_gain(act, -15.0, 0.25, 0.6, int(6 * SR))
    gdb = 20 * np.log10(g)
    assert gdb.min() == pytest.approx(-15.0, abs=0.5)
    assert gdb[int(0.5 * SR)] > -0.5  # przed mową bez ściszenia
    assert gdb[int(3.0 * SR)] < -14.0  # w trakcie mowy
    assert gdb[-1] > -0.5  # powrót
    assert np.max(np.abs(np.diff(gdb))) < 0.05  # brak skoków


def test_speech_activity_and_coverage():
    sig = np.zeros(int(4 * SR), np.float32)
    sig[SR:2 * SR] = _sine(1.0, amp=0.2)
    act = speech_activity(sig)
    assert act[150] and not act[50]
    cg = coverage_gain([(1.0, 2.0)], 0.0, int(3 * SR))
    assert cg[int(0.5 * SR)] > 0.99 and cg[int(1.5 * SR)] < 0.01


def test_true_peak_limiter():
    x = np.stack([_sine(2.0, 997, 0.99), _sine(2.0, 997, 0.99, 0.5)], axis=1) * 1.6
    y = limit_true_peak(x.astype(np.float32), -1.0)
    assert 20 * np.log10(tp_envelope(y).max()) <= -1.0 + 0.02


def test_loudness_reference_sine():
    x = _sine(5.0, 1000, 0.1)
    st = np.stack([x, x], axis=1)
    m = ldn.measure_array(st, SR)
    assert m["lufs"] == pytest.approx(-20.0, abs=0.3)
    assert m["true_peak"] == pytest.approx(-20.0, abs=0.2)
    assert len(m["momentary"]) == pytest.approx(5.0 / 0.4, abs=1.5)


def test_sync_offset_subsample():
    from studio.audio.sync import offset_arrays

    rng = np.random.default_rng(0)
    sr = 16000
    base = np.zeros(sr * 20, np.float32)
    for t in rng.uniform(0.5, 19, 40):  # transjenty (klaśnięcia/sylaby)
        i = int(t * sr)
        n = int(0.03 * sr)
        base[i:i + n] += rng.standard_normal(n).astype(np.float32) * np.exp(-np.arange(n) / 80)
    off = 1.23456
    shift = int(off * sr)
    other = base[shift:].copy() + 0.01 * rng.standard_normal(len(base) - shift).astype(np.float32)
    est, conf = offset_arrays(base, other, sr, max_offset=5)
    assert est == pytest.approx(shift / sr, abs=0.001)
    assert conf > 0.3


def test_fillers_and_sentences():
    from studio.audio.stt import build_sentences

    raw = [{"w": " Eee,", "s": 0.0, "e": 0.4, "p": 0.9}, {"w": " to", "s": 0.5, "e": 0.6, "p": 0.9},
           {"w": " to", "s": 0.62, "e": 0.7, "p": 0.9}, {"w": " to", "s": 0.72, "e": 0.8, "p": 0.9},
           {"w": " jest", "s": 0.85, "e": 1.1, "p": 0.95}, {"w": " dobre.", "s": 1.15, "e": 1.5, "p": 0.95},
           {"w": " yyy", "s": 2.5, "e": 2.9, "p": 0.6}, {"w": " Drugie", "s": 3.0, "e": 3.4, "p": 0.95},
           {"w": " zdanie", "s": 3.45, "e": 3.9, "p": 0.95}]
    sents = build_sentences(raw, "med1")
    assert [s.id for s in sents] == ["med1_0000", "med1_0001"]
    w = sents[0].words
    assert w[0].filler and w[1].filler and w[2].filler and not w[3].filler
    assert sents[1].words[0].filler and not sents[1].words[1].filler
    assert sents[0].fluency < sents[1].fluency or sents[0].fluency < 0.9


@pytest.mark.parametrize("kind", ["whoosh", "swoosh", "riser", "downlifter", "hit", "impact", "click",
                                  "ambience:city"])
def test_sfx_kinds(tmp_path, kind):
    from studio.audio.sfx import make_sfx

    p = make_sfx(kind, tmp_path / f"{kind.replace(':', '_')}.wav", 1.0, prefer_library=False)
    import soundfile as sf

    y, sr = sf.read(str(p), dtype="float32")
    assert sr == SR and y.ndim == 2 and y.shape[1] == 2
    assert np.isfinite(y).all() and np.max(np.abs(y)) <= 1.0
    assert np.abs(y[0]).max() < 0.05 and np.abs(y[-1]).max() < 0.05  # brak kliknięć na krawędziach


def test_find_room_tone_synthetic(tmp_path):
    from studio.audio.roomtone import find_room_tone

    rng = np.random.default_rng(3)
    a = (rng.standard_normal(10 * SR) * 0.002).astype(np.float32)  # tło ~ -54 dBFS
    for s in (0.5, 3.0, 7.0):  # "mowa"
        a[int(s * SR):int((s + 1.5) * SR)] += _sine(1.5, 220, 0.3)
    f = write_wav(tmp_path / "rt.wav", a, SR)
    rt = find_room_tone(str(f))
    assert rt is not None and rt["e"] - rt["s"] >= 0.5
    assert not (rt["s"] < 2.0 and rt["e"] > 0.5) or rt["s"] >= 2.0  # nie nachodzi na pierwszą "mowę"
    for s in (0.5, 3.0, 7.0):
        assert rt["e"] <= s - 0.1 or rt["s"] >= s + 1.6
    assert rt["db"] < -45


def test_mix_fills_gaps_with_room_tone(tmp_path):
    rng = np.random.default_rng(4)
    a = (rng.standard_normal(10 * SR) * 0.003).astype(np.float32)
    for s in (0.5, 3.0, 7.0):
        a[int(s * SR):int((s + 1.5) * SR)] += _sine(1.5, 220, 0.3)
    f = write_wav(tmp_path / "d.wav", a, SR)
    tl = Timeline()
    tl.mix.voice_chain = {"enabled": False}
    tl.mix.room_tone_db = -50.0
    tl.tracks["A1"] = [AClip(path=str(f), src_in=0.5, src_out=2.0, start=0.0),
                       AClip(path=str(f), src_in=3.0, src_out=4.5, start=2.5)]  # dziura 1.5–2.5 s
    res = render_mix(None, tl, tmp_path / "m.wav")
    import soundfile as sf

    y, _ = sf.read(res["path"], dtype="float32")
    gap = y[int(1.8 * SR):int(2.2 * SR), 0]
    assert res["tracks"]["roomtone"] == 1
    assert 20 * np.log10(np.sqrt(np.mean(gap ** 2)) + 1e-12) > -90  # cisza cyfrowa wypełniona


def test_room_tone_loop_is_seamless():
    from studio.audio.roomtone import loop_seamless

    seg = np.random.default_rng(1).standard_normal(int(0.8 * SR)).astype(np.float32) * 0.01
    out = loop_seamless(seg, int(12 * SR))
    win = int(0.1 * SR)
    r = 20 * np.log10(np.sqrt(np.mean(out[win:-win][: (len(out) - 2 * win) // win * win].reshape(-1, win) ** 2,
                                      axis=1)))
    assert r.max() - r.min() < 2.0

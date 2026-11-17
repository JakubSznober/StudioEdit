"""Czyszczenie głosu: kompensacja opóźnienia filtrów, NaN z arnndn i awarie FFmpeg."""
from __future__ import annotations

import json

import numpy as np
import pytest

from studio import paths

paths.setup_env()

from studio.audio import clean, load_wav, write_wav  # noqa: E402
from studio.media import ffmpeg as ff  # noqa: E402

SR = 48000


def _speechlike(seconds: float, seed: int = 0) -> np.ndarray:
    """Modulowany szum w paśmie mowy: sylaby co ~0,2 s, przerwy, bez okresowości."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    env = np.clip(np.sin(2 * np.pi * 2.3 * t) * np.sin(2 * np.pi * 0.37 * t + 1.0), 0, None) ** 0.7
    a = rng.standard_normal(n) * env + 0.3 * np.sin(2 * np.pi * (140 + 30 * np.sin(2 * np.pi * 0.5 * t)) * t) * env
    return (0.2 * a / np.abs(a).max()).astype(np.float32)


@pytest.mark.parametrize("lag", [0, 143, 480, 1823, -100])
def test_measure_delay_exact(lag):
    x = _speechlike(8.0)
    y = clean.shift_and_fit(x, -lag, len(x)) * 0.7  # y spóźnione o `lag`
    assert clean.measure_delay(x, y, SR) == lag


def test_measure_delay_silence_and_short():
    assert clean.measure_delay(np.zeros(SR * 5, np.float32), np.zeros(SR * 5, np.float32), SR) is None
    assert clean.measure_delay(np.ones(1000, np.float32), np.ones(1000, np.float32), SR) is None


def test_shift_and_fit():
    a = np.arange(10, dtype=np.float32)
    assert clean.shift_and_fit(a, 3, 10).tolist() == [3, 4, 5, 6, 7, 8, 9, 0, 0, 0]
    assert clean.shift_and_fit(a, -2, 6).tolist() == [0, 0, 0, 1, 2, 3]


def test_nominal_delay():
    assert clean.nominal_delay("highpass=f=80,arnndn=m='x':mix=1,afftdn=nr=6,alimiter=limit=0.9") == 480 + 1200 + 144
    assert clean.nominal_delay("highpass=f=80") == 0


@pytest.fixture()
def rnnoise_only(monkeypatch, tmp_path):
    if clean.rnnoise_model() is None or "arnndn" not in ff.filters():
        pytest.skip("brak RNNoise/arnndn")
    monkeypatch.setattr(clean, "deepfilter_available", lambda: False)
    rng = np.random.default_rng(5)
    x = _speechlike(6.0, seed=3) + (rng.standard_normal(SR * 6) * 0.01).astype(np.float32)
    return write_wav(tmp_path / "src.wav", x, SR), x


def _lag(ref, y):
    from scipy.signal import correlate

    n = min(len(ref), len(y))
    return int(np.argmax(correlate(y[:n], ref[:n], mode="full", method="fft"))) - (n - 1)


def test_clean_voice_rnnoise_is_time_aligned(rnnoise_only, tmp_path):
    src, x = rnnoise_only
    out = clean.clean_voice(str(src), tmp_path / "dlg.wav", chain={"denoise": "strong"}, effort="high")
    y = load_wav(out)[0]
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert "arnndn=" in side["filter"] and side["delay_samples"] > 1000
    assert np.isfinite(y).all()
    assert abs(_lag(x, y)) <= 2


def test_clean_voice_removes_nan_and_survives_arnndn_crash(rnnoise_only, tmp_path, monkeypatch):
    src, x = rnnoise_only
    real_run = clean.run
    calls = {"crash": 0}

    def fake_run(args, **kw):
        af = args[args.index("-af") + 1]
        if "arnndn=" in af and calls["crash"] < 3:
            calls["crash"] += 1
            raise ff.FFmpegError("awaria", "", clean.ACCESS_VIOLATION)
        real_run(args, **kw)
        a, sr = load_wav(args[-1])
        a[-480:] = np.nan
        write_wav(args[-1], a, sr)

    monkeypatch.setattr(clean, "run", fake_run)
    out = clean.clean_voice(str(src), tmp_path / "dlg2.wav", chain={"denoise": "strong"}, effort="high")
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    y = load_wav(out)[0]
    assert calls["crash"] == 3 and "arnndn=" not in side["filter"] and "afftdn=" in side["filter"]
    assert side["nan_samples_fixed"] == 480 and np.isfinite(y).all()
    assert abs(_lag(x, y)) <= 2

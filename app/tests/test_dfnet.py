"""DeepFilterNet 3 przez ONNX: DSP odtworzone z libDF i (gdy wagi są pobrane) działanie modelu."""
from __future__ import annotations

import numpy as np
import pytest

from studio import paths

paths.setup_env()

from studio.audio import dfnet  # noqa: E402

DF3_WIDTHS = [2] * 13 + [5, 5, 7, 7, 8, 10, 12, 13, 15, 18, 20, 24, 28, 31, 37, 42, 50, 56, 67]


def test_erb_widths_match_libdf():
    w = dfnet.erb_widths(48000, 960, 32, 2)
    assert w.tolist() == DF3_WIDTHS
    assert int(w.sum()) == 481


def test_norm_alpha_matches_libdf():
    assert dfnet.norm_alpha(48000, 480, 1.0) == pytest.approx(0.99)


def test_vorbis_window_is_power_complementary():
    w = dfnet.vorbis_window(960)
    assert np.allclose(w[:480] ** 2 + w[480:] ** 2, 1.0, atol=1e-6)


needs_model = pytest.mark.skipif(not dfnet.installed(), reason="wagi DeepFilterNet 3 niepobrane")


@needs_model
def test_stft_roundtrip_is_identity():
    m = dfnet.get_model()
    x = np.random.default_rng(0).standard_normal(48000).astype(np.float32) * 0.1
    xp = np.concatenate([x, np.zeros(m.n_fft, np.float32)])
    y = m.synthesis(m.analysis(xp))[m.n_fft - m.hop:][: len(x)]
    assert np.abs(y - x).max() < 1e-5


@needs_model
def test_enhance_suppresses_noise_and_keeps_length():
    m = dfnet.get_model()
    x = (np.random.default_rng(1).standard_normal(48000 * 2) * 0.05).astype(np.float32)
    y = m.enhance(x)
    assert y.shape == x.shape and y.dtype == np.float32 and np.isfinite(y).all()
    rms = lambda a: 20 * np.log10(np.sqrt(np.mean(a ** 2)) + 1e-12)  # noqa: E731
    assert rms(y) < rms(x) - 20
    # limit tłumienia: najwyżej ~6 dB
    y6 = m.enhance(x, atten_lim_db=6)
    assert rms(y6) > rms(x) - 6.5


@needs_model
def test_enhance_chunking_matches_single_pass():
    m = dfnet.get_model()
    x = (np.random.default_rng(2).standard_normal(48000 * 4) * 0.05).astype(np.float32)
    x[48000:96000] += 0.2 * np.sin(2 * np.pi * 220 * np.arange(48000) / 48000).astype(np.float32)
    whole = m.enhance(x, chunk_s=30.0)
    parts = m.enhance(x, chunk_s=1.0, ctx_s=1.0)
    # po rozgrzaniu GRU kontekstem fragmenty dają praktycznie ten sam wynik
    err = np.sqrt(np.mean((whole[48000:] - parts[48000:]) ** 2)) / (np.sqrt(np.mean(whole[48000:] ** 2)) + 1e-9)
    assert err < 0.05

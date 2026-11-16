"""Synchronizacja dwóch nagrań po dźwięku (kamera + rekorder, dwie kamery).

`find_offset(ref, other)` zwraca (offset, pewność): treść `other` w chwili t pojawia się w `ref` w chwili
t + offset (offset > 0 => `other` zaczął nagrywać później niż `ref`). Dokładność: zgrubnie korelacja
obwiedni onsetów (1 ms), dokładnie GCC-PHAT na surowym sygnale + interpolacja paraboliczna (< 0,1 ms).
"""
from __future__ import annotations

import numpy as np

from . import extract_wav, load_wav

SR = 16000
ENV_SR = 1000  # obwiednia co 1 ms


def _onset_env(a: np.ndarray, sr: int = SR) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt

    # mowa/transjenty: pasmo 150 Hz - 4 kHz
    sos = butter(4, [150, 4000], btype="band", fs=sr, output="sos")
    x = sosfiltfilt(sos, a.astype(np.float64))
    hop = sr // ENV_SR
    n = len(x) // hop
    e = np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    le = np.log(e + 1e-5)
    on = np.maximum(0.0, np.diff(le, prepend=le[:1]))
    # wygładzenie 5 ms i standaryzacja
    k = np.hanning(7)
    on = np.convolve(on, k / k.sum(), mode="same")
    on -= on.mean()
    return on / (on.std() + 1e-9)


def _xcorr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pełna korelacja krzyżowa przez FFT; indeks i odpowiada przesunięciu lag = i - (len(b)-1)."""
    n = len(a) + len(b) - 1
    nfft = 1 << int(np.ceil(np.log2(max(2, n))))
    A = np.fft.rfft(a, nfft)
    B = np.fft.rfft(b, nfft)
    c = np.fft.irfft(A * np.conj(B), nfft)
    # c[k] = sum a[t+k] b[t]; ujemne lagi na końcu
    return np.concatenate([c[nfft - (len(b) - 1):], c[:len(a)]])


def _gcc_phat(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[float, float]:
    n = len(a) + len(b)
    nfft = 1 << int(np.ceil(np.log2(n)))
    A = np.fft.rfft(a * np.hanning(len(a)), nfft)
    B = np.fft.rfft(b * np.hanning(len(b)), nfft)
    R = A * np.conj(B)
    R /= np.abs(R) + 1e-12
    cc = np.fft.irfft(R, nfft)
    cc = np.concatenate([cc[-max_lag:], cc[:max_lag + 1]])
    i = int(np.argmax(cc))
    # interpolacja paraboliczna
    if 0 < i < len(cc) - 1:
        y0, y1, y2 = cc[i - 1], cc[i], cc[i + 1]
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if abs(den) > 1e-12 else 0.0
    else:
        frac = 0.0
    return (i - max_lag) + float(np.clip(frac, -0.5, 0.5)), float(cc[i])


def find_offset(ref_path: str, other_path: str, max_offset: float = 60) -> tuple[float, float]:
    ra, _ = load_wav(extract_wav(ref_path, SR, True))
    oa, _ = load_wav(extract_wav(other_path, SR, True))
    return offset_arrays(ra, oa, SR, max_offset)


def offset_arrays(ref: np.ndarray, other: np.ndarray, sr: int = SR, max_offset: float = 60) -> tuple[float, float]:
    if sr != SR:
        from . import resample

        ref, other = resample(ref, sr, SR), resample(other, sr, SR)
    # ogranicz długość analizy (pamięć): do 12 min z każdego pliku
    lim = int(12 * 60 * SR)
    ref, other = ref[:lim + int(max_offset * SR)], other[:lim]
    if len(ref) < SR // 2 or len(other) < SR // 2:
        return 0.0, 0.0
    er, eo = _onset_env(ref), _onset_env(other)
    c = _xcorr(er, eo)
    lags = np.arange(len(c)) - (len(eo) - 1)
    ml = int(max_offset * ENV_SR)
    m = (lags >= -ml) & (lags <= ml)
    c, lags = c[m], lags[m]
    # normalizacja przez długość nakładania (żeby długie przesunięcia nie były karane)
    overlap = np.minimum(len(er), lags + len(eo)) - np.maximum(0, lags)
    overlap = np.maximum(overlap, ENV_SR)  # min. 1 s
    cn = c / overlap
    i = int(np.argmax(cn))
    peak = cn[i]
    coarse = int(lags[i])
    # pewność: wysokość piku względem drugiego piku poza ±50 ms i względem tła
    mask = np.abs(lags - coarse) > 50
    second = float(cn[mask].max()) if mask.any() else 0.0
    noise = float(np.std(cn[mask])) if mask.any() else 1.0
    z = (peak - float(np.mean(cn[mask]) if mask.any() else 0.0)) / (noise + 1e-9)
    ratio = 1.0 - max(0.0, second) / max(peak, 1e-9)
    conf = float(np.clip(min(1.0, z / 12.0) * 0.5 + np.clip(ratio * 2.0, 0, 1) * 0.5, 0.0, 1.0))
    # doprecyzowanie GCC-PHAT na surowym sygnale w otoczeniu zgrubnego przesunięcia
    lag_s = coarse * SR // ENV_SR
    o0 = max(0, -lag_s)
    r0 = max(0, lag_s)
    L = min(len(other) - o0, len(ref) - r0, 60 * SR)
    fine = float(lag_s)
    if L > SR // 4:
        # wybierz najgłośniejsze 20 s z nakładki
        seg = min(L, 20 * SR)
        if L > seg:
            hop = SR
            en = np.array([np.sum(other[o0 + k:o0 + k + seg:16] ** 2) for k in range(0, L - seg, hop)])
            k = int(np.argmax(en)) * hop if len(en) else 0
        else:
            k = 0
        a = ref[r0 + k:r0 + k + seg].astype(np.float64)
        b = other[o0 + k:o0 + k + seg].astype(np.float64)
        d, _v = _gcc_phat(a, b, max_lag=int(0.004 * SR))
        fine = lag_s + d
    return round(fine / SR, 5), round(conf, 3)

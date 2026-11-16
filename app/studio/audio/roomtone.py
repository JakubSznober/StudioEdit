"""Room tone: wyszukanie najcichszego fragmentu bez mowy i generowanie dowolnie długiego tła bez szwów."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..project.model import Transcript
from . import SR, equal_power, extract_wav, frame_rms_db, load_wav, write_wav

HOP = 0.05


def _speech_mask(n: int, transcript: Transcript | None, r: np.ndarray) -> np.ndarray:
    mask = np.zeros(n, bool)
    if transcript is not None and transcript.sentences:
        for w in transcript.words():
            a = max(0, int((w.s - 0.2) / HOP))
            b = min(n, int(np.ceil((w.e + 0.25) / HOP)))
            mask[a:b] = True
    # energia: wszystko wyraźnie ponad tłem też traktujemy jak "zajęte" (oddechy, kroki, mowa bez transkrypcji)
    valid = r[r > -100]
    if len(valid):
        floor = float(np.percentile(valid, 8))
        mask |= r > floor + 9.0
    # margines 150 ms wokół zdarzeń (wybrzmienia, oddechy)
    k = 3
    pad = np.concatenate([np.zeros(k, bool), mask, np.zeros(k, bool)])
    return np.any(np.stack([pad[i:i + len(mask)] for i in range(2 * k + 1)]), axis=0)


def find_room_tone(media_path: str, transcript: Transcript | None = None, *, min_len: float = 0.5,
                   max_len: float = 8.0) -> dict | None:
    """Najcichszy, stabilny fragment bez mowy ≥ min_len s: {"s","e","db"} albo None."""
    a, sr = load_wav(extract_wav(media_path, 16000, True))
    if len(a) < sr:
        return None
    r = frame_rms_db(a, sr, HOP)
    n = len(r)
    busy = _speech_mask(n, transcript, r)
    busy[:2] = True  # pomiń krawędzie pliku (fade/klik startu)
    busy[-2:] = True
    need = int(np.ceil(min_len / HOP))
    best = None
    i = 0
    while i < n:
        if busy[i]:
            i += 1
            continue
        j = i
        while j < n and not busy[j]:
            j += 1
        run = r[i:j]
        if j - i >= need:
            # w długich przerwach przesuwne okno max_len, wybierz najcichsze stabilne
            win = min(j - i, int(max_len / HOP))
            cs = np.concatenate([[0.0], np.cumsum(run)])
            cs2 = np.concatenate([[0.0], np.cumsum(run.astype(np.float64) ** 2)])
            for k in range(0, j - i - win + 1, max(1, win // 4)):
                m = (cs[k + win] - cs[k]) / win
                var = max(0.0, (cs2[k + win] - cs2[k]) / win - m * m)
                if m < -95:  # cyfrowa cisza - to nie jest room tone
                    continue
                score = m + 0.8 * np.sqrt(var) - 1.5 * np.log2(win * HOP / min_len)
                if best is None or score < best[0]:
                    best = (score, i + k, i + k + win, m)
        i = j
    if best is None:
        return None
    _sc, s, e, m = best
    seg = a[int(s * HOP * sr):int(e * HOP * sr)]
    rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2) + 1e-12))
    # "cisza" niewiele cichsza od mowy (np. muzyka w tle przez cały plik) to nie jest room tone
    loud = float(np.percentile(r[r > -100], 90)) if np.any(r > -100) else -100.0
    if 20 * np.log10(rms) > loud - 10.0:
        return None
    return {"s": round(s * HOP, 3), "e": round(e * HOP, 3), "db": round(20 * np.log10(rms), 1)}


def loop_seamless(seg: np.ndarray, n_out: int, sr: int = SR, seed: int = 7) -> np.ndarray:
    """Zapętla fragment (mono) do n_out próbek: losowe wycinki + przenikania equal-power (szum nieskorelowany)."""
    L = len(seg)
    if L == 0:
        return np.zeros(n_out, np.float32)
    rng = np.random.default_rng(seed)
    xf = int(min(0.4 * sr, max(0.02 * sr, L * 0.2)))
    # wycinki krótsze od fragmentu => losowe pozycje (brak okresowości); czasem odwrócone w czasie
    piece = int(min(3 * sr, max(xf * 2 + 1, L * 0.6)))
    out = np.zeros(n_out + piece, np.float32)
    fi, fo = equal_power(xf, True), equal_power(xf, False)
    pos = 0
    first = True
    while pos < n_out:
        st = int(rng.integers(0, max(1, L - piece + 1)))
        chunk = seg[st:st + piece].astype(np.float32).copy()
        if rng.random() < 0.5:
            chunk = chunk[::-1].copy()
        if len(chunk) < piece:
            chunk = np.pad(chunk, (0, piece - len(chunk)))
        if not first:
            chunk[:xf] *= fi
        chunk[-xf:] *= fo
        out[pos:pos + piece] += chunk
        pos += piece - xf
        first = False
    out = out[:n_out]
    # łagodne wejście/wyjście
    f = min(int(0.02 * sr), n_out // 4)
    if f > 0:
        out[:f] *= np.linspace(0, 1, f, dtype=np.float32)
        out[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
    return out


def make_room_tone(media_path: str, info: dict, duration: float, out_wav: Path) -> Path:
    """Generuje room tone (WAV 48 kHz mono) o długości `duration` z fragmentu `info` {"s","e"}."""
    s, e = float(info["s"]), float(info["e"])
    a, sr = load_wav(extract_wav(media_path, SR, True), start=s, duration=max(0.1, e - s))
    # usuń DC i najniższe dudnienie
    a = a - float(np.mean(a)) if len(a) else a
    n_out = max(1, int(round(duration * SR)))
    out = loop_seamless(a, n_out, SR)
    return write_wav(out_wav, out, SR)

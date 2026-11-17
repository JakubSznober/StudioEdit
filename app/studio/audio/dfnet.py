"""DeepFilterNet 3 bez pakietu `deepfilternet` i jego natywnej biblioteki `deepfilterlib`.

`deepfilterlib` (Rust) nie ma paczki dla Pythona 3.12 na Windows, a budowanie go ze źródeł wymaga
kompilatora i niepodpisanych plików wykonywalnych (Smart App Control). Ten moduł uruchamia oficjalny
eksport ONNX modelu (enc / erb_dec / df_dec z repozytorium Rikorose/DeepFilterNet) przez onnxruntime,
a część, którą robiła biblioteka natywna, odtwarza w numpy:

- STFT jak w libDF: okno Vorbisa 960 próbek, hop 480, pamięć analizy od zer, synteza overlap-add;
- cechy ERB: średnia moc w 32 pasmach ERB → dB → wykładnicza normalizacja średniej (alfa 0,99, /40);
- cechy widma: 96 pierwszych binów zespolonych podzielone przez pierwiastek wykładniczej średniej |X|;
- przesunięcie cech o `conv_lookahead` ramek, maska ERB na całe widmo, filtr głęboki rzędu 5
  z wyprzedzeniem 2 ramek na 96 najniższych binach, opcjonalny limit tłumienia.

Długie nagrania model przetwarza fragmentami z kontekstem rozgrzewającym GRU, więc zużycie
pamięci nie rośnie z długością pliku. Wejście i wyjście: mono float32, 48 kHz.
"""
from __future__ import annotations

import configparser
import hashlib
import os
import shutil
import tarfile
import threading
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

from .. import paths

MODEL_DIR = paths.WEIGHTS / "deepfilternet3"
MODEL_FILES = ("enc.onnx", "erb_dec.onnx", "df_dec.onnx", "config.ini")
SOURCE_URL = "https://github.com/Rikorose/DeepFilterNet/raw/main/models/DeepFilterNet3_onnx.tar.gz"
SOURCE_SHA256 = "c94d91f70911001c946e0fabb4aa9adc37045f45a03b56008cb0c8244cb63616"

MEAN_NORM_INIT = (-60.0, -90.0)
UNIT_NORM_INIT = (0.001, 0.0001)


# ------------------------------------------------------------------ pobieranie i instalacja wag
def installed(model_dir: Path = MODEL_DIR) -> bool:
    return all((model_dir / f).is_file() for f in MODEL_FILES)


def download(model_dir: Path = MODEL_DIR, progress=None) -> Path:
    """Pobiera archiwum ONNX, sprawdza SHA-256 i rozpakowuje tylko 4 potrzebne pliki."""
    import urllib.request

    try:
        import truststore  # Norton i inne programy z inspekcją TLS: magazyn certyfikatów Windows

        truststore.inject_into_ssl()
    except Exception:
        pass
    tmp_dir = paths.DOWNLOADS / "deepfilternet3"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    archive = tmp_dir / "DeepFilterNet3_onnx.tar.gz"
    try:
        def hook(blocks: int, bs: int, total: int) -> None:
            if progress and total > 0:
                progress(min(0.9, 0.9 * blocks * bs / total))

        urllib.request.urlretrieve(SOURCE_URL, archive, reporthook=hook)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != SOURCE_SHA256:
            raise RuntimeError(f"DeepFilterNet 3: niezgodna suma SHA-256 archiwum ({digest[:16]}…) - "
                               "plik w repozytorium autora się zmienił, pobieranie przerwane")
        staging = tmp_dir / "model"
        staging.mkdir()
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                name = Path(m.name).name
                if m.isfile() and name in MODEL_FILES:
                    with t.extractfile(m) as src, open(staging / name, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        missing = [f for f in MODEL_FILES if not (staging / f).is_file()]
        if missing:
            raise RuntimeError(f"DeepFilterNet 3: w archiwum brakuje {', '.join(missing)}")
        DeepFilterNet3(staging)  # weryfikacja: modele się ładują i mają oczekiwane wejścia
        model_dir.parent.mkdir(parents=True, exist_ok=True)
        if model_dir.exists():
            shutil.rmtree(model_dir)
        os.replace(staging, model_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if progress:
        progress(1.0)
    return model_dir


# ------------------------------------------------------------------ DSP odtworzone z libDF
def vorbis_window(n: int) -> np.ndarray:
    h = n // 2
    s = np.sin(0.5 * np.pi * (np.arange(n) + 0.5) / h)
    return np.sin(0.5 * np.pi * s * s).astype(np.float32)


def freq2erb(f):
    return 9.265 * np.log1p(np.asarray(f, np.float64) / (24.7 * 9.265))


def erb2freq(e):
    return 24.7 * 9.265 * (np.exp(np.asarray(e, np.float64) / 9.265) - 1.0)


def erb_widths(sr: int, fft_size: int, nb_bands: int, min_nb_freqs: int) -> np.ndarray:
    """Szerokości pasm ERB w binach (jak `erb_fb` w libDF); suma = fft_size/2 + 1."""
    freq_width = sr / fft_size
    lo, hi = float(freq2erb(0.0)), float(freq2erb(sr // 2))
    step = (hi - lo) / nb_bands
    widths = np.zeros(nb_bands, np.int64)
    prev, over = 0, 0
    for i in range(1, nb_bands + 1):
        fb = int(np.round(erb2freq(lo + i * step) / freq_width))
        nb = fb - prev - over
        if nb < min_nb_freqs:
            over = min_nb_freqs - nb
            nb = min_nb_freqs
        else:
            over = 0
        widths[i - 1] = nb
        prev = fb
    widths[-1] += 1
    too_large = int(widths.sum()) - (fft_size // 2 + 1)
    if too_large > 0:
        widths[-1] -= too_large
    return widths


def norm_alpha(sr: int, hop: int, tau: float) -> float:
    a = float(np.exp(-(hop / sr) / tau))
    prec = 3
    r = 1.0
    while r >= 1.0:
        r = round(a * 10 ** prec) / 10 ** prec
        prec += 1
    return r


def _ema(x: np.ndarray, alpha: float, init: np.ndarray) -> np.ndarray:
    """s_t = alpha·s_{t-1} + (1-alpha)·x_t wzdłuż osi 0, z s_{-1} = init."""
    zi = (alpha * init)[None, :].astype(np.float64)
    y, _ = lfilter([1.0 - alpha], [1.0, -alpha], x.astype(np.float64), axis=0, zi=zi)
    return y


class DeepFilterNet3:
    def __init__(self, model_dir: Path = MODEL_DIR, threads: int | None = None):
        import onnxruntime as ort

        model_dir = Path(model_dir)
        cfg = configparser.ConfigParser()
        cfg.read(model_dir / "config.ini", encoding="utf-8")
        df, net = cfg["df"], cfg["deepfilternet"]
        self.sr = df.getint("sr")
        self.n_fft = df.getint("fft_size")
        self.hop = df.getint("hop_size")
        self.nb_erb = df.getint("nb_erb")
        self.nb_df = df.getint("nb_df")
        self.order = df.getint("df_order")
        self.df_la = df.getint("df_lookahead")
        self.conv_la = net.getint("conv_lookahead")
        self.alpha = norm_alpha(self.sr, self.hop, df.getfloat("norm_tau"))
        self.window = vorbis_window(self.n_fft)
        # libDF: wnorm = 1 / (window_size² / (2·frame_size)); synteza to nieznormalizowane IFFT,
        # więc analiza→synteza daje tożsamość, a skala widma zgadza się z inicjalizacją normalizacji cech
        self.wnorm = 1.0 / (self.n_fft ** 2 / (2 * self.hop))
        self.widths = erb_widths(self.sr, self.n_fft, self.nb_erb, df.getint("min_nb_erb_freqs"))
        self.band_of_bin = np.repeat(np.arange(self.nb_erb), self.widths)  # mapowanie bin → pasmo ERB

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads or max(1, min(8, (os.cpu_count() or 4) // 2))
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        prov = ["CPUExecutionProvider"]
        self.enc = ort.InferenceSession(str(model_dir / "enc.onnx"), so, providers=prov)
        self.erb_dec = ort.InferenceSession(str(model_dir / "erb_dec.onnx"), so, providers=prov)
        self.df_dec = ort.InferenceSession(str(model_dir / "df_dec.onnx"), so, providers=prov)
        names = {i.name for i in self.enc.get_inputs()}
        if names != {"feat_erb", "feat_spec"} or len(self.band_of_bin) != self.n_fft // 2 + 1:
            raise RuntimeError("DeepFilterNet 3: nieoczekiwany format modelu ONNX")
        self._lock = threading.Lock()  # sesje ORT są bezpieczne wątkowo, ale ograniczamy równoległość CPU

    # ---------------------------------------------------------------- STFT
    def analysis(self, x: np.ndarray) -> np.ndarray:
        """[N] → [T, F] zespolone; ramka t obejmuje próbki [t·hop − (n_fft − hop), t·hop + hop)."""
        t = len(x) // self.hop
        lead = self.n_fft - self.hop
        xp = np.concatenate([np.zeros(lead, np.float32), x[: t * self.hop].astype(np.float32)])
        idx = np.arange(self.n_fft)[None, :] + self.hop * np.arange(t)[:, None]
        frames = xp[idx] * self.window[None, :]
        return (np.fft.rfft(frames, axis=1) * self.wnorm).astype(np.complex64)

    def synthesis(self, spec: np.ndarray) -> np.ndarray:
        """[T, F] → [T·hop] (overlap-add, opóźnienie n_fft − hop próbek jak w libDF)."""
        frames = np.fft.irfft(spec, n=self.n_fft, axis=1).astype(np.float32) * self.n_fft * self.window[None, :]
        t = frames.shape[0]
        out = frames[:, : self.hop].copy()
        out[1:] += frames[:-1, self.hop:]
        return out.reshape(t * self.hop)

    # ---------------------------------------------------------------- cechy
    def features(self, spec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        power = (spec.real.astype(np.float64) ** 2 + spec.imag.astype(np.float64) ** 2)
        starts = np.concatenate([[0], np.cumsum(self.widths)[:-1]])
        band_pow = np.add.reduceat(power, starts, axis=1) / self.widths[None, :]
        erb_db = 10.0 * np.log10(band_pow + 1e-10)
        mean = _ema(erb_db, self.alpha, np.linspace(*MEAN_NORM_INIT, self.nb_erb))
        feat_erb = ((erb_db - mean) / 40.0).astype(np.float32)

        low = spec[:, : self.nb_df]
        s = _ema(np.abs(low), self.alpha, np.linspace(*UNIT_NORM_INIT, self.nb_df))
        feat_spec = (low / np.sqrt(s)).astype(np.complex64)
        return feat_erb, feat_spec

    def _shift(self, a: np.ndarray) -> np.ndarray:
        """Wyprzedzenie konwolucji: cecha z ramki t+L trafia na ramkę t, koniec dopełniony zerami."""
        if self.conv_la <= 0:
            return a
        out = np.zeros_like(a)
        out[: -self.conv_la] = a[self.conv_la:]
        return out

    # ---------------------------------------------------------------- sieć
    def _run(self, feat_erb: np.ndarray, feat_spec: np.ndarray, chunk: int, ctx: int) -> tuple[np.ndarray, np.ndarray]:
        """Maska ERB [T, nb_erb] i współczynniki filtra [T, nb_df, order] (zespolone)."""
        t_all = feat_erb.shape[0]
        mask = np.zeros((t_all, self.nb_erb), np.float32)
        coefs = np.zeros((t_all, self.nb_df, self.order), np.complex64)
        for start in range(0, t_all, chunk):
            a, b = max(0, start - ctx), min(t_all, start + chunk)
            fe = feat_erb[a:b][None, None]                                              # [1,1,S,E]
            fs = np.stack([feat_spec[a:b].real, feat_spec[a:b].imag])[None]            # [1,2,S,F]
            e0, e1, e2, e3, emb, c0, _ = self.enc.run(None, {"feat_erb": fe, "feat_spec": fs.astype(np.float32)})
            m = self.erb_dec.run(None, {"emb": emb, "e3": e3, "e2": e2, "e1": e1, "e0": e0})[0]  # [1,1,S,E]
            c = self.df_dec.run(None, {"emb": emb, "c0": c0})[0]                        # [1,S,F,order*2]
            keep = start - a
            mask[start:b] = m[0, 0, keep:]
            c = c[0, keep:].reshape(b - start, self.nb_df, self.order, 2)
            coefs[start:b] = c[..., 0] + 1j * c[..., 1]
        return mask, coefs

    def _deep_filter(self, spec_low: np.ndarray, coefs: np.ndarray) -> np.ndarray:
        """y[t, f] = Σ_n x[t − (order−1−la) + n, f] · c[t, f, n]."""
        t = spec_low.shape[0]
        pad_front = self.order - 1 - self.df_la
        xp = np.pad(spec_low, ((pad_front, self.df_la), (0, 0)))
        out = np.zeros_like(spec_low)
        for n in range(self.order):
            out += xp[n: n + t] * coefs[:, :, n]
        return out

    def enhance(self, audio: np.ndarray, atten_lim_db: float | None = None, *, chunk_s: float = 30.0,
                ctx_s: float = 3.0) -> np.ndarray:
        """Odszumia mono float32 48 kHz. `atten_lim_db` ogranicza maksymalne tłumienie (np. 12-30 dB)."""
        x = np.asarray(audio, np.float32).reshape(-1)
        orig = len(x)
        if orig == 0:
            return x.copy()
        x = np.concatenate([x, np.zeros(self.n_fft, np.float32)])  # kompensacja opóźnienia STFT
        spec = self.analysis(x)
        feat_erb, feat_spec = self.features(spec)
        frames_per_s = self.sr / self.hop
        with self._lock:
            mask, coefs = self._run(self._shift(feat_erb), self._shift(feat_spec),
                                    chunk=max(50, int(chunk_s * frames_per_s)), ctx=int(ctx_s * frames_per_s))
        out = spec * mask[:, self.band_of_bin]
        out[:, : self.nb_df] = self._deep_filter(spec[:, : self.nb_df], coefs)
        if atten_lim_db is not None and abs(atten_lim_db) > 0:
            lim = 10 ** (-abs(atten_lim_db) / 20)
            out = spec * lim + out * (1 - lim)
        y = self.synthesis(out)
        d = self.n_fft - self.hop
        return y[d: d + orig].astype(np.float32)


_model: DeepFilterNet3 | None = None
_model_lock = threading.Lock()


def get_model() -> DeepFilterNet3:
    global _model
    with _model_lock:
        if _model is None:
            if not installed():
                raise RuntimeError("Model DeepFilterNet 3 nie jest pobrany")
            _model = DeepFilterNet3()
        return _model

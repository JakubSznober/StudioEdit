"""Miks audio osi czasu: A1 dialogi, A2 room tone, A3 muzyka (auto-ducking), A4 SFX, A5 lektor.

Silnik numpy float32 48 kHz. Każdy klip ma mikro-przenikanie equal-power na krawędziach; sąsiadujące klipy
(cięcie na styk) przenikają się przez "handles" ze źródła (jak w NLE), więc cięcia nie klikają.
J-cut / L-cut wynikają po prostu z innych start/src_in audio niż obrazu.
Na końcu normalizacja do `mix.target_lufs` (BS.1770) i limiter true-peak (detekcja 4x).
"""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import get_config
from ..events import bus, log
from ..media.ffmpeg import Cancelled, filters, read_audio
from ..project.model import AClip, Timeline, VClip
from . import SR, extract_wav, file_lock, file_sig, frame_rms_db, load_wav, param_hash, write_wav
from . import loudness as ldn

DIALOG_REF_LUFS = -20.0
MUTE_DB = -60.0  # gain_db <= MUTE_DB → klip wyciszony
FRAME = 0.01  # ramki obwiedni (ducking, gating)


# ------------------------------------------------------------------------------------------
# Segmenty
# ------------------------------------------------------------------------------------------
@dataclass
class Seg:
    src: str  # plik WAV 48 kHz (mono lub stereo)
    src_in: float
    src_out: float
    start: float
    speed: float = 1.0
    gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    role: str = "dialog"
    loop: bool = False
    duck: bool = False
    clip_id: str = ""
    media_id: str | None = None
    file_dur: float = 0.0
    channels: int = 1
    # wyliczane przez plan_fades
    ext_head: float = 0.0
    ext_tail: float = 0.0
    fin: float = 0.0
    fout: float = 0.0
    head_curve: str = "power"
    tail_curve: str = "power"
    extra: dict = field(default_factory=dict)

    @property
    def dur(self) -> float:
        return max(0.0, self.src_out - self.src_in) / max(0.05, self.speed)

    @property
    def end(self) -> float:
        return self.start + self.dur

    @property
    def handle_in(self) -> float:
        return 1e9 if self.loop else max(0.0, self.src_in) / max(0.05, self.speed)

    @property
    def handle_out(self) -> float:
        return 1e9 if self.loop else max(0.0, self.file_dur - self.src_out) / max(0.05, self.speed)


def plan_fades(segs: list[Seg], micro: float) -> list[Seg]:
    """Wylicza przedłużenia (handles) i długości przenikań dla klipów jednej ścieżki (posortowanych)."""
    segs.sort(key=lambda s: s.start)
    half = micro / 2
    for s in segs:
        s.ext_head = s.ext_tail = 0.0
        s.fin = max(s.fade_in, min(half, s.dur / 3))
        s.fout = max(s.fade_out, min(half, s.dur / 3))
        s.head_curve = s.tail_curve = "power"
    for a, b in zip(segs, segs[1:]):
        gap = b.start - a.end
        contiguous = (a.src == b.src and abs(a.src_out - b.src_in) < 0.002 and abs(a.speed - b.speed) < 1e-6
                      and abs(gap) < 0.002)
        if gap < -0.002:
            # nakładanie (np. J/L cut na tej samej ścieżce) - przenikanie na długości nakładki
            ov = min(-gap, a.dur, b.dur)
            a.fout = max(a.fout, ov)
            b.fin = max(b.fin, ov)
            continue
        if gap > max(half, 0.004):
            continue  # wolne krawędzie (cisza między klipami - wypełni room tone)
        g2 = max(0.0, gap) / 2
        y = min(half, a.handle_out - g2, b.dur * 0.5)
        x = min(half, b.handle_in - g2, a.dur * 0.5)
        if y < half:
            x = min(b.handle_in - g2, micro - max(0.0, y), a.dur * 0.5)
        if x < half:
            y = min(a.handle_out - g2, micro - max(0.0, x), b.dur * 0.5)
        if x < 0 or y < 0 or x + y < 0.004:
            continue  # brak materiału po obu stronach - zostają krótkie fade'y w obrębie klipów
        a.ext_tail = g2 + y
        b.ext_head = g2 + x
        a.fout = max(a.fade_out, x + y)
        b.fin = max(b.fade_in, x + y)
        if contiguous:
            # ten sam materiał ciągły (podział klipu) - liniowe przenikanie sumuje się idealnie do 1
            a.tail_curve = b.head_curve = "linear"
    return segs


def envelope(seg: Seg, k0: int, k1: int, sr: int = SR) -> np.ndarray:
    """Obwiednia klipu dla próbek [k0,k1) liczonych od początku przedłużonego zakresu."""
    total = int(round((seg.dur + seg.ext_head + seg.ext_tail) * sr))
    nfi = int(round(seg.fin * sr))
    nfo = int(round(seg.fout * sr))
    if nfi + nfo > total and nfi + nfo > 0:
        sc = total / (nfi + nfo)
        nfi, nfo = int(nfi * sc), int(nfo * sc)
    k = np.arange(k0, k1, dtype=np.float64)
    env = np.ones(len(k), np.float64)
    if nfi > 0:
        m = k < nfi
        x = (k[m] + 0.5) / nfi
        env[m] = np.sin(0.5 * np.pi * x) if seg.head_curve == "power" else x
    if nfo > 0:
        m = k >= total - nfo
        x = (total - k[m] - 0.5) / nfo
        x = np.clip(x, 0, 1)
        env[m] *= np.sin(0.5 * np.pi * x) if seg.tail_curve == "power" else x
    env[(k < 0) | (k >= total)] = 0.0
    return env.astype(np.float32)


# ------------------------------------------------------------------------------------------
# Odczyt źródeł
# ------------------------------------------------------------------------------------------
class _Reader:
    def __init__(self, tmp_dir: Path):
        self.tmp = tmp_dir
        self._full: dict[str, np.ndarray] = {}
        self.has_rb = "rubberband" in filters()

    def full(self, path: str) -> np.ndarray:
        if path not in self._full:
            a, _ = load_wav(path)
            self._full[path] = a
        return self._full[path]

    def _src_samples(self, seg: Seg, p0: int, p1: int) -> np.ndarray:
        """Próbki źródła [p0,p1) (indeksy w pliku 48 kHz), zera poza plikiem lub pętla."""
        n = p1 - p0
        shape = (n,) if seg.channels == 1 else (n, seg.channels)
        if n <= 0:
            return np.zeros(shape, np.float32)
        if seg.loop:
            A = self.full(seg.src)
            F = len(A)
            if F < 16:
                return np.zeros(shape, np.float32)
            xf = int(min(1.0 * SR, F // 4))
            per = F - xf
            p = np.arange(p0, p1)
            p = np.where(p < 0, p % per, p)
            out = np.zeros(shape, np.float32)
            first = p < per
            out[first] = A[p[first]]
            q = (p[~first] - per) % per
            vals = A[q].astype(np.float32)
            m = q < xf
            if m.any():
                fo = np.cos(0.5 * np.pi * (q[m] + 0.5) / xf).astype(np.float32)
                fi = np.sin(0.5 * np.pi * (q[m] + 0.5) / xf).astype(np.float32)
                if seg.channels > 1:
                    fo, fi = fo[:, None], fi[:, None]
                vals[m] = A[per + q[m]] * fo + A[q[m]] * fi
            out[~first] = vals
            return out
        out = np.zeros(shape, np.float32)
        total = int(round(seg.file_dur * SR))
        a0, a1 = max(0, p0), min(total, p1)
        if a1 > a0:
            a, _ = load_wav(seg.src, start=a0 / SR, duration=(a1 - a0) / SR)
            if a.ndim == 1 and seg.channels > 1:
                a = np.repeat(a[:, None], seg.channels, axis=1)
            elif a.ndim == 2 and seg.channels == 1:
                a = a.mean(axis=1)
            m = min(len(a), a1 - a0)
            out[a0 - p0:a0 - p0 + m] = a[:m]
        return out

    def read(self, seg: Seg, j0: int, j1: int) -> np.ndarray:
        """Audio klipu dla próbek osi [j0,j1)."""
        n = j1 - j0
        st = int(round(seg.start * SR))
        if abs(seg.speed - 1.0) < 1e-4:
            p0 = int(round(seg.src_in * SR)) + (j0 - st)
            return self._src_samples(seg, p0, p0 + n)
        # zmiana prędkości z zachowaniem wysokości (rubberband / atempo)
        sp = seg.speed
        marg = int(0.15 * SR)
        p0 = int(round(seg.src_in * SR + (j0 - st) * sp))
        p1 = int(round(seg.src_in * SR + (j1 - st) * sp))
        raw = self._src_samples(seg, p0 - marg, p1 + marg)
        tmp = self.tmp / f"ts_{threading.get_ident()}_{p0}.wav"
        write_wav(tmp, raw, SR)
        try:
            out = read_audio(tmp, sr=SR, mono=seg.channels == 1, af=tempo_filter(sp, self.has_rb))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        cut = int(round(marg / sp))
        out = out[cut:cut + n]
        if len(out) < n:
            pad = np.zeros((n - len(out),) + out.shape[1:], np.float32)
            out = np.concatenate([out, pad])
        return out.astype(np.float32)


def tempo_filter(speed: float, rubberband: bool = False) -> str:
    if rubberband:
        return f"rubberband=tempo={speed:.5f}:pitchq=quality:transients=mixed:formant=preserved"
    parts = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.5f}")
    return ",".join(parts)


# ------------------------------------------------------------------------------------------
# Ducking / gating
# ------------------------------------------------------------------------------------------
def speech_activity(speech: np.ndarray, sr: int = SR) -> np.ndarray:
    """Maska aktywności mowy w ramkach FRAME (bool) z domknięciem krótkich przerw."""
    if len(speech) == 0:
        return np.zeros(0, bool)
    r = frame_rms_db(speech, sr, FRAME, 0.03)
    floor = float(np.percentile(np.maximum(r, -90.0), 10))
    thr = max(-48.0, floor + 12.0)
    act = r > thr
    # domknij przerwy < 0.35 s (między słowami muzyka nie "pompuje"), usuń impulsy < 0.08 s
    act = _close(act, int(0.35 / FRAME))
    act = _open(act, int(0.08 / FRAME))
    return act


def _runs(mask: np.ndarray):
    if len(mask) == 0:
        return []
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _close(mask: np.ndarray, gap: int) -> np.ndarray:
    m = mask.copy()
    runs = _runs(~mask)
    for s, e in runs:
        if s > 0 and e < len(mask) and e - s <= gap:
            m[s:e] = True
    return m


def _open(mask: np.ndarray, length: int) -> np.ndarray:
    m = mask.copy()
    for s, e in _runs(mask):
        if e - s < length:
            m[s:e] = False
    return m


def duck_gain(activity: np.ndarray, duck_db: float, attack: float, release: float, n_samples: int,
              sr: int = SR) -> np.ndarray:
    """Płynna obwiednia ściszania (liniowa) długości n_samples; mowa -> duck_db, z wyprzedzeniem `attack`."""
    if len(activity) == 0 or not activity.any():
        return np.ones(n_samples, np.float32)
    act = activity.copy()
    la = int(round(attack / FRAME))
    if la > 0:  # look-ahead: muzyka zaczyna schodzić przed pierwszym słowem
        shifted = np.zeros_like(act)
        shifted[:-la or None] = act[la:]
        act = act | shifted
    target = np.where(act, float(duck_db), 0.0)
    a_c = math.exp(-FRAME / max(0.01, attack / 3))
    r_c = math.exp(-FRAME / max(0.01, release / 3))
    g = np.empty_like(target)
    cur = 0.0
    for i, t in enumerate(target):
        c = a_c if t < cur else r_c
        cur = t + (cur - t) * c
        g[i] = cur
    t_frames = (np.arange(len(g)) + 0.5) * FRAME * sr
    lin = 10 ** (np.interp(np.arange(n_samples), t_frames, g) / 20)
    return lin.astype(np.float32)


def coverage_gain(spans: list[tuple[float, float]], w0: float, n_samples: int, smooth: float = 0.06,
                  sr: int = SR) -> np.ndarray:
    """1 - wygładzone pokrycie dialogiem (room tone gra tam, gdzie dialog cichnie/nie ma klipu)."""
    nf = int(math.ceil(n_samples / (FRAME * sr))) + 1
    cov = np.zeros(nf, np.float64)
    for s, e in spans:
        a = int(math.floor((s - w0) / FRAME))
        b = int(math.ceil((e - w0) / FRAME))
        cov[max(0, a):max(0, min(nf, b))] = 1.0
    k = max(1, int(smooth / FRAME))
    win = np.hanning(2 * k + 1)
    cov = np.convolve(cov, win / win.sum(), mode="same")
    t_frames = (np.arange(nf) + 0.5) * FRAME * sr
    g = 1.0 - np.interp(np.arange(n_samples), t_frames, cov)
    return np.clip(g, 0.0, 1.0).astype(np.float32)


# ------------------------------------------------------------------------------------------
# Limiter true-peak
# ------------------------------------------------------------------------------------------
def tp_envelope(x: np.ndarray, oversample: int = 4) -> np.ndarray:
    """Szczyt (max |.|) na próbkę z sygnału nadpróbkowanego 4x (max po kanałach)."""
    from scipy.signal import resample_poly

    if x.ndim == 1:
        x = x[:, None]
    n = len(x)
    out = np.empty(n, np.float32)
    blk = SR * 10
    pad = 64
    for s in range(0, n, blk):
        s0, s1 = max(0, s - pad), min(n, s + blk + pad)
        up = np.abs(resample_poly(x[s0:s1].astype(np.float64), oversample, 1, axis=0)).max(axis=1)
        i0 = (s - s0) * oversample
        cnt = min(n, s + blk) - s
        seg = up[i0:i0 + cnt * oversample]
        if len(seg) < cnt * oversample:
            seg = np.pad(seg, (0, cnt * oversample - len(seg)))
        out[s:s + cnt] = seg.reshape(cnt, oversample).max(axis=1)
    return np.maximum(out, np.abs(x).max(axis=1))


def limit_true_peak(x: np.ndarray, ceiling_db: float, release: float = 0.05) -> np.ndarray:
    """Limiter z wyprzedzeniem: gwarantuje |true peak| <= ceiling (min-filtr + filtr pudełkowy)."""
    from scipy.ndimage import minimum_filter1d, uniform_filter1d

    thr = 10 ** (ceiling_db / 20)
    for _ in range(3):
        tp = tp_envelope(x)
        if float(tp.max()) <= thr:
            break
        g = np.minimum(1.0, thr / np.maximum(tp, 1e-9)).astype(np.float64)
        w = 2 * int(release * SR) + 1
        g1 = minimum_filter1d(g, size=w, mode="nearest")
        # g2 <= g z konstrukcji (okno pudełka nie dłuższe niż okno minimum) => brak przesterowań
        g2 = uniform_filter1d(g1, size=w, mode="nearest")
        x =(x * (g2[:, None] if x.ndim == 2 else g2)).astype(np.float32)
        thr *= 10 ** (-0.05 / 20)  # druga iteracja z minimalnym zapasem
    return x


# ------------------------------------------------------------------------------------------
# Budowa segmentów z osi czasu
# ------------------------------------------------------------------------------------------
def _wav48(path: str, stereo: bool) -> str:
    p = Path(path)
    if p.suffix.lower() == ".wav":
        try:
            import soundfile as sf

            info = sf.info(str(p))
            if info.samplerate == SR and (info.channels == (2 if stereo else 1)):
                return str(p)
        except Exception:
            pass
    return str(extract_wav(path, SR, mono=not stereo))


def _file_dur(path: str) -> float:
    import soundfile as sf

    i = sf.info(path)
    return i.frames / float(i.samplerate)


_lufs_cache: dict[str, float] = {}


def file_lufs(path: str) -> float:
    key = file_sig(path)
    if key in _lufs_cache:
        return _lufs_cache[key]
    from . import cache_dir

    f = cache_dir("lufs") / f"{param_hash(key)}.json"
    if f.exists():
        try:
            v = float(json.loads(f.read_text())["lufs"])
            _lufs_cache[key] = v
            return v
        except Exception:
            pass
    a, sr = load_wav(path)
    v = ldn.integrated(a, sr)
    f.write_text(json.dumps({"lufs": v}))
    _lufs_cache[key] = v
    return v


def _media_path(project, media_id: str | None, path: str | None) -> tuple[str | None, object | None]:
    if media_id and project is not None:
        try:
            m = project.get_media(media_id)
            return m.path, m
        except KeyError:
            pass
    return path, None


def dialog_clips(project, tl: Timeline) -> list[tuple[AClip, float]]:
    """Klipy dialogowe (AClip, speed): A1, a gdy pusty - audio z klipów V1 a_roll z mediami z dźwiękiem."""
    if tl.track_off("A1"):
        return []  # dialog wyciszony w studiu - bez zapasowego dźwięku z kamery
    vmap = {v.id: v for v in tl.v("V1")}
    out: list[tuple[AClip, float]] = []
    for c in tl.a_active("A1"):
        sp = float(c.processing.get("speed") or (vmap[c.link].speed if c.link in vmap else 1.0) or 1.0)
        out.append((c, sp))
    if out:
        return out
    for v in tl.v("V1"):
        if v.role != "a_roll":
            continue
        m = None
        if project is not None:
            try:
                m = project.get_media(v.media_id)
            except KeyError:
                m = None
        if m is None:
            continue
        has = m.info.get("has_audio")
        if has is None:
            from . import has_audio

            has = has_audio(m.path)
        if not has:
            continue
        out.append((AClip(id=f"a1_{v.id}", media_id=v.media_id, src_in=v.src_in, src_out=v.src_out, start=v.start,
                          role="dialog", link=v.id, label=v.label), float(v.speed or 1.0)))
    return out


def timeline_key(tl: Timeline) -> str:
    d = tl.to_json()
    for k in ("version", "note", "created_at", "created_by", "plan", "brief"):
        d.pop(k, None)
    if not any(v for st in (d.get("track_state") or {}).values() for v in st.values()):
        d.pop("track_state", None)
    return param_hash(d)


# ------------------------------------------------------------------------------------------
# Główna funkcja
# ------------------------------------------------------------------------------------------
def render_mix(project, timeline: Timeline, out_wav: Path, *, progress=None, cancel=None,
               t0: float = 0, t1: float | None = None) -> dict:
    p = progress or (lambda x: None)
    pid = getattr(project, "id", None)

    def chk():
        if cancel is not None and cancel.is_set():
            raise Cancelled("Przerwano")

    tl = timeline.model_copy(deep=True).normalize()
    mix = tl.mix
    cfg = get_config()
    effort = cfg.effort("sound")
    full_dur = tl.duration
    W0 = max(0.0, float(t0 or 0))
    W1 = float(t1) if t1 is not None else full_dur
    W1 = max(W0 + 0.05, W1)
    partial = W0 > 0.001 or (t1 is not None and t1 < full_dur - 0.001)
    j_w0 = int(round(W0 * SR))
    N = int(round(W1 * SR)) - j_w0
    micro = float(np.clip((mix.crossfade_ms or 80) / 1000.0, 0.004, 0.3))
    cache_root = (project.cache if project is not None else Path(out_wav).parent) / "audio"
    (cache_root / "clean").mkdir(parents=True, exist_ok=True)
    (cache_root / "tmp").mkdir(parents=True, exist_ok=True)
    reader = _Reader(cache_root / "tmp")
    warnings: list[str] = []

    # --- A1 dialogi (czyszczenie głosu per media + łańcuch) -----------------------------------
    from .clean import clean_voice

    dsegs: list[Seg] = []
    dclips = dialog_clips(project, tl)
    clean_done: dict[str, str] = {}
    for i, (c, sp) in enumerate(dclips):
        chk()
        path, m = _media_path(project, c.media_id, c.path)
        if not path or not Path(path).exists():
            warnings.append(f"Brak pliku dialogu klipu {c.id}")
            continue
        chain = {**(mix.voice_chain or {}), **{k: v for k, v in (c.processing or {}).items()
                                               if k in ("highpass", "denoise", "dereverb", "presence_db",
                                                        "compress", "deess", "mud_db")}}
        src = None
        if c.role == "dialog" and chain.get("enabled", True) is not False:
            ck = param_hash(file_sig(path), chain, effort)
            if ck in clean_done:
                src = clean_done[ck]
            else:
                out = cache_root / "clean" / f"{c.media_id or Path(path).stem[:20]}_{ck[:10]}.wav"
                try:
                    src = str(clean_voice(path, out, chain=chain, effort=effort))
                except Exception as e:
                    warnings.append(f"Czyszczenie głosu nie powiodło się ({e}) - surowy dźwięk")
                    log("sound", f"Czyszczenie głosu {Path(path).name} nie powiodło się: {e}", path="Miks",
                        level="warn", project_id=pid)
                clean_done[ck] = src or ""
        if not src:
            src = _wav48(path, stereo=False)
        dsegs.append(Seg(src=src, src_in=c.src_in, src_out=c.src_out, start=c.start, speed=sp, gain_db=c.gain_db,
                         fade_in=c.fade_in, fade_out=c.fade_out, role="dialog", clip_id=c.id, media_id=c.media_id,
                         file_dur=_file_dur(src), channels=1))
        p(0.3 * (i + 1) / max(1, len(dclips)))
    plan_fades(dsegs, micro)

    def render_track(segs: list[Seg], channels: int, gain_fn: Callable[[Seg], float] | None = None) -> np.ndarray:
        buf = np.zeros((N,) if channels == 1 else (N, channels), np.float32)
        for s in segs:
            chk()
            js0 = int(round((s.start - s.ext_head) * SR))
            js1 = int(round((s.end + s.ext_tail) * SR))
            a0, a1 = max(js0, j_w0), min(js1, j_w0 + N)
            if a1 <= a0:
                continue
            audio = reader.read(s, a0, a1)
            env = envelope(s, a0 - js0, a1 - js0)
            # -60 dB i mniej = klip wyciszony ręcznie w studiu (przycisk „Wycisz”)
            g = 0.0 if s.gain_db <= MUTE_DB else 10 ** ((s.gain_db + (gain_fn(s) if gain_fn else 0.0)) / 20)
            if channels > 1 and audio.ndim == 1:
                audio = np.repeat(audio[:, None], channels, axis=1)
            if channels == 1 and audio.ndim == 2:
                audio = audio.mean(axis=1)
            seg_env = env * g
            if "gate" in s.extra:
                seg_env = seg_env * s.extra["gate"][a0 - j_w0:a1 - j_w0]
            buf[a0 - j_w0:a1 - j_w0] += audio * (seg_env[:, None] if audio.ndim == 2 else seg_env)
        return buf

    dialog = render_track(dsegs, 1)
    p(0.4)

    # poziom odniesienia dialogu (do ustawienia muzyki/VO względem mowy)
    d_lufs = ldn.integrated(dialog, SR) if dsegs and np.any(dialog) else DIALOG_REF_LUFS
    if d_lufs < -60:
        d_lufs = DIALOG_REF_LUFS

    # --- A5 lektor ---------------------------------------------------------------------------
    vsegs = _plain_segs(project, tl.a_active("A5"), stereo=False, role="vo")
    plan_fades(vsegs, micro)
    vo = render_track(vsegs, 1, lambda s: d_lufs - file_lufs(s.src) if file_lufs(s.src) > -60 else 0.0)
    speech = dialog + vo
    p(0.5)

    # --- A2 room tone ------------------------------------------------------------------------
    rt = np.zeros(N, np.float32)
    if mix.room_tone:
        rsegs = _plain_segs(project, tl.a_active("A2"), stereo=False, role="roomtone")
        if not rsegs and dsegs:
            auto = _auto_room_tone(project, tl, dsegs, cache_root, warnings)
            if auto is not None:
                rsegs = [auto]
        if rsegs:
            spans = [(s.start, s.end) for s in dsegs]
            gate = coverage_gain(spans, W0, N)
            if dsegs:
                lo, hi = min(s.start for s in dsegs), max(s.end for s in dsegs)
                tt = W0 + np.arange(N) / SR
                gate = gate * ((tt >= lo - 0.05) & (tt <= hi + 0.05)).astype(np.float32)
            for s in rsegs:
                s.extra["gate"] = gate
            plan_fades(rsegs, micro)
            rt = render_track(rsegs, 1, lambda s: _rt_gain(s, mix.room_tone_db))
    p(0.6)

    # --- A3 muzyka z duckingiem ------------------------------------------------------------------
    music = np.zeros((N, 2), np.float32)
    msegs = _plain_segs(project, tl.a_active("A3"), stereo=True, role="music")
    if msegs:
        for s in msegs:
            if s.end >= full_dur - 0.05 and s.fade_out < 0.5:
                s.fade_out = min(2.5, s.dur / 4)  # wyciszenie na końcu filmu
            if s.fade_in <= 0 and s.start <= 0.01:
                s.fade_in = 0.05
        plan_fades(msegs, micro)
        duck_env = None
        if any(s.duck for s in msegs) and np.any(speech):
            act = speech_activity(speech)
            duck_env = duck_gain(act, mix.duck_db, mix.duck_attack, mix.duck_release, N)
        rel = lambda s: (d_lufs + mix.music_gain_db - file_lufs(s.src)) if file_lufs(s.src) > -60 else mix.music_gain_db  # noqa: E731
        for s in msegs:
            part = render_track([s], 2, rel)
            if s.duck and duck_env is not None:
                part *= duck_env[:, None]
            music += part
    p(0.7)

    # --- A4 SFX ------------------------------------------------------------------------------------
    ssegs = _plain_segs(project, tl.a_active("A4"), stereo=True, role="sfx")
    plan_fades(ssegs, 0.006)
    sfx = render_track(ssegs, 2, lambda s: (d_lufs - DIALOG_REF_LUFS) - 4.0)
    p(0.75)

    # --- suma, normalizacja, limiter -----------------------------------------------------------------
    mono = dialog + vo + rt
    out = music + sfx
    out += mono[:, None]
    chk()
    state_f = cache_root / "mix_state.json"
    tkey = timeline_key(tl)
    gain_db = None
    if partial and state_f.exists():
        try:
            st = json.loads(state_f.read_text(encoding="utf-8"))
            if st.get("key") == tkey:
                gain_db = float(st["gain_db"])
        except Exception:
            gain_db = None
    measured = ldn.integrated(out, SR)
    if gain_db is None:
        gain_db = (mix.target_lufs - measured) if measured > -69 else 0.0
    gain_db = float(np.clip(gain_db, -40, 40))
    y = out * (10 ** (gain_db / 20))
    p(0.8)
    y = limit_true_peak(y, mix.true_peak)
    if not partial:
        # limiter mógł obniżyć głośność - dociągnij (maks. 2 iteracje)
        for _ in range(2):
            l2 = ldn.integrated(y, SR)
            if l2 < -69 or abs(l2 - mix.target_lufs) < 0.3:
                break
            extra = mix.target_lufs - l2
            gain_db += extra
            y = limit_true_peak(y * (10 ** (extra / 20)), mix.true_peak)
    p(0.9)
    chk()
    out_wav = Path(out_wav)
    write_wav(out_wav, y, SR)
    stats = ldn.measure_array(y, SR)
    if not partial:
        try:
            state_f.write_text(json.dumps({"key": tkey, "gain_db": gain_db, "lufs": stats["lufs"]}), encoding="utf-8")
        except OSError:
            pass
    res = {"lufs": stats["lufs"], "true_peak": stats["true_peak"], "lra": stats["lra"], "path": str(out_wav),
           "duration": round(N / SR, 3), "t0": W0, "t1": W1, "gain_db": round(gain_db, 2),
           "target_lufs": mix.target_lufs, "target_true_peak": mix.true_peak,
           "short_term": stats.get("short_term", []), "momentary": stats.get("momentary", []), "hop": 0.4,
           "tracks": {"dialog": len(dsegs), "roomtone": int(np.any(rt)), "music": len(msegs), "sfx": len(ssegs),
                      "vo": len(vsegs)}, "warnings": warnings}
    bus.publish("loudness", {"project_id": pid, "lufs": res["lufs"], "true_peak": res["true_peak"], "lra": res["lra"],
                             "target": mix.target_lufs, "path": res["path"], "t0": W0, "t1": W1,
                             "short_term": res["short_term"], "hop": 0.4})
    p(1.0)
    return res


def _plain_segs(project, clips: list[AClip], *, stereo: bool, role: str) -> list[Seg]:
    out = []
    for c in clips:
        path, _m = _media_path(project, c.media_id, c.path)
        if not path or not Path(path).exists():
            log("sound", f"Pomijam klip {c.id} ({role}) - brak pliku", path="Miks", level="warn",
                project_id=getattr(project, "id", None))
            continue
        src = _wav48(path, stereo)
        fd = _file_dur(src)
        src_out = c.src_out if c.src_out > c.src_in else fd
        loop = bool(c.loop) or (src_out > fd + 0.05 and role in ("music", "roomtone"))
        out.append(Seg(src=src, src_in=c.src_in, src_out=src_out, start=c.start,
                       speed=float(c.processing.get("speed", 1.0) or 1.0), gain_db=c.gain_db, fade_in=c.fade_in,
                       fade_out=c.fade_out, role=role, loop=loop, duck=bool(c.duck), clip_id=c.id,
                       media_id=c.media_id, file_dur=fd, channels=2 if stereo else 1))
    return out


def _rt_gain(s: Seg, target_db: float) -> float:
    a = None
    try:
        a, _ = load_wav(s.src, duration=min(30.0, s.file_dur))
    except Exception:
        return 0.0
    rms = float(np.sqrt(np.mean(a.astype(np.float64) ** 2) + 1e-12)) if a is not None and len(a) else 1e-6
    cur = 20 * math.log10(rms)
    return float(np.clip(target_db - cur, -40.0, 6.0))


def _auto_room_tone(project, tl: Timeline, dsegs: list[Seg], cache_root: Path, warnings: list[str]) -> Seg | None:
    """Room tone z najdłużej używanego medium A-roll (z oczyszczonego dźwięku - zgodne widmo)."""
    from .roomtone import find_room_tone, make_room_tone

    tot: dict[str, float] = {}
    srcmap: dict[str, Seg] = {}
    for s in dsegs:
        key = s.media_id or s.src
        tot[key] = tot.get(key, 0.0) + s.dur
        srcmap[key] = s
    if not tot:
        return None
    key = max(tot, key=tot.get)
    base = srcmap[key]
    info = None
    m = None
    if project is not None and base.media_id:
        try:
            m = project.get_media(base.media_id)
            info = m.analysis.room_tone
        except KeyError:
            m = None
    if not info:
        try:
            info = find_room_tone(m.path if m else base.src, m.transcript if m else None)
        except Exception as e:
            warnings.append(f"Room tone: {e}")
            info = None
    if not info:
        return None
    lo, hi = min(s.start for s in dsegs), max(s.end for s in dsegs)
    dur = hi - lo + 0.2
    out = cache_root / "roomtone" / f"auto_{param_hash(base.src, info, round(dur, 1))[:12]}.wav"
    with file_lock(str(out)):
        if not out.exists():
            make_room_tone(base.src, info, dur, out)
    return Seg(src=str(out), src_in=0.0, src_out=dur, start=max(0.0, lo - 0.1), role="roomtone",
               file_dur=_file_dur(str(out)), channels=1, clip_id="auto_roomtone", fade_in=0.05, fade_out=0.05)

"""Diaryzacja mówców bez modeli z ograniczonym dostępem.

Embeddingi głosu: `microsoft/wavlm-base-plus-sv` (AutoModelForAudioXVector) liczone na oknach 1,5 s
w obrębie zdań, klasteryzacja aglomeracyjna (cosine, próg podobieństwa z configu), wygładzanie po słowach.
"""
from __future__ import annotations

import numpy as np

from ..config import get_config
from ..events import log
from ..project.model import Transcript
from ..vram import vram
from . import SPEECH_OWNER, extract_wav, load_wav

WIN = 1.5
HOP = 0.75
SR = 16000


def _load_wavlm():
    import torch
    from transformers import AutoFeatureExtractor, AutoModelForAudioXVector

    from .models import download, entry, hf_local

    e = entry("wavlm_sv")
    src = hf_local(e["repo"])
    if src is None:
        download("wavlm_sv")
        src = hf_local(e["repo"])
    fe = AutoFeatureExtractor.from_pretrained(str(src))
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForAudioXVector.from_pretrained(str(src)).to(dev).eval()
    return fe, model, dev


def _windows(transcript: Transcript, dur: float) -> list[tuple[int, float, float]]:
    """(indeks zdania, s, e) - okna ~1,5 s w obrębie zdań (krótkie zdania = jedno okno)."""
    out = []
    for si, s in enumerate(transcript.sentences):
        a, b = max(0.0, s.s), min(dur, s.e)
        L = b - a
        if L < 0.4:
            continue
        if L <= WIN * 1.3:
            out.append((si, a, b))
            continue
        t = a
        while t + WIN <= b + 1e-6:
            out.append((si, t, t + WIN))
            t += HOP
        if out and out[-1][2] < b - 0.4:
            out.append((si, max(a, b - WIN), b))
    return out


def _embed(audio: np.ndarray, wins: list[tuple[int, float, float]], label: str = "Diaryzacja") -> np.ndarray:
    import torch

    with vram.lease(SPEECH_OWNER, "sound", label):
        fe, model, dev = vram.cached(SPEECH_OWNER, "wavlm-sv", _load_wavlm)
        embs = []
        bs = 32
        for i in range(0, len(wins), bs):
            chunk = []
            for _si, s, e in wins[i:i + bs]:
                seg = audio[int(s * SR):int(e * SR)]
                L = int(WIN * SR)
                if len(seg) > L:  # stała długość okna => stałe kształty tensorów (bez ponownego JIT kerneli)
                    o = (len(seg) - L) // 2
                    seg = seg[o:o + L]
                if len(seg) < int(WIN * SR):  # dopełnij krótkie okna odbiciem, by nie zaniżać embeddingu
                    reps = int(np.ceil(WIN * SR / max(1, len(seg))))
                    seg = np.tile(np.concatenate([seg, seg[::-1]]), reps)[:int(WIN * SR)]
                chunk.append(seg.astype(np.float32))
            inp = fe(chunk, sampling_rate=SR, return_tensors="pt", padding=True)
            inp = {k: v.to(dev) for k, v in inp.items()}
            with torch.inference_mode():
                out = model(**inp).embeddings
            out = torch.nn.functional.normalize(out.float(), dim=-1).cpu().numpy()
            embs.append(out)
    return np.concatenate(embs, axis=0) if embs else np.zeros((0, 512), np.float32)


def cluster(embs: np.ndarray, threshold: float, max_speakers: int) -> np.ndarray:
    """Klasteryzacja aglomeracyjna (cosine, average). threshold = minimalne podobieństwo cosinusowe."""
    n = len(embs)
    if n <= 1:
        return np.zeros(n, int)
    from sklearn.cluster import AgglomerativeClustering

    ac = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                 distance_threshold=max(0.01, 1.0 - threshold))
    lab = ac.fit_predict(embs)
    k = len(set(lab))
    if k > max_speakers:
        lab = AgglomerativeClustering(n_clusters=max_speakers, metric="cosine", linkage="average").fit_predict(embs)
    # małe klastry (< 3 okien i < 5% okien) doklej do najbliższego dużego centroidu - zwykle to szum/śmiech
    counts = np.bincount(lab)
    big = [c for c in range(len(counts)) if counts[c] >= max(3, 0.05 * n)]
    if big and len(big) < len(counts):
        cents = np.stack([embs[lab == c].mean(0) for c in big])
        cents /= np.linalg.norm(cents, axis=1, keepdims=True) + 1e-9
        for i in range(n):
            if lab[i] not in big:
                lab[i] = big[int(np.argmax(cents @ embs[i]))]
    # kolejność wg pierwszego wystąpienia
    order: dict[int, int] = {}
    for c in lab:
        if c not in order:
            order[int(c)] = len(order)
    return np.array([order[int(c)] for c in lab], int)


def diarize(media_path: str, transcript: Transcript, *, max_speakers: int = 6) -> Transcript:
    tr = transcript.model_copy(deep=True)
    if not tr.sentences:
        return tr
    cfg = get_config()
    thr = float(cfg.get("models.diarization.threshold", 0.72))
    max_speakers = int(max_speakers or cfg.get("models.diarization.max_speakers", 6))
    audio, _ = load_wav(extract_wav(media_path, SR, True))
    dur = len(audio) / SR
    wins = _windows(tr, dur)
    if not wins:
        return _assign_single(tr)
    # Szybka ścieżka: próbka okien - jeśli wszystkie bardzo podobne do centroidu, to jeden mówca
    if len(wins) > 24:
        idx = np.linspace(0, len(wins) - 1, 24).round().astype(int)
        sample = [wins[i] for i in idx]
        e = _embed(audio, sample, "Diaryzacja (próbka)")
        c = e.mean(0)
        c /= np.linalg.norm(c) + 1e-9
        sims = e @ c
        if float(np.percentile(sims, 5)) > thr + 0.08 and len(set(cluster(e, thr, max_speakers))) == 1:
            log("sound", f"Diaryzacja: jeden mówca (min. podobieństwo {sims.min():.2f})", path="Diaryzacja mówców")
            return _assign_single(tr)
    embs = _embed(audio, wins)
    labels = cluster(embs, thr, max_speakers)
    k = int(labels.max()) + 1 if len(labels) else 1
    if k <= 1:
        return _assign_single(tr)
    names = [f"Mówca {i + 1}" for i in range(k)]
    centers = np.array([(s + e) / 2 for _si, s, e in wins])
    by_sent: dict[int, list[int]] = {}
    for wi, (si, _s, _e) in enumerate(wins):
        by_sent.setdefault(si, []).append(wi)
    used = set()
    last_lab = 0
    for si, sent in enumerate(tr.sentences):
        wl = by_sent.get(si)
        if not wl:
            # zdanie zbyt krótkie na embedding - najbliższe okno w czasie
            mid = (sent.s + sent.e) / 2
            j = int(np.argmin(np.abs(centers - mid)))
            lab_s = int(labels[j]) if abs(centers[j] - mid) < 3.0 else last_lab
            for w in sent.words:
                w.spk = names[lab_s]
            sent.spk = names[lab_s]
            used.add(lab_s)
            last_lab = lab_s
            continue
        wlabs = labels[wl]
        if len(set(wlabs.tolist())) == 1:
            for w in sent.words:
                w.spk = names[int(wlabs[0])]
        else:
            wc = centers[wl]
            raw = []
            for w in sent.words:
                m = (w.s + w.e) / 2
                raw.append(int(wlabs[int(np.argmin(np.abs(wc - m)))]))
            # mediana/większość w oknie 5 słów - bez migotania etykiet
            sm = []
            for i in range(len(raw)):
                seg = raw[max(0, i - 2):i + 3]
                sm.append(max(set(seg), key=seg.count))
            for w, lab in zip(sent.words, sm):
                w.spk = names[lab]
        votes: dict[str, float] = {}
        for w in sent.words:
            votes[w.spk or names[0]] = votes.get(w.spk or names[0], 0.0) + max(0.05, w.e - w.s)
        sent.spk = max(votes, key=votes.get) if votes else names[int(wlabs[0])]
        lab_s = names.index(sent.spk)
        used.add(lab_s)
        last_lab = lab_s
    # przenumeruj wg kolejności pojawienia się (bez dziur po wchłoniętych klastrach)
    remap: dict[str, str] = {}
    for s in tr.sentences:
        for w in s.words:
            if w.spk and w.spk not in remap:
                remap[w.spk] = f"Mówca {len(remap) + 1}"
    for s in tr.sentences:
        s.spk = remap.get(s.spk or "", s.spk)
        for w in s.words:
            w.spk = remap.get(w.spk or "", w.spk)
    tr.speakers = list(remap.values())
    log("sound", f"Diaryzacja: {len(tr.speakers)} mówców ({len(wins)} okien głosu)", path="Diaryzacja mówców")
    return tr


def _assign_single(tr: Transcript) -> Transcript:
    for s in tr.sentences:
        s.spk = "Mówca 1"
        for w in s.words:
            w.spk = "Mówca 1"
    tr.speakers = ["Mówca 1"]
    return tr

"""Muzyka: analiza (BPM, beaty, downbeaty, sekcje energii), dobór z biblioteki, generacja MusicGen."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np

from .. import paths
from ..events import log
from ..vram import vram
from . import SR, cache_dir, file_lock, file_sig, param_hash, resample, write_wav
from . import loudness as ldn

AUDIO_EXT = {".wav", ".mp3", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".aiff", ".aif"}

# nastrój -> (słowa kluczowe w nazwach, oczekiwana energia 0..1, opis EN dla MusicGen)
MOODS: dict[str, tuple[list[str], float, str]] = {
    "spokojny": (["calm", "chill", "ambient", "relax", "soft", "lofi", "lo-fi", "spokoj", "spokojny", "peaceful",
                  "gentle", "acoustic"], 0.25, "calm gentle ambient acoustic background music, soft piano and guitar"),
    "radosny": (["happy", "upbeat", "fun", "joy", "positive", "radosn", "wesol", "sunny", "ukulele", "summer"], 0.65,
                "happy upbeat positive background music, acoustic guitar, claps, bright"),
    "energiczny": (["energetic", "energy", "action", "sport", "rock", "edm", "drive", "power", "energ", "dynamic",
                    "trap", "hiphop", "hip-hop"], 0.85, "energetic driving electronic music with punchy drums and bass"),
    "epicki": (["epic", "cinematic", "trailer", "heroic", "orchestra", "orchestral", "epick", "film", "dramatic"], 0.8,
               "epic cinematic orchestral music, big drums, strings and brass, heroic"),
    "smutny": (["sad", "emotional", "melancholy", "melancholic", "piano", "smut", "nostalg", "tender"], 0.3,
               "emotional melancholic piano and strings, slow, cinematic"),
    "napiecie": (["tension", "suspense", "dark", "thriller", "mystery", "napiec", "mroczn", "horror"], 0.55,
                 "dark suspenseful cinematic tension music, low drones, pulsing synths"),
    "inspirujacy": (["inspiring", "inspirational", "motivational", "corporate", "uplifting", "inspir", "business",
                     "hope"], 0.6, "uplifting inspiring corporate background music, piano, strings, light drums"),
    "romantyczny": (["romantic", "love", "romant", "wedding", "slub"], 0.35,
                    "romantic warm music, soft strings and piano"),
    "vlog": (["vlog", "travel", "podroz", "lifestyle", "groovy", "funk", "jazzy", "lofi"], 0.55,
             "chill groovy lofi hip hop vlog background music, warm keys"),
}
_ALIASES = {"calm": "spokojny", "chill": "spokojny", "happy": "radosny", "wesoły": "radosny", "wesoly": "radosny",
            "energetic": "energiczny", "dynamiczny": "energiczny", "epic": "epicki", "cinematic": "epicki",
            "filmowy": "epicki", "sad": "smutny", "emocjonalny": "smutny", "napięcie": "napiecie",
            "tension": "napiecie", "mroczny": "napiecie", "inspirujący": "inspirujacy", "inspiring": "inspirujacy",
            "motywujący": "inspirujacy", "romantic": "romantyczny", "dokument": "spokojny", "reklama": "inspirujacy",
            "teledysk": "energiczny", "rolka": "energiczny", "podcast": "spokojny", "tutorial": "vlog",
            "gaming": "energiczny"}


def mood_key(mood: str | None) -> str:
    m = (mood or "").strip().lower()
    if m in MOODS:
        return m
    if m in _ALIASES:
        return _ALIASES[m]
    for k, (kw, _e, _d) in MOODS.items():
        if any(w in m for w in kw) or k in m:
            return k
    return "vlog" if not m else "inspirujacy"


def _load_mono(path: str, sr: int = 22050) -> np.ndarray:
    from . import extract_wav, load_wav

    a, s = load_wav(extract_wav(path, sr, True))
    return a


def analyze_music(path: str) -> dict:
    """{"bpm","beats","downbeats","sections":[{"s","e","energy"}],"duration","energy","lufs"} (cache)."""
    f = cache_dir("music_analysis") / f"{param_hash(file_sig(path))}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    import librosa

    sr = 22050
    y = _load_mono(path, sr)
    dur = len(y) / sr
    if dur < 1.0:
        res = {"bpm": None, "beats": [], "downbeats": [], "sections": [], "duration": dur, "energy": 0.0}
        f.write_text(json.dumps(res), encoding="utf-8")
        return res
    hop = 512
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop, units="frames")
    bpm = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    beat_t = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
    # downbeaty: faza taktu 4/4 o największej energii niskich częstotliwości + onsetów
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low = S[(freqs >= 30) & (freqs <= 150)].sum(axis=0)
    low = low / (low.max() + 1e-9)
    on = onset / (onset.max() + 1e-9)
    score = []
    for ph in range(4):
        idx = beats[ph::4]
        idx = idx[idx < len(low)]
        score.append(float(np.mean(low[idx] + 0.5 * on[idx])) if len(idx) else 0.0)
    phase = int(np.argmax(score)) if score else 0
    downbeats = beat_t[phase::4]
    # sekcje energii: RMS wygładzone ~2 s, granice z segmentacji cech (mfcc+chroma)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    fps = sr / hop
    k = max(1, int(2 * fps))
    sm = np.convolve(rms, np.ones(k) / k, mode="same")
    n_sec = int(np.clip(round(dur / 20), 2, 10)) if dur > 25 else 1
    bounds = [0.0, dur]
    if n_sec > 1:
        try:
            feat = np.vstack([librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=13),
                              librosa.feature.chroma_stft(S=S ** 2, sr=sr, hop_length=hop)])
            feat = librosa.util.sync(feat, beats if len(beats) > n_sec * 2 else np.arange(0, feat.shape[1], int(fps)))
            bidx = librosa.segment.agglomerative(feat, n_sec)
            sync_t = beat_t if len(beats) > n_sec * 2 else np.arange(0, feat.shape[1]) * 1.0
            bt = [float(sync_t[min(len(sync_t) - 1, b)]) if b > 0 else 0.0 for b in bidx]
            bounds = sorted(set([0.0] + [b for b in bt if 1.0 < b < dur - 1.0] + [dur]))
        except Exception:
            bounds = list(np.linspace(0, dur, n_sec + 1))
    emax = float(sm.max()) + 1e-9
    sections = []
    for s, e in zip(bounds, bounds[1:]):
        a, b = int(s * fps), max(int(s * fps) + 1, int(e * fps))
        sections.append({"s": round(s, 2), "e": round(e, 2), "energy": round(float(sm[a:b].mean() / emax), 3)})
    # energia globalna (0..1) - skala dB RMS: -40 dB => 0, -8 dB => 1
    rms_db = 20 * np.log10(np.mean(rms) + 1e-9)
    energy = float(np.clip((rms_db + 40) / 32, 0, 1) * 0.6 + np.clip(bpm / 160, 0, 1) * 0.2 +
                   np.clip(float(np.mean(onset)) / 2.0, 0, 1) * 0.2)
    res = {"bpm": round(bpm, 2) if bpm else None, "beats": [round(float(t), 3) for t in beat_t],
           "downbeats": [round(float(t), 3) for t in downbeats], "sections": sections, "duration": round(dur, 3),
           "energy": round(energy, 3)}
    f.write_text(json.dumps(res), encoding="utf-8")
    return res


def library_files(root: Path | None = None) -> list[Path]:
    root = root or (paths.AUDIO_LIBRARY / "Music")
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in AUDIO_EXT and p.is_file())


def _tags(p: Path, root: Path) -> set[str]:
    try:
        rel = p.relative_to(root)
    except ValueError:
        rel = Path(p.name)
    txt = " ".join(list(rel.parts[:-1]) + [rel.stem]).lower()
    return {t for t in re.split(r"[^0-9a-ząćęłńóśźż\-]+", txt) if t}


def pick_music(mood: str, duration: float, bpm: float | None = None, *, root: Path | None = None,
               exclude: list[str] | None = None) -> Path | None:
    """Najlepiej pasujący utwór z Audio-Library/Music (tagi z nazw + analiza energii/BPM)."""
    root = root or (paths.AUDIO_LIBRARY / "Music")
    files = [f for f in library_files(root) if str(f) not in set(exclude or [])]
    if not files:
        return None
    mk = mood_key(mood)
    kw, want_e, _desc = MOODS[mk]
    scored = []
    for f in files:
        tags = _tags(f, root)
        txt = " ".join(tags)
        s = 0.0
        s += sum(2.0 for w in kw if w in txt)
        s += 3.0 if mk in txt else 0.0
        s -= sum(1.5 for k, (okw, _e, _d) in MOODS.items() if k != mk and any(w in txt for w in okw[:3]))
        if any(t in txt for t in ("vocal", "wokal", "lyrics", "spiew")):
            s -= 2.0
        scored.append((s, f))
    scored.sort(key=lambda x: -x[0])
    best = None
    for s, f in scored[:12]:  # pełna analiza tylko kandydatów (cache)
        try:
            a = analyze_music(str(f))
        except Exception as e:
            log("sound", f"Nie mogę przeanalizować {f.name}: {e}", path="Muzyka", level="warn")
            continue
        sc = s
        sc -= 4.0 * abs(a.get("energy", 0.5) - want_e)
        d = a.get("duration", 0) or 0
        if d < min(duration, 60) * 0.5:
            sc -= 2.0  # bardzo krótki - pętla słyszalna
        elif d >= duration:
            sc += 1.0
        if bpm and a.get("bpm"):
            r = a["bpm"] / bpm
            r = min(abs(r - 1), abs(r - 2), abs(r - 0.5))
            sc -= 3.0 * min(1.0, r * 4)
        if best is None or sc > best[0]:
            best = (sc, f)
    return best[1] if best else scored[0][1]


# ------------------------------------------------------------------------------------------
# MusicGen
# ------------------------------------------------------------------------------------------
_PL_HINT = re.compile(r"[ąćęłńóśźż]|\b(muzyka|spokojn|radosn|energiczn|epick|smutn|wesoł|gitara|perkusja|"
                      r"fortepian|tło|klimat|nastr)", re.I)


def english_prompt(prompt: str) -> str:
    """Polski opis -> angielski prompt MusicGen (LLM, z zapasowym słownikiem nastrojów)."""
    p = (prompt or "").strip()
    if not p:
        return MOODS["vlog"][2]
    if not _PL_HINT.search(p):
        return p
    try:
        from ..llm import chat

        out = chat([{"role": "system", "content": "Translate the user's music description to a concise English "
                                                  "MusicGen prompt (genre, mood, instruments, tempo). "
                                                  "Reply with the prompt only, no quotes."},
                    {"role": "user", "content": p}], role="director_fast", agent="sound", temperature=0.2,
                   num_predict=80, label="Tłumaczenie promptu muzyki", timeout=120)
        out = out.strip().strip('"').splitlines()[0] if out.strip() else ""
        if 3 < len(out) < 300:
            return out
    except Exception as e:
        log("sound", f"Tłumaczenie promptu przez LLM nieudane ({e}) - słownik nastrojów", path="Muzyka", level="warn")
    return MOODS[mood_key(p)][2]


def _load_musicgen():
    import torch
    from transformers import AutoProcessor, MusicgenForConditionalGeneration

    from .models import entry, hf_local

    e = entry("musicgen_stereo_medium")
    src = hf_local(e["repo"], e["allow"])
    if src is None:
        raise RuntimeError("Model MusicGen nie jest pobrany")
    proc = AutoProcessor.from_pretrained(str(src))
    model = MusicgenForConditionalGeneration.from_pretrained(str(src), torch_dtype=torch.float16).to("cuda").eval()
    return proc, model


def generate_music(prompt: str, duration: float, out_path: Path, *, progress=None, seed: int | None = None) -> Path:
    """Generuje muzykę (WAV 48 kHz stereo). >30 s: kontynuacja z ostatnich 10 s + przenikanie."""
    import torch

    out_path = Path(out_path)
    en = english_prompt(prompt)
    if "vocal" not in en.lower():
        en = en.rstrip(". ") + ", instrumental, no vocals, high quality"
    duration = float(max(2.0, min(duration, 600.0)))
    p = progress or (lambda x: None)
    t0 = time.time()
    with file_lock(str(out_path)):
        with vram.lease("torch:musicgen", "sound", "Generowanie muzyki"):
            proc, model = vram.cached("torch:musicgen", "musicgen-stereo-medium", _load_musicgen)
            msr = int(model.config.audio_encoder.sampling_rate)
            fr = float(model.config.audio_encoder.frame_rate)
            if seed is not None:
                torch.manual_seed(seed)
            seg_len = min(30.0, duration)
            ctx = 10.0
            audio = None
            done = 0.0
            while done < duration - 0.05:
                need = min(seg_len if audio is None else seg_len - ctx, duration - done + (0 if audio is None else 1.5))
                need = max(need, 2.0)
                with torch.inference_mode():
                    if audio is None:
                        inp = proc(text=[en], padding=True, return_tensors="pt").to("cuda")
                        gen = model.generate(**inp, do_sample=True, guidance_scale=3.0,
                                             max_new_tokens=int(need * fr) + 4)
                        new = gen[0].float().cpu().numpy()  # (ch, n)
                    else:
                        prompt_audio = audio[:, -int(ctx * msr):]
                        inp = proc(text=[en], audio=[prompt_audio], sampling_rate=msr, padding=True,
                                   return_tensors="pt").to("cuda")
                        inp = {k: (v.half() if v.dtype == torch.float32 else v) for k, v in inp.items()}
                        gen = model.generate(**inp, do_sample=True, guidance_scale=3.0,
                                             max_new_tokens=int(need * fr) + 4)
                        # wynik = zdekodowany ponownie kontekst (ctx s) + kontynuacja
                        new = gen[0].float().cpu().numpy()
                if new.ndim == 1:
                    new = np.stack([new, new])
                if audio is None:
                    audio = new
                else:
                    plen = prompt_audio.shape[1]
                    xf = min(int(0.5 * msr), plen // 2, audio.shape[1] // 3)
                    if new.shape[1] > plen + msr // 4:
                        # przenikanie w obszarze, gdzie oryginał i ponownie zdekodowany kontekst są zgodne w czasie
                        tail = new[:, plen - xf:]
                    else:
                        tail = new
                    lin = (np.arange(xf) + 0.5) / xf  # sygnały skorelowane -> przenikanie liniowe
                    mixz = audio[:, -xf:] * (1 - lin) + tail[:, :xf] * lin
                    audio = np.concatenate([audio[:, :-xf], mixz, tail[:, xf:]], axis=1)
                done = audio.shape[1] / msr
                p(min(0.95, done / duration))
    y = audio.T.astype(np.float32)[: int(duration * msr)]
    y = resample(y, msr, SR)
    # wygładzenie krawędzi + normalizacja -16 LUFS, true peak -1 dBTP
    fi_n, fo_n = int(0.03 * SR), int(min(2.0, duration / 6) * SR)
    y[:fi_n] *= np.linspace(0, 1, fi_n, dtype=np.float32)[:, None]
    y[-fo_n:] *= np.cos(0.5 * np.pi * np.linspace(0, 1, fo_n, dtype=np.float32))[:, None]
    lu = ldn.integrated(y, SR)
    if lu > -69:
        y *= 10 ** ((-16.0 - lu) / 20)
    from .mix import limit_true_peak

    y = limit_true_peak(y, -1.0)
    write_wav(out_path, y, SR)
    out_path.with_suffix(".json").write_text(json.dumps({"prompt": prompt, "prompt_en": en, "duration": duration,
                                                         "model": "facebook/musicgen-stereo-medium",
                                                         "seconds": round(time.time() - t0, 1)},
                                                        ensure_ascii=False), encoding="utf-8")
    p(1.0)
    log("sound", f"Wygenerowano muzykę {duration:.0f} s w {time.time() - t0:.0f} s („{en}”)", path="Muzyka")
    return out_path

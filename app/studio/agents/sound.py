"""Agent "sound" - Reżyser Dźwięku & Kompozytor.

* analysis_tasks   - transkrypcja, diaryzacja i statystyki audio każdego klipu (zapis w MediaItem)
* post_edit_tasks  - po montażu: muzyka (A3, ducking), SFX na przejściach i kulminacjach (A4),
                     room tone (A2), ustawienia miksu; commit nowej wersji osi
* apply_audio_command - polecenia z czatu/UI na zakresie osi (bez commit)
* tts_task         - lektor: synteza, nowe medium, klip A5, ducking muzyki, commit
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np

from ..config import get_config
from ..project.model import AClip, Timeline
from ..tasks import Task, TaskContext, orchestrator

AGENT = "sound"
MUSIC_BINS = {"Muzyka", "Audio"}
NO_STT_BINS = {"Muzyka", "SFX"}


def _mediainfo(m) -> dict:
    if m.info:
        return m.info
    from ..media.ffmpeg import media_info

    try:
        return media_info(m.path)
    except Exception:
        return {}


def _has_audio(m) -> bool:
    if m.kind == "image":
        return False
    info = _mediainfo(m)
    if "has_audio" in info:
        return bool(info["has_audio"])
    from ..audio import has_audio

    return has_audio(m.path)


# ------------------------------------------------------------------------------------------
# Analiza mediów
# ------------------------------------------------------------------------------------------
def analysis_tasks(project, media_ids: list[str], *, group: str, deps_by_media: dict[str, list[str]]) -> list[Task]:
    tasks: list[Task] = []
    pid = project.id
    for mid in media_ids:
        try:
            m = project.get_media(mid)
        except KeyError:
            continue
        deps = list(deps_by_media.get(mid, []))
        t_stt = orchestrator.add(AGENT, "Transkrypcja (STT)", f"Transkrypcja: {m.name}", _stt_fn(mid),
                                 project_id=pid, deps=deps, group=group, priority=3)
        t_dia = orchestrator.add(AGENT, "Diaryzacja mówców", f"Diaryzacja: {m.name}", _diar_fn(mid),
                                 project_id=pid, deps=[t_stt.id], group=group, priority=4)
        t_an = orchestrator.add(AGENT, "Analiza audio", f"Analiza audio: {m.name}", _analysis_fn(mid),
                                project_id=pid, deps=deps + [t_stt.id], soft_deps=True, group=group, priority=5)
        tasks += [t_stt, t_dia, t_an]
    return tasks


def _stt_fn(mid: str):
    def fn(ctx: TaskContext):
        from ..audio.stt import speech_ratio, transcribe

        project = ctx.project
        m = project.get_media(mid)
        if not _has_audio(m):
            ctx.log(f"{m.name}: brak ścieżki audio - pomijam transkrypcję")
            project.update_media(mid, lambda x: x.analysis.stage.__setitem__("stt", "skipped"))
            return {"skipped": True}
        if m.bin in NO_STT_BINS:
            ctx.log(f"{m.name}: plik w binie „{m.bin}” - bez transkrypcji")
            project.update_media(mid, lambda x: x.analysis.stage.__setitem__("stt", "skipped"))
            return {"skipped": True}
        t0 = time.time()
        lang = getattr(project.meta, "language", None) or get_config().get("models.stt.language", "pl")
        tr = transcribe(m.path, language=lang, effort=ctx.effort, progress=ctx.sub(0.0, 0.97), agent=AGENT,
                        media_id=mid)
        dur = float(_mediainfo(m).get("duration") or 0)
        nf = sum(1 for w in tr.words() if w.filler)

        def upd(x):
            x.transcript = tr
            x.analysis.speech_ratio = speech_ratio(tr, dur)
            x.analysis.stage["stt"] = "done"

        project.update_media(mid, upd)
        ctx.log(f"{m.name}: {len(tr.sentences)} zdań, {len(tr.words())} słów, wypełniacze/zająknięcia: {nf} "
                f"({dur:.0f} s materiału w {time.time() - t0:.1f} s, {tr.engine})")
        return {"sentences": len(tr.sentences), "words": len(tr.words()), "fillers": nf, "engine": tr.engine}

    return fn


def _diar_fn(mid: str):
    def fn(ctx: TaskContext):
        from ..audio.diarize import diarize

        project = ctx.project
        m = project.get_media(mid)
        if not m.transcript or not m.transcript.sentences:
            ctx.log(f"{m.name}: brak mowy - diaryzacja niepotrzebna")
            project.update_media(mid, lambda x: x.analysis.stage.__setitem__("diarization", "skipped"))
            return {"speakers": 0}
        ctx.progress(0.1, "Embeddingi głosu")
        mx = int(get_config().get("models.diarization.max_speakers", 6))
        tr = diarize(m.path, m.transcript, max_speakers=mx)

        def upd(x):
            x.transcript = tr
            x.analysis.stage["diarization"] = "done"

        project.update_media(mid, upd)
        ctx.log(f"{m.name}: mówcy - {', '.join(tr.speakers) or 'brak'}")
        return {"speakers": len(tr.speakers)}

    return fn


def _analysis_fn(mid: str):
    def fn(ctx: TaskContext):
        from ..audio import extract_wav, frame_rms_db, load_wav
        from ..audio.loudness import integrated, true_peak
        from ..audio.roomtone import find_room_tone

        project = ctx.project
        m = project.get_media(mid)
        if not _has_audio(m):
            ctx.log(f"{m.name}: brak audio - pomijam analizę dźwięku")
            project.update_media(mid, lambda x: x.analysis.stage.__setitem__("audio", "skipped"))
            return {"skipped": True}
        ctx.progress(0.05, "Wyciąganie audio")
        wav16 = extract_wav(m.path, 16000, True, cancel=ctx.cancel)
        a16, sr16 = load_wav(wav16)
        rms = frame_rms_db(a16, sr16, 0.05)
        ctx.progress(0.3, "Głośność")
        wav48 = extract_wav(m.path, 48000, False, cancel=ctx.cancel)
        a48, sr48 = load_wav(wav48)
        lufs = integrated(a48, sr48)
        peak = true_peak(a48, sr48)
        del a48
        fresh = project.get_media(mid)
        tr = fresh.transcript
        dur = len(a16) / sr16
        if tr and tr.sentences:
            from ..audio.stt import speech_ratio

            sr_ratio = speech_ratio(tr, dur)
        else:
            valid = rms[rms > -90]
            floor = float(np.percentile(valid, 10)) if len(valid) else -90
            sr_ratio = float(np.mean(rms > max(-45, floor + 15))) * 0.6 if len(rms) else 0.0
        ctx.progress(0.5, "Room tone")
        rt = None
        try:
            rt = find_room_tone(m.path, tr)
        except Exception as e:
            ctx.log(f"{m.name}: nie znaleziono room tone ({e})", level="warn")
        music = None
        is_music = m.bin in MUSIC_BINS and (not tr or len(tr.words()) < max(3, dur / 10))
        if is_music or m.bin == "Muzyka":
            ctx.progress(0.6, "Analiza muzyki (BPM, beaty)")
            from ..audio.music import analyze_music

            try:
                music = analyze_music(m.path)
            except Exception as e:
                ctx.log(f"{m.name}: analiza muzyki nieudana ({e})", level="warn")

        def upd(x):
            x.analysis.audio_rms = [round(float(v), 1) for v in rms]
            x.analysis.audio_rms_hop = 0.05
            x.analysis.loudness_lufs = round(float(lufs), 2)
            x.analysis.peak_db = round(float(peak), 2)
            x.analysis.speech_ratio = round(float(sr_ratio), 3)
            x.analysis.room_tone = rt
            if music:
                x.analysis.bpm = music.get("bpm")
                x.analysis.beats = music.get("beats", [])
                x.analysis.downbeats = music.get("downbeats", [])
                x.analysis.energy_sections = music.get("sections", [])
            x.analysis.stage["audio"] = "done"

        project.update_media(mid, upd)
        msg = f"{m.name}: {lufs:.1f} LUFS, szczyt {peak:.1f} dBTP, mowa {sr_ratio * 100:.0f}%"
        if rt:
            msg += f", room tone {rt['s']:.1f}–{rt['e']:.1f} s ({rt['db']:.0f} dB)"
        if music and music.get("bpm"):
            msg += f", {music['bpm']:.0f} BPM"
        ctx.log(msg)
        return {"lufs": lufs, "peak": peak, "speech_ratio": sr_ratio, "room_tone": rt,
                "bpm": music.get("bpm") if music else None}

    return fn


# ------------------------------------------------------------------------------------------
# Po montażu
# ------------------------------------------------------------------------------------------
def _style_moods() -> dict[str, str]:
    from ..edit import styles

    return {k: v["music_mood"] for k, v in styles.STYLES.items()}


STYLE_MOOD = _style_moods()


def _media_duration(project, mid: str) -> float:
    m = project.get_media(mid)
    d = float((m.info or {}).get("duration") or 0)
    if d <= 0:
        from ..media.ffmpeg import media_info

        info = media_info(m.path)
        project.update_media(mid, lambda x: x.info.update(info))
        d = float(info.get("duration") or 0)
    return d


def _resolve_music(project, music: str) -> str | None:
    """music = id medium albo ścieżka pliku -> id medium w projekcie."""
    try:
        project.get_media(music)
        return music
    except KeyError:
        pass
    p = Path(music)
    if p.exists():
        m = project.add_media_file(p, bin_name="Muzyka")
        return m.id
    return None


def post_edit_tasks(project, *, group: str, deps: list[str], music: str | None, mood: str | None) -> list[Task]:
    pid = project.id

    def music_fn(ctx: TaskContext):
        from ..audio.models import installed
        from ..audio.music import analyze_music, generate_music, mood_key, pick_music

        pr = ctx.project
        tl = pr.timeline()
        dur = tl.duration
        if dur <= 0.5:
            ctx.log("Oś jest pusta - pomijam muzykę", level="warn")
            return None
        mk = mood_key(mood or STYLE_MOOD.get(pr.meta.style, "") or pr.meta.style or "vlog")
        mid = None
        if music:
            mid = _resolve_music(pr, music)
            if mid is None:
                ctx.log(f"Nie znaleziono muzyki „{music}” - dobieram z biblioteki", level="warn")
        if mid is None:
            ctx.progress(0.1, "Dobór muzyki z biblioteki")
            p = pick_music(mk, dur)
            if p is not None:
                mid = _resolve_music(pr, str(p))
                ctx.log(f"Dobrano muzykę z biblioteki: {p.name} (nastrój: {mk})")
        if mid is None:
            if ctx.effort_level >= 2 and installed("musicgen_stereo_medium"):
                from ..audio.music import MOODS

                gen_d = min(dur + 1.0, 240.0)
                ctx.log(f"Brak pasującej muzyki w bibliotece - generuję {gen_d:.0f} s (MusicGen, nastrój: {mk})")
                out = pr.cache / "audio" / "music" / f"musicgen_{mk}_{int(time.time())}.wav"
                prompt = (pr.meta.notes or "").strip()
                prompt = MOODS[mk][2] if not prompt else f"{MOODS[mk][2]}; {prompt[:200]}"
                generate_music(prompt, gen_d, out, progress=ctx.sub(0.15, 0.9))
                m = pr.add_media_file(out, bin_name="Wygenerowane", generated=True)
                mid = m.id
            else:
                ctx.log("Brak muzyki w bibliotece i generator niedostępny/niski wysiłek - film bez muzyki",
                        level="warn")
                return None
        ctx.progress(0.92, "Analiza utworu")
        m = pr.get_media(mid)
        ana = {}
        try:
            ana = analyze_music(m.path)

            def upd(x):
                x.analysis.bpm = ana.get("bpm")
                x.analysis.beats = ana.get("beats", [])
                x.analysis.downbeats = ana.get("downbeats", [])
                x.analysis.energy_sections = ana.get("sections", [])
                x.analysis.stage["audio"] = "done"

            pr.update_media(mid, upd)
        except Exception as e:
            ctx.log(f"Analiza muzyki nieudana: {e}", level="warn")
        return {"media_id": mid, "bpm": ana.get("bpm"), "mood": mk}

    t_music = orchestrator.add(AGENT, "Muzyka", "Dobór / generacja muzyki", music_fn, project_id=pid, deps=deps,
                               group=group, priority=4)

    def apply_fn(ctx: TaskContext):
        pr = ctx.project
        res = ctx.result_of(t_music.id) or {}
        cfg = get_config()
        stats: dict = {}

        def place(tl: Timeline, verbose: bool) -> Timeline:
            notes = []
            if res.get("media_id"):
                _place_music(pr, tl, res["media_id"], reason=f"auto: nastrój {res.get('mood', '')}")
                notes.append("muzyka")
            if verbose:
                ctx.progress(0.3, "SFX na przejściach")
            n_sfx = _place_sfx(pr, tl)
            if n_sfx:
                notes.append(f"{n_sfx} SFX")
            if verbose:
                ctx.progress(0.6, "Room tone")
            if _place_room_tone(pr, tl, ctx if verbose else None):
                notes.append("room tone")
            tl.mix.target_lufs = float(cfg.get("audio.target_lufs", -14.0))
            tl.mix.true_peak = float(cfg.get("audio.true_peak", -1.0))
            tl.mix.duck_db = float(cfg.get("audio.duck_db", -15.0))
            tl.mix.crossfade_ms = float(cfg.get("audio.micro_fade_ms", 80))
            tl.mix.voice_chain = {**tl.mix.voice_chain, "denoise": cfg.get("audio.denoise", "auto")}
            tl.mix.room_tone = True
            stats.update(notes=notes, sfx=n_sfx)
            return tl

        # 1) przebieg na migawce: synteza SFX / room tone do cache (ciężka praca poza blokadą osi)
        ctx.progress(0.05, "Przygotowanie dźwięków")
        place(pr.timeline(), True)
        note = "Dźwięk: " + (", ".join(stats["notes"]) if stats["notes"] else "ustawienia miksu") + \
            f", cel {float(cfg.get('audio.target_lufs', -14.0)):.0f} LUFS"
        # 2) atomowo na AKTUALNEJ osi (pliki już w cache - szybko)
        ctx.progress(0.9, "Zapis osi")
        tl = pr.mutate_timeline(lambda t: place(t, False), note, AGENT)
        ctx.log(note)
        return {"version": tl.version, "sfx": stats.get("sfx", 0), "music": res.get("media_id")}

    t_apply = orchestrator.add(AGENT, "Miks", "Rozmieszczenie dźwięku na osi", apply_fn, project_id=pid,
                               deps=deps + [t_music.id], soft_deps=True, group=group, priority=4)
    return [t_music, t_apply]


def _place_music(project, tl: Timeline, mid: str, *, reason: str = "auto", t0: float = 0.0,
                 t1: float | None = None) -> None:
    dur = tl.duration
    t1 = dur if t1 is None else t1
    mdur = _media_duration(project, mid)
    keep = [c for c in tl.a("A3") if not (c.start < t1 and c.end > t0)]
    length = max(0.5, t1 - t0)
    loop = mdur < length - 0.05
    clip = AClip(media_id=mid, src_in=0.0, src_out=length if loop else min(length, mdur), start=t0, role="music",
                 duck=True, loop=loop, fade_in=min(1.0, length / 6), fade_out=min(3.0, length / 4), gain_db=0.0,
                 label="Muzyka", reason=reason)
    tl.tracks["A3"] = keep + [clip]


def _sfx_file(project, kind: str, duration: float) -> str:
    from ..audio.sfx import make_sfx

    d = project.cache / "audio" / "sfx"
    out = d / f"{re.sub(r'[^a-z0-9_]', '_', kind)}_{duration:.2f}.wav"
    if not out.exists():
        make_sfx(kind, out, duration)
    return str(out)


SFX_FOR_TRANSITION = {"whip": ("whoosh", 0.8, -2.0), "slide": ("whoosh", 0.8, -4.0), "zoom": ("swoosh", 0.7, -4.0),
                      "light_leak": ("riser", 1.2, -8.0), "dissolve": ("swoosh", 0.9, -12.0),
                      "dip_black": ("downlifter", 1.2, -10.0), "dip_white": ("riser", 1.0, -9.0)}


def _place_sfx(project, tl: Timeline) -> int:
    keep = [c for c in tl.a("A4") if not (c.reason or "").startswith("auto:")]
    added = []
    for tr in tl.transitions:
        if tr.kind == "cut" or tr.kind not in SFX_FOR_TRANSITION:
            continue
        kind, d, gain = SFX_FOR_TRANSITION[tr.kind]
        path = _sfx_file(project, kind, d)
        # szczyt whoosha (~55% długości) na cięciu; riser kończy się na cięciu
        peak = 0.55 if kind in ("whoosh", "swoosh") else (1.0 if kind == "riser" else 0.05)
        start = max(0.0, tr.at - peak * d)
        added.append(AClip(path=path, src_in=0.0, src_out=_wav_dur(path), start=start, role="sfx", gain_db=gain,
                           label=kind, reason=f"auto: przejście {tr.kind} @ {tr.at:.2f}s"))
    for mk in tl.markers:
        if mk.kind != "climax":
            continue
        hit = _sfx_file(project, "impact", 1.6)
        riser = _sfx_file(project, "riser", 2.0)
        added.append(AClip(path=riser, src_in=0.0, src_out=_wav_dur(riser), start=max(0.0, mk.t - 2.0), role="sfx",
                           gain_db=-10.0, label="riser", reason=f"auto: kulminacja @ {mk.t:.2f}s"))
        added.append(AClip(path=hit, src_in=0.0, src_out=_wav_dur(hit), start=max(0.0, mk.t - 0.005), role="sfx",
                           gain_db=-6.0, label="impact", reason=f"auto: kulminacja @ {mk.t:.2f}s"))
    tl.tracks["A4"] = keep + added
    return len(added)


def _wav_dur(path: str) -> float:
    import soundfile as sf

    i = sf.info(path)
    return round(i.frames / float(i.samplerate), 4)


def _place_room_tone(project, tl: Timeline, ctx: TaskContext | None = None) -> bool:
    from ..audio.mix import dialog_clips
    from ..audio.roomtone import find_room_tone, make_room_tone

    dcl = dialog_clips(project, tl)
    if not dcl:
        return False
    tot: dict[str, float] = {}
    for c, sp in dcl:
        if c.media_id:
            tot[c.media_id] = tot.get(c.media_id, 0.0) + c.duration / max(0.05, sp)
    if not tot:
        return False
    mid = max(tot, key=tot.get)
    m = project.get_media(mid)
    info = m.analysis.room_tone or find_room_tone(m.path, m.transcript)
    if not info:
        if ctx:
            ctx.log(f"Nie znalazłem ciszy bez mowy w {m.name} - bez room tone", level="warn")
        return False
    lo = min(c.start for c, _ in dcl)
    hi = max(c.start + c.duration / max(0.05, sp) for c, sp in dcl)
    dur = round(hi - lo + 0.2, 2)
    out = project.cache / "audio" / "roomtone" / f"rt_{mid}_{dur:.1f}.wav"
    if not out.exists():
        make_room_tone(m.path, info, dur, out)
    keep = [c for c in tl.a("A2") if not (c.reason or "").startswith("auto:")]
    keep.append(AClip(path=str(out), src_in=0.0, src_out=_wav_dur(str(out)), start=max(0.0, lo - 0.1),
                      role="roomtone", label="Room tone", fade_in=0.05, fade_out=0.05,
                      reason=f"auto: room tone z {m.name} ({info['s']:.1f}–{info['e']:.1f} s)",
                      processing={"gate_under_dialog": True, "source_media": mid}))
    tl.tracks["A2"] = keep
    return True


# ------------------------------------------------------------------------------------------
# Polecenia
# ------------------------------------------------------------------------------------------
def _ensure_a1(project, tl: Timeline) -> None:
    """Materializuje A1 z audio V1 (gdy pusty), żeby dało się zmieniać dialog fragmentami."""
    if tl.a("A1"):
        return
    from ..audio.mix import dialog_clips

    clips = []
    for c, sp in dialog_clips(project, tl):
        c.id = AClip().id
        if abs(sp - 1.0) > 1e-4:
            c.processing["speed"] = sp
        clips.append(c)
    tl.tracks["A1"] = clips


def _seg_speed(c: AClip) -> float:
    return float(c.processing.get("speed", 1.0) or 1.0)


def _split_range(tl: Timeline, track: str, t0: float, t1: float) -> list[AClip]:
    """Dzieli klipy ścieżki na granicach t0/t1; zwraca klipy w całości wewnątrz [t0,t1]."""
    out: list[AClip] = []
    inside: list[AClip] = []
    for c in tl.a(track):
        sp = _seg_speed(c)
        end = c.start + c.duration / sp
        if end <= t0 + 1e-4 or c.start >= t1 - 1e-4:
            out.append(c)
            continue
        cuts = [t for t in (t0, t1) if c.start + 1e-3 < t < end - 1e-3]
        bounds = [c.start] + cuts + [end]
        for a, b in zip(bounds, bounds[1:]):
            part = c.model_copy(deep=True)
            if a != c.start or b != end:
                part.id = AClip().id
            part.src_in = c.src_in + (a - c.start) * sp
            part.src_out = c.src_in + (b - c.start) * sp
            part.start = a
            part.fade_in = c.fade_in if a == c.start else 0.0
            part.fade_out = c.fade_out if b == end else 0.0
            out.append(part)
            if a >= t0 - 1e-3 and b <= t1 + 1e-3:
                inside.append(part)
    tl.tracks[track] = out
    return inside


def apply_audio_command(project, timeline: Timeline, t0: float, t1: float, params: dict) -> Timeline:
    tl = timeline.model_copy(deep=True).normalize()
    t0 = max(0.0, float(t0 or 0.0))
    t1 = float(t1) if t1 is not None and float(t1) > t0 else tl.duration
    p = dict(params or {})
    if "gain_db" in p:
        _ensure_a1(project, tl)
        for c in _split_range(tl, "A1", t0, t1):
            c.gain_db = float(np.clip(c.gain_db + float(p["gain_db"]), -60, 24))
    if "denoise" in p or "dereverb" in p or "presence_db" in p:
        _ensure_a1(project, tl)
        whole = t0 <= 0.001 and t1 >= tl.duration - 0.001
        if whole:
            for k in ("denoise", "dereverb", "presence_db"):
                if k in p:
                    tl.mix.voice_chain[k] = p[k]
        else:
            for c in _split_range(tl, "A1", t0, t1):
                for k in ("denoise", "dereverb", "presence_db"):
                    if k in p:
                        c.processing[k] = p[k]
    if p.get("mute_music") is not None:
        mute = bool(p["mute_music"])
        for c in _split_range(tl, "A3", t0, t1):
            if mute:
                c.processing["pre_mute_gain"] = c.processing.get("pre_mute_gain", c.gain_db)
                c.gain_db = -96.0
            elif "pre_mute_gain" in c.processing:
                c.gain_db = float(c.processing.pop("pre_mute_gain"))
    if "music_gain_db" in p:
        tl.mix.music_gain_db = float(p["music_gain_db"])
    if p.get("music_mood"):
        from ..audio.music import pick_music

        cur = [project.get_media(c.media_id).path for c in tl.a("A3") if c.media_id]
        f = pick_music(str(p["music_mood"]), t1 - t0, exclude=cur)
        if f is not None:
            mid = _resolve_music(project, str(f))
            whole = t0 <= 0.001 and t1 >= tl.duration - 0.001
            _place_music(project, tl, mid, reason=f"polecenie: nastrój {p['music_mood']}",
                         t0=0.0 if whole else t0, t1=None if whole else t1)
        else:
            from ..events import log

            log(AGENT, f"Brak muzyki o nastroju „{p['music_mood']}” w bibliotece", path="Muzyka", level="warn",
                project_id=project.id)
    if p.get("music"):
        mid = _resolve_music(project, str(p["music"]))
        if mid:
            _place_music(project, tl, mid, reason="polecenie: wybrana muzyka")
    if p.get("sfx"):
        kind = str(p["sfx"])
        at = float(p.get("at", t0))
        d = float(p.get("duration", 1.0 if kind not in ("riser", "downlifter") else 2.0))
        path = _sfx_file(project, kind, d)
        peak = 0.55 if kind in ("whoosh", "swoosh") else (1.0 if kind == "riser" else 0.0)
        c = AClip(path=path, src_in=0.0, src_out=_wav_dur(path), start=max(0.0, at - peak * d), role="sfx",
                  gain_db=float(p.get("gain_db_sfx", -4.0)), label=kind, reason=f"polecenie: {kind} @ {at:.2f}s")
        tl.tracks["A4"] = tl.a("A4") + [c]
    for k in ("target_lufs", "true_peak", "duck_db", "duck_attack", "duck_release", "room_tone_db", "crossfade_ms"):
        if k in p:
            setattr(tl.mix, k, float(p[k]))
    if "room_tone" in p:
        tl.mix.room_tone = bool(p["room_tone"])
    if "duck" in p:
        tl.tracks["A3"] = [c.model_copy(update={"duck": bool(p["duck"])}) for c in tl.a("A3")]
    return tl.normalize()


# ------------------------------------------------------------------------------------------
# Lektor
# ------------------------------------------------------------------------------------------
def tts_task(project, text: str, at: float, *, group: str, voice: str | None = None,
             ref_audio: str | None = None) -> Task:
    def fn(ctx: TaskContext):
        import json

        from ..audio import param_hash
        from ..audio.tts import synthesize

        pr = ctx.project
        ctx.progress(0.05, "Synteza mowy")
        out = pr.cache / "audio" / "tts" / f"vo_{param_hash(text, voice, ref_audio, n=12)}.wav"
        if not out.exists():
            synthesize(text, out, voice=voice, ref_audio=ref_audio, language=pr.meta.language or "pl")
        meta = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
        ctx.progress(0.7, "Wstawianie na A5")
        m = pr.add_media_file(out, bin_name="Wygenerowane", generated=True)
        from ..media.ffmpeg import media_info

        info = media_info(out)
        pr.update_media(m.id, lambda x: x.info.update(info))
        dur = float(meta.get("duration") or info.get("duration") or 0)
        clip = AClip(media_id=m.id, src_in=0.0, src_out=dur, start=max(0.0, float(at)), role="vo", label="Lektor",
                     reason="lektor (TTS)", fade_in=0.01, fade_out=0.05,
                     processing={"text": text, "words": meta.get("words", []), "engine": meta.get("engine")})

        def add_vo(tl: Timeline) -> Timeline:
            tl.tracks["A5"] = tl.a("A5") + [clip]
            tl.tracks["A3"] = [c.model_copy(update={"duck": True}) for c in tl.a("A3")]
            return tl

        short = text if len(text) <= 40 else text[:37] + "…"
        tl = pr.mutate_timeline(add_vo, f"Dźwięk: lektor „{short}”", AGENT)
        ctx.log(f"Lektor ({meta.get('engine')}): {dur:.1f} s od {float(at):.2f} s")
        return {"media_id": m.id, "path": str(out), "duration": dur, "version": tl.version}

    return orchestrator.add(AGENT, "Lektor (TTS)", "Lektor: synteza mowy", fn, project_id=project.id, group=group,
                            priority=4)

"""Silnik renderu (Agent Montażysta Online): plastry -> concat -> audio -> napisy -> kodek docelowy.

Przebieg `render_timeline`:
 1. plan (slices.py): oś dzielona na plastry o stałym zestawie warstw, hash parametrów każdego plastra,
 2. tytuły V3 -> plik ASS (czas absolutny osi; w plastrach przesuwamy PTS o początek plastra),
 3. równolegle: brakujące plastry (pula wątków, limit `render.nvenc_sessions`) i miks audio
    (`studio.audio.mix.render_mix`, a gdy niedostępny - prosty miks zapasowy w tym module),
 4. concat demuxer (stream copy) -> video_full.mp4,
 5. napisy (`studio.subtitles.build.build_subtitles`) -> wypalenie `ass=` w kroku końcowym,
 6. krok końcowy: kodek docelowy wg ustawień (final) lub szybkie złożenie (preview).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..config import get_config
from ..events import bus, log
from ..media import ffmpeg as ff
from ..media.ffmpeg import Cancelled, FFmpegError, escape_filter_path
from ..project.model import AClip, Timeline, VClip, Variant
from . import presets
from .filters import FONTS_DIR, build_slice_graph, titles_ass
from .slices import RenderPlan, SliceJob, log_warnings, plan_render, spec_hash

AGENT = "render"


# ---------------------------------------------------------------------------------------------
# Postęp
# ---------------------------------------------------------------------------------------------
class _Progress:
    STAGES = {"plan": (0.0, 0.03), "slices": (0.03, 0.80), "concat": (0.80, 0.83), "audio_wait": (0.83, 0.87),
              "subtitles": (0.87, 0.88), "encode": (0.88, 1.0)}

    def __init__(self, project_id: str | None, variant: str, quality: str, group: str | None,
                 cb: Callable[[float], None] | None):
        self.pid, self.variant, self.quality, self.group, self.cb = project_id, variant, quality, group, cb
        self.last_pub = 0.0
        self.value = 0.0
        self.stage = "plan"

    def set(self, stage: str, frac: float = 0.0, force: bool = False, **extra) -> None:
        a, b = self.STAGES.get(stage, (self.value, self.value))
        self.stage = stage
        self.value = max(self.value, a + (b - a) * max(0.0, min(1.0, frac)))
        now = time.time()
        if force or now - self.last_pub > 0.3:
            self.last_pub = now
            data = {"project_id": self.pid, "variant": self.variant, "quality": self.quality,
                    "progress": round(self.value, 4), "stage": stage, **extra}
            if self.group:
                data["group"] = self.group
            bus.publish("render.progress", data)
        if self.cb:
            self.cb(self.value)


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled("Przerwano")


# ---------------------------------------------------------------------------------------------
# Plastry
# ---------------------------------------------------------------------------------------------
def slices_dir(project, quality: str, variant: Variant) -> Path:
    d = project.cache / "slices" / f"{quality}_{variant.name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hw_decode_enabled() -> bool:
    return bool(get_config().get("render.hw_decode", True)) and bool(ff.hw_decode_args())


def render_slice(job: SliceJob, plan: RenderPlan, *, pix: str, codec_args: list[str], titles_file: Path | None,
                 workdir: Path, cancel: threading.Event | None, hw_decode: bool,
                 on_progress: Callable[[float], None] | None = None) -> Path:
    assert job.path is not None
    F = plan.fps
    attempts = [hw_decode, False] if hw_decode else [False]
    last: Exception | None = None
    for hw in attempts:
        g, out = build_slice_graph(job.spec, hw_decode=hw, pix=pix, titles_ass=titles_file)
        script = workdir / f"{job.hash}.fg.txt"
        script.write_text(g.script(), encoding="utf-8")
        tmp = job.path.with_name(job.path.stem + ".part.mp4")
        args = [*g.input_args(), "-/filter_complex", str(script), "-map", f"[{out}]", "-frames:v", str(job.n),
                "-an", "-sn", "-dn", *codec_args, "-r", str(F),
                "-video_track_timescale", str(presets.timescale(F)),
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
                "-map_metadata", "-1", "-f", "mp4", str(tmp)]
        try:
            ff.run(args, duration=job.n / float(F), progress=on_progress, cancel=cancel)
            os.replace(tmp, job.path)
            try:
                script.unlink()
            except OSError:
                pass
            return job.path
        except Cancelled:
            _unlink(tmp)
            raise
        except FFmpegError as e:
            _unlink(tmp)
            last = e
            continue
    raise FFmpegError(f"Render plastra {job.p}-{job.q} nieudany: {last}", getattr(last, "stderr", ""))


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def concat_slices(paths: list[Path], out: Path, workdir: Path, cancel: threading.Event | None) -> Path:
    lst = workdir / f"concat_{hashlib.sha1(str(out).encode()).hexdigest()[:10]}.txt"
    lines = ["ffconcat version 1.0"]
    for p in paths:
        s = str(p).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{s}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp = out.with_name(out.stem + ".part.mp4")
    ff.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0:v:0", "-c", "copy", "-an",
            "-movflags", "+faststart", str(tmp)], cancel=cancel)
    os.replace(tmp, out)
    return out


# ---------------------------------------------------------------------------------------------
# Audio: render_mix z modułu audio albo prosty miks zapasowy
# ---------------------------------------------------------------------------------------------
def fallback_mix(project, tl: Timeline, out_wav: Path, *, t0: float, t1: float,
                 cancel: threading.Event | None = None, progress: Callable[[float], None] | None = None) -> dict:
    """Prosty miks: A1 (lub audio klipów V1, gdy A1 puste) + A2..A5; fade 10 ms na krawędziach, bez normalizacji."""
    sr = int(tl.sample_rate or 48000)
    L = max(0.0, t1 - t0)
    n = int(round(L * sr))
    buf = np.zeros((n, 2), np.float32)
    items: list[tuple[str, float, float, float, float, float, float, float]] = []
    # (ścieżka, start_osi, src_in, długość_osi, speed, gain_db, fade_in, fade_out)
    a1 = [c for c in tl.tracks.get("A1", []) if isinstance(c, AClip)]
    if not a1:
        for c in tl.tracks.get("V1", []):
            if not isinstance(c, VClip) or c.role not in ("a_roll", "b_roll", "generated"):
                continue
            try:
                m = project.get_media(c.media_id)
            except Exception:
                continue
            if not m.info.get("has_audio", True) or m.kind == "image":
                continue
            if c.speed_ramp:
                continue
            items.append((m.path, c.start, c.src_in, c.duration, c.speed, 0.0, 0.0, 0.0))
    for track in ("A1", "A2", "A3", "A4", "A5"):
        for c in tl.tracks.get(track, []):
            if not isinstance(c, AClip):
                continue
            path = c.path
            if not path and c.media_id:
                try:
                    path = project.get_media(c.media_id).path
                except Exception:
                    path = None
            if not path or not Path(path).exists():
                continue
            items.append((path, c.start, c.src_in, c.duration, 1.0, c.gain_db, c.fade_in, c.fade_out))
    fade = int(sr * 0.010)
    for i, (path, start, src_in, dur, speed, gain, fi, fo) in enumerate(items):
        _check(cancel)
        a = max(start, t0)
        b = min(start + dur, t1)
        if b - a <= 1e-3:
            continue
        src_a = src_in + (a - start) * speed
        try:
            af = None
            if abs(speed - 1.0) > 1e-3:
                sp = speed
                chain = []
                while sp > 2.0:
                    chain.append("atempo=2.0")
                    sp /= 2.0
                while sp < 0.5:
                    chain.append("atempo=0.5")
                    sp /= 0.5
                chain.append(f"atempo={sp:.6f}")
                af = ",".join(chain)
            seg = ff.read_audio(path, sr=sr, mono=False, start=src_a, duration=(b - a) * speed + 0.05, af=af)
        except Exception:
            continue
        if seg.ndim == 1:
            seg = np.stack([seg, seg], axis=1)
        k = min(len(seg), int(round((b - a) * sr)))
        seg = seg[:k].copy()
        if k == 0:
            continue
        seg *= np.float32(10 ** (gain / 20.0))
        env = np.ones(k, np.float32)
        fl = min(fade, k // 2)
        if fl > 0:
            env[:fl] = np.linspace(0, 1, fl, dtype=np.float32)
            env[-fl:] = np.minimum(env[-fl:], np.linspace(1, 0, fl, dtype=np.float32))
        if fi > 0 and a <= start + 1e-3:
            m = min(k, int(fi * sr))
            env[:m] *= np.linspace(0, 1, m, dtype=np.float32)
        if fo > 0 and b >= start + dur - 1e-3:
            m = min(k, int(fo * sr))
            env[-m:] *= np.linspace(1, 0, m, dtype=np.float32)
        seg *= env[:, None]
        o = int(round((a - t0) * sr))
        e = min(n, o + k)
        buf[o:e] += seg[: e - o]
        if progress:
            progress((i + 1) / max(1, len(items)))
    np.clip(buf, -1.0, 1.0, out=buf)
    ff.write_wav(out_wav, buf, sr)
    peak = float(np.max(np.abs(buf))) if n else 0.0
    return {"path": str(out_wav), "lufs": None, "true_peak": round(20 * np.log10(peak), 2) if peak > 0 else None,
            "lra": None, "fallback": True}


def _audio_job(project, tl: Timeline, out_wav: Path, t0: float, t1: float, cancel, state: dict) -> dict:
    def prog(p: float) -> None:
        state["p"] = p

    try:
        from ..audio.mix import render_mix  # type: ignore
    except Exception as e:
        state["warn"] = f"Moduł miksu audio niedostępny ({type(e).__name__}) - użyto prostego miksu zapasowego"
        return fallback_mix(project, tl, out_wav, t0=t0, t1=t1, cancel=cancel, progress=prog)
    try:
        rep = render_mix(project, tl, out_wav, progress=prog, cancel=cancel, t0=t0, t1=t1)
        if not Path(out_wav).exists():
            raise RuntimeError("render_mix nie zapisał pliku WAV")
        return dict(rep or {}, path=str(out_wav))
    except Cancelled:
        raise
    except Exception as e:
        state["warn"] = f"Miks audio nieudany ({e}) - użyto prostego miksu zapasowego"
        return fallback_mix(project, tl, out_wav, t0=t0, t1=t1, cancel=cancel, progress=prog)


# ---------------------------------------------------------------------------------------------
# Napisy
# ---------------------------------------------------------------------------------------------
def build_subs(project, tl: Timeline, variant: Variant, out_dir: Path) -> tuple[dict | None, str | None]:
    st = tl.subtitles
    if not st.enabled or st.mode == "none":
        return None, None
    try:
        from ..subtitles.build import build_subtitles  # type: ignore
    except Exception as e:
        return None, f"Moduł napisów niedostępny ({type(e).__name__}) - render bez napisów"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        res = build_subtitles(project, tl, variant, out_dir) or {}
        res = {k: str(v) for k, v in res.items() if v}
        if not res.get("ass") and not res.get("srt"):
            return None, None
        return res, None
    except Cancelled:
        raise
    except Exception as e:
        return None, f"Budowa napisów nieudana ({e}) - render bez napisów"


def subs_filter(ass: str | Path, t_offset: float = 0.0) -> str:
    f = f"ass=filename='{escape_filter_path(ass)}':fontsdir='{escape_filter_path(FONTS_DIR)}'"
    if t_offset > 1e-6:
        return f"setpts=PTS+{t_offset:.6f}/TB,{f},setpts=PTS-STARTPTS"
    return f


# ---------------------------------------------------------------------------------------------
# Meta renderu (dla QC i podglądu)
# ---------------------------------------------------------------------------------------------
def _meta_file(project, path: str | Path) -> Path:
    key = hashlib.sha1(str(Path(path).resolve()).lower().encode()).hexdigest()[:16]
    d = project.cache / "render" / "meta"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def render_meta(project, path: str | Path) -> dict | None:
    f = _meta_file(project, path)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def timeline_hash(tl: Timeline) -> str:
    d = tl.model_copy(deep=True).to_json()
    for k in ("version", "note", "created_at", "created_by", "duration"):
        d.pop(k, None)
    return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------
# Główna funkcja
# ---------------------------------------------------------------------------------------------
def prepare_plan(project, tl: Timeline, variant: Variant, *, quality: str, t0: float, t1: float | None,
                 settings: dict | None) -> RenderPlan:
    W, H = presets.render_size(variant, settings, quality)
    F = presets.output_fps(variant, tl, settings)
    steps = int((settings or {}).get("ramp_steps") or get_config().get("render.ramp_steps", 8) or 8)
    max_slice = float(get_config().get("render.max_slice_s", 6.0) or 6.0)
    plan, _pl = plan_render(project, tl, variant, W=W, H=H, fps=F, quality=quality, t0=t0, t1=t1,
                            ramp_steps=steps, max_slice_s=max_slice)
    d = slices_dir(project, quality, variant)
    for job in plan.slices:
        job.path = d / f"{job.hash}.mp4"
        job.cached = job.path.exists() and job.path.stat().st_size > 0
    return plan


def render_timeline(project, timeline: Timeline, variant: Variant, out_path: Path, *, quality: str = "final",
                    t0: float = 0, t1: float | None = None, progress=None, cancel=None,
                    settings: dict | None = None) -> dict:
    t_start = time.time()
    settings = dict(settings or {})
    quality = "preview" if quality == "preview" else "final"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    pid = getattr(project, "id", None)
    prog = _Progress(pid, variant.name, quality, settings.get("group"), progress)
    prog.set("plan", 0.0, force=True)
    plan = prepare_plan(project, tl, variant, quality=quality, t0=t0, t1=t1, settings=settings)
    log_warnings(plan, pid)
    F = plan.fps
    T0, T1 = float(Fraction(plan.FA) / F), float(Fraction(plan.FB) / F)
    workdir = project.cache / "render" / f"{quality}_{variant.name}"
    workdir.mkdir(parents=True, exist_ok=True)
    todo = [j for j in plan.slices if not j.cached]
    # unikalne plastry (ten sam hash może wystąpić wielokrotnie, np. powtórzone ujęcie)
    uniq: dict[str, SliceJob] = {}
    for j in todo:
        uniq.setdefault(j.hash, j)
    log(AGENT, f"Plan renderu {variant.name}/{quality}: {plan.W}x{plan.H} @ {float(F):.3f} fps, "
               f"{plan.duration:.2f} s, plastrów {len(plan.slices)} (do przeliczenia {len(uniq)}, "
               f"z cache {len(plan.slices) - len(todo)})", path="Render", project_id=pid)
    prog.set("plan", 1.0, force=True)
    # tytuły
    titles_file = None
    if any(j.spec.get("titles") for j in plan.slices):
        th = hashlib.sha1(json.dumps([t.model_dump(mode="json") for t in plan.titles], sort_keys=True,
                                     default=str).encode()).hexdigest()[:12]
        titles_file = titles_ass(plan.titles, variant.width, variant.height,
                                 project.cache / "render" / "titles" / f"{variant.name}_{th}.ass")
    # audio równolegle
    audio_state: dict[str, Any] = {"p": 0.0}
    wav = workdir / f"mix_{os.getpid()}_{int(t_start * 1000) % 10_000_000}.wav"
    audio_res: dict[str, Any] = {}

    def _audio_thread():
        try:
            audio_res["report"] = _audio_job(project, tl, wav, T0, T1, cancel, audio_state)
        except BaseException as e:  # noqa: BLE001 - przekazujemy do wątku głównego
            audio_res["error"] = e

    ath = threading.Thread(target=_audio_thread, daemon=True, name="render-audio")
    ath.start()
    # plastry równolegle
    codec_args, pix = presets.chunk_codec_args(quality, F)
    hw = _hw_decode_enabled()
    sessions = max(1, int(get_config().get("render.nvenc_sessions", 3) or 3))
    total_frames = sum(j.n for j in uniq.values()) or 1
    done_frames = {h: 0.0 for h in uniq}
    errors: list[BaseException] = []
    local_cancel = threading.Event()

    def _cancelled() -> bool:
        return (cancel is not None and cancel.is_set()) or local_cancel.is_set()

    class _Either:
        def is_set(self) -> bool:
            return _cancelled()

    either = _Either()

    def _job(j: SliceJob):
        def onp(x: float):
            done_frames[j.hash] = x * j.n
        render_slice(j, plan, pix=pix, codec_args=codec_args, titles_file=titles_file, workdir=workdir,
                     cancel=either, hw_decode=hw, on_progress=onp)  # type: ignore[arg-type]
        done_frames[j.hash] = j.n

    t_sl = time.time()
    if uniq:
        with ThreadPoolExecutor(max_workers=sessions, thread_name_prefix="render-slice") as pool:
            futs: set[Future] = {pool.submit(_job, j) for j in uniq.values()}
            while futs:
                fin, futs = wait(futs, timeout=0.3, return_when=FIRST_COMPLETED)
                for f in fin:
                    exc = f.exception()
                    if exc is not None and not errors:
                        errors.append(exc)
                        local_cancel.set()
                if cancel is not None and cancel.is_set():
                    local_cancel.set()
                prog.set("slices", sum(done_frames.values()) / total_frames)
        if errors:
            local_cancel.set()
            ath.join(timeout=1)
            if cancel is not None and cancel.is_set():
                raise Cancelled("Przerwano")
            raise errors[0]
    _check(cancel)
    slices_s = time.time() - t_sl
    prog.set("slices", 1.0, force=True)
    # concat
    video_full = workdir / f"video_full_{hashlib.sha1(''.join(j.hash for j in plan.slices).encode()).hexdigest()[:12]}.mp4"
    if not video_full.exists():
        concat_slices([j.path for j in plan.slices], video_full, workdir, cancel)  # type: ignore[misc]
    for old in workdir.glob("video_full_*.mp4"):
        if old != video_full:
            _unlink(old)
    prog.set("concat", 1.0, force=True)
    # audio
    while ath.is_alive():
        ath.join(timeout=0.3)
        _check(cancel)
        prog.set("audio_wait", audio_state.get("p", 0.0))
    if "error" in audio_res:
        raise audio_res["error"]
    audio_report = audio_res.get("report")
    if audio_state.get("warn"):
        log(AGENT, audio_state["warn"], path="Render", level="warn", project_id=pid)
    # napisy
    subs, warn = build_subs(project, tl, variant, project.cache / "render" / f"subs_{variant.name}")
    if warn:
        log(AGENT, warn, path="Render", level="warn", project_id=pid)
    burn = bool(subs and tl.subtitles.burn_in and subs.get("ass") and Path(subs["ass"]).exists())
    prog.set("subtitles", 1.0, force=True)
    # krok końcowy
    L = plan.duration
    tmp = out_path.with_name(out_path.stem + ".part" + out_path.suffix)
    ext = out_path.suffix.lower()
    meta_title = settings.get("title") or getattr(getattr(project, "meta", None), "name", "") or "StudioEdit"
    args: list[str] = ["-i", str(video_full), "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0"]
    if quality == "preview":
        if burn:
            args += ["-vf", subs_filter(subs["ass"], T0)]  # type: ignore[index]
            args += presets._no_bframes(ff.video_codec_args("h264_nvenc", cq=26, preset="p2", ten_bit=False,
                                                            fps=float(F)))
        else:
            args += ["-c:v", "copy"]
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        norm = None
    else:
        norm = presets.normalize_settings(settings)
        if ext == ".mp4" and norm["audio_codec"] == "pcm":
            log(AGENT, "PCM w kontenerze MP4 nieobsługiwany - użyto AAC 320k", path="Render", level="warn",
                project_id=pid)
            norm["audio_codec"] = "aac"
        if burn:
            args += ["-vf", subs_filter(subs["ass"], T0)]  # type: ignore[index]
        args += presets.video_encode_args(norm, F)
        args += presets.audio_encode_args(norm, 48000)
        args += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    args += ["-af", "apad", "-t", f"{L:.6f}", "-metadata", f"title={meta_title}",
             "-metadata", "encoder=StudioEdit"]
    if ext in (".mp4", ".mov", ".m4v"):
        args += ["-movflags", "+faststart"]
    args.append(str(tmp))
    t_enc = time.time()
    ff.run(args, duration=L, progress=lambda x: prog.set("encode", x), cancel=cancel)
    os.replace(tmp, out_path)
    encode_s = time.time() - t_enc
    if subs and not burn and subs.get("srt") and Path(subs["srt"]).exists():
        try:
            shutil.copyfile(subs["srt"], out_path.with_suffix(".srt"))
        except OSError:
            pass
    try:
        wav_keep = workdir / f"mix_last_{quality}.wav"
        os.replace(wav, wav_keep)
        if audio_report:
            audio_report["path"] = str(wav_keep)
    except OSError:
        pass
    elapsed = time.time() - t_start
    result = {
        "path": str(out_path), "duration": round(L, 6), "frames": plan.frames, "t0": T0, "t1": T1,
        "slices_total": len(plan.slices), "slices_rendered": len(uniq),
        "slices_cached": len(plan.slices) - len(todo), "audio": audio_report,
        "subtitles": ({**subs, "burned": burn} if subs else None), "elapsed": round(elapsed, 2),
        "timings": {"slices": round(slices_s, 2), "encode": round(encode_s, 2)},
        "variant": variant.name, "quality": quality, "width": plan.W, "height": plan.H, "fps": str(F),
        "settings": norm or {"codec": "h264", "container": ext.lstrip("."), "audio_codec": "aac"},
        "titles_ass": str(titles_file) if titles_file else None, "warnings": plan.warnings,
        "timeline_version": tl.version, "timeline_hash": timeline_hash(tl),
        "windows": plan.windows, "rendered_at": time.time(),
    }
    try:
        _meta_file(project, out_path).write_text(json.dumps({**result, "variant_obj": variant.model_dump(mode="json")},
                                                            ensure_ascii=False, indent=1, default=str),
                                                 encoding="utf-8")
    except OSError:
        pass
    prog.set("encode", 1.0, force=True, path=str(out_path))
    log(AGENT, f"Render {variant.name}/{quality} gotowy: {L:.1f} s materiału w {elapsed:.1f} s "
               f"(plastry {len(uniq)}/{len(plan.slices)} przeliczone) → {out_path.name}",
        path="Render", project_id=pid)
    return result

"""Agent Montażysta Online (Render): podgląd, Final Master, eksport Adobe (Premiere XML, After Effects)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import paths
from ..config import get_config
from ..events import log
from ..render import presets
from ..tasks import Task, TaskContext, orchestrator

AGENT = "render"
RAMP_STEPS = {"low": 4, "medium": 6, "high": 8, "max": 12}


def _name(project) -> str:
    from ..exports import safe_name

    return safe_name(getattr(project.meta, "name", "") or project.id, 50).replace(" ", "_")


def preview_path(project, variant: str) -> Path:
    return project.dir("renders") / f"preview_{variant}.mp4"


def _meta_path(project, variant: str) -> Path:
    return project.dir("renders") / f"preview_{variant}.json"


def preview_info(project, variant: str) -> dict:
    """{url (ścieżka względna w projekcie), version, variant, rendered_at, stale}."""
    from ..render.engine import timeline_hash

    f = preview_path(project, variant)
    mf = _meta_path(project, variant)
    meta = {}
    if mf.exists():
        try:
            meta = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    if not f.exists():
        return {"url": None, "version": None, "variant": variant, "rendered_at": None, "stale": True}
    tl = project.timeline()
    stale = True
    if meta:
        stale = meta.get("timeline_hash") != timeline_hash(tl) or meta.get("version") != tl.version
        if meta.get("t0", 0) > 0.01 or (meta.get("t1") is not None and meta.get("t1") < tl.duration - 0.05):
            stale = True
    return {"url": f"renders/preview_{variant}.mp4", "version": meta.get("version"), "variant": variant,
            "rendered_at": meta.get("rendered_at") or f.stat().st_mtime, "stale": stale,
            "duration": meta.get("duration"), "t0": meta.get("t0"), "t1": meta.get("t1")}


def render_preview_now(project, variant: str, *, progress=None, cancel=None, t0: float = 0.0,
                       t1: float | None = None, settings: dict | None = None, effort: str = "high") -> dict:
    """Render podglądu AKTUALNEJ osi do renders/preview_<variant>.mp4 + meta JSON (wersja osi)."""
    from ..render.engine import render_timeline, timeline_hash

    tl = project.timeline()
    v = presets.resolve_variant(tl, variant)
    s = dict(settings or {})
    s.setdefault("ramp_steps", RAMP_STEPS.get(effort, 8))
    res = render_timeline(project, tl, v, preview_path(project, v.name), quality="preview", t0=t0, t1=t1,
                          progress=progress, cancel=cancel, settings=s)
    meta = {"version": tl.version, "timeline_hash": timeline_hash(tl), "variant": v.name,
            "rendered_at": time.time(), "duration": res["duration"], "t0": res["t0"], "t1": res["t1"],
            "path": res["path"], "slices_total": res["slices_total"], "slices_rendered": res["slices_rendered"]}
    _meta_path(project, v.name).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    res["version"] = tl.version
    return res


def final_path(project, variant: str, settings: dict | None) -> Path:
    d = paths.EXPORTS / project.id
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return d / f"{_name(project)}_{variant}_{stamp}{presets.container_ext(settings)}"


def render_task(project, *, variant: str, quality: str, group: str, deps: list[str], settings: dict | None = None,
                t0: float = 0, t1: float | None = None) -> Task:
    quality = "preview" if quality == "preview" else "final"
    path_name = "Render podglądu" if quality == "preview" else "Render Final Master"
    title = f"{'Podgląd' if quality == 'preview' else 'Final Master'} {variant}"
    pid = project.id

    def fn(ctx: TaskContext):
        from ..render.engine import render_timeline

        ctx.progress(0.0, "Planowanie plastrów")
        s = dict(settings or {})
        s["group"] = group
        s.setdefault("ramp_steps", RAMP_STEPS.get(ctx.effort, 8))
        tl = project.timeline()
        if not any(tl.tracks.get(k) for k in ("V1", "V2", "V3")):
            raise RuntimeError("Oś czasu jest pusta — brak materiału do renderu")
        v = presets.resolve_variant(tl, variant)
        prog = ctx.sub(0.0, 0.99)
        if quality == "preview":
            res = render_preview_now(project, v.name, progress=prog, cancel=ctx.cancel, t0=t0, t1=t1, settings=s,
                                     effort=ctx.effort)
        else:
            out = final_path(project, v.name, s)
            res = render_timeline(project, tl, v, out, quality="final", t0=t0, t1=t1, progress=prog,
                                  cancel=ctx.cancel, settings=s)
            res["version"] = tl.version
            try:
                project.update_meta(status="rendered")
            except Exception:
                pass
        speed = res["duration"] / max(0.01, res["elapsed"])
        ctx.log(f"{title}: {res['duration']:.1f} s materiału w {res['elapsed']:.1f} s ({speed:.1f}× czasu "
                f"rzeczywistego), przeliczone plastry {res['slices_rendered']}/{res['slices_total']} "
                f"(reszta z cache) → {Path(res['path']).name}")
        if res.get("audio") and res["audio"].get("fallback"):
            ctx.log("Audio: użyto prostego miksu zapasowego (bez normalizacji LUFS)", level="warn")
        for w in res.get("warnings", [])[:5]:
            ctx.log(w, level="warn")
        ctx.progress(1.0, "Gotowe")
        return res

    return orchestrator.add(AGENT, path_name, title, fn, project_id=pid, deps=list(deps or []), group=group,
                            priority=4 if quality == "preview" else 5)


def export_tasks(project, *, variants: list[str], settings: dict, group: str, deps: list[str]) -> list[Task]:
    tl = project.timeline()
    names = variants or [v.name for v in tl.variants if v.enabled] or ["youtube"]
    norm = presets.normalize_settings(settings)
    log(AGENT, f"Eksport Final Master: {', '.join(names)} — kodek {norm['codec']}, {norm['container']}, "
               f"audio {norm['audio_codec']}, rozdzielczość {norm['resolution']}", path="Render Final Master",
        project_id=project.id)
    return [render_task(project, variant=n, quality="final", group=group, deps=deps, settings=settings)
            for n in names]


def _adobe_dir(project) -> Path:
    d = paths.EXPORTS / project.id / "adobe"
    d.mkdir(parents=True, exist_ok=True)
    return d


def adobe_tasks(project, *, targets: list[str], group: str) -> list[Task]:
    """premiere → .xml (+ .srt); ae_jsx → .jsx; ae_run → uruchomienie AE (gdy zainstalowany); edl / otio."""
    pid = project.id
    tasks: list[Task] = []
    jsx_task: Task | None = None
    base = _name(project)

    def premiere_fn(ctx: TaskContext):
        from ..exports.premiere import export_premiere_xml, validate_xml

        tl = project.timeline()
        out = export_premiere_xml(project, tl, _adobe_dir(project) / f"{base}.xml")
        rep = validate_xml(out, tl)
        ctx.log(f"Premiere XML (FCP7 xmeml v4): {rep['sequences']} sekwencji, "
                f"{'poprawny' if rep['ok'] else 'uwagi: ' + '; '.join(rep['problems'][:3])} → {out.name}"
                + (" + napisy SRT" if out.with_suffix(".srt").exists() else ""))
        return {"path": str(out), "srt": str(out.with_suffix(".srt")) if out.with_suffix(".srt").exists() else None,
                "validation": rep}

    def jsx_fn(ctx: TaskContext):
        from ..exports.aftereffects import export_ae_jsx

        out = export_ae_jsx(project, project.timeline(), _adobe_dir(project) / f"{base}.jsx")
        ctx.log(f"Skrypt After Effects (ExtendScript ES3) zapisany → {out.name}")
        return {"path": str(out)}

    def run_fn(ctx: TaskContext):
        from ..exports.aftereffects import export_ae_jsx, find_adobe, run_ae

        jsx = _adobe_dir(project) / f"{base}.jsx"
        prev = ctx.result_of(jsx_task.id) if jsx_task is not None else None
        if prev and prev.get("path"):
            jsx = Path(prev["path"])
        else:
            jsx = export_ae_jsx(project, project.timeline(), jsx)
        ad = find_adobe()
        if not ad.get("aftereffects"):
            msg = "After Effects nie jest zainstalowany — skrypt JSX gotowy do ręcznego uruchomienia"
            ctx.log(msg, level="warn")
            return {"ok": False, "message": msg, "jsx": str(jsx)}
        ctx.progress(0.1, "Uruchamianie After Effects")
        res = run_ae(jsx, timeout=float(get_config().get("adobe.ae_timeout_s", 900) or 900))
        ctx.log(res["message"], level="info" if res["ok"] else "warn")
        return {**res, "jsx": str(jsx)}

    def edl_fn(ctx: TaskContext):
        from ..exports.edl import export_edl

        out = export_edl(project, project.timeline(), _adobe_dir(project) / f"{base}.edl")
        ctx.log(f"EDL CMX3600 → {out.name}")
        return {"path": str(out)}

    def otio_fn(ctx: TaskContext):
        from ..exports.edl import export_otio

        out = export_otio(project, project.timeline(), _adobe_dir(project) / f"{base}.otio")
        ctx.log(f"OpenTimelineIO → {out.name}")
        return {"path": str(out)}

    for t in targets or ["premiere", "ae_jsx"]:
        if t == "premiere":
            tasks.append(orchestrator.add(AGENT, "Premiere XML", "Eksport Premiere Pro XML", premiere_fn,
                                          project_id=pid, group=group))
        elif t == "ae_jsx":
            jsx_task = orchestrator.add(AGENT, "After Effects JSX", "Eksport skryptu After Effects", jsx_fn,
                                        project_id=pid, group=group)
            tasks.append(jsx_task)
    for t in targets or []:
        if t == "ae_run":
            tasks.append(orchestrator.add(AGENT, "After Effects runtime", "Uruchomienie After Effects", run_fn,
                                          project_id=pid, group=group, deps=[jsx_task.id] if jsx_task else [],
                                          soft_deps=True))
        elif t == "edl":
            tasks.append(orchestrator.add(AGENT, "Eksport EDL", "Eksport CMX3600 EDL", edl_fn, project_id=pid,
                                          group=group))
        elif t == "otio":
            tasks.append(orchestrator.add(AGENT, "Eksport OTIO", "Eksport OpenTimelineIO", otio_fn, project_id=pid,
                                          group=group))
    return tasks


__all__ = ["render_task", "export_tasks", "adobe_tasks", "preview_info", "render_preview_now"]

"""Pipeline'y wieloagentowe: pełny automatyczny montaż i operacje złożone.

Pełny montaż (POST /montage):
  [Archiwista + Dźwięk: analiza]  (równolegle na wielu wątkach / ścieżkach)
        └─► [Reżyser: dublowanie → plan LLM → Assembly Cut]
                 ├─► [Reżyser: Smart Reframe]
                 ├─► [Kolorysta: analiza → CST → ratowanie → matching → LUT]   (równolegle)
                 └─► [Dźwięk: muzyka, SFX, room tone, miks] ─► [Reżyser: cięcia do bitu]
                                     └──────────────► [Render podglądu (NVENC, przyrostowo)] ─► [QC + auto-poprawki]
"""
from __future__ import annotations

import uuid

from .events import log
from .tasks import Task, orchestrator


def _gid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def deferred(project, *, path: str, title: str, builder, group: str, deps: list[str]) -> Task:
    """Zadanie-koordynator: w chwili wykonania buduje pod-zadania (na AKTUALNEJ osi) i czeka na nie.

    Dzięki temu np. Kolorysta dostaje listę klipów dopiero po zakończeniu montażu."""
    def fn(ctx):
        tasks = builder(ctx.project) or []
        ids = [t.id for t in tasks]
        while not orchestrator.wait(ids, timeout=1.0):
            if ctx.cancel.is_set():
                for i in ids:
                    orchestrator.cancel(task_id=i)
                ctx.check()
            done = sum(1 for i in ids if orchestrator.get(i).status in ("done", "failed", "cancelled", "skipped"))
            ctx.progress(done / max(1, len(ids)), f"{done}/{len(ids)} zadań")
        failed = [orchestrator.get(i) for i in ids if orchestrator.get(i).status == "failed"]
        if ids and len(failed) == len(ids):
            raise RuntimeError(f"Wszystkie zadania nieudane: {failed[0].error}")
        return {"tasks": len(ids), "failed": len(failed)}
    return orchestrator.add("producer", "Delegowanie", title, fn, project_id=project.id, group=group, deps=deps,
                            soft_deps=False, priority=2)


def analyze(project, media_ids: list[str] | None = None, force: bool = False) -> str:
    from .agents import archivist

    group, _tasks = archivist.analysis_pipeline(project, media_ids, force=force)
    return group


def montage(project, opts: dict) -> str:
    """Pełny automatyczny pipeline. opts: brief, target_duration, platforms, style, look, music_mood, subtitles."""
    from .agents import archivist, director

    group = _gid("montage")
    pid = project.id
    project.update_meta(**{k: v for k, v in {"platforms": opts.get("platforms"), "style": opts.get("style"),
                                             "target_duration": opts.get("target_duration"),
                                             "look": opts.get("look")}.items() if v})
    deps: list[str] = []
    pending = [m for m in project.media() if m.bin != "Odrzucone_Duble" and not archivist._fully_analyzed(m)]
    if pending:
        _g, a_tasks = archivist.analysis_pipeline(project, [m.id for m in pending], group=group)
        deps = [a_tasks[-1].id]
    d_tasks = director.montage_tasks(project, opts, group=group, deps=deps)
    t_asm, t_ref = d_tasks[2], d_tasks[3]
    after_edit = [t_ref.id]
    # Kolorysta (równolegle z dźwiękiem)
    look = opts.get("look")

    def build_color(p):
        from .agents import colorist

        tl = p.timeline()
        from .color.looks import look_exists, normalize_look
        from .edit import styles

        cands = [look, tl.look, tl.plan.get("look"), styles.get(opts.get("style") or p.meta.style)["look"]]
        chosen = next((c for c in cands if c and normalize_look(c) and look_exists(c)), None)
        return colorist.auto_grade_tasks(p, tl, look=chosen, group=group, deps=[])

    def build_sound(p):
        from .agents import sound

        tl = p.timeline()
        return sound.post_edit_tasks(p, group=group, deps=[], music=opts.get("music"),
                                     mood=opts.get("music_mood") or tl.plan.get("music_mood"))

    t_color = deferred(project, path="Delegowanie", title="Kolorysta: korekcja i grading całej osi",
                       builder=build_color, group=group, deps=after_edit)
    t_sound = deferred(project, path="Delegowanie", title="Reżyser Dźwięku: muzyka, SFX, room tone, miks",
                       builder=build_sound, group=group, deps=after_edit)
    color_final = [t_color.id]
    sound_final = [t_sound.id]
    t_beat = director.beat_sync_task(project, group=group, deps=sound_final)
    render_deps = [t_beat.id, *color_final]
    plats = opts.get("platforms") or project.meta.platforms or ["youtube"]
    _render_and_qc(project, group=group, deps=render_deps, variant=plats[0])
    log("producer", f"Uruchomiono pełny montaż ({len(orchestrator.list(group=group))} zadań w grafie)",
        path="Delegowanie", project_id=pid)
    return group


def _render_and_qc(project, *, group: str, deps: list[str], variant: str | None, qc: bool = True,
                   t0: float = 0.0, t1: float | None = None) -> list[Task]:
    tasks: list[Task] = []
    try:
        from .agents import render

        v = variant or (project.timeline().variants[0].name if project.timeline().variants else "youtube")
        t_r = render.render_task(project, variant=v, quality="preview", group=group, deps=deps, t0=t0, t1=t1)
        tasks.append(t_r)
        if qc:
            try:
                from .agents import qc as qcmod

                tasks.append(qcmod.qc_task(project, render_task_id=t_r.id, group=group, deps=[t_r.id], auto_fix=True))
            except Exception as e:
                log("qc", f"QC niedostępny: {e}", level="warn", project_id=project.id)
    except Exception as e:
        log("render", f"Render niedostępny: {e}", level="warn", project_id=project.id)
    return tasks


def preview(project, variant: str | None = None, *, deps: list[str] | None = None, group: str | None = None,
            qc: bool = False) -> str:
    group = group or _gid("preview")
    _render_and_qc(project, group=group, deps=deps or [], variant=variant, qc=qc)
    return group


def recolor(project, clip_ids: list[str] | None, look: str | None, group: str | None = None) -> str:
    group = group or _gid("color")

    def build(p):
        from .agents import colorist

        return colorist.auto_grade_tasks(p, p.timeline(), look=look, group=group, deps=[], clip_ids=clip_ids)

    t = deferred(project, path="Delegowanie", title="Kolorysta: ponowny grading", builder=build, group=group, deps=[])
    preview(project, deps=[t.id], group=group)
    return group


def export(project, variants: list[str], settings: dict) -> str:
    from .agents import render

    group = _gid("export")
    render.export_tasks(project, variants=variants, settings=settings, group=group, deps=[])
    return group


def adobe(project, targets: list[str]) -> str:
    from .agents import render

    group = _gid("adobe")
    render.adobe_tasks(project, targets=targets, group=group)
    return group


def run_qc(project) -> str:
    from .agents import qc as qcmod

    group = _gid("qc")
    qcmod.qc_task(project, render_task_id=None, group=group, deps=[], auto_fix=True)
    return group


def reframe_and_preview(project, group: str | None = None) -> str:
    from .agents import director
    from .tasks import orchestrator as orch

    group = group or _gid("reframe")
    t = orch.add("director", "Smart Reframe", "Smart Reframing wariantów", director._reframe_fn(),
                 project_id=project.id, group=group)
    preview(project, deps=[t.id], group=group)
    return group

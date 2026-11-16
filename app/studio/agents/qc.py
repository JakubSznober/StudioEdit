"""Agent Nadzorca Jakości (AI QC Supervisor): QC renderu, automatyczne poprawki, ponowny render i QC."""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..events import bus
from ..tasks import Task, TaskContext, orchestrator

AGENT = "qc"
MAX_ITER = {"low": 0, "medium": 1, "high": 2, "max": 3}


def _save_report(project, report: dict) -> Path:
    d = project.dir("qc")
    report = {**report, "created": time.time()}
    f = d / "last.json"
    f.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (d / f"qc_{time.strftime('%Y%m%d_%H%M%S')}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    bus.publish("qc.report", {"project_id": project.id, "report": report})
    return f


def _latest_preview(project) -> tuple[str | None, str | None]:
    from .render import preview_info, preview_path

    tl = project.timeline()
    best = (None, None, -1.0)
    for v in [x.name for x in tl.variants] or ["youtube"]:
        info = preview_info(project, v)
        if info.get("url") and (info.get("rendered_at") or 0) > best[2]:
            best = (str(preview_path(project, v)), v, float(info.get("rendered_at") or 0))
    return best[0], best[1]


def qc_task(project, *, render_task_id: str | None, group: str, deps: list[str], auto_fix: bool = True) -> Task:
    pid = project.id

    def fn(ctx: TaskContext):
        from ..qc.checks import run_qc
        from ..qc.fixes import apply_fixes, fixable
        from .render import render_preview_now

        res = ctx.result_of(render_task_id) if render_task_id else None
        path, variant, audio = None, None, None
        quality = "preview"
        if isinstance(res, dict) and res.get("path"):
            path, variant, audio = res["path"], res.get("variant"), res.get("audio")
            quality = res.get("quality", "preview")
        if not path or not Path(path).exists():
            path, variant = _latest_preview(project)
        if not path:
            ctx.log("Brak renderu do sprawdzenia — renderuję podgląd", level="warn")
            tl = project.timeline()
            variant = tl.variants[0].name if tl.variants else "youtube"
            r = render_preview_now(project, variant, progress=ctx.sub(0, 0.4), cancel=ctx.cancel, effort=ctx.effort)
            path, audio = r["path"], r.get("audio")
        effort = ctx.effort
        max_iter = MAX_ITER.get(effort, 2) if auto_fix else 0
        ctx.progress(0.45, "Analiza renderu")
        rep = run_qc(project, project.timeline(), Path(path), audio_report=audio, effort=effort)
        ctx.log(f"QC ({Path(path).name}): {rep['summary']}", level="info" if rep["passed"] else "warn")
        it = 0
        history = []
        while it < max_iter:
            todo = fixable(rep["issues"])
            if not todo:
                break
            _probe, dry = apply_fixes(project, project.timeline(), todo)
            if not any(not n.startswith("Poprawka ") and "wymaga decyzji" not in n and "bez zmian" not in n
                       for n in dry):
                ctx.log("Brak poprawek możliwych do wykonania automatycznie" + (f": {'; '.join(dry)}" if dry else ""))
                break
            it += 1
            notes_box: list[list[str]] = []

            def mutate(tl):
                tl2, notes = apply_fixes(project, tl, todo)
                notes_box.append(notes)
                return tl2

            new_tl = project.mutate_timeline(mutate, "QC: automatyczne poprawki", "qc")
            notes = notes_box[0] if notes_box else []
            for n in notes:
                ctx.log(f"Poprawka: {n}")
            history.append({"iteration": it, "fixes": notes, "version": new_tl.version,
                            "score_before": rep["score"]})
            ctx.log(f"Iteracja {it}/{max_iter}: zapisano oś v{new_tl.version}, ponowny render podglądu "
                    f"(tylko zmienione plastry)")
            r = render_preview_now(project, variant or "youtube", progress=ctx.sub(0.5, 0.85), cancel=ctx.cancel,
                                   effort=effort)
            ctx.log(f"Przeliczono {r['slices_rendered']}/{r['slices_total']} plastrów w {r['elapsed']:.1f} s")
            path, audio = r["path"], r.get("audio")
            rep = run_qc(project, project.timeline(), Path(path), audio_report=audio, effort=effort)
            ctx.log(f"QC po poprawkach: {rep['summary']}", level="info" if rep["passed"] else "warn")
            if quality == "final":
                ctx.log("Poprawki wprowadzono na osi — wyeksportuj Final Master ponownie", level="warn")
        rep["auto_fix"] = history
        rep["render_quality"] = quality
        _save_report(project, rep)
        ctx.progress(1.0, "Raport QC gotowy")
        return rep

    return orchestrator.add(AGENT, "Kontrola jakości", "Nadzorca jakości: QC renderu", fn, project_id=pid,
                            deps=list(deps or []), group=group, soft_deps=False, priority=5)

"""Agent 2 - Reżyser Montażu & Narracji (AI Director, AI Montage)."""
from __future__ import annotations

import time
from typing import Any

from ..config import get_config
from ..events import log
from ..edit import assemble as asm
from ..edit import ops, planner, reframe
from ..edit.material import collect
from ..edit.takes import group_takes
from ..tasks import Task, TaskContext, orchestrator

AGENT = "director"


def montage_tasks(project, opts: dict, *, group: str, deps: list[str]) -> list[Task]:
    """Selekcja dubli → plan narracji (LLM) → Assembly Cut (pacing, J/L, ruch kamery, B-roll, przejścia) →
    Smart Reframe. Zwraca listę zadań; ostatnie commituje oś z reframingiem."""
    pid = project.id
    t_takes = orchestrator.add(AGENT, "Selekcja dubli", "Selekcja dubli i materiału", _takes_fn(),
                               project_id=pid, group=group, deps=deps, soft_deps=True, priority=3)
    t_plan = orchestrator.add(AGENT, "Plan narracji (LLM)", "Plan narracji (hook → kulminacja → zakończenie)",
                              _plan_fn(opts, t_takes.id), project_id=pid, group=group, deps=[t_takes.id], priority=3)
    t_asm = orchestrator.add(AGENT, "Niewidzialne cięcia", "Assembly Cut: pacing, J/L-cut, ruch kamery, B-roll",
                             _assemble_fn(opts, t_takes.id, t_plan.id), project_id=pid, group=group,
                             deps=[t_plan.id], priority=3)
    t_ref = orchestrator.add(AGENT, "Smart Reframe", "Smart Reframing wariantów (9:16 / 16:9 / 4:5)", _reframe_fn(),
                             project_id=pid, group=group, deps=[t_asm.id], priority=4)
    return [t_takes, t_plan, t_asm, t_ref]


def beat_sync_task(project, *, group: str, deps: list[str]) -> Task:
    return orchestrator.add(AGENT, "Rytm muzyki", "Cięcia do bitu muzyki", _beat_fn(), project_id=project.id,
                            group=group, deps=deps, soft_deps=True, priority=4)


# ------------------------------------------------------------------------------------------------
def _takes_fn():
    def fn(ctx: TaskContext):
        p = ctx.project
        sentences, shots = collect(p)
        groups = group_takes(sentences)
        n_rep = sum(len(g) - 1 for g in groups.values())
        for gid, members in list(groups.items())[:12]:
            hero = next(m for m in members if m.hero)
            ctx.log(f"Dubel ×{len(members)}: „{hero.text[:60]}…” → HERO_TAKE {hero.id} "
                    f"(płynność {hero.fluency:.2f}, ocena {hero.score:.2f})")
        # zapisz HERO w transkrypcjach
        hero_ids = {u.id: (u.take_group, u.hero) for u in sentences}
        for m in p.media():
            if not m.transcript:
                continue

            def upd(x, ids=hero_ids):
                for s in x.transcript.sentences:
                    if s.id in ids:
                        s.take_group, s.hero = ids[s.id]
            p.update_media(m.id, upd)
        ctx.log(f"Materiał: {len(sentences)} zdań (A-roll), {len(shots)} ujęć/przebitek; {len(groups)} grup dubli, "
                f"odrzucono {n_rep} słabszych powtórzeń")
        return {"sentences": sentences, "shots": shots}
    return fn


def _plan_fn(opts: dict, takes_id: str):
    def fn(ctx: TaskContext):
        p = ctx.project
        data = ctx.result_of(takes_id) or {}
        sentences, shots = data.get("sentences", []), data.get("shots", [])
        if not sentences and not shots:
            raise RuntimeError("Brak przeanalizowanego materiału — zaimportuj nagrania i uruchom analizę")
        cfg = get_config()
        hier = (cfg.get(f"scale_profiles.{p.meta.scale}.director_mode") == "hierarchical")
        ctx.progress(0.05, "LLM układa historię")
        t0 = time.time()
        plan = planner.make_plan(brief=opts.get("brief", ""), style=opts.get("style") or p.meta.style,
                                 platforms=opts.get("platforms") or p.meta.platforms, target=opts.get("target_duration")
                                 or p.meta.target_duration, sentences=sentences, shots=shots, effort=ctx.effort,
                                 hierarchical=hier, project_id=p.id,
                                 media_names={m.id: m.name for m in p.media()})
        plan["_brief"] = opts.get("brief", "")
        ctx.log(f"Plan ({plan.get('_source')}, {time.time() - t0:.0f} s): {planner.plan_to_text(plan)}")
        if plan.get("logline"):
            ctx.log(f"Logline: {plan['logline']}")
        for sec in plan.get("structure", []):
            whys = [it.get("why") for it in sec.get("items", []) if it.get("why")]
            if whys:
                ctx.log(f"{sec['part'].upper()}: {whys[0]}")
        return plan
    return fn


def _assemble_fn(opts: dict, takes_id: str, plan_id: str):
    def fn(ctx: TaskContext):
        p = ctx.project
        data = ctx.result_of(takes_id) or {}
        plan = ctx.result_of(plan_id) or {}
        style = opts.get("style") or p.meta.style
        platforms = opts.get("platforms") or p.meta.platforms or ["youtube"]

        def lg(text, level="info", path="Niewidzialne cięcia"):
            log(AGENT, text, path=path, level=level, project_id=p.id)

        tl = asm.assemble(p, plan, data.get("sentences", []), data.get("shots", []),
                          target=opts.get("target_duration") or p.meta.target_duration, platforms=platforms,
                          style=style, look=opts.get("look"), effort_level=ctx.effort_level, log=lg,
                          subtitles=opts.get("subtitles"))
        if not tl.v("V1"):
            raise RuntimeError("Plan nie dał żadnych klipów — sprawdź materiał lub brief")
        v1, v2 = tl.v("V1"), tl.v("V2")
        jl = sum(1 for c in v1 if "J-cut" in c.reason or "L-cut" in c.reason)
        pin = sum(1 for c in v1 if "punch-in" in c.reason)
        sz = sum(1 for c in v1 if "slow zoom" in c.reason)
        lg(f"Assembly Cut: {len(v1)} cięć na V1, {len(v2)} przebitek, {len(tl.transitions)} przejść, "
           f"długość {tl.duration:.1f} s", path="Niewidzialne cięcia")
        lg(f"J/L-cut: {jl}, punch-in: {pin}, slow zoom: {sz}", path="Ruch kamery")
        slow = sum(1 for c in v2 if c.speed < 1 or c.speed_ramp)
        if slow:
            lg(f"Slow motion / speed ramp na {slow} przebitkach (HFR)", path="Speed ramp")
        for tr in tl.transitions:
            lg(f"Przejście {tr.kind} w {tr.at:.2f} s — {tr.reason}", path="Niewidzialne cięcia")
        p.update_meta(status="edited", look=opts.get("look") or p.meta.look,
                      platforms=platforms, style=style or p.meta.style)
        p.commit_timeline(tl, f"Reżyser: Assembly Cut „{plan.get('title', '')}”", AGENT)
        return {"version": tl.version, "duration": tl.duration, "music_mood": plan.get("music_mood"),
                "look": plan.get("look")}
    return fn


def _reframe_fn():
    def fn(ctx: TaskContext):
        p = ctx.project
        holder: dict[str, Any] = {}

        def mut(tl):
            n = reframe.reframe_timeline(p, tl, effort_level=ctx.effort_level,
                                         log=lambda t, level="info": ctx.log(t, level), progress=ctx.sub(0, 0.95))
            holder["n"] = n
            return tl

        tl = p.timeline()
        need = any(reframe.needs_reframe(m, v) for v in tl.variants for m in p.media() if m.kind == "video")
        if not need:
            ctx.log("Proporcje materiału zgodne z wariantami — reframing niepotrzebny")
            return {"reframed": 0}
        tl = p.mutate_timeline(mut, "Reżyser: Smart Reframe", AGENT)
        ctx.log(f"Smart Reframe: ścieżki kadrowania dla {holder.get('n', 0)} klipów × wariantów "
                f"({', '.join(v.name for v in tl.variants)})")
        return {"reframed": holder.get("n", 0)}
    return fn


def _beat_fn():
    def fn(ctx: TaskContext):
        p = ctx.project
        tl = p.timeline()
        music = tl.a("A3")
        if not music:
            ctx.log("Brak muzyki — pomijam synchronizację z bitem")
            return {"moved": 0}
        beats: list[float] = []
        for mc in music:
            info = None
            if mc.media_id:
                m = p.get_media(mc.media_id)
                info = {"beats": m.analysis.beats, "bpm": m.analysis.bpm}
                path = m.path
            else:
                path = mc.path
            if not info or not info.get("beats"):
                try:
                    from ..audio.music import analyze_music

                    info = analyze_music(path)
                except Exception as e:
                    ctx.log(f"Analiza bitu niedostępna: {e}", level="warn")
                    continue
            for b in info.get("beats", []):
                t = mc.start + (b - mc.src_in)
                if mc.start <= t <= mc.end:
                    beats.append(t)
            if info.get("bpm"):
                ctx.log(f"Muzyka {info['bpm']:.0f} BPM — {len(beats)} uderzeń na osi")
        if not beats:
            return {"moved": 0}

        def mut(t):
            return asm.beat_sync(t, beats, log=lambda x, level="info": ctx.log(x, level))

        p.mutate_timeline(mut, "Reżyser: cięcia do bitu", AGENT)
        return {"beats": len(beats)}
    return fn


# ------------------------------------------------------------------------------------------------
def apply_edit_command(project, tl, t0: float, t1: float, action: str, params: dict) -> tuple[Any, list[str]]:
    """Polecenie montażowe z czatu dla zakresu IN/OUT. Zwraca (oś, opisy zmian) - bez commit."""
    media = {m.id: m for m in project.media()}
    msgs: list[str] = []
    if action == "slow_motion":
        msgs += ops.set_speed(tl, media, t0, t1, float(params.get("factor", 0.5)))
    elif action == "speed":
        msgs += ops.set_speed(tl, media, t0, t1, float(params.get("factor", 1.5)))
    elif action == "speed_ramp":
        msgs += ops.set_speed(tl, media, t0, t1, float(params.get("factor", 0.4)), ramp=True)
    elif action == "punch_in":
        msgs += ops.punch_in(tl, t0, t1, float(params.get("scale", 1.15)))
    elif action == "slow_zoom":
        msgs += ops.slow_zoom(tl, t0, t1, float(params.get("scale", 1.08)), track=params.get("track", "V1"))
    elif action == "cut_out":
        d = ops.ripple_delete(tl, t0, t1)
        msgs.append(f"Wycięto {d:.2f} s ({t0:.2f}–{t1:.2f}) i zamknięto lukę")
    elif action in ("replace_broll", "add_broll"):
        if action == "replace_broll":
            n = ops.remove_broll(tl, t0, t1)
            if n:
                msgs.append(f"Usunięto {n} przebitkę(-ki)")
        got = ops.add_broll(project, tl, t0, t1, params.get("query", ""), shot_size=params.get("shot_size"),
                            slowmo=bool(params.get("slowmo")))
        msgs.append(f"Nowa przebitka → {got}" if got else "Nie znalazłem pasującej przebitki — mogę ją wygenerować (VFX)")
    elif action == "remove_broll":
        msgs.append(f"Usunięto {ops.remove_broll(tl, t0, t1)} przebitek")
    elif action == "transition":
        got = ops.add_transition(tl, t0, t1, params.get("kind", "dissolve"), float(params.get("duration", 0.6)))
        msgs.append(f"Przejście {got}" if got else "Brak cięcia w zaznaczeniu")
    elif action == "title":
        ops.add_title(tl, params.get("text", ""), t0, max(1.5, (t1 - t0) or 3.0), params.get("style", "lower_third"))
        msgs.append(f"Dodano napis „{params.get('text', '')}”")
    elif action == "tighten":
        msgs.append(f"Zacieśniono tempo w {ops.trim_pauses(tl, t0, t1)} miejscach")
    else:
        raise ValueError(f"Nieznana akcja montażowa: {action}")
    return tl, msgs

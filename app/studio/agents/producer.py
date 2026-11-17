"""Agent Producent - AI Chat (Command Center po polsku).

Polecenie użytkownika (+ opcjonalny zakres IN/OUT z osi) → LLM wybiera narzędzia (JSON tool calling) →
Producent deleguje do właściwych agentów (Reżyser, Kolorysta, Dźwięk, VFX, Render, QC) i odpowiada.
Gdy LLM jest niedostępny, działa deterministyczny parser poleceń po polsku.
"""
from __future__ import annotations

import re
import time
from typing import Any

from .. import llm, pipelines
from ..events import log
from ..tasks import TaskContext, orchestrator

AGENT = "producer"

ACTIONS = ["montage", "analyze", "edit", "color", "audio", "tts", "vfx", "subtitles", "variants", "preview", "export",
           "adobe", "qc", "undo", "redo", "none"]

CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"type": {"type": "string", "enum": ACTIONS}, "params": {"type": "object"}},
                "required": ["type", "params"],
            },
        },
    },
    "required": ["reply", "actions"],
}

TOOLS_DOC = """NARZĘDZIA (actions[].type i params):
- montage {brief, target_duration (s), platforms:[youtube|tiktok|reels|shorts|instagram_feed], style:
  vlog|dokument|reklama|teledysk|rolka|podcast|tutorial|gaming, look, music_mood, subtitles:bool} — pełny montaż od zera.
- analyze {} — analiza nowych nagrań.
- edit {action, ...} — montaż w zaznaczonym zakresie (lub całości): action =
  slow_motion{factor 0.25-0.9} | speed{factor} | speed_ramp{factor} | punch_in{scale 1.1-1.3} | slow_zoom{scale} |
  cut_out{} (wytnij zakres) | replace_broll{query, shot_size?: szeroki|średni|zbliżenie|detal, slowmo?} |
  add_broll{query, shot_size?} | remove_broll{} | transition{kind: dissolve|dip_black|dip_white|whip|light_leak|zoom} |
  title{text, style: title|lower_third|caption} | tighten{} (szybsze tempo, mniej pauz)
- color {exposure (EV, np. -1..1), temperature (-1 zimno..1 ciepło), tint, contrast (-1..1), saturation (0..2),
  look: teal_orange|warm_film|clean_doc|moody|bw_film|vibrant_social|pastel, look_strength (0..1),
  sky_darken (0..1), highlight_recovery (0..1), shadow_lift (0..1), relight {direction: left|right|top|front,
  strength 0..1, color: warm|cool|neutral}, scope: range|all}
- audio {gain_db (dialog), music_gain_db, music_mood, mute_music:bool, sfx: whoosh|riser|hit|swoosh|ambience,
  denoise: light|strong, target_lufs (-14 YouTube/social, -23 TV)}
- tts {text, voice?} — lektor AI wstawiany w zaznaczeniu (lub na końcu).
- vfx {kind: stabilize|remove_object|generate_broll|blur_region, prompt (co usunąć / co wygenerować, po polsku)}
- subtitles {enabled, mode: static|karaoke|word_pop, style: modern|classic|bold_social|minimal|cinema,
  uppercase, position: bottom|middle|top, burn_in}
- variants {add:[tiktok|reels|shorts|instagram_feed|youtube], remove:[...]} — wersje na inne platformy (smart reframe).
- preview {variant?} — odśwież podgląd.
- export {variants:[...], codec: h264|hevc|av1|prores|dnxhr, resolution: 1080p|1440p|2160p|source, fps, container:
  mp4|mov|mkv, quality: auto|high|max} — Final Master do C:\\StudioEdit\\Exports.
- adobe {targets:[premiere|ae_jsx|ae_run]} — TYLKO gdy użytkownik wyraźnie prosi o Premiere/After Effects.
- qc {} — kontrola jakości. undo {} / redo {} — cofnij / ponów. none {} — tylko odpowiedź."""

SYSTEM = f"""Jesteś Producentem w lokalnym studiu montażowym AI (StudioEdit). Rozmawiasz po polsku, krótko i konkretnie,
jak doświadczony montażysta. Zamieniasz polecenia użytkownika na akcje narzędzi, które wykonają wyspecjalizowani agenci
(Archiwista, Reżyser Montażu, Kolorysta, Reżyser Dźwięku, Artysta VFX, Montażysta Online, Nadzorca Jakości).
Zasady: jeśli polecenie dotyczy zaznaczonego fragmentu, akcje odnoszą się do niego. Jedno polecenie może dać kilka akcji
(np. slow motion + przyciemnienie nieba + lektor). Nie wymyślaj plików. Eksport do Adobe tylko na wyraźną prośbę.
Gdy brak montażu, a użytkownik prosi o poprawki — zaproponuj montaż (akcja montage). W "reply" napisz, co zlecasz
(1-3 zdania). Odpowiadasz wyłącznie JSON.
{TOOLS_DOC}"""


def fmt_t(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:05.2f}"


def context_text(project, rng: dict | None) -> str:
    media = project.media()
    tl = project.timeline()
    analyzed = sum(1 for m in media if m.analysis.stage.get("probe") == "done")
    lines = [f"PROJEKT: {project.meta.name} | skala {project.meta.scale} | platformy {', '.join(project.meta.platforms)} | "
             f"styl {project.meta.style or '-'}",
             f"MEDIA: {len(media)} plików ({analyzed} przeanalizowanych), biny: " +
             ", ".join(sorted({m.bin for m in media})),
             f"OŚ: wersja v{tl.version}, długość {tl.duration:.1f} s, V1 {len(tl.tracks.get('V1', []))} klipów, "
             f"V2 {len(tl.tracks.get('V2', []))} przebitek, muzyka: {'tak' if tl.tracks.get('A3') else 'nie'}, "
             f"napisy: {'tak' if tl.subtitles.enabled else 'nie'}, look: {tl.look or '-'}, "
             f"warianty: {', '.join(v.name for v in tl.variants)}"]
    if rng:
        t0, t1 = float(rng.get("in", 0)), float(rng.get("out", 0))
        lines.append(f"ZAZNACZONY FRAGMENT: {fmt_t(t0)} – {fmt_t(t1)} ({t1 - t0:.2f} s). Klipy w zaznaczeniu:")
        for k, c in tl.clips_in_range(t0, t1, ("V1", "V2", "V3")):
            desc = getattr(c, "reason", "") or getattr(c, "text", "")
            lines.append(f"  {k} {getattr(c, 'label', '')} [{c.start:.2f}-{c.end:.2f}] {desc[:120]}")
        words = []
        for a in tl.a("A1"):
            if a.end <= t0 or a.start >= t1 or not a.media_id:
                continue
            m = project.get_media(a.media_id)
            if m.transcript:
                for w in m.transcript.words():
                    tt = a.start + (w.s - a.src_in)
                    if a.src_in <= w.s < a.src_out and t0 <= tt <= t1 and not w.filler:
                        words.append(w.w)
        if words:
            lines.append("  Słowa w zaznaczeniu: " + " ".join(words)[:400])
    return "\n".join(lines)


def handle_chat(project, text: str, rng: dict | None) -> dict:
    """Wywoływane z API: zapisuje wiadomość i uruchamia zadanie Producenta (asynchronicznie)."""
    msg = project.add_chat({"role": "user", "text": text, "range": rng})
    group = f"chat-{msg['id'][2:]}"
    orchestrator.add(AGENT, "Rozumienie polecenia", f"Czat: {text[:60]}", _chat_fn(text, rng, group),
                     project_id=project.id, group=group, priority=1)
    return {"message": msg, "group": group}


def _chat_fn(text: str, rng: dict | None, group: str):
    def fn(ctx: TaskContext):
        p = ctx.project
        history = [h for h in p.chat_history()[-9:-1] if h.get("role") in ("user", "assistant")]
        msgs = [{"role": "system", "content": SYSTEM}]
        for h in history:
            msgs.append({"role": h["role"], "content": h.get("text", "")[:800]})
        msgs.append({"role": "user", "content": f"{context_text(p, rng)}\n\nPOLECENIE: {text}"})
        t0 = time.time()
        try:
            res = llm.chat_json(msgs, CHAT_SCHEMA, role=llm.role_for_effort(ctx.effort if ctx.effort != "max" else "high"),
                                agent=AGENT, label="Czat", temperature=0.3, project_id=p.id, timeout=300)
            if not isinstance(res, dict):
                raise llm.LLMError("zła odpowiedź")
            source = "llm"
        except Exception as e:
            ctx.log(f"LLM niedostępny ({e}) — używam parsera poleceń", level="warn")
            res = rule_parse(text, rng)
            source = "rules"
        actions = [a for a in res.get("actions", []) if isinstance(a, dict) and a.get("type") in ACTIONS]
        ctx.log(f"Plan działań ({source}, {time.time() - t0:.1f} s): " +
                (", ".join(a["type"] + (f"/{a.get('params', {}).get('action')}" if a["type"] == "edit" else "")
                           for a in actions) or "tylko odpowiedź"), )
        ctx.task.path = "Delegowanie"
        results = []
        for a in actions:
            try:
                results.append(execute(p, a["type"], a.get("params") or {}, rng, group=group))
            except Exception as e:
                results.append({"type": a["type"], "ok": False, "text": f"Błąd: {e}"})
                ctx.log(f"Akcja {a['type']} nieudana: {e}", level="error")
        reply = (res.get("reply") or "").strip() or "Zrobione."
        detail = [r["text"] for r in results if r.get("text")]
        if detail:
            reply += "\n" + "\n".join(f"• {d}" for d in detail)
        ctx.task.path = "Raport"
        p.add_chat({"role": "assistant", "text": reply, "actions": results, "group": group})
        return {"actions": results}
    return fn


# ------------------------------------------------------------------------------------------------
def _range(project, rng: dict | None) -> tuple[float, float, bool]:
    tl = project.timeline()
    if rng and float(rng.get("out", 0)) > float(rng.get("in", 0)):
        return float(rng["in"]), float(rng["out"]), True
    return 0.0, tl.duration, False


def _commit_and_preview(project, fn, note: str, author: str, group: str, t0: float, t1: float) -> int:
    tl = project.mutate_timeline(fn, note, author)
    pipelines.preview(project, group=group)
    return tl.version


def execute(project, kind: str, params: dict, rng: dict | None, *, group: str) -> dict:
    t0, t1, has_range = _range(project, rng)
    pid = project.id
    if kind == "none":
        return {"type": kind, "ok": True, "text": ""}
    if kind == "montage":
        opts = {k: params.get(k) for k in ("brief", "target_duration", "platforms", "style", "look", "music_mood",
                                            "subtitles", "music")}
        if isinstance(opts.get("platforms"), str):
            opts["platforms"] = [opts["platforms"]]
        g = pipelines.montage(project, opts)
        return {"type": kind, "ok": True, "group": g, "text": "Zespół agentów rozpoczął pełny montaż"}
    if kind == "analyze":
        return {"type": kind, "ok": True, "group": pipelines.analyze(project), "text": "Analiza materiału w toku"}
    if kind == "undo":
        tl = project.undo()
        return {"type": kind, "ok": tl is not None, "text": f"Cofnięto do v{tl.version}" if tl else "Brak wcześniejszej wersji"}
    if kind == "redo":
        tl = project.redo()
        return {"type": kind, "ok": tl is not None, "text": f"Przywrócono v{tl.version}" if tl else "Nic do ponowienia"}
    if not project.timeline().tracks.get("V1") and kind not in ("export", "adobe", "qc"):
        return {"type": kind, "ok": False, "text": "Najpierw potrzebny jest montaż — napisz np. „Zmontuj film na YouTube”"}
    if kind == "edit":
        from . import director

        action = params.get("action", "")
        out: dict[str, Any] = {}

        def mut(tl):
            tl2, msgs = director.apply_edit_command(project, tl, t0, t1, action, params)
            out["msgs"] = msgs
            return tl2
        v = _commit_and_preview(project, mut, f"Czat: {action} {fmt_t(t0)}–{fmt_t(t1)}", "director", group, t0, t1)
        return {"type": kind, "ok": True, "version": v, "text": "; ".join(out.get("msgs", [])) or action}
    if kind == "color":
        from . import colorist

        scope_all = params.get("scope") == "all" or not has_range
        tl = project.timeline()
        ids = [c.id for k, c in tl.clips_in_range(t0, t1, ("V1", "V2"))] if not scope_all else \
            [c.id for c in tl.v("V1") + tl.v("V2")]
        p2 = {k: v for k, v in params.items() if k != "scope"}

        def mut(tl):
            return colorist.apply_color_command(project, tl, ids, p2)
        v = _commit_and_preview(project, mut, f"Czat: kolor ({', '.join(p2)})", "colorist", group, t0, t1)
        return {"type": kind, "ok": True, "version": v, "text": f"Kolorysta: zmieniono {len(ids)} klipów ({', '.join(p2)})"}
    if kind == "audio":
        from . import sound

        def mut(tl):
            return sound.apply_audio_command(project, tl, t0, t1, params)
        v = _commit_and_preview(project, mut, f"Czat: dźwięk ({', '.join(params)})", "sound", group, t0, t1)
        return {"type": kind, "ok": True, "version": v, "text": f"Reżyser Dźwięku: {', '.join(f'{k}={v}' for k, v in params.items())}"}
    if kind == "tts":
        from . import sound

        at = t0 if has_range else project.timeline().duration
        t = sound.tts_task(project, params.get("text", ""), at, group=group)
        pipelines.preview(project, deps=[t.id], group=group)
        return {"type": kind, "ok": True, "group": group, "text": f"Lektor AI nagrywa: „{params.get('text', '')[:60]}”"}
    if kind == "vfx":
        from . import vfx

        tl = project.timeline()
        vk = params.get("kind", "stabilize")
        if vk == "generate_broll":
            clip_id = ""
        else:
            hits = tl.clips_in_range(t0, t1, ("V2", "V1"))
            if not hits:
                return {"type": kind, "ok": False, "text": "Zaznacz fragment z klipem, na którym mam pracować"}
            clip_id = hits[0][1].id
        t = vfx.vfx_task(project, tl, vk, clip_id, t0, t1, params, group=group)
        pipelines.preview(project, deps=[t.id], group=group)
        return {"type": kind, "ok": True, "group": group, "text": f"Artysta VFX: {vk} na {fmt_t(t0)}–{fmt_t(t1)}"}
    if kind == "subtitles":
        def mut(tl):
            for k, v in params.items():
                if hasattr(tl.subtitles, k):
                    setattr(tl.subtitles, k, v)
            if "enabled" not in params:
                tl.subtitles.enabled = True
            return tl
        v = _commit_and_preview(project, mut, "Czat: napisy", "sound", group, t0, t1)
        return {"type": kind, "ok": True, "version": v, "text": "Napisy zaktualizowane"}
    if kind == "variants":
        from ..edit.assemble import make_variant

        def mut(tl):
            names = {v.name for v in tl.variants}
            for n in params.get("add", []) or []:
                if n not in names:
                    tl.variants.append(make_variant(n, tl.fps))
            rm = set(params.get("remove", []) or [])
            if rm and len(tl.variants) > len(rm):
                tl.variants = [v for v in tl.variants if v.name not in rm]
            return tl
        project.mutate_timeline(mut, "Czat: warianty", "director")
        g = pipelines.reframe_and_preview(project, group=group)
        added = ", ".join(params.get("add", []) or [])
        return {"type": kind, "ok": True, "group": g, "text": f"Dodano warianty: {added} (smart reframe w toku)"}
    if kind == "preview":
        return {"type": kind, "ok": True, "group": pipelines.preview(project, params.get("variant"), group=group),
                "text": "Renderuję podgląd"}
    if kind == "export":
        tl = project.timeline()
        variants = params.get("variants") or [v.name for v in tl.variants]
        settings = {k: params[k] for k in ("codec", "resolution", "fps", "container", "audio_codec", "quality") if k in params}
        g = pipelines.export(project, variants, settings)
        return {"type": kind, "ok": True, "group": g, "text": f"Montażysta Online renderuje Final Master: {', '.join(variants)}"}
    if kind == "adobe":
        targets = params.get("targets") or ["premiere", "ae_jsx"]
        return {"type": kind, "ok": True, "group": pipelines.adobe(project, targets),
                "text": f"Eksport awaryjny Adobe: {', '.join(targets)}"}
    if kind == "qc":
        return {"type": kind, "ok": True, "group": pipelines.run_qc(project), "text": "Nadzorca Jakości sprawdza film"}
    raise ValueError(f"Nieznana akcja {kind}")


# ------------------------------------------------------------------------------------------------
LOOK_WORDS = [("teal", "teal_orange"), ("pomarańcz", "teal_orange"), ("ciepł", "warm_film"), ("filmow", "warm_film"),
              ("analog", "warm_film"), ("dokument", "clean_doc"), ("czyst", "clean_doc"), ("mrocz", "moody"),
              ("moody", "moody"), ("czarno", "bw_film"), ("czarno-bia", "bw_film"), ("żyw", "vibrant_social"),
              ("pastel", "pastel"), ("złot", "golden_hour"), ("zachód", "golden_hour"), ("high-key", "high_key"),
              ("jasny i czysty", "high_key"), ("portret", "portrait_soft"), ("natur", "nature_vivid"),
              ("soczyst", "nature_vivid"), ("matow", "matte_fade"), ("fade", "matte_fade"), ("retro", "vintage_70s"),
              ("vintage", "vintage_70s"), ("thriller", "cold_thriller"), ("chłodn", "cold_thriller"),
              ("bleach", "bleach_bypass"), ("neon", "neon_night"), ("noir", "noir"), ("sepi", "sepia")]
PLATFORM_WORDS = [("youtube", "youtube"), ("yt", "youtube"), ("tiktok", "tiktok"), ("tik tok", "tiktok"),
                  ("reels", "reels"), ("rolk", "reels"), ("shorts", "shorts"), ("instagram", "instagram_feed"),
                  ("9:16", "tiktok"), ("pion", "tiktok")]
def _style_words() -> list[tuple[str, str]]:
    from ..edit import styles

    generic = {"film", "short", "youtube"}  # występują w zwykłych poleceniach („zmontuj film”)
    words = [(k, k) for k in styles.STYLES] + [(a, s) for a, s in styles.ALIASES.items() if a not in generic]
    return sorted(words, key=lambda kv: -len(kv[0]))  # dłuższe najpierw („reportaż” przed „report”)


STYLE_WORDS = _style_words()


def _duration(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(min|minut|m\b|sek|s\b|sekund)", text)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v * 60 if m.group(2).startswith("m") else v


def rule_parse(text: str, rng: dict | None) -> dict:
    t = text.lower()
    acts: list[dict] = []
    look = next((v for k, v in sorted(LOOK_WORDS, key=lambda kv: -len(kv[0])) if k in t), None)
    plats = []
    for k, v in PLATFORM_WORDS:
        if k in t and v not in plats:
            plats.append(v)
    if any(w in t for w in ("zmontuj", "zrób film", "zrob film", "montaż od", "zrób montaż", "złóż film")):
        acts.append({"type": "montage", "params": {"brief": text, "target_duration": _duration(t), "platforms": plats or None,
                                                   "style": next((v for k, v in STYLE_WORDS if k in t), None), "look": look,
                                                   "subtitles": None if "napis" not in t else True}})
        return {"reply": "Przekazuję materiał zespołowi — zaczynamy montaż.", "actions": acts}
    if "cofnij" in t:
        acts.append({"type": "undo", "params": {}})
    if "ponów" in t or "przywróć" in t:
        acts.append({"type": "redo", "params": {}})
    if "slow" in t or "zwolni" in t or "spowoln" in t:
        acts.append({"type": "edit", "params": {"action": "speed_ramp" if "ramp" in t else "slow_motion", "factor": 0.5}})
    if "przyspiesz" in t:
        acts.append({"type": "edit", "params": {"action": "speed", "factor": 1.5}})
    if "punch" in t or "najazd" in t or "przybliż" in t:
        acts.append({"type": "edit", "params": {"action": "punch_in", "scale": 1.15}})
    if ("wytnij" in t or "usuń ten fragment" in t or "usun ten fragment" in t) and "obiekt" not in t:
        acts.append({"type": "edit", "params": {"action": "cut_out"}})
    if "przebitk" in t or "b-roll" in t or "broll" in t:
        if "wygeneruj" in t or "wygenerować" in t:
            acts.append({"type": "vfx", "params": {"kind": "generate_broll", "prompt": text}})
        else:
            size = next((s for s in ("zbliżenie", "detal", "szeroki", "średni") if s[:5] in t), None)
            acts.append({"type": "edit", "params": {"action": "replace_broll" if ("zmień" in t or "zamień" in t) else "add_broll",
                                                    "query": text, "shot_size": size, "slowmo": "slow" in t}})
    if any(w in t for w in ("przejści", "przenikan", "dissolve")):
        acts.append({"type": "edit", "params": {"action": "transition",
                                                "kind": "dip_black" if "czarn" in t else "whip" if "whip" in t else "dissolve"}})
    color: dict[str, Any] = {}
    if "niebo" in t and ("przyciem" in t or "ciemn" in t):
        color["sky_darken"] = 0.6
    if "rozjaśnij" in t or "jaśniej" in t:
        color["exposure"] = 0.5
    if "przyciemnij" in t and "niebo" not in t:
        color["exposure"] = -0.4
    if "cieplej" in t or "ciepł" in t and "kolor" in t:
        color["temperature"] = 0.3
    if "zimniej" in t or "chłodniej" in t:
        color["temperature"] = -0.3
    if "przeświet" in t or "uratuj" in t:
        color["highlight_recovery"] = 0.8
    if "doświetl" in t or "relight" in t:
        color["relight"] = {"direction": "left" if "lew" in t else "right" if "praw" in t else "front", "strength": 0.5,
                            "color": "warm"}
    if look and "zmontuj" not in t:
        color["look"] = look
    if color:
        color["scope"] = "range" if rng else "all"
        acts.append({"type": "color", "params": color})
    if "stabiliz" in t:
        acts.append({"type": "vfx", "params": {"kind": "stabilize"}})
    if "usuń" in t and any(w in t for w in ("obiekt", "kabel", "statyw", "mikrofon", "osob", "przechodni", "logo")):
        acts.append({"type": "vfx", "params": {"kind": "remove_object", "prompt": text}})
    if "rozmyj" in t or "zamaż" in t:
        acts.append({"type": "vfx", "params": {"kind": "blur_region", "prompt": text}})
    m = re.search(r"(lektor|narracj|powiedz)[^:]*:\s*(.+)", text, re.I | re.S)
    if m:
        acts.append({"type": "tts", "params": {"text": m.group(2).strip().strip('"„”')}})
    if "napis" in t:
        acts.append({"type": "subtitles", "params": {"enabled": "bez napis" not in t and "usuń napis" not in t}})
    if plats and any(w in t for w in ("wersj", "dodaj", "zrób też", "pionow")):
        acts.append({"type": "variants", "params": {"add": plats}})
    if "głośniej" in t:
        acts.append({"type": "audio", "params": {"gain_db": 3}})
    if "ciszej" in t and "muzyk" in t:
        acts.append({"type": "audio", "params": {"music_gain_db": -4}})
    if "bez muzyki" in t or "wycisz muzyk" in t:
        acts.append({"type": "audio", "params": {"mute_music": True}})
    if "-23" in t or "telewiz" in t:
        acts.append({"type": "audio", "params": {"target_lufs": -23}})
    if "premiere" in t or "after effects" in t:
        tg = []
        if "premiere" in t:
            tg.append("premiere")
        if "after effects" in t:
            tg += ["ae_jsx"] + (["ae_run"] if "uruchom" in t or "otwórz" in t else [])
        acts.append({"type": "adobe", "params": {"targets": tg}})
    elif "eksport" in t or "wyrenderuj" in t or "final" in t:
        acts.append({"type": "export", "params": {"variants": plats or None}})
    if "jakoś" in t or " qc" in t or t.startswith("qc") or "sprawdź" in t:
        acts.append({"type": "qc", "params": {}})
    if "podgląd" in t or "odśwież" in t:
        acts.append({"type": "preview", "params": {}})
    reply = "Zlecam: " + ", ".join(a["type"] for a in acts) if acts else \
        "Nie jestem pewien, co zrobić. Spróbuj np. „Zmontuj dynamiczny film na YouTube, max 3 min” albo zaznacz " \
        "fragment i napisz „zrób tu slow motion”."
    return {"reply": reply, "actions": acts}

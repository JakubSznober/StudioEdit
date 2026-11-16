"""Plan narracji (Assembly Cut) przez lokalny LLM reasoning - z trybem hierarchicznym dla dużych projektów
i heurystycznym fallbackiem, gdy model jest niedostępny."""
from __future__ import annotations

import json
from typing import Any

from .. import llm
from ..events import log
from .material import SentenceUnit, ShotUnit

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "pace": {"type": "string", "enum": ["spokojne", "umiarkowane", "dynamiczne", "bardzo dynamiczne"]},
        "music_mood": {"type": "string"},
        "music_bpm": {"type": "number"},
        "look": {"type": "string"},
        "structure": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "part": {"type": "string", "enum": ["hook", "rozwinięcie", "kulminacja", "zakończenie"]},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "why": {"type": "string"},
                                "emphasis": {"type": "array", "items": {"type": "string"}},
                                "broll": {"type": "string"},
                            },
                            "required": ["id"],
                        },
                    },
                },
                "required": ["part", "items"],
            },
        },
        "transitions": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "after": {"type": "string"},
                "kind": {"type": "string", "enum": ["dissolve", "dip_black", "dip_white", "whip", "light_leak",
                                                    "zoom"]},
                "why": {"type": "string"}}, "required": ["after", "kind"]},
        },
        "title_card": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["title", "pace", "music_mood", "structure"],
}

SELECT_SCHEMA = {
    "type": "object",
    "properties": {"keep": {"type": "array", "items": {"type": "string"}},
                   "summary": {"type": "string"}},
    "required": ["keep", "summary"],
}

SYSTEM = """Jesteś wybitnym montażystą i reżyserem montażu (dokument, YouTube, reklama, social media).
Budujesz historię WYŁĄCZNIE z dostarczonego materiału (identyfikatory zdań i ujęć). Zasady:
- Struktura: HOOK (najmocniejszy, intrygujący moment w pierwszych 3 s) → ROZWINIĘCIE (logiczny ciąg) →
  KULMINACJA (najważniejsza puenta / emocja) → ZAKOŃCZENIE (domknięcie, wezwanie do działania jeśli pasuje).
- Wybieraj najlepsze duble (HERO), pomijaj powtórzenia, przejęzyczenia i wątki niepasujące do briefu.
- Zachowaj sens wypowiedzi - nie sklejaj zdań w sposób zmieniający znaczenie.
- Trzymaj się docelowej długości (suma czasów wybranych elementów).
- Przy zdaniach możesz podać "broll": krótkie zapytanie (po polsku), jaką przebitkę nałożyć,
  i "emphasis": 1-2 kluczowe słowa z tego zdania do podkreślenia punch-inem.
- Przejścia wizualne tylko przy zmianie miejsca/czasu/nastroju (domyślnie czyste cięcia).
- Odpowiadasz wyłącznie JSON zgodnym ze schematem. Teksty po polsku."""


def _fmt_sentences(units: list[SentenceUnit]) -> str:
    lines = []
    for u in units:
        tag = "" if u.hero else " [słabszy dubel]"
        vis = f" | kadr: {u.shot.shot_size}" if u.shot and u.shot.shot_size else ""
        lines.append(f"{u.id} ({u.dur:.1f}s, {u.spk or 'mówca'}, płynność {u.fluency:.2f}{vis}){tag}: {u.text}")
    return "\n".join(lines)


def _fmt_shots(shots: list[ShotUnit], limit: int = 120) -> str:
    best = sorted(shots, key=lambda s: -s.shot.score)[:limit]
    best.sort(key=lambda s: (s.media_id, s.s))
    lines = []
    last_parent = None
    for s in best:
        sh = s.shot
        extra = " HFR" if s.hfr else ""
        if s.id.startswith(f"{sh.id}_w") and last_parent == sh.id:
            # kolejne okno tego samego ujęcia - bez powtarzania opisu
            lines.append(f"{s.id} ({s.dur:.1f}s, {s.s:.1f}-{s.e:.1f}s tego samego ujęcia)")
            continue
        last_parent = sh.id
        lines.append(f"{s.id} ({s.dur:.1f}s, {sh.shot_size or '?'}, {sh.camera_move or '?'}, ocena {sh.score:.2f}{extra}): "
                     f"{sh.description or ', '.join(sh.tags)}" + (f" | akcja: {sh.action}" if sh.action else ""))
    return "\n".join(lines)


def make_plan(*, brief: str, style: str, platforms: list[str], target: float | None, sentences: list[SentenceUnit],
              shots: list[ShotUnit], effort: str, hierarchical: bool, project_id: str | None = None,
              on_token=None) -> dict:
    role = llm.role_for_effort(effort)
    usable = [u for u in sentences if u.hero]
    if hierarchical and len(usable) > 160:
        usable = _map_select(usable, brief=brief, target=target, role=role, project_id=project_id)
    target_txt = f"{target:.0f} s" if target else "dobierz sam (zwięźle, bez dłużyzn)"
    if usable:
        material = f"ZDANIA (A-roll, id: tekst):\n{_fmt_sentences(usable)}\n\nPRZEBITKI (B-roll):\n{_fmt_shots(shots)}"
        rules = ("Każdy element structure.items to id ZDANIA. Ujęcia B-roll przypisuj przez pole 'broll' "
                 "(opis) - system dobierze konkretne ujęcie.")
    else:
        material = f"UJĘCIA (brak mowy - montaż obrazowy/teledysk):\n{_fmt_shots(shots, limit=200)}"
        rules = "Każdy element structure.items to id UJĘCIA. Układaj dynamicznie, przeplataj plany (szeroki/średni/detal)."
    user = f"""BRIEF: {brief or 'Zmontuj najlepszy możliwy film z tego materiału.'}
STYL: {style or 'dopasuj do materiału'} | PLATFORMY: {', '.join(platforms) or 'youtube'} | DOCELOWA DŁUGOŚĆ: {target_txt}
{rules}

{material}"""
    try:
        plan = llm.chat_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                             PLAN_SCHEMA, role=role, agent="director", label="Plan narracji", temperature=0.5,
                             project_id=project_id, on_token=on_token)
        if not isinstance(plan, dict) or not plan.get("structure"):
            raise llm.LLMError("pusty plan")
        plan["_source"] = "llm"
        return plan
    except Exception as e:
        log("director", f"LLM niedostępny lub błędny plan ({e}) — buduję plan heurystyczny", path="Plan narracji (LLM)",
            level="warn", project_id=project_id)
        return heuristic_plan(sentences=sentences, shots=shots, target=target, style=style)


def _map_select(units: list[SentenceUnit], *, brief: str, target: float | None, role: str,
                project_id: str | None) -> list[SentenceUnit]:
    """Tryb hierarchiczny (masywne projekty): wstępna selekcja w paczkach, potem plan globalny."""
    keep_ids: set[str] = set()
    chunk = 120
    per_chunk_budget = (target or 600) * 2.5 / max(1, len(units) / chunk)
    for i in range(0, len(units), chunk):
        part = units[i:i + chunk]
        user = (f"BRIEF: {brief}\nZ poniższych zdań wybierz najważniejsze dla historii (łącznie ok. "
                f"{per_chunk_budget:.0f} s). Zwróć ich id w 'keep' i jednozdaniowe podsumowanie wątku.\n\n"
                f"{_fmt_sentences(part)}")
        try:
            res = llm.chat_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                SELECT_SCHEMA, role=role, agent="director", label="Selekcja hierarchiczna",
                                temperature=0.3, project_id=project_id)
            ids = set(res.get("keep", []))
        except Exception:
            ids = {u.id for u in sorted(part, key=lambda u: -u.score)[:max(5, len(part) // 4)]}
        keep_ids |= ids
        log("director", f"Selekcja hierarchiczna: paczka {i // chunk + 1} → {len(ids)} zdań", path="Plan narracji (LLM)",
            project_id=project_id)
    return [u for u in units if u.id in keep_ids]


def heuristic_plan(*, sentences: list[SentenceUnit], shots: list[ShotUnit], target: float | None,
                   style: str) -> dict:
    heroes = [u for u in sentences if u.hero]
    structure: list[dict[str, Any]] = []
    if heroes:
        budget = target or sum(u.dur for u in heroes)
        hook = max(heroes[: max(3, len(heroes) // 3)] or heroes, key=lambda u: u.score / max(1.0, u.dur / 6))
        body = [u for u in heroes if u is not hook]
        chosen, total = [], hook.dur
        for u in sorted(body, key=lambda u: -u.score):
            if total + u.dur <= budget * 1.05:
                chosen.append(u)
                total += u.dur
        chosen.sort(key=lambda u: u.order)
        climax = chosen[-2:-1] if len(chosen) >= 3 else []
        ending = chosen[-1:] if chosen else []
        middle = [u for u in chosen if u not in climax and u not in ending]
        structure = [{"part": "hook", "items": [{"id": hook.id, "why": "najmocniejsze zdanie na start"}]},
                     {"part": "rozwinięcie", "items": [{"id": u.id} for u in middle]},
                     {"part": "kulminacja", "items": [{"id": u.id} for u in climax]},
                     {"part": "zakończenie", "items": [{"id": u.id} for u in ending]}]
    else:
        budget = target or min(60.0, sum(s.dur for s in shots))
        ranked = sorted(shots, key=lambda s: -s.shot.score)
        chosen, total = [], 0.0
        for s in ranked:
            d = min(s.dur, 4.0)
            if total + d <= budget:
                chosen.append(s)
                total += d
        chosen.sort(key=lambda s: (s.media_id, s.s))
        if chosen:
            best = max(chosen, key=lambda s: s.shot.score)
            rest = [s for s in chosen if s is not best]
            structure = [{"part": "hook", "items": [{"id": best.id}]},
                         {"part": "rozwinięcie", "items": [{"id": s.id} for s in rest[:-1]]},
                         {"part": "zakończenie", "items": [{"id": s.id} for s in rest[-1:]]}]
    return {"title": "Montaż", "pace": "dynamiczne" if style in ("rolka", "teledysk", "reklama", "gaming") else "umiarkowane",
            "music_mood": "energetyczna" if style in ("rolka", "teledysk", "reklama", "gaming") else "lekka inspirująca",
            "structure": structure, "transitions": [], "_source": "heuristic"}


def plan_to_text(plan: dict) -> str:
    parts = []
    for sec in plan.get("structure", []):
        parts.append(f"{sec.get('part')}: {len(sec.get('items', []))} el.")
    return f"„{plan.get('title', '')}” — tempo {plan.get('pace', '?')}, muzyka: {plan.get('music_mood', '?')}; " + \
        ", ".join(parts)


def dump(plan: dict) -> str:
    return json.dumps(plan, ensure_ascii=False)[:4000]

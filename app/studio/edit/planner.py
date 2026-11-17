"""Plan narracji (Assembly Cut) przez lokalny LLM reasoning - z trybem hierarchicznym dla dużych projektów
i heurystycznym fallbackiem, gdy model jest niedostępny."""
from __future__ import annotations

import json
import time
from typing import Any

from .. import llm
from ..events import log
from . import styles
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
- NIGDY nie bierz dwóch wersji tej samej kwestii (to samo powiedziane innymi słowami w różnych plikach to
  dubel - wybierz jedną, najpełniejszą i najpłynniejszą). Pomijaj komentarze z planu („jeszcze raz”, „źle to
  brzmi”, „stop” rzucone do ekipy), przekleństwa i urwane zdania.
- Kontekst przed reakcją: jeśli zdanie na coś odpowiada („Stop, sprawdźmy to”, „Dokładnie, czyli…”), scena,
  na którą reaguje, MUSI być w filmie wcześniej. Scenki z dialogiem kilku osób zachowuj jako ciąg wymiany zdań.
- Numer #N przy zdaniu to kolejność nagrywania - autor zwykle nagrywa w kolejności scenariusza. Domyślnie
  zachowuj tę kolejność; zmieniaj ją tylko dla mocnego hooka.
- Zachowaj sens wypowiedzi - nie sklejaj zdań w sposób zmieniający znaczenie.
- Trzymaj się docelowej długości (suma czasów wybranych elementów). Gdy materiału jest za dużo, skracaj
  w obrębie scen (wybierz kluczowe kwestie), ale nie wycinaj całych scen potrzebnych do zrozumienia historii.
- Przy zdaniach możesz podać "broll": krótkie zapytanie (po polsku), jaką przebitkę nałożyć,
  i "emphasis": 1-2 kluczowe słowa z tego zdania do podkreślenia punch-inem.
- Przejścia wizualne tylko przy zmianie miejsca/czasu/nastroju (domyślnie czyste cięcia).
- Odpowiadasz wyłącznie JSON zgodnym ze schematem. Teksty po polsku."""


def _fmt_sentences(units: list[SentenceUnit], names: dict[str, str] | None = None) -> str:
    lines = []
    for u in units:
        tag = "" if u.hero else " [słabszy dubel]"
        vis = f" | kadr: {u.shot.shot_size}" if u.shot and u.shot.shot_size else ""
        src = f", plik {names[u.media_id]}" if names and u.media_id in names else ""
        lines.append(f"{u.id} [#{u.order}{src}] ({u.dur:.1f}s, {u.spk or 'mówca'}, płynność {u.fluency:.2f}{vis}){tag}: "
                     f"{u.text}")
    return "\n".join(lines)


SHORT_PLAN_RATIO = 0.75


def plan_duration(plan: dict, units: list[SentenceUnit]) -> float:
    by_id = {u.id: u for u in units}
    return sum(by_id[it["id"]].dur for sec in plan.get("structure", []) for it in sec.get("items", [])
               if it.get("id") in by_id)


def _refine_if_short(plan: dict, messages: list[dict], usable: list[SentenceUnit], *, target: float | None,
                     role: str, project_id: str | None) -> dict:
    """Model czasem tnie za mocno (np. 29 s przy celu 45 s i gubi puentę) - jedna prośba o uzupełnienie."""
    if not target or not usable:
        return plan
    have = plan_duration(plan, usable)
    available = sum(u.dur for u in usable)
    if have >= SHORT_PLAN_RATIO * target or available < 0.9 * target:
        return plan
    log("director", f"Plan trwa ok. {have:.0f} s przy celu {target:.0f} s - proszę model o uzupełnienie historii",
        path="Plan narracji (LLM)", project_id=project_id)
    follow = messages + [
        {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
        {"role": "user", "content": (f"Ten plan trwa ok. {have:.0f} s, a film ma trwać ok. {target:.0f} s. Dodaj "
                                     "brakujące elementy historii z listy zdań (szczególnie wyjaśnienie / puentę, "
                                     "na którą reagują inne zdania), zachowując zasady i kolejność. Zwróć pełny plan.")},
    ]
    try:
        better = _llm_plan(follow, role=role, project_id=project_id, on_token=None)
    except Exception as e:  # noqa: BLE001
        log("director", f"Uzupełnienie planu nieudane ({e}) - zostaje krótszy plan", path="Plan narracji (LLM)",
            level="warn", project_id=project_id)
        return plan
    return better if plan_duration(better, usable) > have else plan


PLAN_IDLE_TIMEOUT_S = 180.0   # cisza ze strony modelu (brak kolejnego fragmentu odpowiedzi)
PLAN_DEADLINE_S = {"director_max": 600.0}  # łączny limit próby; domyślnie DEFAULT_PLAN_DEADLINE_S
DEFAULT_PLAN_DEADLINE_S = 300.0
PLAN_MAX_TOKENS = 4096
FAST_ROLE = "director"


def _deadline(limit: float, outer):
    """Callback strumienia: przerywa generowanie po przekroczeniu łącznego limitu czasu."""
    t0 = time.time()

    def cb(tok: str) -> None:
        if outer:
            outer(tok)
        if time.time() - t0 > limit:
            raise llm.LLMError(f"przekroczono {limit:.0f} s")
    return cb


def _llm_plan(messages: list[dict], *, role: str, project_id: str | None, on_token) -> dict:
    """Plan z modelu: strumieniowo, z limitem długości odpowiedzi i łącznym limitem czasu, jedna ponowna próba.

    - Bez `num_predict` model z wymuszonym schematem JSON potrafi generować bez końca (pętla białych znaków),
      co wcześniej blokowało montaż na 15 min.
    - Strumień: limit dotyczy ciszy, a nie wolnego (ale postępującego) generowania gęstego modelu w trybie MAX.
    - Ponowna próba idzie do szybszego modelu, jeśli pierwsza używała wolniejszego.
    """
    last: Exception | None = None
    attempts = [(role, 0.5), (FAST_ROLE if role != FAST_ROLE else role, 0.2)]
    for n, (r, temp) in enumerate(attempts):
        t0 = time.time()
        limit = PLAN_DEADLINE_S.get(r, DEFAULT_PLAN_DEADLINE_S)
        try:
            plan = llm.chat_json(messages, PLAN_SCHEMA, role=r, agent="director", label="Plan narracji",
                                 temperature=temp, num_predict=PLAN_MAX_TOKENS, timeout=PLAN_IDLE_TIMEOUT_S,
                                 think=False, project_id=project_id, on_token=_deadline(limit, on_token))
            if not isinstance(plan, dict) or not plan.get("structure"):
                raise llm.LLMError("pusty plan")
            return plan
        except Exception as e:  # noqa: BLE001
            last = e
            if n == 0:
                nxt = attempts[1][0]
                log("director", f"Plan z modelu nieudany po {time.time() - t0:.0f} s ({e}) - ponawiam"
                    + (" szybszym modelem" if nxt != r else ""), path="Plan narracji (LLM)", level="warn",
                    project_id=project_id)
    raise last if last else llm.LLMError("brak planu")


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
              on_token=None, media_names: dict[str, str] | None = None) -> dict:
    role = llm.role_for_effort(effort)
    usable = [u for u in sentences if u.hero]
    if hierarchical and len(usable) > 160:
        usable = _map_select(usable, brief=brief, target=target, role=role, project_id=project_id)
    target_txt = f"{target:.0f} s" if target else "dobierz sam (zwięźle, bez dłużyzn)"
    if usable:
        material = (f"ZDANIA (A-roll, id [#kolejność nagrania, plik]: tekst):\n{_fmt_sentences(usable, media_names)}"
                    f"\n\nPRZEBITKI (B-roll):\n{_fmt_shots(shots)}")
        rules = ("Każdy element structure.items to id ZDANIA. Ujęcia B-roll przypisuj przez pole 'broll' "
                 "(opis) - system dobierze konkretne ujęcie.")
    else:
        material = f"UJĘCIA (brak mowy - montaż obrazowy/teledysk):\n{_fmt_shots(shots, limit=200)}"
        rules = "Każdy element structure.items to id UJĘCIA. Układaj dynamicznie, przeplataj plany (szeroki/średni/detal)."
    st = styles.get(style)
    user = f"""BRIEF: {brief or 'Zmontuj najlepszy możliwy film z tego materiału.'}
STYL: {st['label']} - {st['description']} | TEMPO: {st['pace']} | PLATFORMY: {', '.join(platforms) or 'youtube'} | DOCELOWA DŁUGOŚĆ: {target_txt}
WSKAZÓWKI STYLU: {st['director']}
{rules}

{material}"""
    try:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        plan = _llm_plan(messages, role=role, project_id=project_id, on_token=on_token)
        plan = _refine_if_short(plan, messages, usable, target=target, role=role, project_id=project_id)
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
        # bez modelu nie rozumiemy historii - najbezpieczniej trzymać kolejność nagrywania (scenariusz)
        hook = min(heroes, key=lambda u: u.order)
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
    st = styles.get(style)
    return {"title": "Montaż", "pace": st["pace"], "music_mood": st["music_mood"], "structure": structure,
            "transitions": [], "_source": "heuristic"}


def plan_to_text(plan: dict) -> str:
    parts = []
    for sec in plan.get("structure", []):
        parts.append(f"{sec.get('part')}: {len(sec.get('items', []))} el.")
    return f"„{plan.get('title', '')}” — tempo {plan.get('pace', '?')}, muzyka: {plan.get('music_mood', '?')}; " + \
        ", ".join(parts)


def dump(plan: dict) -> str:
    return json.dumps(plan, ensure_ascii=False)[:4000]

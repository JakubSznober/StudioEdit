"""Analiza wizyjna ujęć przez lokalny Video-VLM (Ollama, Qwen3-VL) - opis po polsku w strukturze JSON."""
from __future__ import annotations

from ..llm import LLMError, vision
from ..project.model import Shot

SHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "shot_size": {"type": "string", "enum": ["bardzo szeroki", "szeroki", "średni", "plan amerykański",
                                                  "zbliżenie", "detal"]},
        "camera_move": {"type": "string", "enum": ["statyczny", "panorama", "jazda", "z ręki", "drgania",
                                                    "zoom", "dron", "gimbal"]},
        "description": {"type": "string"},
        "action": {"type": "string"},
        "people": {"type": "integer"},
        "emotion": {"type": "string"},
        "looking_at_camera": {"type": "boolean"},
        "smiling": {"type": "boolean"},
        "location": {"type": "string"},
        "time_of_day": {"type": "string"},
        "colors": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "visual_score": {"type": "number"},
        "is_broll": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["shot_size", "camera_move", "description", "action", "people", "emotion", "colors", "tags",
                 "visual_score", "is_broll", "location"],
}

PROMPT = """Jesteś doświadczonym montażystą filmowym. Oglądasz {n} klatek z JEDNEGO ujęcia wideo
(w kolejności czasowej, ujęcie trwa {dur:.1f} s). Opisz je po polsku jako notatkę montażową:
- shot_size: plan filmowy; camera_move: ruch kamery (porównaj klatki);
- description: 1-2 zdania co widać (konkretnie: kto, co, gdzie);
- action: co się dzieje / zmienia między klatkami;
- people: liczba osób; emotion: dominująca emocja (lub "brak");
- looking_at_camera / smiling: czy główna postać patrzy w obiektyw / uśmiecha się;
- location, time_of_day; colors: 2-4 dominujące kolory; tags: 3-8 słów kluczowych do wyszukiwania przebitek;
- visual_score: 0-1 atrakcyjność i użyteczność ujęcia w montażu;
- is_broll: true jeśli to przebitka (bez mówiącej do kamery osoby);
- problems: np. "prześwietlone niebo", "nieostre", "trzęsie się", "zasłonięty obiektyw" (pusta lista gdy brak).
Odpowiedz wyłącznie JSON."""


def describe_shot(shot: Shot, keyframes: list[str], *, project_id: str | None = None) -> dict:
    if not keyframes:
        return {}
    try:
        res = vision(PROMPT.format(n=len(keyframes), dur=shot.e - shot.s), keyframes, schema=SHOT_SCHEMA,
                     agent="archivist", label="Video-VLM", project_id=project_id)
    except LLMError:
        raise
    if not isinstance(res, dict):
        return {}
    return res


def apply_description(shot: Shot, d: dict) -> Shot:
    if not d:
        return shot
    shot.shot_size = str(d.get("shot_size", shot.shot_size) or "")
    shot.camera_move = str(d.get("camera_move", shot.camera_move) or "")
    shot.description = str(d.get("description", "") or "")
    shot.action = str(d.get("action", "") or "")
    try:
        shot.people = int(d.get("people", shot.people) or 0)
    except (TypeError, ValueError):
        pass
    shot.emotion = str(d.get("emotion", "") or "")
    shot.colors = [str(c) for c in (d.get("colors") or [])][:5]
    tags = [str(t) for t in (d.get("tags") or [])][:10]
    if d.get("location"):
        tags.append(f"miejsce:{d['location']}")
    if d.get("time_of_day"):
        tags.append(f"pora:{d['time_of_day']}")
    if d.get("looking_at_camera"):
        tags.append("patrzy_w_obiektyw")
    if d.get("smiling"):
        tags.append("uśmiech")
    if d.get("is_broll"):
        tags.append("b-roll")
    for p in d.get("problems") or []:
        tags.append(f"problem:{p}")
    shot.tags = tags
    try:
        vs = float(d.get("visual_score", 0.5))
        shot.score = round(max(0.0, min(1.0, 0.7 * vs + 0.3 * shot.score)), 3)
    except (TypeError, ValueError):
        pass
    return shot

"""Opis ujęcia przez lokalny VLM (Ollama). Wynik to sprawdzalny JSON, nie wolny tekst."""
from __future__ import annotations

import json
import logging
from typing import Any

import ollama

from ..config import SETTINGS
from ..gpu import ollama_unload

log = logging.getLogger(__name__)

SHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "opis": {"type": "string", "description": "1-2 zdania po polsku: co widać i co się dzieje"},
        "ludzie": {"type": "integer", "description": "liczba widocznych osób"},
        "miejsce": {"type": "string", "description": "wnętrze/plener i rodzaj miejsca, krótko"},
        "akcja": {"type": "string", "description": "główna czynność, krótko"},
        "ujecie": {"type": "string", "enum": ["szeroki", "sredni", "zblizenie", "detal", "nieznany"]},
        "kamera": {"type": "string", "enum": ["statyczna", "reczna", "jazda", "panorama", "nieznana"]},
        "tagi": {"type": "array", "items": {"type": "string"}, "description": "5-10 słów kluczowych po polsku"},
        "ekspozycja": {"type": "string", "enum": ["ok", "za_ciemno", "za_jasno", "mieszana"]},
        "uwagi": {"type": "string", "description": "problemy techniczne lub pusty string"},
    },
    "required": ["opis", "ludzie", "miejsce", "akcja", "ujecie", "kamera", "tagi", "ekspozycja", "uwagi"],
}

SYSTEM = (
    "Jesteś asystentem montażysty. Dostajesz klatki z jednego ujęcia filmowego (kolejne w czasie). "
    "Opisz rzeczowo, po polsku, tylko to, co naprawdę widać. Nie zgaduj imion ani miejsc, których nie da się rozpoznać. "
    "Odpowiadasz wyłącznie JSON-em zgodnym ze schematem."
)


class ShotDescriber:
    def __init__(self) -> None:
        self.client = ollama.Client(host=SETTINGS.ollama_host)
        self.model = SETTINGS.vlm_model

    def describe(self, keyframes: list[str], hint: str = "") -> dict[str, Any] | None:
        if not keyframes:
            return None
        prompt = "Opisz to ujęcie." + (f" Kontekst: {hint}" if hint else "")
        try:
            resp = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt, "images": keyframes},
                ],
                format=SHOT_SCHEMA,
                think=False,
                options={"temperature": 0.2, "num_ctx": 8192},
                keep_alive="10m",
            )
            data = json.loads(resp["message"]["content"])
        except Exception as e:  # noqa: BLE001
            log.warning("VLM: %s", e)
            return None
        data["tagi"] = [str(t).strip().lower() for t in data.get("tagi", []) if str(t).strip()][:12]
        return data

    def close(self) -> None:
        ollama_unload(self.model, SETTINGS.ollama_host)

"""Klient Ollama dla agentów: czat, wizja, strukturalny JSON (schema), auto-wake i VRAM lease."""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from .config import get_config
from .events import log
from .services import services
from .vram import vram


class LLMError(RuntimeError):
    pass


def role_for_effort(effort: str) -> str:
    return {"low": "director_fast", "medium": "director", "high": "director", "max": "director_max"}.get(
        effort, "director")


def _available() -> set[str]:
    try:
        o = services["ollama"]
        return {m["name"] for m in o.models()}  # type: ignore[attr-defined]
    except Exception:
        return set()


def _norm(name: str) -> str:
    return name if ":" in name else name + ":latest"


def resolve_model(role: str) -> str:
    cfg = get_config().get(f"models.{role}") or {}
    names = [cfg.get("name", "")] + list(cfg.get("fallbacks", []))
    avail = _available()
    for n in names:
        if n and (_norm(n) in avail or n in avail):
            return n
    # nic nie zainstalowane - zwróć pierwszy (Ollama zwróci czytelny błąd)
    if not avail:
        raise LLMError("Ollama nie ma żadnego modelu. Uruchom scripts\\install_models.ps1")
    # ostatnia deska ratunku: dowolny zainstalowany model
    return sorted(avail)[0]


def _encode_image(p: str | Path | bytes) -> str:
    if isinstance(p, (bytes, bytearray)):
        return base64.b64encode(p).decode()
    return base64.b64encode(Path(p).read_bytes()).decode()


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def extract_json(text: str) -> Any:
    t = _THINK_RE.sub("", text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # znajdź najbardziej zewnętrzny obiekt/tablicę
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s = t.find(open_c)
        e = t.rfind(close_c)
        if s != -1 and e > s:
            try:
                return json.loads(t[s:e + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Model nie zwrócił poprawnego JSON: {t[:300]}")


def chat(messages: list[dict], *, role: str = "director", agent: str = "director", schema: dict | None = None,
         images: list[str | Path | bytes] | None = None, think: bool | None = None, temperature: float = 0.4,
         num_predict: int | None = None, on_token: Callable[[str], None] | None = None,
         label: str = "", timeout: float = 900, project_id: str | None = None) -> str:
    """Wywołanie /api/chat. Zwraca treść odpowiedzi (bez bloku myślenia)."""
    services.ensure("ollama", agent)
    model = resolve_model(role)
    cfg = get_config()
    mcfg = cfg.get(f"models.{role}") or {}
    if think is None:
        think = bool(mcfg.get("think", False))
    msgs = [dict(m) for m in messages]
    if images:
        for m in reversed(msgs):
            if m.get("role") == "user":
                m["images"] = [_encode_image(i) for i in images]
                break
    body: dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "stream": on_token is not None,
        "keep_alive": cfg.get("services.ollama.keep_alive", "10m"),
        "options": {"temperature": temperature, "num_ctx": int(mcfg.get("num_ctx", 16384))},
        "think": think,
    }
    if num_predict:
        body["options"]["num_predict"] = num_predict
    if schema is not None:
        body["format"] = schema
    url = services["ollama"].url + "/api/chat"
    t0 = time.time()
    with vram.lease(f"ollama:{model}", agent, label or role):
        try:
            if on_token is None:
                r = httpx.post(url, json=body, timeout=timeout)
                if r.status_code == 400 and "think" in r.text:
                    body.pop("think", None)
                    r = httpx.post(url, json=body, timeout=timeout)
                if r.status_code != 200:
                    raise LLMError(f"Ollama {r.status_code}: {r.text[:400]}")
                content = r.json().get("message", {}).get("content", "")
            else:
                parts: list[str] = []
                with httpx.stream("POST", url, json=body, timeout=timeout) as r:
                    if r.status_code != 200:
                        raise LLMError(f"Ollama {r.status_code}: {r.read()[:400]!r}")
                    for line in r.iter_lines():
                        if not line:
                            continue
                        d = json.loads(line)
                        tok = d.get("message", {}).get("content", "")
                        if tok:
                            parts.append(tok)
                            on_token(tok)
                        if d.get("done"):
                            break
                content = "".join(parts)
        except httpx.HTTPError as e:
            raise LLMError(f"Błąd połączenia z Ollama: {e}") from e
    dt = time.time() - t0
    log(agent, f"LLM {model} odpowiedział w {dt:.1f} s", path=label or "LLM", level="debug", project_id=project_id)
    return _THINK_RE.sub("", content).strip()


def chat_json(messages: list[dict], schema: dict, **kw) -> Any:
    """Strukturalna odpowiedź JSON zgodna ze schematem (z jedną próbą naprawy)."""
    text = chat(messages, schema=schema, **kw)
    try:
        return extract_json(text)
    except LLMError:
        fix = messages + [{"role": "assistant", "content": text},
                          {"role": "user", "content": "Odpowiedz WYŁĄCZNIE poprawnym JSON zgodnym ze schematem."}]
        return extract_json(chat(fix, schema=schema, **kw))


def vision(prompt: str, images: list[str | Path | bytes], *, schema: dict | None = None, agent: str = "archivist",
           label: str = "Video-VLM", project_id: str | None = None) -> Any:
    msgs = [{"role": "user", "content": prompt}]
    if schema:
        return chat_json(msgs, schema, role="vision", agent=agent, images=images, temperature=0.2, label=label,
                         think=False, project_id=project_id)
    return chat(msgs, role="vision", agent=agent, images=images, temperature=0.2, label=label, think=False,
                project_id=project_id)

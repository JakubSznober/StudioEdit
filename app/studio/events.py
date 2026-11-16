"""Magistrala zdarzeń: wątki agentów publikują, WebSocket rozsyła do przeglądarki.

Typy zdarzeń (pole "type"):
  task.update      - zmiana stanu zadania w grafie (status, progress, message)
  agent.state      - stan agenta (idle/working, aktywne ścieżki)
  agent.log        - wpis logu decyzyjnego agenta {agent, path, level, text, project_id}
  gpu.stats        - telemetria GPU {util, mem_used, mem_total, temp, power, owner}
  services.update  - stan usług lokalnych (Ollama, ComfyUI)
  vram.update      - właściciel VRAM / kolejka
  project.update   - zmiana projektu / mediów
  timeline.update  - nowa wersja osi czasu {project_id, version}
  render.progress  - postęp renderu {project_id, job, progress, stage}
  chat.message     - wiadomość czatu {project_id, role, text, actions}
  qc.report        - raport kontroli jakości
  loudness         - pomiar LUFS / dB
  toast            - krótkie powiadomienie dla UI
"""
from __future__ import annotations

import asyncio
import collections
import itertools
import threading
import time
from typing import Any

_seq = itertools.count(1)


class EventBus:
    def __init__(self, history: int = 800):
        self._lock = threading.Lock()
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._history: collections.deque = collections.deque(maxlen=history)
        self._last_by_key: dict[str, dict] = {}
        self._listeners: list = []

    def add_listener(self, fn) -> None:
        """Synchroniczny odbiorca zdarzeń w procesie (np. cache ostatniego pomiaru LUFS)."""
        self._listeners.append(fn)

    def publish(self, type_: str, data: dict[str, Any] | None = None, *, sticky_key: str | None = None) -> dict:
        evt = {"id": next(_seq), "type": type_, "ts": time.time(), "data": data or {}}
        with self._lock:
            if sticky_key:
                self._last_by_key[sticky_key] = evt
            elif type_ not in ("gpu.stats", "task.update", "render.progress"):
                self._history.append(evt)
            subs = list(self._subs)
        for fn in list(self._listeners):
            try:
                fn(evt)
            except Exception:
                pass
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put_nowait, q, evt)
            except RuntimeError:
                pass
        return evt

    def subscribe(self) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subs.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(l, x) for (l, x) in self._subs if x is not q]

    def snapshot(self, project_id: str | None = None, limit: int = 400) -> list[dict]:
        with self._lock:
            items = list(self._history)
            sticky = list(self._last_by_key.values())
        if project_id:
            items = [e for e in items if e["data"].get("project_id") in (None, project_id)]
        return sticky + items[-limit:]


def _put_nowait(q: asyncio.Queue, evt: dict) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(evt)


bus = EventBus()


def log(agent: str, text: str, *, path: str = "", level: str = "info", project_id: str | None = None,
        **extra: Any) -> None:
    """Skrót: wpis do logu decyzyjnego agenta (widoczny w strumieniu pracy w UI)."""
    import logging

    logging.getLogger(f"studio.{agent}").log(
        {"debug": 10, "info": 20, "warn": 30, "error": 40}.get(level, 20), "[%s] %s", path, text)
    bus.publish("agent.log", {"agent": agent, "path": path, "level": level, "text": text,
                              "project_id": project_id, **extra})


def toast(text: str, kind: str = "info", project_id: str | None = None) -> None:
    bus.publish("toast", {"text": text, "kind": kind, "project_id": project_id})

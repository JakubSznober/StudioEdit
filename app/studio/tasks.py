"""Graf zadań i pule wątków agentów.

Każdy agent ma WŁASNĄ pulę wątków (osobne ścieżki wykonania). Zadanie (Task) należy do agenta
i ścieżki (np. agent "sound", ścieżka "Room Tone"), ma zależności (deps) - dyspozytor wpuszcza je do kolejki
agenta dopiero, gdy wszystkie zależności się zakończą. Dzięki temu np. kolorysta liczy LUT-y równolegle
z transkrypcją u inżyniera dźwięku, a GPU i tak jest pilnowane przez VRAMOrchestrator.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import get_config
from .events import bus, log
from .media.ffmpeg import Cancelled

_ids = itertools.count(1)

TERMINAL = {"done", "failed", "cancelled", "skipped"}


@dataclass
class Task:
    agent: str
    path: str
    title: str
    fn: Callable[["TaskContext"], Any]
    project_id: str | None = None
    deps: list[str] = field(default_factory=list)
    soft_deps: bool = False  # wykonaj nawet jeśli zależność się nie powiodła
    group: str | None = None  # identyfikator przebiegu (pipeline run)
    priority: int = 5  # mniejsza = ważniejsza
    id: str = field(default_factory=lambda: f"t{next(_ids)}")
    status: str = "queued"  # queued | waiting | running | done | failed | cancelled | skipped
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {"id": self.id, "agent": self.agent, "path": self.path, "title": self.title,
                "project_id": self.project_id, "deps": self.deps, "group": self.group, "status": self.status,
                "progress": round(self.progress, 4), "message": self.message, "error": self.error,
                "created": self.created, "started": self.started, "finished": self.finished,
                "elapsed": round((self.finished or time.time()) - self.started, 1) if self.started else None}


class TaskContext:
    def __init__(self, task: Task, orch: "Orchestrator"):
        self.task = task
        self.orch = orch
        self._last_pub = 0.0

    @property
    def project(self):
        from .project.manager import projects

        return projects.get(self.task.project_id) if self.task.project_id else None

    @property
    def cancel(self) -> threading.Event:
        return self.task.cancel_event

    @property
    def effort(self) -> str:
        return get_config().effort(self.task.agent)

    @property
    def effort_level(self) -> int:
        return get_config().effort_level(self.task.agent)

    def check(self) -> None:
        if self.task.cancel_event.is_set():
            raise Cancelled("Przerwano")

    def progress(self, p: float, message: str | None = None) -> None:
        self.task.progress = max(0.0, min(1.0, p))
        if message is not None:
            self.task.message = message
        now = time.time()
        if now - self._last_pub > 0.25 or p >= 1.0:
            self._last_pub = now
            self.orch._publish(self.task)
        self.check()

    def log(self, text: str, level: str = "info", **extra) -> None:
        log(self.task.agent, text, path=self.task.path, level=level, project_id=self.task.project_id, **extra)

    def result_of(self, task_id: str) -> Any:
        t = self.orch.get(task_id)
        return t.result if t else None

    def sub(self, p0: float, p1: float) -> Callable[[float], None]:
        """Zwraca callback postępu mapujący 0..1 na zakres p0..p1 tego zadania."""
        return lambda x: self.progress(p0 + (p1 - p0) * x)


class AgentPool:
    def __init__(self, orch: "Orchestrator", agent: str, workers: int):
        self.orch = orch
        self.agent = agent
        self.q: queue.PriorityQueue = queue.PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.running: dict[str, Task] = {}
        self._lock = threading.Lock()
        self.resize(workers)

    def resize(self, n: int) -> None:
        n = max(1, n)
        while len(self.threads) < n:
            t = threading.Thread(target=self._loop, daemon=True, name=f"agent-{self.agent}-{len(self.threads) + 1}")
            self.threads.append(t)
            t.start()

    def put(self, task: Task) -> None:
        self.q.put((task.priority, task.created, task.id, task))

    def _loop(self) -> None:
        while True:
            _p, _c, _id, task = self.q.get()
            if task.status != "waiting":
                continue
            with self._lock:
                self.running[task.id] = task
            self.orch._publish_agent(self.agent)
            self.orch._execute(task)
            with self._lock:
                self.running.pop(task.id, None)
            self.orch._publish_agent(self.agent)

    def state(self) -> dict:
        with self._lock:
            run = [{"id": t.id, "path": t.path, "title": t.title, "progress": t.progress, "message": t.message,
                    "project_id": t.project_id} for t in self.running.values()]
        return {"agent": self.agent, "workers": len(self.threads), "busy": len(run), "running": run,
                "queued": self.q.qsize()}


class Orchestrator:
    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.pools: dict[str, AgentPool] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True, name="task-dispatcher")
        self._dispatcher.start()

    def pool(self, agent: str) -> AgentPool:
        with self._lock:
            if agent not in self.pools:
                n = int(get_config().get(f"agents.{agent}.workers", 2) or 2)
                self.pools[agent] = AgentPool(self, agent, n)
            return self.pools[agent]

    def submit(self, task: Task) -> Task:
        with self._lock:
            self.tasks[task.id] = task
            self.pool(task.agent)
        self._publish(task)
        self._wake.set()
        return task

    def add(self, agent: str, path: str, title: str, fn: Callable[[TaskContext], Any], **kw) -> Task:
        return self.submit(Task(agent=agent, path=path, title=title, fn=fn, **kw))

    def get(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def list(self, project_id: str | None = None, group: str | None = None, limit: int = 400) -> list[dict]:
        with self._lock:
            items = [t for t in self.tasks.values() if (project_id is None or t.project_id == project_id)
                     and (group is None or t.group == group)]
        items.sort(key=lambda t: t.created)
        return [t.public() for t in items[-limit:]]

    def cancel(self, task_id: str | None = None, group: str | None = None, project_id: str | None = None) -> int:
        n = 0
        with self._lock:
            for t in self.tasks.values():
                if t.status in TERMINAL:
                    continue
                if (task_id and t.id == task_id) or (group and t.group == group) or \
                        (project_id and t.project_id == project_id and not task_id and not group):
                    t.cancel_event.set()
                    if t.status in ("queued", "waiting"):
                        t.status = "cancelled"
                        t.finished = time.time()
                        self._publish(t)
                    n += 1
        self._wake.set()
        return n

    def wait(self, task_ids: list[str], timeout: float | None = None) -> bool:
        t0 = time.time()
        while True:
            if all(self.tasks[t].status in TERMINAL for t in task_ids if t in self.tasks):
                return True
            if timeout and time.time() - t0 > timeout:
                return False
            time.sleep(0.2)

    def group_status(self, group: str) -> dict:
        ts = [t for t in self.tasks.values() if t.group == group]
        if not ts:
            return {"group": group, "status": "unknown", "progress": 0}
        done = sum(1 for t in ts if t.status in TERMINAL)
        failed = [t.public() for t in ts if t.status == "failed"]
        st = "done" if done == len(ts) else "running"
        if st == "done" and failed:
            st = "failed" if len(failed) == len(ts) else "partial"
        return {"group": group, "status": st, "total": len(ts), "done": done,
                "progress": sum(t.progress if t.status not in TERMINAL else 1 for t in ts) / len(ts),
                "failed": failed}

    def agents_state(self) -> list[dict]:
        with self._lock:
            return [p.state() for p in self.pools.values()]

    def cleanup(self, max_age_s: float = 6 * 3600) -> None:
        now = time.time()
        with self._lock:
            for tid in [t.id for t in self.tasks.values() if t.status in TERMINAL and t.finished
                        and now - t.finished > max_age_s]:
                self.tasks.pop(tid, None)

    # -- wnętrze ------------------------------------------------------------------------------
    def _dispatch_loop(self) -> None:
        last_clean = time.time()
        while True:
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            with self._lock:
                pending = [t for t in self.tasks.values() if t.status == "queued"]
                for t in pending:
                    deps = [self.tasks.get(d) for d in t.deps]
                    if any(d is None for d in deps):
                        # zależność usunięta - traktuj jak wykonaną
                        deps = [d for d in deps if d is not None]
                    if all(d.status in TERMINAL for d in deps):
                        bad = [d for d in deps if d.status in ("failed", "cancelled", "skipped")]
                        if bad and not t.soft_deps:
                            t.status = "skipped"
                            t.message = f"Pominięto - zależność nieudana: {bad[0].title}"
                            t.finished = time.time()
                            self._publish(t)
                            self._wake.set()
                            continue
                        t.status = "waiting"
                        self._publish(t)
                        self.pool(t.agent).put(t)
            if time.time() - last_clean > 600:
                self.cleanup()
                last_clean = time.time()

    def _execute(self, task: Task) -> None:
        if task.cancel_event.is_set():
            task.status = "cancelled"
            task.finished = time.time()
            self._publish(task)
            self._wake.set()
            return
        task.status = "running"
        task.started = time.time()
        self._publish(task)
        ctx = TaskContext(task, self)
        try:
            task.result = task.fn(ctx)
            task.status = "done"
            task.progress = 1.0
        except Cancelled:
            task.status = "cancelled"
            task.message = "Przerwano"
        except Exception as e:
            task.status = "failed"
            task.error = f"{type(e).__name__}: {e}"
            tb = traceback.format_exc()
            log(task.agent, f"Błąd zadania „{task.title}”: {e}", path=task.path, level="error",
                project_id=task.project_id, traceback=tb[-3000:])
        task.finished = time.time()
        self._publish(task)
        self._wake.set()

    def _publish(self, task: Task) -> None:
        bus.publish("task.update", task.public())

    def _publish_agent(self, agent: str) -> None:
        p = self.pools.get(agent)
        if p:
            bus.publish("agent.state", p.state(), sticky_key=f"agent.{agent}")


orchestrator = Orchestrator()

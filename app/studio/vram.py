"""VRAMOrchestrator - jeden właściciel karty graficznej naraz.

Zasady:
* Każdy ciężki model GPU ma "właściciela" (owner), np. "ollama:qwen3-vl:8b", "torch:whisper",
  "torch:sam2", "comfy". Dwa różne modele nigdy nie są w VRAM jednocześnie.
* Przy zmianie właściciela poprzedni jest zwalniany (unload) przez zarejestrowaną funkcję zwalniającą.
* Leniwe zwalnianie: po zakończeniu zadania model zostaje w VRAM, dopóki ktoś inny nie poprosi o GPU
  albo nie minie `idle_unload_s` (usypianie) - to pozwala kolejnym zadaniom tego samego modelu
  działać bez przeładowania (batchowanie po modelu).
* Kolejka preferuje oczekujących z tym samym właścicielem (mniej przełączeń), z limitem
  zagłodzenia, żeby inne agenty też dostały GPU.
"""
from __future__ import annotations

import gc
import itertools
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

from .events import bus, log

_ticket = itertools.count(1)


class _Waiter:
    __slots__ = ("owner", "agent", "label", "ticket", "since")

    def __init__(self, owner: str, agent: str, label: str):
        self.owner, self.agent, self.label = owner, agent, label
        self.ticket = next(_ticket)
        self.since = time.time()


class VRAMOrchestrator:
    def __init__(self, idle_unload_s: float = 180.0, max_same_owner_streak: int = 12):
        self._cond = threading.Condition()
        self.owner: str | None = None
        self.owner_agent: str = ""
        self.owner_label: str = ""
        self.users = 0
        self._concurrency: dict[str, int] = {}
        self._releasers: list[tuple[str, Callable[[str], None]]] = []
        self._cache: dict[str, dict[str, Any]] = {}
        self._waiters: list[_Waiter] = []
        self._streak = 0
        self._last_used = time.time()
        self.idle_unload_s = idle_unload_s
        self.max_same_owner_streak = max_same_owner_streak
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True, name="vram-reaper")
        self._reaper.start()

    # -- rejestracja --------------------------------------------------------------------------
    def register_releaser(self, prefix: str, fn: Callable[[str], None]) -> None:
        """fn(owner) zwalnia VRAM danego właściciela (np. Ollama keep_alive=0, ComfyUI /free)."""
        self._releasers.append((prefix, fn))

    def set_concurrency(self, prefix: str, n: int) -> None:
        self._concurrency[prefix] = max(1, n)

    def _max_users(self, owner: str) -> int:
        for p, n in self._concurrency.items():
            if owner.startswith(p):
                return n
        return 1

    # -- leasing --------------------------------------------------------------------------------
    @contextmanager
    def lease(self, owner: str, agent: str = "", label: str = "", timeout: float | None = None):
        self.acquire(owner, agent, label, timeout)
        try:
            yield self
        finally:
            self.release(owner)

    def acquire(self, owner: str, agent: str = "", label: str = "", timeout: float | None = None) -> None:
        w = _Waiter(owner, agent, label)
        deadline = time.time() + timeout if timeout else None
        with self._cond:
            self._waiters.append(w)
            self._publish()
            while not self._can_grant(w):
                remaining = None if deadline is None else deadline - time.time()
                if remaining is not None and remaining <= 0:
                    self._waiters.remove(w)
                    self._publish()
                    raise TimeoutError(f"GPU zajęte przez {self.owner}")
                self._cond.wait(timeout=min(1.0, remaining) if remaining else 1.0)
            switching = self.owner is not None and self.owner != owner
            prev = self.owner
            self._waiters.remove(w)
            if switching or self.owner is None:
                self._streak = 0
            else:
                self._streak += 1
            # zwolnienie poprzedniego właściciela poza blokadą byłoby lepsze, ale musimy być atomowi
            if switching and prev:
                self._release_owner(prev)
            self.owner = owner
            self.owner_agent = agent
            self.owner_label = label
            self.users += 1
            self._last_used = time.time()
            self._publish()
        if switching:
            log(agent or "system", f"Przełączam VRAM: {prev} → {owner}", path="VRAM")

    def _can_grant(self, w: _Waiter) -> bool:
        if self.owner == w.owner and self.users < self._max_users(w.owner):
            # ten sam model - wpuszczaj, chyba że ktoś inny czeka zbyt długo
            others = [x for x in self._waiters if x.owner != w.owner]
            if not others or self._streak < self.max_same_owner_streak:
                return True
        if self.users > 0:
            return False
        # GPU wolne (users == 0): wybierz najlepszego oczekującego
        best = self._pick_next()
        return best is w

    def _pick_next(self) -> _Waiter | None:
        if not self._waiters:
            return None
        if self.owner is not None and self._streak < self.max_same_owner_streak:
            same = [x for x in self._waiters if x.owner == self.owner]
            if same:
                return min(same, key=lambda x: x.ticket)
        return min(self._waiters, key=lambda x: x.ticket)

    def release(self, owner: str) -> None:
        with self._cond:
            if self.owner == owner and self.users > 0:
                self.users -= 1
            self._last_used = time.time()
            self._cond.notify_all()
            self._publish()

    # -- zwalnianie -----------------------------------------------------------------------------
    def _release_owner(self, owner: str) -> None:
        for prefix, fn in self._releasers:
            if owner.startswith(prefix):
                try:
                    fn(owner)
                except Exception as e:  # pragma: no cover
                    log("system", f"Błąd zwalniania {owner}: {e}", path="VRAM", level="warn")
        self._cache.pop(owner, None)
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
        except Exception:
            pass

    def unload_all(self, reason: str = "") -> None:
        """Usypia bieżącego właściciela (gdy nikt go nie używa)."""
        with self._cond:
            if self.owner and self.users == 0:
                prev = self.owner
                self._release_owner(prev)
                self.owner = None
                self.owner_agent = ""
                self.owner_label = ""
                self._publish()
                log("system", f"Zwolniono VRAM ({prev}){' - ' + reason if reason else ''}", path="VRAM")
            self._cond.notify_all()

    def _reap_loop(self) -> None:
        while True:
            time.sleep(5)
            with self._cond:
                idle = self.owner and self.users == 0 and not self._waiters and \
                    time.time() - self._last_used > self.idle_unload_s
            if idle:
                self.unload_all("bezczynność")

    # -- cache modeli torch ----------------------------------------------------------------------
    def cached(self, owner: str, key: str, loader: Callable[[], Any]) -> Any:
        """Zwraca model z cache bieżącego właściciela (wołać wewnątrz lease(owner))."""
        slot = self._cache.setdefault(owner, {})
        if key not in slot:
            slot[key] = loader()
        return slot[key]

    # -- stan -----------------------------------------------------------------------------------
    def state(self) -> dict:
        return {
            "owner": self.owner,
            "owner_agent": self.owner_agent,
            "owner_label": self.owner_label,
            "users": self.users,
            "queue": [{"owner": w.owner, "agent": w.agent, "label": w.label,
                       "wait_s": round(time.time() - w.since, 1)} for w in self._waiters],
        }

    def _publish(self) -> None:
        bus.publish("vram.update", self.state(), sticky_key="vram.update")


vram = VRAMOrchestrator()
vram.set_concurrency("ollama:", 2)


def torch_owner(name: str) -> str:
    return f"torch:{name}"

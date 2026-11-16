"""ProjectManager - projekty, biblioteka mediów, biny i wersjonowanie osi czasu (edl_vN.json, Undo/Redo).

Układ projektu (Projects\\<id>\\):
  project.json           - metadane (ProjectMeta)
  media.json             - biblioteka mediów (lista MediaItem)
  edl\\edl_v1.json ...    - migawki osi czasu (każda zmiana = nowa wersja)
  edl\\history.json       - wskaźnik bieżącej wersji + stos redo
  chat.json              - historia czatu reżyserskiego
  footage\\              - skopiowane oryginały (tryb "copy"), tylko do odczytu
  footage\\Odrzucone_Duble\\ - odrzucone dublowane ujęcia (nigdy nie kasujemy)
  cache\\                - proxy, miniatury, LUT-y, audio, kawałki renderu
  renders\\              - podglądy
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

from .. import paths
from ..events import bus, log
from .model import MediaItem, ProjectMeta, Timeline, new_id

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".m4v", ".webm", ".mts", ".m2ts", ".3gp", ".insv", ".lrv"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".aiff", ".aif", ".wma"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".heic"}


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:40] or "projekt"


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class Project:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.RLock()
        self.meta = ProjectMeta(**json.loads((root / "project.json").read_text(encoding="utf-8")))
        self._media: dict[str, MediaItem] = {}
        mf = root / "media.json"
        if mf.exists():
            for d in json.loads(mf.read_text(encoding="utf-8")):
                m = MediaItem(**d)
                self._media[m.id] = m
        self._timeline: Timeline | None = None

    # -- ścieżki ---------------------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.meta.id

    def dir(self, *parts: str) -> Path:
        p = self.root.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache(self) -> Path:
        return self.dir("cache")

    # -- metadane --------------------------------------------------------------------------
    def save_meta(self) -> None:
        with self._lock:
            self.meta.updated_at = time.time()
            _atomic_write(self.root / "project.json", self.meta.model_dump(mode="json"))
        bus.publish("project.update", {"project_id": self.id, "meta": self.meta.model_dump(mode="json")})

    def update_meta(self, **kw) -> ProjectMeta:
        with self._lock:
            for k, v in kw.items():
                if hasattr(self.meta, k) and v is not None:
                    setattr(self.meta, k, v)
            self.save_meta()
            return self.meta

    # -- media -----------------------------------------------------------------------------
    def media(self) -> list[MediaItem]:
        with self._lock:
            return sorted(self._media.values(), key=lambda m: (m.added_at, m.name))

    def get_media(self, media_id: str) -> MediaItem:
        with self._lock:
            if media_id not in self._media:
                raise KeyError(f"Brak mediów {media_id}")
            return self._media[media_id]

    def save_media(self, item: MediaItem | None = None, publish: bool = True) -> None:
        with self._lock:
            if item is not None:
                self._media[item.id] = item
            _atomic_write(self.root / "media.json", [m.model_dump(mode="json") for m in self._media.values()])
        if publish:
            bus.publish("project.update", {"project_id": self.id, "media_changed": item.id if item else None})

    def update_media(self, media_id: str, fn: Callable[[MediaItem], None]) -> MediaItem:
        """Bezpieczna (wątkowo) modyfikacja elementu biblioteki."""
        with self._lock:
            m = self.get_media(media_id)
            fn(m)
            self.save_media(m)
            return m

    def add_media_file(self, src: str | Path, *, bin_name: str | None = None, copy: bool | None = None,
                       generated: bool = False, derived_from: str | None = None) -> MediaItem:
        src = Path(src)
        if not src.exists():
            raise FileNotFoundError(str(src))
        ext = src.suffix.lower()
        kind = "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else "image" if ext in IMAGE_EXT else "unknown"
        if kind == "unknown":
            raise ValueError(f"Nieobsługiwany typ pliku: {src.name}")
        with self._lock:
            for m in self._media.values():
                if Path(m.path) == src.resolve():
                    return m
        do_copy = (self.meta.ingest_mode == "copy") if copy is None else copy
        path = src.resolve()
        if do_copy and not generated:
            dst = self.dir("footage") / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                try:
                    os.chmod(dst, stat.S_IREAD)  # ochrona oryginału
                except OSError:
                    pass
            path = dst
        default_bin = {"video": "A-Roll", "audio": "Audio", "image": "Grafika"}[kind]
        item = MediaItem(id=new_id("m_"), name=src.name, path=str(path), kind=kind,
                         bin=bin_name or ("Wygenerowane" if generated else default_bin),
                         generated=generated, derived_from=derived_from)
        self.save_media(item)
        log("archivist", f"Dodano {'wygenerowany ' if generated else ''}plik {src.name}", path="Import",
            project_id=self.id)
        return item

    def reject_media(self, media_id: str, reason: str) -> MediaItem:
        """Odrzucenie = przeniesienie do binu Odrzucone_Duble (fizycznie tylko kopie w projekcie)."""
        def _fn(m: MediaItem):
            m.bin = "Odrzucone_Duble"
            if "REJECTED" not in m.flags:
                m.flags.append("REJECTED")
            m.flag_details["rejected_reason"] = reason
            p = Path(m.path)
            footage = self.root / "footage"
            if footage in p.parents and p.exists():
                dst_dir = self.dir("footage", "Odrzucone_Duble")
                dst = dst_dir / p.name
                if not dst.exists():
                    try:
                        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
                        shutil.move(str(p), dst)
                        os.chmod(dst, stat.S_IREAD)
                        m.path = str(dst)
                    except OSError:
                        pass
        return self.update_media(media_id, _fn)

    def remove_media(self, media_id: str) -> None:
        """Usuwa wpis z biblioteki (NIE kasuje pliku oryginału)."""
        with self._lock:
            self._media.pop(media_id, None)
            self.save_media()

    # -- oś czasu i historia ----------------------------------------------------------------
    def _hist_file(self) -> Path:
        return self.dir("edl") / "history.json"

    def history(self) -> dict:
        f = self._hist_file()
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return {"current": 0, "versions": [], "redo": []}

    def timeline(self) -> Timeline:
        with self._lock:
            if self._timeline is None:
                h = self.history()
                cur = h.get("current", 0)
                f = self.root / "edl" / f"edl_v{cur}.json"
                if cur and f.exists():
                    self._timeline = Timeline(**json.loads(f.read_text(encoding="utf-8"))).normalize()
                else:
                    self._timeline = Timeline()
            return self._timeline.model_copy(deep=True)

    def commit_timeline(self, tl: Timeline, note: str, author: str = "") -> Timeline:
        """Zapisuje nową wersję osi czasu (czyści stos redo)."""
        with self._lock:
            h = self.history()
            versions = h.get("versions", [])
            nxt = (max([v["version"] for v in versions]) if versions else 0) + 1
            tl.version = nxt
            tl.note = note
            tl.created_by = author
            tl.created_at = time.time()
            tl.normalize()
            _atomic_write(self.root / "edl" / f"edl_v{nxt}.json", tl.to_json())
            # porzucona gałąź redo nie jest kasowana z dysku (można ją przywrócić ręcznie)
            versions.append({"version": nxt, "note": note, "author": author, "ts": tl.created_at,
                             "duration": tl.duration, "parent": h.get("current", 0)})
            h = {"current": nxt, "versions": versions, "redo": []}
            _atomic_write(self._hist_file(), h)
            self._timeline = tl.model_copy(deep=True)
        bus.publish("timeline.update", {"project_id": self.id, "version": nxt, "note": note, "author": author})
        return tl

    def mutate_timeline(self, fn: Callable[[Timeline], Any], note: str, author: str = "") -> Timeline:
        """Atomowa zmiana osi: wczytaj bieżącą wersję, zastosuj fn(tl) i zapisz nową wersję.

        Używaj ZAWSZE, gdy kilka agentów może równolegle modyfikować oś (chroni przed nadpisaniem)."""
        with self._lock:
            tl = self.timeline()
            res = fn(tl)
            if isinstance(res, Timeline):
                tl = res
            return self.commit_timeline(tl, note, author)

    def undo(self) -> Timeline | None:
        with self._lock:
            h = self.history()
            cur = h.get("current", 0)
            ver = next((v for v in h["versions"] if v["version"] == cur), None)
            if not ver or not ver.get("parent"):
                return None
            h["redo"].append(cur)
            h["current"] = ver["parent"]
            _atomic_write(self._hist_file(), h)
            self._timeline = None
            tl = self.timeline()
        bus.publish("timeline.update", {"project_id": self.id, "version": tl.version, "note": "Cofnij"})
        return tl

    def redo(self) -> Timeline | None:
        with self._lock:
            h = self.history()
            if not h.get("redo"):
                return None
            h["current"] = h["redo"].pop()
            _atomic_write(self._hist_file(), h)
            self._timeline = None
            tl = self.timeline()
        bus.publish("timeline.update", {"project_id": self.id, "version": tl.version, "note": "Ponów"})
        return tl

    def checkout(self, version: int) -> Timeline:
        with self._lock:
            f = self.root / "edl" / f"edl_v{version}.json"
            if not f.exists():
                raise KeyError(f"Brak wersji {version}")
            h = self.history()
            h["current"] = version
            h["redo"] = []
            _atomic_write(self._hist_file(), h)
            self._timeline = None
            tl = self.timeline()
        bus.publish("timeline.update", {"project_id": self.id, "version": version, "note": f"Przywrócono v{version}"})
        return tl

    def load_version(self, version: int) -> Timeline:
        f = self.root / "edl" / f"edl_v{version}.json"
        return Timeline(**json.loads(f.read_text(encoding="utf-8"))).normalize()

    # -- czat ------------------------------------------------------------------------------
    def chat_history(self) -> list[dict]:
        f = self.root / "chat.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    def add_chat(self, msg: dict) -> dict:
        with self._lock:
            h = self.chat_history()
            msg = {"id": new_id("c_"), "ts": time.time(), **msg}
            h.append(msg)
            _atomic_write(self.root / "chat.json", h[-500:])
        bus.publish("chat.message", {"project_id": self.id, **msg})
        return msg

    def summary(self) -> dict:
        media = self.media()
        tl = self.timeline()
        return {**self.meta.model_dump(mode="json"), "media_count": len(media),
                "media_duration": round(sum(m.info.get("duration", 0) for m in media if m.bin != "Odrzucone_Duble"), 1),
                "timeline_version": tl.version, "timeline_duration": tl.duration,
                "size_gb": round(_dir_size(self.root) / 1024**3, 2)}


def _dir_size(p: Path) -> int:
    total = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


class ProjectManager:
    def __init__(self, root: Path = paths.PROJECTS):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._open: dict[str, Project] = {}
        self._lock = threading.RLock()
        self.active_id: str | None = None
        af = self.root / ".active"
        if af.exists():
            aid = af.read_text(encoding="utf-8").strip()
            if (self.root / aid / "project.json").exists():
                self.active_id = aid

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()):
            if (d / "project.json").exists():
                try:
                    out.append(self.get(d.name).summary())
                except Exception as e:  # uszkodzony projekt
                    out.append({"id": d.name, "name": d.name, "error": str(e)})
        out.sort(key=lambda p: p.get("updated_at", 0), reverse=True)
        return out

    def create(self, name: str, **kw) -> Project:
        with self._lock:
            base = slugify(name)
            pid = base
            i = 2
            while (self.root / pid).exists():
                pid = f"{base}-{i}"
                i += 1
            d = self.root / pid
            d.mkdir(parents=True)
            meta = ProjectMeta(id=pid, name=name, **{k: v for k, v in kw.items() if v is not None})
            _atomic_write(d / "project.json", meta.model_dump(mode="json"))
            _atomic_write(d / "media.json", [])
            p = Project(d)
            self._open[pid] = p
            self.set_active(pid)
            log("archivist", f"Utworzono projekt „{name}”", path="ProjectManager", project_id=pid)
            bus.publish("project.update", {"project_id": pid, "created": True})
            return p

    def get(self, pid: str) -> Project:
        with self._lock:
            if pid not in self._open:
                d = self.root / pid
                if not (d / "project.json").exists():
                    raise KeyError(f"Projekt {pid} nie istnieje")
                self._open[pid] = Project(d)
            return self._open[pid]

    def set_active(self, pid: str) -> None:
        self.get(pid)
        self.active_id = pid
        (self.root / ".active").write_text(pid, encoding="utf-8")
        bus.publish("project.update", {"project_id": pid, "active": True})

    def active(self) -> Project | None:
        return self.get(self.active_id) if self.active_id else None

    def delete(self, pid: str) -> Path:
        """Przenosi projekt do Backups (nie kasuje)."""
        with self._lock:
            p = self.get(pid)
            self._open.pop(pid, None)
            dst = paths.BACKUPS / "deleted_projects" / f"{pid}_{int(time.time())}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            # pliki tylko-do-odczytu muszą dostać prawo zapisu, by move się udał
            for dp, _dn, fn in os.walk(p.root):
                for f in fn:
                    try:
                        os.chmod(os.path.join(dp, f), stat.S_IWRITE | stat.S_IREAD)
                    except OSError:
                        pass
            shutil.move(str(p.root), dst)
            if self.active_id == pid:
                self.active_id = None
                try:
                    (self.root / ".active").unlink()
                except OSError:
                    pass
            bus.publish("project.update", {"project_id": pid, "deleted": True})
            return dst


projects = ProjectManager()

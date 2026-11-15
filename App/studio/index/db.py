"""SQLite: media, ujęcia, transkrypcja, FTS."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    fingerprint TEXT NOT NULL,
    size INTEGER, mtime REAL,
    kind TEXT NOT NULL DEFAULT 'video',          -- video | audio | image
    duration REAL, width INTEGER, height INTEGER, fps REAL,
    video_codec TEXT, audio_codec TEXT, audio_channels INTEGER, sample_rate INTEGER,
    pix_fmt TEXT, bit_depth INTEGER,
    color_primaries TEXT, color_transfer TEXT, color_space TEXT, color_range TEXT,
    color_profile TEXT,                           -- sdr | log | hdr-pq | hdr-hlg | unknown
    camera_make TEXT, camera_model TEXT, created_at TEXT,
    proxy_path TEXT, audio_path TEXT,
    language TEXT,
    stages_done TEXT NOT NULL DEFAULT '[]',
    probe_json TEXT,
    indexed_at TEXT
);
CREATE TABLE IF NOT EXISTS shots (
    id INTEGER PRIMARY KEY,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    start REAL NOT NULL, "end" REAL NOT NULL,
    cut_start INTEGER NOT NULL DEFAULT 1,         -- 1 = prawdziwe cięcie, 0 = podział długiego ujęcia
    keyframes TEXT,                               -- JSON: lista ścieżek JPG
    sharpness REAL, luma_mean REAL, clip_hi REAL, clip_lo REAL,
    motion REAL, cam_motion REAL, shake REAL,
    speech_ratio REAL,
    caption TEXT, people INTEGER, place TEXT, action TEXT, shot_size TEXT, cam_move TEXT,
    tags TEXT,                                    -- JSON lista
    vlm_json TEXT,
    embedding BLOB,                               -- float16, znormalizowany
    UNIQUE(media_id, idx)
);
CREATE TABLE IF NOT EXISTS transcript (
    id INTEGER PRIMARY KEY,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    start REAL NOT NULL, "end" REAL NOT NULL,
    text TEXT NOT NULL,
    words TEXT,                                   -- JSON: [{w,s,e,p}]
    speaker TEXT,
    avg_logprob REAL, no_speech_prob REAL,
    UNIQUE(media_id, idx)
);
CREATE INDEX IF NOT EXISTS shots_media ON shots(media_id, start);
CREATE INDEX IF NOT EXISTS transcript_media ON transcript(media_id, start);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    kind UNINDEXED, ref_id UNINDEXED, media_id UNINDEXED, text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.has_fts = self._init_fts()

    def _init_fts(self) -> bool:
        try:
            self.conn.executescript(FTS_SCHEMA)
            return True
        except sqlite3.OperationalError:
            return False

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---- media -------------------------------------------------------------
    def get_media(self, path: str | Path) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM media WHERE path = ?", (str(path),)).fetchone()

    def get_media_by_id(self, media_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()

    def list_media(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM media ORDER BY path").fetchall()

    def upsert_media(self, path: str | Path, fingerprint: str, size: int, mtime: float, kind: str) -> int:
        row = self.get_media(path)
        with self.tx() as c:
            if row is None:
                cur = c.execute(
                    "INSERT INTO media(path, fingerprint, size, mtime, kind) VALUES (?,?,?,?,?)",
                    (str(path), fingerprint, size, mtime, kind),
                )
                return int(cur.lastrowid)
            if row["fingerprint"] != fingerprint:
                # plik się zmienił: czyścimy wszystko, co o nim wiedzieliśmy
                c.execute("DELETE FROM shots WHERE media_id = ?", (row["id"],))
                c.execute("DELETE FROM transcript WHERE media_id = ?", (row["id"],))
                self._fts_delete(c, media_id=row["id"])
                c.execute(
                    "UPDATE media SET fingerprint=?, size=?, mtime=?, kind=?, stages_done='[]' WHERE id=?",
                    (fingerprint, size, mtime, kind, row["id"]),
                )
            return int(row["id"])

    def update_media(self, media_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE media SET {cols} WHERE id = ?", (*fields.values(), media_id))

    def stages_done(self, media_id: int) -> set[str]:
        row = self.get_media_by_id(media_id)
        return set(json.loads(row["stages_done"])) if row else set()

    def mark_stage(self, media_id: int, stage: str) -> None:
        done = self.stages_done(media_id)
        done.add(stage)
        self.update_media(media_id, stages_done=json.dumps(sorted(done)))

    def clear_stages(self, media_id: int, stages: set[str]) -> None:
        done = self.stages_done(media_id) - stages
        self.update_media(media_id, stages_done=json.dumps(sorted(done)))

    # ---- shots -------------------------------------------------------------
    def replace_shots(self, media_id: int, shots: list[dict[str, Any]]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM shots WHERE media_id = ?", (media_id,))
            self._fts_delete(c, media_id=media_id, kind="shot")
            for i, s in enumerate(shots):
                c.execute(
                    'INSERT INTO shots(media_id, idx, start, "end", cut_start) VALUES (?,?,?,?,?)',
                    (media_id, i, s["start"], s["end"], int(s.get("cut_start", True))),
                )

    def list_shots(self, media_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM shots WHERE media_id = ? ORDER BY idx", (media_id,)).fetchall()

    def update_shot(self, shot_id: int, **fields: Any) -> None:
        if not fields:
            return
        for k in ("keyframes", "tags"):
            if k in fields and not isinstance(fields[k], (str, bytes, type(None))):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        if "embedding" in fields and isinstance(fields["embedding"], np.ndarray):
            fields["embedding"] = fields["embedding"].astype(np.float16).tobytes()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE shots SET {cols} WHERE id = ?", (*fields.values(), shot_id))

    def set_shot_text(self, shot_id: int, media_id: int, text: str) -> None:
        if not self.has_fts:
            return
        with self.tx() as c:
            c.execute("DELETE FROM fts WHERE kind='shot' AND ref_id=?", (shot_id,))
            c.execute("INSERT INTO fts(kind, ref_id, media_id, text) VALUES ('shot',?,?,?)", (shot_id, media_id, text))

    # ---- transcript --------------------------------------------------------
    def replace_transcript(self, media_id: int, segments: list[dict[str, Any]]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM transcript WHERE media_id = ?", (media_id,))
            self._fts_delete(c, media_id=media_id, kind="transcript")
            for i, s in enumerate(segments):
                cur = c.execute(
                    'INSERT INTO transcript(media_id, idx, start, "end", text, words, avg_logprob, no_speech_prob)'
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        media_id, i, s["start"], s["end"], s["text"],
                        json.dumps(s.get("words", []), ensure_ascii=False),
                        s.get("avg_logprob"), s.get("no_speech_prob"),
                    ),
                )
                if self.has_fts:
                    c.execute(
                        "INSERT INTO fts(kind, ref_id, media_id, text) VALUES ('transcript',?,?,?)",
                        (cur.lastrowid, media_id, s["text"]),
                    )

    def list_transcript(self, media_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM transcript WHERE media_id = ? ORDER BY idx", (media_id,)
        ).fetchall()

    # ---- fts ---------------------------------------------------------------
    def _fts_delete(self, c: sqlite3.Connection, media_id: int, kind: str | None = None) -> None:
        if not self.has_fts:
            return
        if kind:
            c.execute("DELETE FROM fts WHERE media_id=? AND kind=?", (media_id, kind))
        else:
            c.execute("DELETE FROM fts WHERE media_id=?", (media_id,))

    def fts_search(self, query: str, kind: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
        if not self.has_fts:
            like = f"%{query}%"
            if kind == "transcript":
                return self.conn.execute(
                    "SELECT 'transcript' kind, id ref_id, media_id, 1.0 score FROM transcript WHERE text LIKE ? LIMIT ?",
                    (like, limit),
                ).fetchall()
            return self.conn.execute(
                "SELECT 'shot' kind, id ref_id, media_id, 1.0 score FROM shots WHERE caption LIKE ? OR tags LIKE ? LIMIT ?",
                (like, like, limit),
            ).fetchall()
        # FTS5: każde słowo jako prefiks, OR między słowami
        terms = [t for t in "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in query).split() if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"*' for t in terms)
        sql = "SELECT kind, ref_id, media_id, bm25(fts) AS score FROM fts WHERE fts MATCH ?"
        params: list[Any] = [match]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    # ---- embeddings --------------------------------------------------------
    def shot_embeddings(self, media_ids: list[int] | None = None) -> tuple[list[int], np.ndarray]:
        sql = "SELECT id, embedding FROM shots WHERE embedding IS NOT NULL"
        params: tuple = ()
        if media_ids:
            sql += f" AND media_id IN ({','.join('?' * len(media_ids))})"
            params = tuple(media_ids)
        rows = self.conn.execute(sql, params).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        ids = [r["id"] for r in rows]
        mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float16).astype(np.float32) for r in rows])
        return ids, mat

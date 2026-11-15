"""Zapytania do indeksu: filtry SQL + embedding tekstu + FTS po opisach i transkrypcji."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .db import Database


@dataclass
class ShotFilter:
    media_ids: list[int] | None = None
    min_sharpness: float | None = None      # wartość bezwzględna (var Laplace'a na 640 px)
    sharp_quantile: float | None = None     # np. 0.3 = odrzuć 30% najmniej ostrych w bibliotece
    has_speech: bool | None = None          # True: speech_ratio >= 0.3, False: < 0.1
    min_people: int | None = None
    max_people: int | None = None
    min_duration: float | None = None
    max_clip_hi: float | None = None        # np. 0.05 = max 5% prześwietlonych pikseli
    max_clip_lo: float | None = None
    max_shake: float | None = None
    cut_start_only: bool = False
    extra_sql: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)


def _apply_filter(db: Database, f: ShotFilter) -> tuple[str, list[Any]]:
    where, params = ["1=1"], []
    if f.media_ids:
        where.append(f"s.media_id IN ({','.join('?' * len(f.media_ids))})")
        params += f.media_ids
    if f.sharp_quantile is not None:
        vals = [r[0] for r in db.conn.execute("SELECT sharpness FROM shots WHERE sharpness IS NOT NULL")]
        if vals:
            f.min_sharpness = max(f.min_sharpness or 0.0, float(np.quantile(vals, f.sharp_quantile)))
    if f.min_sharpness is not None:
        where.append("s.sharpness >= ?"), params.append(f.min_sharpness)
    if f.has_speech is True:
        where.append("s.speech_ratio >= 0.3")
    elif f.has_speech is False:
        where.append("COALESCE(s.speech_ratio, 0) < 0.1")
    if f.min_people is not None:
        where.append("COALESCE(s.people, 0) >= ?"), params.append(f.min_people)
    if f.max_people is not None:
        where.append("COALESCE(s.people, 0) <= ?"), params.append(f.max_people)
    if f.min_duration is not None:
        where.append('(s."end" - s.start) >= ?'), params.append(f.min_duration)
    if f.max_clip_hi is not None:
        where.append("COALESCE(s.clip_hi, 0) <= ?"), params.append(f.max_clip_hi)
    if f.max_clip_lo is not None:
        where.append("COALESCE(s.clip_lo, 0) <= ?"), params.append(f.max_clip_lo)
    if f.max_shake is not None:
        where.append("COALESCE(s.shake, 0) <= ?"), params.append(f.max_shake)
    if f.cut_start_only:
        where.append("s.cut_start = 1")
    for sql, p in f.extra_sql:
        where.append(sql), params.extend(p)
    return " AND ".join(where), params


def search(db: Database, text: str | None = None, flt: ShotFilter | None = None, top_k: int = 20,
           embedder: Any | None = None, w_embed: float = 2.0, w_fts: float = 0.5) -> list[dict[str, Any]]:
    """Zwraca ujęcia posortowane malejąco wg trafności. Bez tekstu: sortowanie po ostrości."""
    flt = flt or ShotFilter()
    where, params = _apply_filter(db, flt)
    rows = db.conn.execute(
        f'SELECT s.*, m.path AS media_path, m.fps FROM shots s JOIN media m ON m.id = s.media_id WHERE {where}', params
    ).fetchall()
    if not rows:
        return []
    cand = {r["id"]: dict(r) for r in rows}
    scores = {sid: 0.0 for sid in cand}

    if text:
        # 1) podobieństwo embeddingu tekstu do embeddingu ujęcia
        ids, mat = db.shot_embeddings(list({c["media_id"] for c in cand.values()}))
        if ids and embedder is not None:
            q = embedder.text([text])[0]
            probs = embedder.match_prob(mat @ q)  # skalibrowane p(dopasowanie) w [0,1], ~0 gdy nic nie pasuje
            for sid, p in zip(ids, probs):
                if sid in scores:
                    scores[sid] += w_embed * float(p)
                    cand[sid]["embed_p"] = round(float(p), 4)
        # 2) FTS po opisie/tagach i po transkrypcji (segmenty nakładające się na ujęcie).
        #    bm25 w FTS5 jest ujemne (bardziej ujemne = lepsze); skalujemy do (0,1] względem najlepszego trafienia,
        #    żeby trafienie w rzadkie słowo ("rozmyty") ważyło więcej niż w pospolite ("obraz").
        def _weights(hits: list[Any]) -> dict[int, float]:
            if not hits:
                return {}
            best = min(float(h["score"]) for h in hits)
            if best >= 0:  # fallback LIKE bez bm25
                return {int(h["ref_id"]): 1.0 for h in hits}
            return {int(h["ref_id"]): float(h["score"]) / best for h in hits}

        for sid, w in _weights(db.fts_search(text, kind="shot")).items():
            if sid in scores:
                scores[sid] += w_fts * w
                cand[sid]["fts_caption"] = round(w, 3)
        tr_w = _weights(db.fts_search(text, kind="transcript"))
        if tr_w:
            seg_ids = list(tr_w)
            segs = db.conn.execute(
                f'SELECT id, media_id, start, "end" FROM transcript WHERE id IN ({",".join("?" * len(seg_ids))})', seg_ids
            ).fetchall()
            for seg in segs:
                for sid, c in cand.items():
                    if c["media_id"] == seg["media_id"] and seg["start"] < c["end"] and seg["end"] > c["start"]:
                        scores[sid] += w_fts * tr_w[seg["id"]]
                        cand[sid]["fts_transcript"] = round(max(cand[sid].get("fts_transcript", 0.0), tr_w[seg["id"]]), 3)
    else:
        mx = max((c["sharpness"] or 0.0) for c in cand.values()) or 1.0
        for sid, c in cand.items():
            scores[sid] = (c["sharpness"] or 0.0) / mx

    ranked = sorted(cand.values(), key=lambda c: scores[c["id"]], reverse=True)[:top_k]
    out = []
    for c in ranked:
        c["score"] = round(scores[c["id"]], 4)
        c["tags"] = json.loads(c["tags"]) if c.get("tags") else []
        c["keyframes"] = json.loads(c["keyframes"]) if c.get("keyframes") else []
        c.pop("embedding", None)
        c.pop("vlm_json", None)
        out.append(c)
    return out


def transcript_text(db: Database, media_id: int) -> str:
    return "\n".join(f"[{r['start']:7.2f}-{r['end']:7.2f}] {r['text']}" for r in db.list_transcript(media_id))

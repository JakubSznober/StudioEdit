"""CLI indeksu.

  python -m studio.index index  C:\\StudioEdit\\Footage [--stages probe,proxy,...] [--force]
  python -m studio.index query  "osoba mówi do kamery" [--top 10] [--sharp 0.3] [--speech|--no-speech] [--people N]
  python -m studio.index show   <media_id|ścieżka>
  python -m studio.index stats
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..config import SETTINGS
from .db import Database
from .pipeline import STAGES, index_paths
from .query import ShotFilter, search, transcript_text

console = Console(width=220)


def cmd_index(a: argparse.Namespace) -> int:
    stages = a.stages.split(",") if a.stages else list(STAGES)
    index_paths(a.paths, stages=stages, force=a.force)
    return 0


def cmd_query(a: argparse.Namespace) -> int:
    db = Database(SETTINGS.index_db)
    flt = ShotFilter(sharp_quantile=a.sharp, has_speech=a.speech, min_people=a.people, min_duration=a.min_dur)
    emb = None
    if a.text:
        from .embeddings import Embedder

        emb = Embedder()
    try:
        rows = search(db, a.text, flt, top_k=a.top, embedder=emb)
    finally:
        if emb:
            emb.close()
    t = Table(title=f"zapytanie: {a.text or '(bez tekstu, wg ostrości)'}")
    for col in ("score", "p_emb", "plik", "ujęcie", "czas", "ostrość", "luma", "ruch", "mowa", "os.", "opis"):
        t.add_column(col)
    for r in rows:
        t.add_row(
            f"{r['score']:.3f}", f"{r.get('embed_p', 0):.3f}", Path(r["media_path"]).name, str(r["idx"]),
            f"{r['start']:.1f}-{r['end']:.1f}",
            f"{(r['sharpness'] or 0):.0f}", f"{(r['luma_mean'] or 0):.2f}", f"{(r['motion'] or 0):.3f}",
            f"{(r['speech_ratio'] or 0):.2f}", str(r["people"] if r["people"] is not None else "-"),
            (r["caption"] or "")[:70],
        )
    console.print(t)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    db = Database(SETTINGS.index_db)
    m = db.get_media_by_id(int(a.media)) if a.media.isdigit() else db.get_media(Path(a.media).resolve())
    if not m:
        console.print("[red]nie znaleziono[/]")
        return 1
    d = {k: m[k] for k in m.keys() if k != "probe_json"}
    console.print(d)
    t = Table(title="ujęcia")
    for col in ("idx", "cut", "czas", "ostrość", "luma", "clip hi/lo", "ruch", "kamera", "shake", "mowa", "os.", "rodzaj", "opis"):
        t.add_column(col)
    for s in db.list_shots(m["id"]):
        t.add_row(
            str(s["idx"]), "✂" if s["cut_start"] else "·", f"{s['start']:.1f}-{s['end']:.1f}",
            f"{(s['sharpness'] or 0):.0f}", f"{(s['luma_mean'] or 0):.2f}",
            f"{(s['clip_hi'] or 0):.2f}/{(s['clip_lo'] or 0):.2f}", f"{(s['motion'] or 0):.3f}",
            f"{(s['cam_motion'] or 0):.1f}", f"{(s['shake'] or 0):.1f}", f"{(s['speech_ratio'] or 0):.2f}",
            str(s["people"] if s["people"] is not None else "-"), f"{s['shot_size'] or ''}/{s['cam_move'] or ''}",
            (s["caption"] or "")[:60],
        )
    console.print(t)
    tt = transcript_text(db, m["id"])
    if tt:
        console.print("[bold]transkrypcja[/]")
        console.print(tt)
    return 0


def cmd_stats(a: argparse.Namespace) -> int:
    db = Database(SETTINGS.index_db)
    c = db.conn
    n_media = c.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    n_shots = c.execute("SELECT COUNT(*) FROM shots").fetchone()[0]
    n_desc = c.execute("SELECT COUNT(*) FROM shots WHERE caption IS NOT NULL").fetchone()[0]
    n_emb = c.execute("SELECT COUNT(*) FROM shots WHERE embedding IS NOT NULL").fetchone()[0]
    n_tr = c.execute("SELECT COUNT(*) FROM transcript").fetchone()[0]
    dur = c.execute("SELECT COALESCE(SUM(duration),0) FROM media").fetchone()[0]
    console.print(f"baza: {db.path}\nmedia: {n_media} ({dur/60:.1f} min)  ujęcia: {n_shots}  opisane: {n_desc}  "
                  f"embeddingi: {n_emb}  segmenty mowy: {n_tr}  FTS: {db.has_fts}")
    for m in db.list_media():
        console.print(f"  [{m['id']}] {Path(m['path']).name}  {m['kind']}  {m['width']}x{m['height']}@{m['fps']}  "
                      f"{m['color_profile']}  etapy={m['stages_done']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("pyscenedetect").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="studio.index", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index")
    pi.add_argument("paths", nargs="+")
    pi.add_argument("--stages", help=f"lista po przecinku z: {','.join(STAGES)}")
    pi.add_argument("--force", action="store_true", help="powtórz etapy nawet jeśli zrobione")
    pi.set_defaults(fn=cmd_index)

    pq = sub.add_parser("query")
    pq.add_argument("text", nargs="?")
    pq.add_argument("--top", type=int, default=15)
    pq.add_argument("--sharp", type=float, help="kwantyl ostrości do odrzucenia, np. 0.3")
    pq.add_argument("--speech", dest="speech", action="store_true", default=None)
    pq.add_argument("--no-speech", dest="speech", action="store_false")
    pq.add_argument("--people", type=int, help="min. liczba osób")
    pq.add_argument("--min-dur", type=float)
    pq.add_argument("--json", action="store_true")
    pq.set_defaults(fn=cmd_query)

    ps = sub.add_parser("show")
    ps.add_argument("media")
    ps.set_defaults(fn=cmd_show)

    pst = sub.add_parser("stats")
    pst.set_defaults(fn=cmd_stats)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

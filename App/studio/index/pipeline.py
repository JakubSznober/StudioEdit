"""Orkiestracja etapów indeksowania z budżetem GPU: jeden ciężki model naraz."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from rich.console import Console

from ..config import SETTINGS
from .db import Database
from .probe import fingerprint, probe_media
from .proxy import extract_audio, make_proxy, proxy_paths
from .quality import analyze_image, analyze_media
from .shots import detect_shots
from .speech import speech_ratio

log = logging.getLogger(__name__)
console = Console()

STAGES = ("probe", "proxy", "shots", "quality", "speech", "embed", "vision")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def collect_files(paths: Iterable[str | Path]) -> list[Path]:
    exts = set(SETTINGS.media_extensions + SETTINGS.audio_extensions + SETTINGS.image_extensions)
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += [f for f in sorted(p.rglob("*")) if f.is_file() and f.suffix.lower() in exts and not f.name.startswith(".")]
        elif p.is_file() and p.suffix.lower() in exts:
            out.append(p)
    return out


def _needs(db: Database, media_id: int, stage: str, force: bool) -> bool:
    return force or stage not in db.stages_done(media_id)


# ---------------------------------------------------------------- etapy per plik
def stage_probe(db: Database, media_id: int, path: Path) -> None:
    row = probe_media(path)
    db.update_media(media_id, **row)
    db.mark_stage(media_id, "probe")


def stage_proxy(db: Database, media_id: int, path: Path) -> None:
    m = db.get_media_by_id(media_id)
    proxy, audio = proxy_paths(media_id, path)
    if m["kind"] == "video":
        p = make_proxy(path, proxy, m["color_profile"])
        db.update_media(media_id, proxy_path=str(p) if p else None)
    if m["kind"] in ("video", "audio") and m["audio_codec"]:
        a = extract_audio(path, audio)
        db.update_media(media_id, audio_path=str(a) if a else None)
    db.mark_stage(media_id, "proxy")


def stage_shots(db: Database, media_id: int, path: Path) -> None:
    m = db.get_media_by_id(media_id)
    if m["kind"] == "image":
        units = [{"start": 0.0, "end": 0.0, "cut_start": True}]
    elif m["kind"] == "audio":
        # audio dzielimy na jednostki jak długie ujęcie, żeby mieć na czym liczyć speech_ratio
        d = m["duration"] or 0.0
        n = max(1, int(d // SETTINGS.max_shot_len) + 1)
        step = d / n
        units = [{"start": round(k * step, 3), "end": round((k + 1) * step, 3), "cut_start": k == 0} for k in range(n)]
    else:
        src = Path(m["proxy_path"]) if m["proxy_path"] else path
        units = detect_shots(src, m["duration"] or 0.0)
    db.replace_shots(media_id, units)
    db.mark_stage(media_id, "shots")


def stage_quality(db: Database, media_id: int, path: Path) -> None:
    m = db.get_media_by_id(media_id)
    shots = [dict(r) for r in db.list_shots(media_id)]
    kdir = Path(SETTINGS.keyframes_dir) / f"{media_id:06d}"
    if m["kind"] == "image":
        res = [analyze_image(path, kdir / "shot_0000_0.jpg")]
    elif m["kind"] == "audio":
        res = [{} for _ in shots]
    else:
        src = Path(m["proxy_path"]) if m["proxy_path"] else path
        res = analyze_media(src, m["fps"] or 25.0, shots, kdir)
    for shot, r in zip(shots, res):
        r.pop("_frame_dt", None)
        if r:
            db.update_shot(shot["id"], **r)
    db.mark_stage(media_id, "quality")


# ---------------------------------------------------------------- etapy partiami (model ładowany raz)
def batch_speech(db: Database, items: list[tuple[int, Path]]) -> None:
    items = [(mid, p) for mid, p in items if db.get_media_by_id(mid)["audio_path"]]
    if not items:
        for mid, _ in items:
            db.mark_stage(mid, "speech")
        return
    from .speech import Transcriber

    tr = Transcriber()
    try:
        for mid, p in items:
            m = db.get_media_by_id(mid)
            segs, lang = tr.transcribe(Path(m["audio_path"]))
            db.replace_transcript(mid, segs)
            db.update_media(mid, language=lang)
            for shot in db.list_shots(mid):
                db.update_shot(shot["id"], speech_ratio=speech_ratio(segs, shot["start"], shot["end"]))
            console.print(f"  [green]speech[/] {p.name}: {len(segs)} segm., język={lang}")
            db.mark_stage(mid, "speech")
    finally:
        tr.close()


def batch_embed(db: Database, items: list[tuple[int, Path]]) -> None:
    from .embeddings import Embedder

    emb = Embedder()
    try:
        for mid, p in items:
            n = 0
            for shot in db.list_shots(mid):
                kfs = json.loads(shot["keyframes"]) if shot["keyframes"] else []
                v = emb.shot_embedding(kfs)
                if v is not None:
                    db.update_shot(shot["id"], embedding=v)
                    n += 1
            console.print(f"  [green]embed[/] {p.name}: {n} ujęć")
            db.mark_stage(mid, "embed")
    finally:
        emb.close()


def batch_vision(db: Database, items: list[tuple[int, Path]]) -> None:
    from .vision import ShotDescriber

    vd = ShotDescriber()
    try:
        for mid, p in items:
            m = db.get_media_by_id(mid)
            n = 0
            for shot in db.list_shots(mid):
                kfs = json.loads(shot["keyframes"]) if shot["keyframes"] else []
                if not kfs:
                    continue
                hint = f"plik {p.name}, ujęcie {shot['idx'] + 1}, {shot['end'] - shot['start']:.1f} s"
                if m["camera_model"]:
                    hint += f", kamera {m['camera_model']}"
                data = vd.describe(kfs, hint)
                if not data:
                    continue
                db.update_shot(
                    shot["id"], caption=data["opis"], people=int(data.get("ludzie") or 0), place=data.get("miejsce"),
                    action=data.get("akcja"), shot_size=data.get("ujecie"), cam_move=data.get("kamera"),
                    tags=data["tagi"], vlm_json=json.dumps(data, ensure_ascii=False),
                )
                expo = {"za_ciemno": "za ciemno niedoświetlone", "za_jasno": "za jasno prześwietlone",
                        "mieszana": "ekspozycja mieszana"}.get(data.get("ekspozycja", ""), "")
                db.set_shot_text(shot["id"], mid, " ".join([
                    data["opis"], data.get("miejsce", ""), data.get("akcja", ""), " ".join(data["tagi"]),
                    expo, data.get("uwagi", ""),
                ]))
                n += 1
            console.print(f"  [green]vision[/] {p.name}: {n} ujęć opisanych")
            db.mark_stage(mid, "vision")
    finally:
        vd.close()


# ---------------------------------------------------------------- główny przebieg
def index_paths(paths: Iterable[str | Path], stages: Iterable[str] = STAGES, force: bool = False,
                db: Database | None = None) -> list[int]:
    stages = [s for s in STAGES if s in set(stages)]
    own_db = db is None
    db = db or Database(SETTINGS.index_db)
    files = collect_files(paths)
    if not files:
        console.print("[yellow]brak plików do indeksowania[/]")
        return []
    console.print(f"[bold]Indeksowanie {len(files)} plików[/] → {db.path}")

    ids: list[tuple[int, Path]] = []
    for f in files:
        st = f.stat()
        kind_guess = "video" if f.suffix.lower() in SETTINGS.media_extensions else (
            "audio" if f.suffix.lower() in SETTINGS.audio_extensions else "image")
        mid = db.upsert_media(f, fingerprint(f), st.st_size, st.st_mtime, kind_guess)
        if force:
            db.clear_stages(mid, set(stages))
        ids.append((mid, f))

    per_file = {"probe": stage_probe, "proxy": stage_proxy, "shots": stage_shots, "quality": stage_quality}
    for stage in stages:
        if stage not in per_file:
            continue
        for mid, f in ids:
            if _needs(db, mid, stage, force=False):
                try:
                    per_file[stage](db, mid, f)
                    console.print(f"  [green]{stage}[/] {f.name}")
                except Exception as e:  # noqa: BLE001
                    log.exception("%s %s", stage, f)
                    console.print(f"  [red]{stage}[/] {f.name}: {e}")

    for stage, fn in (("speech", batch_speech), ("embed", batch_embed), ("vision", batch_vision)):
        if stage not in stages:
            continue
        todo = [(mid, f) for mid, f in ids if _needs(db, mid, stage, force=False)]
        if todo:
            console.print(f"[bold]{stage}[/]: {len(todo)} plików")
            try:
                fn(db, todo)
            except Exception as e:  # noqa: BLE001
                log.exception("%s", stage)
                console.print(f"  [red]{stage}[/]: {e}")

    for mid, _ in ids:
        db.update_media(mid, indexed_at=_now())
    if own_db:
        db.close()
    return [mid for mid, _ in ids]

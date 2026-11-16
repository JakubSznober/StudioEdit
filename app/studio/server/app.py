"""Serwer HTTP/WebSocket StudioEdit (FastAPI) - API dla przeglądarki + serwowanie frontendu."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__, health, models_catalog, paths, pipelines, storage
from ..agents.registry import AGENT_BY_KEY
from ..config import EFFORTS, get_config
from ..events import bus, log, toast
from ..gpu import GPUMonitor
from ..project.manager import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT, projects
from ..project.model import Timeline
from ..services import register_vram_releasers, services
from ..tasks import orchestrator

_last_loudness: dict[str, dict] = {}


def _on_event(evt: dict) -> None:
    if evt["type"] == "loudness":
        pid = evt["data"].get("project_id")
        if pid:
            _last_loudness[pid] = evt["data"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    paths.ensure_dirs()
    register_vram_releasers()
    bus.add_listener(_on_event)
    mon = GPUMonitor()
    mon.start()
    (paths.LOGS / "server.pid").write_text(str(os.getpid()), encoding="utf-8")
    log("system", f"StudioEdit {__version__} uruchomiony na http://{get_config().get('server.host')}:"
                  f"{get_config().get('server.port')}", path="Serwer")
    # rozgrzej Ollamę w tle (Auto-Wake) - nie blokuje startu
    if services["ollama"].cfg.get("auto_wake", True) and services["ollama"].installed():
        threading.Thread(target=lambda: services["ollama"].start(wait=False), daemon=True).start()
    yield
    mon.stop()
    services.stop_all()


app = FastAPI(title="StudioEdit", version=__version__, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2048)


def _proj(pid: str):
    try:
        return projects.get(pid)
    except KeyError:
        raise HTTPException(404, f"Projekt {pid} nie istnieje")


def _media(p, mid: str):
    try:
        return p.get_media(mid)
    except KeyError:
        raise HTTPException(404, "Nie znaleziono pliku w bibliotece")


@app.exception_handler(Exception)
async def _err(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    log("system", f"Błąd API {request.url.path}: {exc}", path="Serwer", level="error")
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


# =============================================================================================
# System
# =============================================================================================
@app.get("/api/health")
def api_health():
    return {"ok": True, "version": __version__}


@app.get("/api/system/status")
def api_status():
    return health.system_status()


@app.post("/api/system/shutdown")
def api_shutdown():
    def _bye():
        time.sleep(0.8)
        services.stop_all()
        os._exit(0)
    threading.Thread(target=_bye, daemon=True).start()
    return {"ok": True}


@app.get("/api/gpu")
def api_gpu():
    from .. import gpu

    return gpu.stats()


@app.get("/api/services")
def api_services():
    return services.all_info()


@app.post("/api/services/{name}/start")
def api_service_start(name: str):
    if name not in services.services:
        raise HTTPException(404, "Nieznana usługa")
    ok = services[name].start(wait=True, timeout=240)
    if not ok:
        raise HTTPException(500, services[name].error or "Nie udało się uruchomić")
    return services[name].info()


@app.post("/api/services/{name}/stop")
def api_service_stop(name: str):
    if name not in services.services:
        raise HTTPException(404, "Nieznana usługa")
    from ..vram import vram

    if (vram.owner or "").startswith("ollama" if name == "ollama" else "comfy"):
        vram.unload_all("zatrzymanie usługi")
    services[name].stop()
    return services[name].info()


@app.patch("/api/services/{name}")
def api_service_patch(name: str, body: dict = Body(...)):
    if name not in services.services:
        raise HTTPException(404, "Nieznana usługa")
    allowed = {k: v for k, v in body.items() if k in ("auto_wake", "auto_sleep", "url", "enabled", "keep_alive")}
    get_config().update({"services": {name: allowed}})
    return services[name].info()


@app.get("/api/models")
def api_models():
    return models_catalog.catalog()


@app.post("/api/models/{key:path}/install")
def api_model_install(key: str):
    group = f"model-{int(time.time())}"
    orchestrator.add("archivist", "Modele", f"Instalacja modelu {key}",
                     lambda ctx: models_catalog.install(key, progress=lambda p: ctx.progress(p)), group=group)
    return {"group": group}


@app.delete("/api/models/{key:path}")
def api_model_delete(key: str):
    models_catalog.remove(key)
    return {"ok": True}


@app.get("/api/config")
def api_config():
    return get_config().data


@app.patch("/api/config")
def api_config_patch(body: dict = Body(...)):
    return get_config().update(body)


@app.get("/api/agents")
def api_agents():
    return health.agents()


@app.patch("/api/agents/{key}")
def api_agent_patch(key: str, body: dict = Body(...)):
    if key not in AGENT_BY_KEY:
        raise HTTPException(404, "Nieznany agent")
    patch: dict[str, Any] = {}
    if "effort" in body:
        if body["effort"] not in EFFORTS:
            raise HTTPException(400, "effort musi być: low, medium, high, max")
        patch["effort"] = body["effort"]
    if "workers" in body:
        n = max(1, min(16, int(body["workers"])))
        patch["workers"] = n
        orchestrator.pool(key).resize(n)
    get_config().update({"agents": {key: patch}})
    return next(a for a in health.agents() if a["key"] == key)


@app.get("/api/storage")
def api_storage():
    return storage.usage()


@app.post("/api/storage/clean")
def api_storage_clean(body: dict = Body(...)):
    freed = storage.clean(body.get("target", "cache"), body.get("project_id"))
    toast(f"Zwolniono {freed:.2f} GB", "success")
    return {"freed_gb": freed}


@app.get("/api/tasks")
def api_tasks(project_id: str | None = None, group: str | None = None):
    return orchestrator.list(project_id=project_id, group=group)


@app.post("/api/tasks/{task_id}/cancel")
def api_task_cancel(task_id: str):
    return {"cancelled": orchestrator.cancel(task_id=task_id)}


@app.post("/api/tasks/cancel")
def api_tasks_cancel(body: dict = Body(...)):
    return {"cancelled": orchestrator.cancel(group=body.get("group"), project_id=body.get("project_id"))}


@app.get("/api/looks")
def api_looks():
    try:
        from ..color.looks import looks

        return looks()
    except Exception:
        return [{"id": "teal_orange", "name": "Teal & Orange", "description": "kinowy", "builtin": True},
                {"id": "warm_film", "name": "Ciepły film", "description": "analogowy", "builtin": True},
                {"id": "clean_doc", "name": "Czysty dokument", "description": "naturalny", "builtin": True}]


@app.get("/api/logs")
def api_logs(project_id: str | None = None, agent: str | None = None, limit: int = 300):
    evts = [e for e in bus.snapshot(project_id, limit=2000) if e["type"] == "agent.log"]
    if agent:
        evts = [e for e in evts if e["data"].get("agent") == agent]
    return evts[-limit:]


# =============================================================================================
# Projekty
# =============================================================================================
@app.get("/api/projects")
def api_projects():
    return projects.list()


@app.post("/api/projects")
def api_project_create(body: dict = Body(...)):
    name = (body.get("name") or "").strip() or "Nowy projekt"
    kw = {k: body.get(k) for k in ("scale", "platforms", "style", "target_duration", "ingest_mode", "notes")}
    p = projects.create(name, **{k: v for k, v in kw.items() if v not in (None, "")})
    return p.summary()


@app.get("/api/projects/{pid}")
def api_project(pid: str):
    return _proj(pid).summary()


@app.patch("/api/projects/{pid}")
def api_project_patch(pid: str, body: dict = Body(...)):
    p = _proj(pid)
    p.update_meta(**{k: v for k, v in body.items() if k in ("name", "scale", "platforms", "style", "target_duration",
                                                             "ingest_mode", "notes", "look")})
    return p.summary()


@app.delete("/api/projects/{pid}")
def api_project_delete(pid: str):
    _proj(pid)
    orchestrator.cancel(project_id=pid)
    dst = projects.delete(pid)
    return {"ok": True, "moved_to": str(dst)}


@app.post("/api/projects/{pid}/activate")
def api_project_activate(pid: str):
    _proj(pid)
    projects.set_active(pid)
    return projects.get(pid).summary()


def _media_public(m) -> dict:
    d = m.model_dump(mode="json")
    tr = d.pop("transcript", None)
    d["transcript_summary"] = None
    if tr:
        sents = tr.get("sentences", [])
        d["transcript_summary"] = {"sentences": len(sents), "speakers": tr.get("speakers", []),
                                   "words": sum(len(s.get("words", [])) for s in sents),
                                   "preview": " ".join(s.get("text", "") for s in sents[:3])[:300]}
    for sh in d.get("analysis", {}).get("shots", []):
        sh.pop("keyframes", None)
    d.get("analysis", {}).pop("audio_rms", None)
    d.get("analysis", {}).pop("motion_curve", None)
    return d


@app.get("/api/projects/{pid}/media")
def api_media(pid: str):
    return [_media_public(m) for m in _proj(pid).media()]


@app.get("/api/projects/{pid}/media/{mid}")
def api_media_one(pid: str, mid: str):
    return _media(_proj(pid), mid).model_dump(mode="json")


def _collect_files(paths_in: list[str]) -> list[Path]:
    exts = VIDEO_EXT | AUDIO_EXT | IMAGE_EXT
    out = []
    for s in paths_in:
        p = Path(str(s).strip().strip('"'))
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in exts and "Odrzucone_Duble" not in f.parts:
                    out.append(f)
        elif p.is_file() and p.suffix.lower() in exts:
            out.append(p)
    return out


@app.post("/api/projects/{pid}/media/import")
def api_media_import(pid: str, body: dict = Body(...)):
    p = _proj(pid)
    files = _collect_files(body.get("paths") or [])
    if not files:
        raise HTTPException(400, "Nie znaleziono obsługiwanych plików wideo/audio/obrazów pod podanymi ścieżkami")
    added = []
    for f in files:
        try:
            added.append(p.add_media_file(f, copy=body.get("copy"), bin_name=body.get("bin")))
        except Exception as e:
            log("archivist", f"Pominięto {f.name}: {e}", path="Import", level="warn", project_id=pid)
    group = pipelines.analyze(p, [m.id for m in added]) if body.get("analyze", True) and added else None
    return {"added": [_media_public(m) for m in added], "group": group}


@app.post("/api/projects/{pid}/media/upload")
async def api_media_upload(pid: str, files: list[UploadFile] = File(...), analyze: bool = True):
    p = _proj(pid)
    up = p.dir("footage", "uploads")
    saved = []
    for f in files:
        name = Path(f.filename or "plik").name
        dst = up / name
        i = 1
        while dst.exists():
            dst = up / f"{Path(name).stem}_{i}{Path(name).suffix}"
            i += 1
        with open(dst, "wb") as out:
            while chunk := await f.read(8 * 1024 * 1024):
                out.write(chunk)
        saved.append(dst)
    added = []
    for f in saved:
        try:
            added.append(p.add_media_file(f, copy=False))
        except Exception as e:
            log("archivist", f"Pominięto {f.name}: {e}", path="Import", level="warn", project_id=pid)
    group = pipelines.analyze(p, [m.id for m in added]) if analyze and added else None
    return {"added": [_media_public(m) for m in added], "group": group}


@app.patch("/api/projects/{pid}/media/{mid}")
def api_media_patch(pid: str, mid: str, body: dict = Body(...)):
    p = _proj(pid)
    _media(p, mid)

    def fn(m):
        for k in ("bin", "rating", "name"):
            if k in body:
                setattr(m, k, body[k])
    return _media_public(p.update_media(mid, fn))


@app.post("/api/projects/{pid}/media/{mid}/reject")
def api_media_reject(pid: str, mid: str, body: dict = Body(default={})):
    p = _proj(pid)
    _media(p, mid)
    return _media_public(p.reject_media(mid, body.get("reason", "odrzucone ręcznie")))


@app.delete("/api/projects/{pid}/media/{mid}")
def api_media_delete(pid: str, mid: str):
    p = _proj(pid)
    _media(p, mid)
    p.remove_media(mid)
    return {"ok": True}


@app.get("/api/projects/{pid}/media/{mid}/thumb")
def api_media_thumb(pid: str, mid: str):
    m = _media(_proj(pid), mid)
    if m.thumb and Path(m.thumb).exists():
        return FileResponse(m.thumb, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
    if m.kind == "image":
        return FileResponse(m.path)
    raise HTTPException(404, "Miniatura jeszcze nie gotowa")


@app.get("/api/projects/{pid}/media/{mid}/proxy")
def api_media_proxy(pid: str, mid: str):
    m = _media(_proj(pid), mid)
    src = m.proxy if m.proxy and Path(m.proxy).exists() else m.path
    return FileResponse(src, media_type="video/mp4" if src.lower().endswith(".mp4") else None)


@app.get("/api/projects/{pid}/media/{mid}/waveform")
def api_media_wave(pid: str, mid: str):
    m = _media(_proj(pid), mid)
    if m.waveform and Path(m.waveform).exists():
        return FileResponse(m.waveform, media_type="image/png")
    raise HTTPException(404, "Brak waveformu")


@app.get("/api/projects/{pid}/file")
def api_project_file(pid: str, rel: str):
    p = _proj(pid)
    f = (p.root / rel).resolve()
    if not str(f).startswith(str(p.root.resolve())) or not f.is_file():
        raise HTTPException(404, "Nie znaleziono pliku")
    return FileResponse(f)


@app.post("/api/projects/{pid}/analyze")
def api_analyze(pid: str, body: dict = Body(default={})):
    p = _proj(pid)
    return {"group": pipelines.analyze(p, body.get("media_ids"), force=bool(body.get("force")))}


@app.post("/api/projects/{pid}/montage")
def api_montage(pid: str, body: dict = Body(...)):
    p = _proj(pid)
    if not p.media():
        raise HTTPException(400, "Projekt nie ma jeszcze materiału — zaimportuj nagrania")
    opts = {k: body.get(k) for k in ("brief", "target_duration", "platforms", "style", "look", "music_mood",
                                      "subtitles", "music")}
    if opts.get("target_duration") in ("", 0):
        opts["target_duration"] = None
    group = pipelines.montage(p, opts)
    p.add_chat({"role": "system", "text": f"Rozpoczęto pełny montaż: {opts.get('brief') or 'bez briefu'}", "group": group})
    return {"group": group}


@app.get("/api/projects/{pid}/pipeline/{group}")
def api_pipeline(pid: str, group: str):
    _proj(pid)
    return orchestrator.group_status(group)


@app.get("/api/projects/{pid}/timeline")
def api_timeline(pid: str):
    return _proj(pid).timeline().to_json()


@app.get("/api/projects/{pid}/timeline/versions")
def api_versions(pid: str):
    return _proj(pid).history()


@app.post("/api/projects/{pid}/timeline/undo")
def api_undo(pid: str):
    p = _proj(pid)
    tl = p.undo()
    if tl is None:
        raise HTTPException(400, "Brak wcześniejszej wersji")
    return tl.to_json()


@app.post("/api/projects/{pid}/timeline/redo")
def api_redo(pid: str):
    p = _proj(pid)
    tl = p.redo()
    if tl is None:
        raise HTTPException(400, "Nic do ponowienia")
    return tl.to_json()


@app.post("/api/projects/{pid}/timeline/checkout/{version}")
def api_checkout(pid: str, version: int):
    try:
        return _proj(pid).checkout(version).to_json()
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.patch("/api/projects/{pid}/timeline/clips/{clip_id}")
def api_clip_patch(pid: str, clip_id: str, body: dict = Body(...)):
    p = _proj(pid)
    holder = {}

    def mut(tl: Timeline):
        found = tl.find_clip(clip_id)
        if not found:
            raise HTTPException(404, "Nie znaleziono klipu")
        _k, c = found
        for k, v in body.items():
            if k in ("id", "duration", "end"):
                continue
            if hasattr(c, k):
                field_type = type(getattr(c, k))
                try:
                    if hasattr(field_type, "model_validate") and isinstance(v, dict):
                        v = field_type.model_validate(v)
                    setattr(c, k, v)
                except Exception as e:
                    raise HTTPException(400, f"Pole {k}: {e}")
        holder["ok"] = True
        return tl.normalize()
    tl = p.mutate_timeline(mut, f"Ręczna zmiana klipu {clip_id}", "user")
    return tl.to_json()


@app.post("/api/projects/{pid}/preview/render")
def api_preview_render(pid: str, body: dict = Body(default={})):
    p = _proj(pid)
    if not p.timeline().tracks.get("V1"):
        raise HTTPException(400, "Oś czasu jest pusta — najpierw zmontuj film")
    return {"group": pipelines.preview(p, body.get("variant"))}


@app.get("/api/projects/{pid}/preview")
def api_preview(pid: str, variant: str | None = None):
    p = _proj(pid)
    tl = p.timeline()
    v = variant or (tl.variants[0].name if tl.variants else "youtube")
    try:
        from ..agents.render import preview_info

        info = preview_info(p, v)
    except Exception:
        f = p.root / "renders" / f"preview_{v}.mp4"
        info = {"url": f"renders/preview_{v}.mp4" if f.exists() else None, "version": None, "variant": v,
                "rendered_at": f.stat().st_mtime if f.exists() else None, "stale": True}
    if info.get("url") and not str(info["url"]).startswith("/api/"):
        rel = str(info["url"]).replace("\\", "/")
        info["url"] = f"/api/projects/{pid}/file?rel={rel}&v={int(info.get('rendered_at') or 0)}"
    return info


@app.get("/api/projects/{pid}/frame")
def api_frame(pid: str, t: float = 0.0, variant: str | None = None, mode: str = "after", width: int = 960):
    p = _proj(pid)
    tl = p.timeline()
    out = p.dir("cache", "frames") / f"f_{tl.version}_{variant or 'v'}_{mode}_{int(t * 1000)}_{width}.jpg"
    if not out.exists():
        try:
            from ..render.frame import render_frame

            var = next((v for v in tl.variants if v.name == variant), tl.variants[0] if tl.variants else None)
            render_frame(p, tl, t, out, variant=var, before=(mode == "before"), width=width)
        except HTTPException:
            raise
        except Exception as e:
            # fallback: klatka źródła z V1
            from ..media.ffmpeg import extract_frame

            clip = next((c for c in tl.v("V1") if c.start <= t < c.end), None)
            if clip is None:
                raise HTTPException(404, f"Brak klatki w {t:.2f} s ({e})")
            m = p.get_media(clip.media_id)
            extract_frame(m.proxy or m.path, clip.src_in + (t - clip.start) * clip.speed, out, width=width)
    return FileResponse(out, media_type="image/jpeg")


@app.get("/api/projects/{pid}/chat")
def api_chat_history(pid: str):
    return _proj(pid).chat_history()


@app.post("/api/projects/{pid}/chat")
def api_chat(pid: str, body: dict = Body(...)):
    p = _proj(pid)
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Pusta wiadomość")
    from ..agents.producer import handle_chat

    rng = body.get("range")
    if rng and (rng.get("in") is None or rng.get("out") is None):
        rng = None
    return handle_chat(p, text, rng)


@app.post("/api/projects/{pid}/export")
def api_export(pid: str, body: dict = Body(default={})):
    p = _proj(pid)
    tl = p.timeline()
    if not tl.tracks.get("V1"):
        raise HTTPException(400, "Oś czasu jest pusta")
    variants = body.get("variants") or [v.name for v in tl.variants]
    settings = {k: body[k] for k in ("codec", "resolution", "fps", "container", "audio_codec", "quality") if k in body}
    return {"group": pipelines.export(p, variants, settings)}


@app.get("/api/projects/{pid}/exports")
def api_exports(pid: str):
    p = _proj(pid)
    d = paths.EXPORTS / pid
    out = []
    if d.exists():
        for f in sorted(d.rglob("*"), key=lambda x: -x.stat().st_mtime):
            if f.is_file() and f.suffix.lower() in (".mp4", ".mov", ".mkv", ".xml", ".jsx", ".srt", ".edl", ".wav",
                                                     ".aep", ".json", ".otio"):
                variant = next((v.name for v in p.timeline().variants if f"_{v.name}_" in f.name), None)
                out.append({"name": f.name, "path": str(f), "size_mb": round(f.stat().st_size / 1024**2, 1),
                            "created": f.stat().st_mtime, "variant": variant,
                            "url": f"/api/exports/{pid}/{f.relative_to(d).as_posix()}"})
    return out


@app.get("/api/exports/{pid}/{rel:path}")
def api_export_file(pid: str, rel: str):
    base = (paths.EXPORTS / pid).resolve()
    f = (base / rel).resolve()
    if not str(f).startswith(str(base)) or not f.is_file():
        raise HTTPException(404, "Brak pliku")
    return FileResponse(f, filename=f.name)


@app.post("/api/projects/{pid}/export/adobe")
def api_export_adobe(pid: str, body: dict = Body(default={})):
    p = _proj(pid)
    return {"group": pipelines.adobe(p, body.get("targets") or ["premiere", "ae_jsx"])}


@app.post("/api/projects/{pid}/qc")
def api_qc_run(pid: str):
    return {"group": pipelines.run_qc(_proj(pid))}


@app.get("/api/projects/{pid}/qc")
def api_qc_last(pid: str):
    f = _proj(pid).root / "qc" / "last.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


@app.get("/api/projects/{pid}/loudness")
def api_loudness(pid: str):
    _proj(pid)
    if pid in _last_loudness:
        return _last_loudness[pid]
    f = _proj(pid).root / "renders" / "loudness.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


# =============================================================================================
# AI Developer
# =============================================================================================
@app.get("/api/dev/diagnostics")
def api_dev_diag():
    from .. import devtools

    return devtools.diagnostics()


@app.post("/api/dev/propose")
def api_dev_propose(body: dict = Body(...)):
    from .. import devtools

    if not body.get("request"):
        raise HTTPException(400, "Opisz, co zmienić")
    return devtools.propose(body["request"], body.get("files"))


@app.post("/api/dev/apply/{prop_id}")
def api_dev_apply(prop_id: str):
    from .. import devtools

    try:
        return devtools.apply(prop_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/api/dev/backups")
def api_dev_backups():
    from .. import devtools

    return devtools.backups()


@app.post("/api/dev/restore/{bid}")
def api_dev_restore(bid: str):
    from .. import devtools

    return devtools.restore(bid)


@app.post("/api/dev/tests")
def api_dev_tests():
    from .. import devtools

    group = f"tests-{int(time.time())}"

    def fn(ctx):
        res = devtools.run_tests()
        ctx.log(("Testy OK" if res["ok"] else "Testy NIEUDANE") + "\n" + res["output"][-1500:],
                level="info" if res["ok"] else "error")
        if not res["ok"]:
            raise RuntimeError("Testy nie przeszły")
        return res
    orchestrator.add("qc", "Testy", "AI Developer: testy jednostkowe", fn, group=group)
    return {"group": group}


# =============================================================================================
# WebSocket
# =============================================================================================
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, project_id: str | None = Query(default=None)):
    await ws.accept()
    q = bus.subscribe()
    try:
        await ws.send_text(json.dumps({"type": "snapshot", "data": {
            "events": bus.snapshot(project_id), "tasks": orchestrator.list(project_id=project_id, limit=300)}},
            ensure_ascii=False, default=str))

        async def reader():
            while True:
                msg = await ws.receive_text()
                try:
                    d = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if d.get("type") == "ping":
                    await ws.send_text('{"type":"pong"}')

        rt = asyncio.create_task(reader())
        try:
            while True:
                evt = await q.get()
                epid = evt["data"].get("project_id") if isinstance(evt.get("data"), dict) else None
                if project_id and epid and epid != project_id:
                    continue
                await ws.send_text(json.dumps(evt, ensure_ascii=False, default=str))
        finally:
            rt.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)


# =============================================================================================
# Frontend
# =============================================================================================
if paths.WEB_DIST.exists() and (paths.WEB_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=paths.WEB_DIST / "assets"), name="assets") \
        if (paths.WEB_DIST / "assets").exists() else None


@app.get("/{full:path}", include_in_schema=False)
def spa(full: str):
    if full.startswith("api/") or full == "ws":
        raise HTTPException(404, "Nie znaleziono")
    f = (paths.WEB_DIST / full).resolve()
    if full and f.is_file() and str(f).startswith(str(paths.WEB_DIST.resolve())):
        return FileResponse(f)
    idx = paths.WEB_DIST / "index.html"
    if idx.exists():
        return FileResponse(idx, headers={"Cache-Control": "no-cache"})
    return HTMLResponse("<h1>StudioEdit</h1><p>Frontend nie jest zbudowany. Uruchom scripts\\build_frontend.ps1</p>")

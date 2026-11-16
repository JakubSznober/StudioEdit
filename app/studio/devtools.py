"""AI Developer - tryb bezpieczny: diagnostyka, propozycje zmian z diffem, kopie zapasowe przed każdą zmianą,
zatwierdzanie przez użytkownika, przywracanie, testy."""
from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import llm, paths
from .events import log

DEV_BACKUPS = paths.BACKUPS / "dev"
PROPOSALS: dict[str, dict] = {}

ALLOWED_ROOTS = [paths.APP / "studio", paths.APP / "tests", paths.ROOT / "frontend" / "src", paths.ROOT / "scripts",
                 paths.CONFIG_DIR]


def _safe(rel: str) -> Path:
    p = (paths.APP / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    if not any(str(p).startswith(str(r.resolve())) for r in ALLOWED_ROOTS):
        raise PermissionError(f"Ścieżka poza dozwolonym obszarem: {rel}")
    return p


def diagnostics() -> dict:
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    add("Python", True, sys.version.split()[0])
    try:
        import torch

        add("PyTorch CUDA", torch.cuda.is_available(),
            f"{torch.__version__} · {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'brak GPU'}")
    except Exception as e:
        add("PyTorch CUDA", False, e)
    from .media import ffmpeg as ff

    add("FFmpeg", paths.FFMPEG.exists(), paths.FFMPEG)
    add("NVENC", ff.nvenc_ok(), "sprzętowe kodowanie H.264/HEVC/AV1")
    add("ffprobe", not getattr(ff, "_FFPROBE_BLOCKED", False),
        "zablokowany przez Smart App Control — używany PyAV" if getattr(ff, "_FFPROBE_BLOCKED", False) else "OK lub nieużywany")
    for f in ("lut3d", "ass", "vidstabdetect", "arnndn", "xfade", "minterpolate", "zscale", "loudnorm"):
        add(f"Filtr ffmpeg: {f}", f in ff.filters())
    from .services import services

    for s in services.all_info():
        add(f"Usługa {s['label']}", s["installed"], f"{s['state']} · {s['url']}")
    from .models_catalog import catalog

    for m in catalog():
        add(f"Model: {m['name']}", m["installed"], m["description"])
    from .health import _module_code_ok

    for k, ok in _module_code_ok().items():
        add(f"Moduł {k}", ok)
    du = shutil.disk_usage(paths.ROOT)
    add("Wolne miejsce", du.free > 50 * 1024**3, f"{du.free / 1024**3:.0f} GB")
    return {"checks": checks}


PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "files": {"type": "array", "items": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    },
    "required": ["summary", "files"],
}


def propose(request: str, files: list[str] | None = None) -> dict:
    files = files or []
    ctx_parts = []
    for rel in files[:4]:
        p = _safe(rel)
        if p.exists():
            ctx_parts.append(f"=== {rel} ===\n{p.read_text(encoding='utf-8')[:24000]}")
    prompt = (f"Jesteś programistą systemu StudioEdit (Python 3.12, pakiet studio w app/studio, FastAPI). "
              f"Prośba użytkownika: {request}\n\nZwróć PEŁNĄ nową treść każdego zmienianego pliku (ścieżka względna "
              f"do katalogu app/, np. studio/edit/ops.py). Nie usuwaj istniejącej funkcjonalności. Opisz zmiany "
              f"po polsku w 'summary'.\n\n" + "\n\n".join(ctx_parts))
    res = llm.chat_json([{"role": "user", "content": prompt}], PROPOSE_SCHEMA, role="director_max", agent="producer",
                        label="AI Developer", temperature=0.2, timeout=1800)
    diffs = []
    out_files = []
    for f in res.get("files", []):
        p = _safe(f["path"])
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        d = "".join(difflib.unified_diff(old.splitlines(True), f["content"].splitlines(True),
                                         fromfile=f"a/{f['path']}", tofile=f"b/{f['path']}"))
        diffs.append(d)
        out_files.append({"path": f["path"], "content": f["content"]})
    pid = uuid.uuid4().hex[:10]
    PROPOSALS[pid] = {"id": pid, "summary": res.get("summary", ""), "files": out_files, "created": time.time()}
    return {"id": pid, "summary": res.get("summary", ""), "diff": "\n".join(diffs), "files": [f["path"] for f in out_files]}


def backup(files: list[str], note: str = "") -> str:
    bid = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    d = DEV_BACKUPS / bid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": bid, "ts": time.time(), "note": note, "files": []}
    for rel in files:
        p = _safe(rel)
        if p.exists():
            dst = d / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            meta["files"].append({"path": rel, "existed": True})
        else:
            meta["files"].append({"path": rel, "existed": False})
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return bid


def apply(proposal_id: str) -> dict:
    prop = PROPOSALS.get(proposal_id)
    if not prop:
        raise KeyError("Propozycja wygasła lub nie istnieje")
    bid = backup([f["path"] for f in prop["files"]], note=prop["summary"])
    for f in prop["files"]:
        p = _safe(f["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".py":
            compile(f["content"], str(p), "exec")  # odrzuć błędną składnię zanim zapiszemy
        p.write_text(f["content"], encoding="utf-8")
    PROPOSALS.pop(proposal_id, None)
    log("system", f"AI Developer: zastosowano zmiany ({len(prop['files'])} plików), kopia {bid}", path="AI Developer")
    return {"ok": True, "backup": bid, "restart_required": any(f["path"].endswith(".py") for f in prop["files"])}


def backups() -> list[dict]:
    out = []
    if DEV_BACKUPS.exists():
        for d in sorted(DEV_BACKUPS.iterdir(), reverse=True):
            mf = d / "meta.json"
            if mf.exists():
                out.append(json.loads(mf.read_text(encoding="utf-8")))
    return out


def restore(bid: str) -> dict:
    d = DEV_BACKUPS / bid
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    backup([f["path"] for f in meta["files"]], note=f"przed przywróceniem {bid}")
    for f in meta["files"]:
        p = _safe(f["path"])
        if f["existed"]:
            shutil.copy2(d / "files" / f["path"], p)
        elif p.exists():
            p.unlink()
    return {"ok": True}


def run_tests(progress=None) -> dict:
    py = sys.executable
    p = subprocess.run([py, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider", "tests"],
                       cwd=str(paths.APP), capture_output=True, text=True, timeout=1800,
                       creationflags=0x08000000 if sys.platform == "win32" else 0)
    tail = (p.stdout + p.stderr)[-6000:]
    return {"ok": p.returncode == 0, "output": tail}

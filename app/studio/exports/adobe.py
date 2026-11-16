"""Wykrywanie zainstalowanych programów Adobe (Premiere Pro, After Effects)."""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

from ..config import get_config

PF = [os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"]


def _version_key(path: str) -> tuple:
    m = re.search(r"(\d{4}|\d+(?:\.\d+)?)", Path(path).parts[-2] if "Support Files" not in path
                  else Path(path).parts[-3])
    try:
        return (float(m.group(1)) if m else 0.0, path)
    except ValueError:
        return (0.0, path)


def _find(patterns: list[str]) -> list[str]:
    found: set[str] = set()
    for base in dict.fromkeys(PF):
        for pat in patterns:
            found.update(glob.glob(os.path.join(base, pat)))
    return sorted(found, key=_version_key)


def find_adobe() -> dict:
    """{"premiere": path|None, "aftereffects": path|None, "versions": {"premiere": [...], "aftereffects": [...]}}"""
    cfg = get_config()
    pr = _find([r"Adobe\Adobe Premiere Pro*\Adobe Premiere Pro.exe"])
    ae = _find([r"Adobe\Adobe After Effects*\Support Files\AfterFX.exe"])
    pr_cfg = str(cfg.get("adobe.premiere_exe", "auto") or "auto")
    ae_cfg = str(cfg.get("adobe.afterfx_exe", "auto") or "auto")
    premiere = pr_cfg if pr_cfg != "auto" and Path(pr_cfg).exists() else (pr[-1] if pr else None)
    aftereffects = ae_cfg if ae_cfg != "auto" and Path(ae_cfg).exists() else (ae[-1] if ae else None)

    def ver(p: str) -> str:
        part = Path(p).parts[-3] if "Support Files" in p else Path(p).parts[-2]
        return part.replace("Adobe ", "")

    return {"premiere": premiere, "aftereffects": aftereffects,
            "versions": {"premiere": [ver(p) for p in pr], "aftereffects": [ver(p) for p in ae]}}

"""Obszar VFX: stabilizacja, maski SAM 2.1, usuwanie obiektów (LaMa), rozmycie, B-Roll Wan 2.2, śledzenie.
Kontrakt: docs/ARCHITECTURE.md → VFX. Importy leniwe (ciężkie biblioteki ładowane dopiero przy użyciu)."""
from __future__ import annotations

_EXPORTS = {
    "stabilize": "stabilize", "estimate_shake": "stabilize",
    "remove_object": "inpaint", "blur_region": "inpaint",
    "segment_object": "segment",
    "generate_broll": "broll",
    "track_subject": "track",
}


def __getattr__(name: str):
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(name)
    import importlib

    return getattr(importlib.import_module(f"{__name__}.{mod}"), name)

"""Kontrola jakości renderu (QC) i automatyczne poprawki."""
from __future__ import annotations


def run_qc(*a, **kw):
    from .checks import run_qc as _f

    return _f(*a, **kw)


def apply_fixes(*a, **kw):
    from .fixes import apply_fixes as _f

    return _f(*a, **kw)


__all__ = ["run_qc", "apply_fixes"]

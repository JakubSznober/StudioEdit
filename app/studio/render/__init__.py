"""Render osi czasu (plastry, przeliczanie przyrostowe), klatki podglądu i presety platform."""
from __future__ import annotations


def render_timeline(*a, **kw):
    from .engine import render_timeline as _f

    return _f(*a, **kw)


def render_frame(*a, **kw):
    from .frame import render_frame as _f

    return _f(*a, **kw)


def variant_for(name: str):
    from .presets import variant_for as _f

    return _f(name)


__all__ = ["render_timeline", "render_frame", "variant_for"]

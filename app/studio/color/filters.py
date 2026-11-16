"""Łańcuch filtrów ffmpeg dla klipu: LUT 3D gradu + lokalne korekcje (niebo, relighting).

Kontrakt (ARCHITECTURE.md): `video_filter(clip, media, project) -> str` - "" gdy brak korekcji.
Wejście łańcucha: klatki już przeskalowane do docelowej rozdzielczości (dowolny format piksela).
Wyjście (gdy coś jest stosowane): yuv420p10le, BT.709, zakres TV (oznaczone setparams).

Precyzja: dla źródeł 10-bit / LOG / HDR LUT liczony w gbrp16le (bez bandingu), dla 8-bit Rec.709
w gbrp (szybciej). lut3d z interpolacją tetrahedralną.
"""
from __future__ import annotations

from pathlib import Path

from ..media.ffmpeg import escape_filter_path, media_info
from ..project.model import Grade, MediaItem, VClip
from . import analyze, relight
from .lut import LOG_PROFILES, _profile, compile_grade_lut, grade_hash

_NEUTRAL_SKIP = {"lut", "match", "match_to", "relight", "sky_darken", "skin_protect", "look_strength"}


def grade_is_neutral(grade: Grade) -> bool:
    """True, gdy grade nie wymaga LUT (wartości domyślne, bez looku i bez profilu LOG)."""
    d = grade.model_dump()
    ref = Grade().model_dump()
    for k, v in d.items():
        if k in _NEUTRAL_SKIP:
            continue
        if k == "input_profile":
            if str(v or "rec709").lower() not in ("rec709", ""):
                return False
            continue
        if v != ref.get(k):
            return False
    return not grade.match


def _matrix(info: dict) -> str:
    cs = str(info.get("color_space") or "").lower()
    if cs.startswith("bt2020"):
        return "bt2020"
    if cs in ("smpte170m", "bt470bg", "bt601"):
        return "bt601"
    if cs in ("bt709",):
        return "bt709"
    return "bt709" if int(info.get("height") or 1080) >= 700 else "bt601"


def face_box_for(media: MediaItem, src_in: float, src_out: float) -> list[float] | None:
    """face_box z ujęcia (Shot) pokrywającego środek klipu."""
    mid = (src_in + src_out) / 2
    best = None
    for sh in media.analysis.shots or []:
        if sh.face_box and sh.s <= mid <= sh.e:
            return list(sh.face_box)
        if sh.face_box and sh.s < src_out and sh.e > src_in:
            best = best or list(sh.face_box)
    return best


def stats_for(project, media: MediaItem, src_in: float, src_out: float, samples: int = 6) -> dict:
    return analyze.load_or_compute(Path(project.cache) / "color" / "stats", media.id, media.path,
                                   t0=float(src_in), t1=float(src_out), samples=samples)


def ensure_lut(clip: VClip, media: MediaItem, project, *, size: int = 33) -> str | None:
    """Ścieżka LUT klipu: istniejący grade.lut albo kompilacja w locie (gdy grade zmieniony bez LUT)."""
    g = clip.grade
    if g.lut and Path(g.lut).exists():
        return g.lut
    if grade_is_neutral(g):
        return None
    st = stats_for(project, media, clip.src_in, clip.src_out)
    ref = g.match.get("ref") if isinstance(g.match, dict) else None
    out = Path(project.cache) / "luts" / f"{clip.id}_{grade_hash(g, st, ref, size)}.cube"
    if not out.exists():
        compile_grade_lut(g, st, out, size=size, ref_stats=ref)
    return str(out)


def lut_chain(lut_path: str | Path, info: dict, profile: str = "rec709") -> list[str]:
    hi = int(info.get("bit_depth") or 8) > 8 or profile in LOG_PROFILES or profile == "flat"
    rng = "pc" if str(info.get("color_range", "")).lower() in ("pc", "full", "jpeg") else "tv"
    return [
        f"scale=in_color_matrix={_matrix(info)}:in_range={rng}",
        "format=gbrp16le" if hi else "format=gbrp",
        f"lut3d=file='{escape_filter_path(lut_path)}':interp=tetrahedral",
    ]


def back_to_yuv() -> list[str]:
    return ["scale=out_color_matrix=bt709:out_range=tv", "format=yuv420p10le",
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"]


def grade_chain(grade: Grade, info: dict, *, lut_path: str | Path | None = None,
                face_box: list[float] | None = None, sky_horizon: float = 0.5) -> str:
    """Łańcuch dla dowolnego gradu (używany przez video_filter i podgląd Przed/Po)."""
    parts: list[str] = []
    prof = _profile(grade, {})
    if lut_path:
        parts += lut_chain(lut_path, info, prof)
    local = relight.geq_filter(grade.relight, grade.sky_darken, face_box=face_box, horizon=sky_horizon)
    if lut_path or local:
        parts += back_to_yuv()
    if local:
        parts.append(local)
    return ",".join(parts)


def _sky_horizon(project, media: MediaItem, clip: VClip) -> float:
    try:
        st = stats_for(project, media, clip.src_in, clip.src_out)
        sky = float(st.get("sky_pct", 0)) / 100.0
        return max(0.3, min(0.65, sky * 1.8 + 0.25))
    except Exception:
        return 0.5


def video_filter(clip: VClip, media: MediaItem, project) -> str:
    """Łańcuch filtrów ffmpeg dla klipu ("" gdy brak korekcji)."""
    g = clip.grade
    try:
        lut_path = ensure_lut(clip, media, project)
    except Exception as e:  # brak LUT nie może zatrzymać renderu
        from ..events import log

        log("colorist", f"Nie udało się przygotować LUT dla klipu {clip.id}: {e}", path="Kompilacja LUT",
            level="warn", project_id=getattr(project, "id", None))
        lut_path = None
    rl = relight.normalize_relight(g.relight)
    if lut_path is None and rl is None and not (g.sky_darken and g.sky_darken > 0.001):
        return ""
    info = dict(media.info or {})
    if not info.get("bit_depth"):
        try:
            info = media_info(media.path)
        except Exception:
            info = {}
    face = face_box_for(media, clip.src_in, clip.src_out) if rl and rl.get("face") else None
    hz = _sky_horizon(project, media, clip) if g.sky_darken and g.sky_darken > 0.001 else 0.5
    return grade_chain(g, info, lut_path=lut_path, face_box=face, sky_horizon=hz)

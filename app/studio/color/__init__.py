"""Obszar Kolor: analiza, kompilacja LUT (CST, ratowanie świateł, dopasowanie, looki, ochrona skóry),
filtry ffmpeg dla renderu i relighting. Kontrakt: docs/ARCHITECTURE.md → Kolor."""
from .analyze import clip_stats, detect_log_profile  # noqa: F401
from .filters import video_filter  # noqa: F401
from .lut import compile_grade_lut  # noqa: F401

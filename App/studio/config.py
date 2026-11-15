"""Ustawienia studia. Domyślne wartości tutaj, nadpisania w C:\\StudioEdit\\Config\\studio.json."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields

from . import CACHE_DIR, PROJECTS_DIR, PROXIES_DIR, ROOT, WHISPER_MODEL_DIR

CONFIG_FILE = ROOT / "Config" / "studio.json"


@dataclass
class Settings:
    # modele
    llm_model: str = "qwen3.5:9b"
    vlm_model: str = "qwen3.5:9b"
    whisper_model_dir: str = str(WHISPER_MODEL_DIR)
    whisper_compute: str = "float16"
    whisper_language: str | None = None  # None = autodetekcja
    embed_model: str = "google/siglip-base-patch16-256-multilingual"
    ollama_host: str = "http://127.0.0.1:11434"

    # indeks
    index_db: str = str(PROJECTS_DIR / "_library" / "index.sqlite")
    keyframes_dir: str = str(CACHE_DIR / "Index" / "keyframes")
    proxies_dir: str = str(PROXIES_DIR)
    proxy_height: int = 720
    proxy_encoder: str = "h264_nvenc"
    frames_per_shot: int = 3
    max_shot_len: float = 20.0  # dłuższe ujęcia dzielimy na jednostki analizy
    min_shot_len: float = 0.5
    vlm_image_max_side: int = 768

    media_extensions: tuple[str, ...] = (
        ".mp4", ".mov", ".mkv", ".mts", ".m2ts", ".avi", ".mxf", ".webm", ".m4v", ".3gp",
    )
    audio_extensions: tuple[str, ...] = (".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg")
    image_extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff", ".webp")


def load_settings() -> Settings:
    s = Settings()
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Settings)}
        for k, v in data.items():
            if k in known:
                setattr(s, k, v)
    return s


def save_settings(s: Settings) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(asdict(s), indent=2, ensure_ascii=False), encoding="utf-8")


SETTINGS = load_settings()

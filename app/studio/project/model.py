"""Model danych projektu: media, analiza, oś czasu (EDL) - wspólny kontrakt wszystkich agentów.

Jednostki czasu: SEKUNDY (float). `src_in/src_out` to czas w pliku źródłowym, `start` to czas na osi.
Ścieżki:
  V1 - A-Roll (główny obraz, ciągły)          A1 - Dialogi (powiązane z V1)
  V2 - B-Roll / przebitki (nakładane na V1)    A2 - Room Tone / ambience
  V3 - VFX / grafiki / tytuły                  A3 - Muzyka
                                               A4 - SFX
                                               A5 - Lektor (TTS)
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, computed_field


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------------------------
# Media i analiza
# ---------------------------------------------------------------------------------------------
class Word(BaseModel):
    w: str
    s: float  # start (sekundy w źródle)
    e: float  # koniec
    p: float = 1.0  # pewność
    spk: Optional[str] = None
    filler: bool = False


class Sentence(BaseModel):
    id: str
    text: str
    s: float
    e: float
    spk: Optional[str] = None
    words: list[Word] = Field(default_factory=list)
    fluency: float = 1.0  # 0..1 (mniej zająknięć = więcej)
    take_group: Optional[str] = None  # grupa powtórzeń tej samej kwestii
    hero: bool = False  # najlepszy dubel w grupie


class Transcript(BaseModel):
    language: str = "pl"
    engine: str = ""
    sentences: list[Sentence] = Field(default_factory=list)
    speakers: list[str] = Field(default_factory=list)

    def words(self) -> list[Word]:
        return [w for s in self.sentences for w in s.words]


class Shot(BaseModel):
    id: str
    s: float
    e: float
    shot_size: str = ""  # szeroki | średni | zbliżenie | detal | plan amerykański
    camera_move: str = ""  # statyczny | panorama | jazda | drgania | zoom | z ręki
    description: str = ""
    people: int = 0
    emotion: str = ""
    action: str = ""
    colors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    score: float = 0.5  # atrakcyjność wizualna 0..1
    sharpness: float = 0.0
    luma_mean: float = 0.0
    clipped_pct: float = 0.0  # % przepalonych pikseli
    crushed_pct: float = 0.0  # % zgniecionych cieni
    motion: float = 0.0  # średnia energia ruchu
    shake: float = 0.0  # drgania kamery 0..1
    face_box: Optional[list[float]] = None  # [cx, cy, w, h] znormalizowane (do reframingu)
    keyframes: list[str] = Field(default_factory=list)  # ścieżki miniatur


class Analysis(BaseModel):
    shots: list[Shot] = Field(default_factory=list)
    motion_curve: list[float] = Field(default_factory=list)  # energia ruchu w czasie
    motion_fps: float = 0.0
    audio_rms: list[float] = Field(default_factory=list)  # dB co 50 ms
    audio_rms_hop: float = 0.05
    loudness_lufs: Optional[float] = None
    peak_db: Optional[float] = None
    speech_ratio: float = 0.0
    bpm: Optional[float] = None
    beats: list[float] = Field(default_factory=list)
    downbeats: list[float] = Field(default_factory=list)
    energy_sections: list[dict] = Field(default_factory=list)
    room_tone: Optional[dict] = None  # {"s":..,"e":..,"db":..}
    summary: str = ""
    log_profile: str = ""  # "" | slog3 | clog3 | dlog | vlog | hlg | flat
    stage: dict[str, str] = Field(default_factory=dict)  # etap -> done|failed|skipped


class MediaItem(BaseModel):
    id: str
    name: str
    path: str  # oryginał (tylko do odczytu)
    kind: Literal["video", "audio", "image", "unknown"] = "video"
    bin: str = "A-Roll"  # A-Roll | B-Roll | Audio | Muzyka | SFX | Grafika | Wygenerowane | Odrzucone_Duble
    info: dict[str, Any] = Field(default_factory=dict)
    proxy: Optional[str] = None
    thumb: Optional[str] = None
    waveform: Optional[str] = None
    flags: list[str] = Field(default_factory=list)  # NEEDS_EXPOSURE_RECOVERY, HFR, LOG, SHAKY, BLURRY, REJECTED...
    flag_details: dict[str, Any] = Field(default_factory=dict)
    analysis: Analysis = Field(default_factory=Analysis)
    transcript: Optional[Transcript] = None
    rating: int = 0
    added_at: float = Field(default_factory=time.time)
    generated: bool = False  # wygenerowane przez AI (TTS, B-roll, SFX...)
    derived_from: Optional[str] = None


# ---------------------------------------------------------------------------------------------
# Oś czasu
# ---------------------------------------------------------------------------------------------
class Keyframe(BaseModel):
    t: float  # sekundy od początku klipu (czas osi)
    scale: float = 1.0
    x: float = 0.0  # przesunięcie środka kadru, -0.5..0.5 szerokości
    y: float = 0.0
    rot: float = 0.0
    ease: Literal["linear", "ease", "hold"] = "ease"


class SpeedPoint(BaseModel):
    t: float  # 0..1 względna pozycja w klipie (czas osi)
    speed: float


class Grade(BaseModel):
    """Parametry korekcji koloru - Kolorysta kompiluje je do LUT .cube na klip."""
    lut: Optional[str] = None  # skompilowany LUT (ścieżka) - wypełnia Kolorysta
    input_profile: str = "rec709"  # rec709 | slog3 | clog3 | dlog | vlog | hlg
    exposure: float = 0.0  # EV
    temperature: float = 0.0  # -1..1 (zimno..ciepło)
    tint: float = 0.0
    contrast: float = 0.0  # -1..1
    saturation: float = 1.0
    highlight_recovery: float = 0.0  # 0..1
    shadow_lift: float = 0.0
    sky_darken: float = 0.0  # 0..1
    look: Optional[str] = None  # nazwa looku (teal_orange, warm_film, clean_doc, moody, bw...) lub plik .cube
    look_strength: float = 0.8
    skin_protect: float = 0.7
    match_to: Optional[str] = None  # media_id ujęcia referencyjnego
    match: dict[str, Any] = Field(default_factory=dict)  # wyliczone statystyki dopasowania
    relight: Optional[dict[str, Any]] = None  # {"direction": "left", "strength": 0.5, "color": "warm"}


class Effect(BaseModel):
    type: str  # stabilize | denoise_video | vfx_render | blur_region | sharpen | vignette | film_grain | ...
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    status: str = "pending"  # pending | done | failed
    output: Optional[str] = None  # ścieżka do wyrenderowanego fragmentu (VFX), który zastępuje źródło


class VClip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("v_"))
    media_id: str
    src_in: float
    src_out: float
    start: float
    speed: float = 1.0
    speed_ramp: list[SpeedPoint] = Field(default_factory=list)
    interpolate: Literal["none", "blend", "optical"] = "none"
    role: Literal["a_roll", "b_roll", "generated", "vfx", "image"] = "a_roll"
    transform: list[Keyframe] = Field(default_factory=list)
    reframe: dict[str, list[dict]] = Field(default_factory=dict)  # wariant -> [{"t":..,"cx":..,"cy":..}]
    grade: Grade = Field(default_factory=Grade)
    effects: list[Effect] = Field(default_factory=list)
    opacity: float = 1.0
    label: str = ""
    reason: str = ""  # uzasadnienie decyzji montażowej (log reżysera)
    locked: bool = False

    @computed_field  # type: ignore[misc]
    @property
    def duration(self) -> float:
        src = max(0.0, self.src_out - self.src_in)
        if self.speed_ramp:
            # średnia prędkość krzywej (całka trapezowa po czasie osi jest przybliżana średnią)
            pts = sorted(self.speed_ramp, key=lambda p: p.t)
            sp = [p.speed for p in pts]
            avg = sum(sp) / len(sp) if sp else 1.0
            return src / max(0.05, avg)
        return src / max(0.05, self.speed)

    @computed_field  # type: ignore[misc]
    @property
    def end(self) -> float:
        return self.start + self.duration


class AClip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("a_"))
    media_id: Optional[str] = None
    path: Optional[str] = None  # alternatywnie plik (TTS/SFX/muzyka wygenerowana)
    src_in: float = 0.0
    src_out: float = 0.0
    start: float = 0.0
    role: Literal["dialog", "roomtone", "music", "sfx", "vo"] = "dialog"
    gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    link: Optional[str] = None  # id powiązanego VClip
    duck: bool = False  # muzyka ściszana pod dialogami
    loop: bool = False
    processing: dict[str, Any] = Field(default_factory=dict)  # łańcuch czyszczenia {"denoise":..,"eq":..}
    label: str = ""
    reason: str = ""

    @computed_field  # type: ignore[misc]
    @property
    def duration(self) -> float:
        return max(0.0, self.src_out - self.src_in)

    @computed_field  # type: ignore[misc]
    @property
    def end(self) -> float:
        return self.start + self.duration


class TitleClip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("t_"))
    text: str
    start: float
    duration: float
    style: str = "lower_third"  # lower_third | title | caption | callout
    position: str = "bottom"
    params: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[misc]
    @property
    def end(self) -> float:
        return self.start + self.duration


class Transition(BaseModel):
    id: str = Field(default_factory=lambda: new_id("x_"))
    at: float  # czas cięcia na osi (granica między klipami V1)
    kind: Literal["cut", "dissolve", "dip_black", "dip_white", "whip", "light_leak", "zoom", "slide"] = "dissolve"
    duration: float = 0.5
    reason: str = ""


class SubtitleSettings(BaseModel):
    enabled: bool = True
    burn_in: bool = True
    mode: Literal["static", "karaoke", "word_pop", "none"] = "word_pop"
    style: str = "modern"  # modern | classic | bold_social | minimal | cinema
    font: str = "Segoe UI Black"
    size: int = 0  # 0 = auto wg rozdzielczości
    primary: str = "#FFFFFF"
    highlight: str = "#FFD400"
    outline: str = "#000000"
    box: bool = False
    position: Literal["bottom", "middle", "top"] = "bottom"
    max_chars: int = 32
    max_lines: int = 2
    uppercase: bool = False


class AudioMix(BaseModel):
    target_lufs: float = -14.0
    true_peak: float = -1.0
    music_gain_db: float = -6.0
    duck_db: float = -15.0
    duck_attack: float = 0.25
    duck_release: float = 0.6
    room_tone: bool = True
    room_tone_db: float = -50.0
    crossfade_ms: float = 80.0
    voice_chain: dict[str, Any] = Field(default_factory=lambda: {
        "highpass": 80, "denoise": "auto", "dereverb": 0.0, "presence_db": 2.5, "compress": True, "deess": True})


class Variant(BaseModel):
    name: str  # youtube | tiktok | reels | shorts | instagram_feed | master
    aspect: str = "16:9"
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    max_duration: Optional[float] = None
    enabled: bool = True


class Marker(BaseModel):
    t: float
    label: str
    kind: str = "info"  # hook | climax | outro | beat | qc | note
    color: str = "#7c5cff"


class Timeline(BaseModel):
    version: int = 0
    fps: float = 30.0
    width: int = 1920
    height: int = 1080
    sample_rate: int = 48000
    tracks: dict[str, list[Any]] = Field(default_factory=lambda: {
        "V1": [], "V2": [], "V3": [], "A1": [], "A2": [], "A3": [], "A4": [], "A5": []})
    transitions: list[Transition] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)
    subtitles: SubtitleSettings = Field(default_factory=SubtitleSettings)
    mix: AudioMix = Field(default_factory=AudioMix)
    look: Optional[str] = None
    look_strength: float = 0.8
    variants: list[Variant] = Field(default_factory=lambda: [Variant(name="youtube")])
    brief: str = ""
    plan: dict[str, Any] = Field(default_factory=dict)
    note: str = ""
    created_at: float = Field(default_factory=time.time)
    created_by: str = ""

    # pomocnicze ---------------------------------------------------------------------------
    def v(self, track: str = "V1") -> list[VClip]:
        return [c if isinstance(c, VClip) else VClip(**c) for c in self.tracks.get(track, [])]

    def a(self, track: str = "A1") -> list[AClip]:
        return [c if isinstance(c, AClip) else AClip(**c) for c in self.tracks.get(track, [])]

    def titles(self) -> list[TitleClip]:
        return [c if isinstance(c, TitleClip) else TitleClip(**c) for c in self.tracks.get("V3", [])
                if (c.get("text") if isinstance(c, dict) else getattr(c, "text", None)) is not None]

    def normalize(self) -> "Timeline":
        """Zamienia słowniki na modele i sortuje klipy po czasie."""
        for k in list(self.tracks.keys()):
            items = []
            for c in self.tracks[k]:
                if isinstance(c, BaseModel):
                    items.append(c)
                elif k.startswith("V3") and isinstance(c, dict) and "text" in c:
                    items.append(TitleClip(**c))
                elif k.startswith("V"):
                    items.append(VClip(**c))
                else:
                    items.append(AClip(**c))
            items.sort(key=lambda c: c.start)
            self.tracks[k] = items
        self.transitions.sort(key=lambda t: t.at)
        return self

    @property
    def duration(self) -> float:
        end = 0.0
        for k, items in self.tracks.items():
            for c in items:
                e = c.end if isinstance(c, BaseModel) else float(c.get("start", 0)) + 0
                end = max(end, e)
        return round(end, 4)

    def find_clip(self, clip_id: str) -> tuple[str, Any] | None:
        for k, items in self.tracks.items():
            for c in items:
                cid = c.id if isinstance(c, BaseModel) else c.get("id")
                if cid == clip_id:
                    return k, c
        return None

    def clips_in_range(self, t0: float, t1: float, tracks: tuple[str, ...] = ("V1", "V2")) -> list[tuple[str, Any]]:
        out = []
        for k in tracks:
            for c in self.tracks.get(k, []):
                if c.start < t1 and c.end > t0:
                    out.append((k, c))
        return out

    def to_json(self) -> dict:
        self.normalize()
        d = self.model_dump(mode="json")
        d["duration"] = self.duration
        return d


class ProjectMeta(BaseModel):
    id: str
    name: str
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    scale: Literal["small", "medium", "massive"] = "medium"
    platforms: list[str] = Field(default_factory=lambda: ["youtube"])
    style: str = ""  # vlog | dokument | reklama | teledysk | rolka | podcast | tutorial | gaming
    target_duration: Optional[float] = None
    language: str = "pl"
    ingest_mode: Literal["copy", "link"] = "link"
    status: str = "new"  # new | ingesting | analyzed | edited | rendered
    thumb: Optional[str] = None
    notes: str = ""
    look: Optional[str] = None
    stats: dict[str, Any] = Field(default_factory=dict)

# StudioEdit — architektura i kontrakt modułów

Lokalny, wieloagentowy system postprodukcji wideo. Backend: Python 3.12 (FastAPI + wątki agentów),
frontend: React + Vite + Tailwind (zbudowany do `app/web`, serwowany przez FastAPI na `http://127.0.0.1:8765`).
Wszystko offline, wszystko w `C:\StudioEdit`.

## Katalogi

```
C:\StudioEdit\
  app\studio\            pakiet backendu (import: `studio.*`, uruchamiany z cwd=app)
  app\web\               zbudowany frontend (vite build)
  frontend\              źródła React
  config\config.json     konfiguracja (studio/config.py: DEFAULTS)
  tools\ffmpeg\bin       ffmpeg/ffprobe 9.0 (NVENC, libass, vidstab, arnndn, zscale)
  tools\rnnoise\*.rnnn   modele RNNoise dla arnndn
  AI-Models\             Ollama, huggingface (HF_HOME), piper, weights (sam2, yolo, lama)
  ComfyUI\               ComfyUI portable (python_embeded)
  Projects\<id>\         projekty (patrz studio/project/manager.py)
  Exports\               gotowe filmy
  Cache\  Backups\  LUTs\  Audio-Library\Music|SFX  Logs\
  .venv\                 venv Pythona (torch cu128)
```

## Rdzeń (gotowy — NIE zmieniać sygnatur bez potrzeby)

| Moduł | Zawartość |
|---|---|
| `studio.paths` | stałe ścieżek, `setup_env()` |
| `studio.config` | `get_config()` → `.get("a.b")`, `.update(patch)`, `.effort(agent)`, `.effort_level(agent)` (0..3) |
| `studio.events` | `bus.publish(type, data)`, `log(agent, text, path=, level=, project_id=)`, `toast()` |
| `studio.media.ffmpeg` | `run(args, duration=, progress=, cancel=)`, `run_capture(args)`, `media_info(path)`, `video_codec_args(codec, cq=, preset=, ten_bit=, fps=)`, `hw_decode_args()`, `nvenc_ok()`, `extract_frame()`, `read_frames()`, `read_audio()`, `write_wav()`, `escape_filter_path()`, `filters()`, `encoders()`; wyjątki `FFmpegError`, `Cancelled` |
| `studio.gpu` | `stats()`, `GPUMonitor` |
| `studio.vram` | `vram.lease(owner, agent, label)` (context manager), `vram.cached(owner, key, loader)`, `vram.unload_all()`; owner np. `"torch:whisper"`, `"torch:sam2"`, `"comfy"`, `"ollama:<model>"` |
| `studio.services` | `services.ensure("ollama"|"comfyui", agent)`, `services["ollama"].models()` itd. |
| `studio.llm` | `chat(messages, role=, agent=, schema=, images=, think=)`, `chat_json(messages, schema, **kw)`, `vision(prompt, images, schema=)`, `role_for_effort(effort)` |
| `studio.comfy` | `run_workflow(workflow_api_dict, agent=, label=, progress=, cancel=) -> list[Path]`, `upload_image(path) -> name`, `has_nodes(*names)`, `models_in(folder)` |
| `studio.project.model` | Pydantic: `MediaItem, Analysis, Shot, Transcript, Sentence, Word, Timeline, VClip, AClip, TitleClip, Transition, Grade, Effect, Keyframe, SpeedPoint, SubtitleSettings, AudioMix, Variant, Marker, ProjectMeta` |
| `studio.project.manager` | `projects.get(pid) -> Project`; `Project.media()`, `.get_media(id)`, `.update_media(id, fn)`, `.add_media_file(path, bin_name=, generated=True)`, `.timeline()` (kopia), `.commit_timeline(tl, note, author)`, `.undo()`, `.redo()`, `.cache` (Path), `.dir(*parts)`, `.add_chat()` |
| `studio.tasks` | `orchestrator.add(agent, path, title, fn, project_id=, deps=[ids], group=, priority=) -> Task`; `fn(ctx: TaskContext)`; `ctx.progress(p, msg)`, `ctx.log(text)`, `ctx.check()`, `ctx.cancel`, `ctx.effort`, `ctx.effort_level`, `ctx.project`, `ctx.sub(p0,p1)` |

### Zasady wspólne
* Czas zawsze w **sekundach (float)**. `VClip.src_in/src_out` = czas w źródle; `start` = czas na osi;
  `VClip.duration` = `(src_out-src_in)/speed` (computed). `Keyframe.t` = sekundy od początku klipu na osi.
* Oryginały są **tylko do odczytu**. Wszystkie pliki pośrednie → `project.cache / <podfolder>`.
* Każda operacja GPU (torch) w `with vram.lease("torch:<nazwa>", agent, label):`; modele ładuj przez
  `vram.cached(owner, key, loader)` (zwalniane automatycznie przy zmianie właściciela).
* Logi decyzyjne po polsku przez `ctx.log()` / `events.log()` — to widzi użytkownik w strumieniu pracy.
* Kodowanie wideo: `video_codec_args()` (NVENC z automatycznym fallbackiem CPU). Dekodowanie: `hw_decode_args()`.
* Moduł nie może wysypać całego systemu, gdy brakuje opcjonalnego modelu — degraduje się do metody klasycznej
  i loguje ostrzeżenie (`level="warn"`).
* Ścieżki do filtrów ffmpeg: `escape_filter_path()`.
* Teksty dla użytkownika: po polsku.

## Agenci (osobne pule wątków)

| klucz | nazwa | moduły (kafelki) |
|---|---|---|
| `producer` | Producent (czat) | AI Chat |
| `archivist` | Asystent & Archiwista | AI Analysis, ProjectManager |
| `director` | Reżyser Montażu & Narracji | AI Director, AI Montage |
| `colorist` | Kolorysta & Lighting Artist | AI Colorist |
| `sound` | Reżyser Dźwięku & Kompozytor | AI Audio, AI Subtitles |
| `vfx` | Artysta VFX & B-Roll | AI Effects, Region Editor |
| `qc` | Nadzorca Jakości | AI QC Supervisor |
| `render` | Montażysta Online (Render) | Export Studio, Premiere XML, After Effects JSX export, After Effects runtime |
| — | (system) | AI Developer |

## Kontrakty modułów specjalistycznych

### Kolor — `studio/color/` + `studio/agents/colorist.py`
```python
# studio/color/analyze.py
def clip_stats(media_path: str, *, t0: float = 0, t1: float | None = None, samples: int = 8) -> dict
    # {"luma_mean","luma_p01","luma_p99","clipped_pct","crushed_pct","wb_rgb":[r,g,b],"sat_mean",
    #  "lab_mean":[L,a,b],"lab_std":[...],"skin_pct","sky_pct"}
# studio/color/lut.py
def compile_grade_lut(grade: Grade, stats: dict, out_path: Path, size: int = 33,
                      ref_stats: dict | None = None) -> Path   # .cube
def looks() -> list[dict]       # [{"id","name","description","builtin":bool}] (wbudowane + LUTs\*.cube)
# studio/color/filters.py
def video_filter(clip: VClip, media: MediaItem, project) -> str
    # łańcuch filtrów ffmpeg dla klipu (np. "lut3d=file='...':interp=tetrahedral,..."); "" gdy brak korekcji
# studio/agents/colorist.py
def auto_grade_tasks(project, timeline, *, look: str | None, group: str, deps: list[str],
                     clip_ids: list[str] | None = None) -> list[Task]
    # analiza klipów, wybór ujęcia referencyjnego w scenie, Grade dla każdego VClip, kompilacja LUT
    # ostatnie zadanie zapisuje nową wersję osi (commit_timeline) i zwraca jej numer
def apply_color_command(project, timeline, clip_ids: list[str], params: dict) -> Timeline
    # params: {"exposure":..,"temperature":..,"look":..,"sky_darken":..,"highlight_recovery":..,
    #          "saturation":..,"contrast":..,"relight":{...}} -> zmienia Grade, kompiluje LUT, zwraca tl (bez commit)
```
Przestrzenie LOG: slog3, clog3, dlog (D-Log), vlog, hlg → Rec.709 (matematyka w LUT). Highlight roll-off,
ochrona skóry (oś odcienia skóry), dopasowanie ujęć (statystyki Lab), looki: `teal_orange, warm_film, clean_doc,
moody, bw_film, vibrant_social, pastel` + pliki `.cube` z `C:\StudioEdit\LUTs`.
Relighting: klasyczny (maska kierunkowa + twarz) w filtrze; opcjonalnie IC-Light przez ComfyUI, gdy dostępny.

### Audio i napisy — `studio/audio/`, `studio/subtitles/`, `studio/agents/sound.py`
```python
# studio/audio/stt.py
def transcribe(media_path: str, *, language: str = "pl", effort: str = "high",
               progress=None, agent="sound") -> Transcript          # słowa z czasami, zdania, wypełniacze
# studio/audio/diarize.py
def diarize(media_path: str, transcript: Transcript, *, max_speakers=6) -> Transcript   # ustawia spk
# studio/audio/sync.py
def find_offset(ref_path: str, other_path: str, max_offset: float = 60) -> tuple[float, float]  # (sekundy, pewność)
# studio/audio/mix.py
def render_mix(project, timeline: Timeline, out_wav: Path, *, progress=None, cancel=None,
               t0: float = 0, t1: float | None = None) -> dict
    # składa A1..A5 (+ audio z V1 gdy role a_roll i brak A1) w float32 48 kHz:
    # czyszczenie głosu (cache per media), mikro-przenikania na każdym cięciu, J/L cut, room tone,
    # auto-ducking muzyki, SFX, normalizacja LUFS + true peak. Zwraca {"lufs","true_peak","lra","path"}
def loudness(path: str) -> dict                         # {"lufs","true_peak","lra"}
# studio/audio/tts.py
def synthesize(text: str, out_path: Path, *, voice: str | None = None, ref_audio: str | None = None) -> Path
# studio/audio/music.py
def pick_music(mood: str, duration: float, bpm: float | None = None) -> Path | None   # z Audio-Library\Music
def generate_music(prompt: str, duration: float, out_path: Path) -> Path             # MusicGen (GPU)
def analyze_music(path: str) -> dict          # {"bpm","beats":[..],"downbeats":[..],"sections":[{"s","e","energy"}]}
# studio/audio/sfx.py
def make_sfx(kind: str, out_path: Path, duration: float = 1.0) -> Path   # whoosh|riser|hit|impact|swoosh|ambience
# studio/subtitles/build.py
def build_subtitles(project, timeline: Timeline, variant: Variant, out_dir: Path) -> dict  # {"ass": Path, "srt": Path}
# studio/agents/sound.py
def analysis_tasks(project, media_ids: list[str], *, group: str, deps_by_media: dict[str, list[str]]) -> list[Task]
    # transkrypcja + diaryzacja + statystyki audio (RMS, LUFS, room tone) dla każdego klipu; zapis w MediaItem
def post_edit_tasks(project, *, group: str, deps: list[str], music: str | None, mood: str | None) -> list[Task]
    # po montażu: dobór/generacja muzyki (A3), SFX na przejściach (A4), room tone (A2), ustawienia miksu; commit
def apply_audio_command(project, timeline, t0: float, t1: float, params: dict) -> Timeline
def tts_task(project, text: str, at: float, *, group: str) -> Task
```

### VFX — `studio/vfx/`, `studio/agents/vfx.py`
```python
def stabilize(media_path: str, t0: float, t1: float, out_path: Path, *, strength: float = 0.5,
              progress=None, cancel=None) -> Path                  # vidstab 2-pass, minimalny crop
def remove_object(media_path: str, t0: float, t1: float, out_path: Path, *, points: list[list[float]] | None,
                  box: list[float] | None, prompt: str | None, progress=None, cancel=None) -> Path
    # SAM 2.1 (ultralytics) maska w ruchu + inpainting (LaMa / ProPainter w ComfyUI jeśli dostępny)
def generate_broll(prompt: str, duration: float, out_path: Path, *, width=832, height=480,
                   progress=None, cancel=None) -> Path               # ComfyUI Wan 2.2 TI2V 5B
def track_subject(media_path: str, t0: float, t1: float, *, fps: float = 5) -> list[dict]
    # [{"t","cx","cy","w","h"}] - twarz/osoba (YOLO) do smart reframingu 9:16
# studio/agents/vfx.py
def vfx_task(project, timeline, kind: str, clip_id: str, t0: float, t1: float, params: dict, *,
             group: str, deps: list[str] = []) -> Task
    # kind: stabilize | remove_object | generate_broll | blur_region; wynik -> Effect(output=...) w VClip; commit
```

### Render, eksport, QC — `studio/render/`, `studio/exports/`, `studio/qc/`, `studio/agents/render.py`, `studio/agents/qc.py`
```python
# studio/render/engine.py
def render_timeline(project, timeline: Timeline, variant: Variant, out_path: Path, *, quality: str = "final",
                    t0: float = 0, t1: float | None = None, progress=None, cancel=None,
                    settings: dict | None = None) -> dict
    # quality: "preview" (proxy, wysokość render.preview_height, szybki NVENC) | "final"
    # Oś dzielona na plastry (stały zestaw warstw); każdy plaster renderowany do cache z hashem parametrów
    # -> przeliczanie przyrostowe (zmieniony fragment = tylko jego plastry). Potem concat + audio (audio.mix.render_mix)
    # + napisy (subtitles.build) + kodek docelowy. Zwraca {"path","duration","slices_total","slices_rendered","audio"}
def render_frame(project, timeline: Timeline, t: float, out_jpg: Path, *, variant: Variant | None = None,
                 before: bool = False, width: int = 960) -> Path      # klatka "Przed"/"Po"
# studio/exports/premiere.py
def export_premiere_xml(project, timeline: Timeline, out_path: Path) -> Path   # FCP7 xmeml v4 (Premiere import)
# studio/exports/aftereffects.py
def export_ae_jsx(project, timeline: Timeline, out_path: Path) -> Path
def run_ae(jsx_path: Path) -> dict      # After Effects runtime (AfterFX.exe -r), {"ok","message"}
def find_adobe() -> dict                # {"premiere": path|None, "aftereffects": path|None}
# studio/qc/checks.py
def run_qc(project, timeline: Timeline, render_path: Path, *, audio_report: dict | None = None,
           effort: str = "high") -> dict
    # {"passed":bool,"score":0..100,"issues":[{"id","kind","severity":"info|warn|error","t0","t1","message",
    #   "fix":{"agent","action","params"}|None}],"metrics":{...}}
# studio/agents/render.py
def render_task(project, *, variant: str, quality: str, group: str, deps: list[str], settings: dict | None = None,
                t0: float = 0, t1: float | None = None) -> Task      # wynik: dict z render_timeline
def export_tasks(project, *, variants: list[str], settings: dict, group: str, deps: list[str]) -> list[Task]
def adobe_tasks(project, *, targets: list[str], group: str) -> list[Task]   # premiere | ae_jsx | ae_run
# studio/agents/qc.py
def qc_task(project, *, render_task_id: str | None, group: str, deps: list[str], auto_fix: bool = True) -> Task
```

## API HTTP (FastAPI, prefiks `/api`) i WebSocket

Patrz `docs/API.md`.

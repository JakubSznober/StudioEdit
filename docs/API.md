# StudioEdit — API (backend `http://127.0.0.1:8765`)

Wszystkie odpowiedzi JSON. Błędy: HTTP 4xx/5xx z `{"detail": "komunikat po polsku"}`.
Zadania długie zwracają `{"group": "<id>"}` — postęp przychodzi przez WebSocket (`task.update`) i
`GET /api/tasks?group=<id>`.

## WebSocket `GET /ws?project_id=<pid>`
Po połączeniu serwer wysyła `{"type":"snapshot","data":{"events":[...ostatnie zdarzenia...]}}`,
potem strumień zdarzeń `{"id", "type", "ts", "data"}`:

| type | data |
|---|---|
| `gpu.stats` (co 1 s) | `{available, name, util, mem_used_gb, mem_total_gb, mem_free_gb, temp, power_w, enc_util, dec_util, owner, owner_agent}` |
| `vram.update` | `{owner, owner_agent, owner_label, users, queue:[{owner, agent, label, wait_s}]}` |
| `services.update` | `{name, state, error}` (state: stopped/starting/running/stopping/error/external/disabled) |
| `task.update` | `{id, agent, path, title, project_id, deps, group, status, progress, message, error, created, started, finished, elapsed}` |
| `agent.state` | `{agent, workers, busy, queued, running:[{id, path, title, progress, message, project_id}]}` |
| `agent.log` | `{agent, path, level: debug/info/warn/error, text, project_id}` |
| `project.update` | `{project_id, ...}` |
| `timeline.update` | `{project_id, version, note, author}` |
| `render.progress` | `{project_id, group, variant, quality, progress, stage, path?}` |
| `chat.message` | `{project_id, id, ts, role: user/assistant/system, text, range?, actions?, group?}` |
| `qc.report` | `{project_id, report}` |
| `loudness` | `{project_id, lufs, true_peak, lra, momentary?: [..]}` |
| `toast` | `{text, kind: info/success/warn/error}` |

Klient może wysłać `{"type":"ping"}` → `{"type":"pong"}`.

## System
| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/health` | `{ok, version}` |
| GET | `/api/system/status` | `{gpu, vram, services:[Service], modules:[Module], agents:[Agent], storage, active_project}` |
| GET | `/api/services` | `[Service]` |
| POST | `/api/services/{name}/start` / `/stop` | `Service` |
| PATCH | `/api/services/{name}` | body `{auto_wake?, auto_sleep?, url?, enabled?}` |
| GET | `/api/models` | `[ModelEntry]` |
| POST | `/api/models/{key}/install` | `{group}` |
| DELETE | `/api/models/{key}` | `{ok}` |
| GET/PATCH | `/api/config` | pełny config / głęboki patch |
| GET | `/api/agents` | `[Agent]` |
| PATCH | `/api/agents/{key}` | `{effort?: low/medium/high/max, workers?: int}` |
| GET | `/api/storage` | `{limit_gb, used_gb, free_disk_gb, folders:[{key,label,path,size_gb}]}` |
| POST | `/api/storage/clean` | `{target: "cache"|"renders"|"proxies"|"comfy"|"all_cache", project_id?}` → `{freed_gb}` |
| GET | `/api/tasks?project_id=&group=` | `[Task]` |
| POST | `/api/tasks/{id}/cancel` | `{cancelled}` |
| POST | `/api/tasks/cancel` | body `{group?, project_id?}` |
| GET | `/api/looks` | `[{id, name, description, builtin}]` |
| GET | `/api/logs?project_id=&agent=&limit=` | ostatnie `agent.log` |

Typy:
```
Service = {name, label, url, state, error, installed, managed, auto_wake, auto_sleep, enabled, pid, uptime_s,
           models?: [{name,size_gb,params,quant}], loaded?: [{name, vram_gb}]}
Module  = {key, name, agent, state: "ok"|"warn"|"error"|"off"|"busy", detail, requires:[...]}
           (15 kafelków: ai_director, ai_chat, ai_montage, ai_effects, ai_colorist, ai_audio, ai_subtitles,
            ai_analysis, region_editor, export_studio, premiere_xml, ae_jsx, ae_runtime, ai_developer, ai_qc)
Agent   = {key, name, role, color, icon, modules:[...], effort, workers, paths:[...], description,
           state:{busy, queued, running:[...]}}
ModelEntry = {key, name, kind: "llm"|"vlm"|"stt"|"tts"|"music"|"vfx"|"video"|"detector"|"diarization",
              provider: "ollama"|"hf"|"file"|"comfy", installed: bool, size_gb, used_by:[agent], description, role?}
```

## Projekty
| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/projects` | `[ProjectSummary]` |
| POST | `/api/projects` | `{name, scale?: small/medium/massive, platforms?: [...], style?, target_duration?, ingest_mode?: link/copy}` → `ProjectSummary` |
| GET/PATCH/DELETE | `/api/projects/{pid}` | szczegóły / zmiana meta / usunięcie (przeniesienie do Backups) |
| POST | `/api/projects/{pid}/activate` | ustaw aktywny |
| GET | `/api/projects/{pid}/media` | `[MediaItem]` (bez pełnych transkrypcji: `transcript_summary`) |
| GET | `/api/projects/{pid}/media/{mid}` | pełny `MediaItem` |
| POST | `/api/projects/{pid}/media/import` | `{paths: [plik lub folder], copy?: bool, analyze?: bool=true}` → `{added:[MediaItem], group?}` |
| POST | `/api/projects/{pid}/media/upload` | multipart `files[]` → `{added, group?}` |
| PATCH | `/api/projects/{pid}/media/{mid}` | `{bin?, rating?, name?}` |
| POST | `/api/projects/{pid}/media/{mid}/reject` | `{reason}` |
| DELETE | `/api/projects/{pid}/media/{mid}` | usuwa wpis (plik zostaje) |
| GET | `/api/projects/{pid}/media/{mid}/thumb` | JPEG |
| GET | `/api/projects/{pid}/media/{mid}/proxy` | MP4 (Range) |
| GET | `/api/projects/{pid}/file?rel=<ścieżka względna w projekcie>` | dowolny plik projektu (Range) |
| POST | `/api/projects/{pid}/analyze` | `{media_ids?: [...]}` → `{group}` |
| POST | `/api/projects/{pid}/montage` | `{brief, target_duration?, platforms?, style?, look?, music_mood?, subtitles?: bool}` → `{group}` (pełny automatyczny pipeline: analiza→montaż→kolor→audio→render podglądu→QC) |
| GET | `/api/projects/{pid}/pipeline/{group}` | `{group, status, total, done, progress, failed}` |
| GET | `/api/projects/{pid}/timeline` | `Timeline` (bieżąca wersja; `duration` w polu) |
| GET | `/api/projects/{pid}/timeline/versions` | `{current, versions:[{version, note, author, ts, duration, parent}], redo:[...]}` |
| POST | `/api/projects/{pid}/timeline/undo` / `/redo` | `Timeline` |
| POST | `/api/projects/{pid}/timeline/checkout/{version}` | `Timeline` |
| PATCH | `/api/projects/{pid}/timeline/clips/{clip_id}` | częściowe pola klipu → nowa wersja `Timeline` |
| POST | `/api/projects/{pid}/preview/render` | `{variant?: "youtube", t0?, t1?}` → `{group}` |
| GET | `/api/projects/{pid}/preview?variant=` | `{url|null, version, variant, rendered_at, stale: bool}` |
| GET | `/api/projects/{pid}/frame?t=&variant=&mode=before|after&width=` | JPEG |
| GET/POST | `/api/projects/{pid}/chat` | GET: historia; POST `{text, range?: {in, out}}` → `{message, reply?, group?}` |
| POST | `/api/projects/{pid}/export` | `{variants:[...], codec?: h264|hevc|av1|prores|dnxhr, resolution?: "1080p"|"1440p"|"2160p"|"source", fps?, container?: mp4|mov|mkv, audio_codec?: aac|wav|pcm, quality?: "auto"|"high"|"max"|int(cq)}` → `{group}` |
| GET | `/api/projects/{pid}/exports` | `[{name, path, size_mb, created, variant, url}]` |
| POST | `/api/projects/{pid}/export/adobe` | `{targets: ["premiere","ae_jsx","ae_run"]}` → `{group}` |
| POST | `/api/projects/{pid}/qc` | → `{group}` |
| GET | `/api/projects/{pid}/qc` | ostatni raport QC lub `null` |
| GET | `/api/projects/{pid}/loudness` | ostatni pomiar `{lufs, true_peak, lra}` lub `null` |

`Timeline` — patrz `studio/project/model.py` (`tracks.V1..V3, A1..A5`, `transitions`, `markers`, `subtitles`,
`mix`, `variants`, `version`, `duration`, `fps`, `width`, `height`). Każdy klip ma `id, start, end, duration`;
`VClip` także `media_id, src_in, src_out, speed, role, transform, grade, effects, label, reason`;
`AClip`: `role (dialog/roomtone/music/sfx/vo), gain_db, fade_in, fade_out, duck`; `TitleClip`: `text, style`.

## AI Developer (tryb bezpieczny)
| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/dev/diagnostics` | `{checks:[{name, ok, detail}]}` |
| POST | `/api/dev/propose` | `{request, files?: [ścieżki względne w app/]}` → `{id, summary, diff, files}` |
| POST | `/api/dev/apply/{id}` | kopia zapasowa w `Backups\dev\<ts>` + zastosowanie → `{ok, backup}` |
| GET | `/api/dev/backups` | `[{id, ts, files}]` |
| POST | `/api/dev/restore/{id}` | `{ok}` |
| POST | `/api/dev/tests` | uruchamia pytest → `{group}` |

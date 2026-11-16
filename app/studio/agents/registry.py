"""Rejestr agentów i modułów (kafelków) - metadane dla UI i dyspozytora."""
from __future__ import annotations

AGENTS: list[dict] = [
    {"key": "producer", "name": "Producent", "role": "Koordynator czatu reżyserskiego",
     "color": "#7c5cff", "icon": "messages-square", "modules": ["ai_chat"],
     "description": "Rozumie polecenia po polsku (również dla zaznaczonego zakresu IN/OUT), rozdziela pracę "
                    "między agentów i raportuje postęp.",
     "paths": ["Rozumienie polecenia", "Plan działań", "Delegowanie", "Raport"]},
    {"key": "archivist", "name": "Asystent & Archiwista", "role": "Ingest, proxy, analiza materiału",
     "color": "#14b8a6", "icon": "archive", "modules": ["ai_analysis", "project_manager"],
     "description": "Skanuje metadane (FPS, HFR, 10-bit, LOG/HDR), robi proxy NVENC, wykrywa ujęcia, mierzy "
                    "ekspozycję/ostrość/drgania, opisuje ujęcia modelem wizyjnym i segreguje biny. Nigdy nie "
                    "odrzuca prześwietleń — oznacza je do ratowania.",
     "paths": ["Metadane", "Proxy NVENC", "Detekcja ujęć", "Jakość i ruch", "Video-VLM", "Indeks i biny"]},
    {"key": "director", "name": "Reżyser Montażu", "role": "Narracja, cięcia, rytm",
     "color": "#f59e0b", "icon": "clapperboard", "modules": ["ai_director", "ai_montage"],
     "description": "Buduje historię (hook → rozwinięcie → kulminacja → zakończenie), wybiera najlepsze duble, "
                    "wycina ciszę i wypełniacze z naturalnym oddechem, robi J/L-cut, cięcia w ruchu, punch-in, "
                    "slow zoom, speed ramp, B-roll, cięcia do bitu i smart reframing 9:16.",
     "paths": ["Plan narracji (LLM)", "Selekcja dubli", "Pacing", "Niewidzialne cięcia", "Ruch kamery",
               "Speed ramp", "B-Roll", "Rytm muzyki", "Smart Reframe"]},
    {"key": "colorist", "name": "Kolorysta & Lighting", "role": "Korekcja i grading",
     "color": "#ec4899", "icon": "palette", "modules": ["ai_colorist"],
     "description": "Konwersja LOG→Rec.709, ratowanie prześwietleń w 32-bit float, balans bieli, shot matching, "
                    "ochrona skóry, relighting i filmowe looki (LUT).",
     "paths": ["Analiza ekspozycji", "Konwersja LOG (CST)", "Ratowanie prześwietleń", "Shot Matching",
               "Ochrona skóry & Look", "Relighting", "Kompilacja LUT"]},
    {"key": "sound", "name": "Reżyser Dźwięku", "role": "Dialogi, miks, muzyka, napisy",
     "color": "#3b82f6", "icon": "audio-lines", "modules": ["ai_audio", "ai_subtitles"],
     "description": "Transkrypcja co do słowa i diaryzacja, synchronizacja, czyszczenie głosu, mikro-przenikania, "
                    "room tone, vocal chain, ducking, LUFS, lektor TTS, muzyka i SFX, animowane napisy.",
     "paths": ["Transkrypcja (STT)", "Diaryzacja mówców", "Analiza audio", "Czyszczenie głosu", "Room Tone",
               "Muzyka", "SFX", "Lektor (TTS)", "Miks & LUFS", "Napisy"]},
    {"key": "vfx", "name": "Artysta VFX", "role": "Efekty, maski, B-roll AI",
     "color": "#10b981", "icon": "wand-sparkles", "modules": ["ai_effects", "region_editor"],
     "description": "Stabilizacja, śledzenie i wycinanie obiektów (SAM 2.1), usuwanie obiektów (inpainting), "
                    "generowanie brakujących przebitek (Wan 2.2 w ComfyUI) — tylko na zaznaczonym fragmencie.",
     "paths": ["Stabilizacja", "Maskowanie SAM 2.1", "Usuwanie obiektów", "Generowanie B-Roll", "Śledzenie obiektu"]},
    {"key": "qc", "name": "Nadzorca Jakości", "role": "Ostatnia bramka QC",
     "color": "#ef4444", "icon": "shield-check", "modules": ["ai_qc"],
     "description": "Szuka czarnych klatek, zamrożeń, jump cutów, przesterów, zbyt cichych dialogów, uciętych "
                    "napisów i błędów synchronizacji; błędy cofa do właściwego agenta z instrukcją poprawki.",
     "paths": ["Skan obrazu", "Skan dźwięku", "Napisy i kadr", "Synchronizacja", "Auto-poprawki"]},
    {"key": "render", "name": "Montażysta Online", "role": "Render i eksport",
     "color": "#8b5cf6", "icon": "film", "modules": ["export_studio", "premiere_xml", "ae_jsx", "ae_runtime"],
     "description": "Składa warstwy wideo, audio, VFX, kolor, ruch kamery, speed ramping i napisy FFmpeg + NVENC; "
                    "przelicza przyrostowo tylko zmienione plastry. Opcjonalnie eksport do Premiere / After Effects.",
     "paths": ["Plastry (NVENC)", "Przejścia", "Złożenie", "Napisy", "Kodek docelowy", "Adobe"]},
]

AGENT_BY_KEY = {a["key"]: a for a in AGENTS}

MODULES: list[dict] = [
    {"key": "ai_director", "name": "AI Director", "agent": "director", "requires": ["ollama", "llm"]},
    {"key": "ai_chat", "name": "AI Chat", "agent": "producer", "requires": ["ollama", "llm"]},
    {"key": "ai_montage", "name": "AI Montage", "agent": "director", "requires": ["ffmpeg"]},
    {"key": "ai_effects", "name": "AI Effects", "agent": "vfx", "requires": ["ffmpeg", "sam"]},
    {"key": "ai_colorist", "name": "AI Colorist", "agent": "colorist", "requires": ["ffmpeg"]},
    {"key": "ai_audio", "name": "AI Audio", "agent": "sound", "requires": ["ffmpeg", "stt"]},
    {"key": "ai_subtitles", "name": "AI Subtitles", "agent": "sound", "requires": ["ffmpeg", "stt"]},
    {"key": "ai_analysis", "name": "AI Analysis", "agent": "archivist", "requires": ["ffmpeg", "vlm"]},
    {"key": "region_editor", "name": "Region Editor", "agent": "vfx", "requires": ["ffmpeg"]},
    {"key": "export_studio", "name": "Export Studio", "agent": "render", "requires": ["ffmpeg", "nvenc"]},
    {"key": "premiere_xml", "name": "Premiere XML", "agent": "render", "requires": []},
    {"key": "ae_jsx", "name": "After Effects JSX export", "agent": "render", "requires": []},
    {"key": "ae_runtime", "name": "After Effects runtime", "agent": "render", "requires": ["aftereffects"]},
    {"key": "ai_developer", "name": "AI Developer", "agent": "system", "requires": ["ollama", "llm"]},
    {"key": "ai_qc", "name": "AI QC Supervisor", "agent": "qc", "requires": ["ffmpeg"]},
    {"key": "project_manager", "name": "ProjectManager", "agent": "archivist", "requires": []},
]

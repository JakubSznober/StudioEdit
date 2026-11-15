# StudioEdit — lokalny agent AI do montażu filmów

Cel: agent, który robi to, co montażysta. Użytkownik dodaje nagrania, opisuje film po polsku,
agent analizuje materiał, montuje, poprawia kolor i dźwięk, pokazuje podgląd, przyjmuje poprawki
i eksportuje gotowy film lub projekt do Premiere Pro / After Effects.

## Twarde założenia (nie zmieniać bez decyzji użytkownika)

- **100% lokalnie.** Zero wywołań do API w chmurze, zero telemetrii, zero Ollama Cloud. Internet tylko
  do jednorazowego pobrania narzędzi i wag modeli.
- **Wszystko w `C:\StudioEdit`.** Modele, cache, narzędzia, projekty, eksporty. Ustawiamy
  `OLLAMA_MODELS`, `HF_HOME`, `TORCH_HOME`, `PIP_CACHE_DIR` na podkatalogi tego folderu.
- **Oryginalne nagrania są tylko do odczytu.** `Footage\` nigdy nie jest modyfikowany ani nadpisywany.
  Każda operacja tworzy nowy plik lub zmienia tylko model timeline'u.
- **Bez DaVinci Resolve.** Integracja z Adobe Premiere Pro 2027 i After Effects 2027 (zainstalowane).
- **Jakość ważniejsza niż czas renderu.**
- **Język użytkownika: polski.** Interfejs, czat i komunikaty po polsku. Kod, nazwy funkcji i commity po angielsku.

## Sprzęt

| Element | Wartość |
|---|---|
| GPU | NVIDIA RTX 5060 Ti 16 GB (Blackwell, sm_120), sterownik 621.24, CUDA UMD 13.5 |
| CPU | Intel Core Ultra 7 270K Plus |
| RAM | 32 GB |
| Dysk C: | 2 TB, ok. 1,5 TB wolne |
| OS | Windows 11 Pro 26300 |
| Python | 3.12.10 (`C:\Users\kubas\AppData\Local\Python\pythoncore-3.12-64`) |
| Ollama | 0.40.16 |

Konsekwencje dla kodu:
- Blackwell wymaga PyTorch zbudowanego pod CUDA 12.8+ (sm_120). Stare wheels i stare poradniki nie działają.
- 16 GB VRAM nie pomieści naraz LLM + VLM + Whisper + SAM 2 + ComfyUI. Modele ładujemy etapami przez
  kolejkę zadań GPU; Ollama ma `keep_alive=0` przed uruchomieniem ciężkich zadań w PyTorch/ComfyUI.
- Enkodowanie przez NVENC (h264/hevc/av1), ProRes i DNxHR przez CPU.

## Architektura (pięć warstw)

1. **Indeks materiałów** (`App\studio\index\`): SQLite + embeddingi. Dla każdego ujęcia: metadane ffprobe,
   granice scen, transkrypcja, mówcy, opis VLM, twarze, ostrość, ekspozycja, ruch kamery, głośność, profil koloru.
   Wszystkie polecenia typu "wybierz najlepsze ujęcia X" to zapytania do tego indeksu.
   Etapy: probe → proxy → shots → quality (per plik, CPU/NVENC), potem speech → embed → vision (partiami,
   jeden model na GPU naraz). Każdy etap zapisuje się w `media.stages_done` i jest wznawialny.
   Jednostka analizy = ujęcie, ale ujęcia dłuższe niż `max_shot_len` (20 s) są dzielone (`cut_start=0`).
2. **Model timeline'u** (`App\studio\timeline\`): OpenTimelineIO jako jedyne źródło prawdy o projekcie.
   Historia, cofanie, wersje i Region Editor to operacje na tym modelu, nie osobne moduły.
3. **Narzędzia atomowe** (`App\studio\tools\`): małe, testowalne operacje (cut, trim, place, color_match,
   loudnorm, mask, stabilize…). Każde ma schemat JSON, żeby agent mógł je wołać.
4. **Agent** (`App\studio\agent\`): Qwen3.x przez Ollama z tool calling. Rozumie polecenie, planuje,
   woła narzędzia, sprawdza wynik sygnałowo (czarne klatki, flash frames, clipping, LUFS, dryf synchronizacji).
5. **Wyjścia** (`App\studio\export\`): podgląd proxy przez FFmpeg, render końcowy, FCP7 XML do Premiere,
   JSX do After Effects, później panel czatu wewnątrz Premiere.

Szczegóły i kolejność budowy: `docs\PLAN.md`.

## Uruchamianie (zawsze z `App\`, przez venv)

```
.\.venv\Scripts\python.exe check_env.py                       # sprawdzenie środowiska
.\.venv\Scripts\python.exe -m pytest tests -q                 # szybkie testy (bez GPU)
.\.venv\Scripts\python.exe -m studio.index index <ścieżki>    # indeksowanie nagrań
.\.venv\Scripts\python.exe -m studio.index query "tekst" --no-speech --sharp 0.3
.\.venv\Scripts\python.exe -m studio.index show <id|ścieżka>
.\.venv\Scripts\python.exe scripts\make_test_footage.py       # syntetyczny klip z polską mową (TTS)
```

Indeks: `Projects\_library\index.sqlite`; proxy w `Proxies\`; klatki kluczowe w `Cache\Index\keyframes\`.
Ustawienia (modele, wysokość proxy, katalogi): `App\studio\config.py`, nadpisania w `Config\studio.json`.

## Struktura katalogów

```
C:\StudioEdit\
  App\          kod aplikacji (pakiet studio/, .venv, testy)
  AI-Models\    wagi modeli (Ollama, Whisper, SAM2, HF cache)
  Tools\        FFmpeg, Ollama portable, inne binaria
  ComfyUI\      ComfyUI portable + workflowy
  Projects\     projekty (OTIO + indeks + historia) — jeden folder na projekt
  Footage\      oryginały, tylko do odczytu
  Proxies\      podglądy niskiej rozdzielczości, generowane
  Exports\      gotowe filmy, XML, JSX
  Cache\        pliki tymczasowe, można czyścić
  Config\       ustawienia
  Backups\      kopie przed ważnymi zmianami
  docs\         plan, decyzje, notatki
```

## Poprzednia próba: `C:\Editing Studio`

Wcześniejsza wersja (listopad 2026, budowana z ChatGPT) leży w `C:\Editing Studio` (ok. 41 GB).
Nie rozwijamy jej dalej: brak wspólnego modelu timeline'u, każdy moduł renderuje osobny MP4,
brak gita, wersje w nazwach plików. Traktujemy ją jako **źródło narzędzi, modeli i sprawdzonych fragmentów**:
FFmpeg 9.0.2, Ollama portable + modele, SAM 2.1, Whisper large-v3-turbo, ComfyUI portable + SDXL,
`premiere_export.py` (FCP7 XML, import do Premiere 2027 potwierdzony), `after_effects_export.py` (JSX potwierdzony),
`audio_tools.py`, `sam2_video_worker.py`, detektor scen. Patrz `docs\PLAN.md`, sekcja "Co przenosimy".

## Zasady pracy

- Każdy etap kończy się testem na prawdziwym nagraniu, nie tylko na syntetycznej planszy.
- Nie obiecujemy w kodzie ani w UI funkcji, której lokalny model nie udźwignie (RAW, usuwanie obiektów w 4K,
  generatywne wideo w pełnej rozdzielczości). Ograniczenia są jawnie opisane.
- Uzasadnienia decyzji montażowych zapisywane w planie muszą być sprawdzalne (odwołania do indeksu),
  nie wolny tekst modelu.
- Przed każdą zmianą systemową (zmienne środowiskowe, instalacje) opisać ją i uzyskać zgodę.

# Plan budowy StudioEdit

Stan na 2026-11-15. Pełne założenia: `..\CLAUDE.md`.

## Co ma robić agent (zakres "wszystko, co montażysta")

Codzienne czynności, w kolejności wartości dla użytkownika:

1. Przegląd i logowanie materiału (indeks ujęć, selekty, odrzuty: nieostre, prześwietlone, powtórki).
2. Montaż po transkrypcji: edycja filmu jak tekstu, usuwanie zdań, ciszy, przejęzyczeń, powtórek.
3. Rough cut wg briefu: struktura (intro, rozwinięcie, zakończenie), wybór ujęć, kolejność, tempo.
4. B-roll nad A-rollem: dobór ujęć z indeksu do tego, co mówi narrator.
5. Rytm: cięcia J/L, cięcie do beatu muzyki, długości ujęć zależne od gatunku.
6. Synchronizacja multicam i dźwięku zewnętrznego po fali audio.
7. Dźwięk: normalizacja do normy platformy, odszumianie, ducking muzyki pod dialog, separacja.
8. Kolor: LOG do Rec.709, auto-ekspozycja i balans, dopasowanie między ujęciami, look, LUT .cube.
9. Grafika: napisy, lower thirds, tytuły (MOGRT w Premiere, JSX w AE).
10. Efekty: maski SAM 2, zamiana tła, stabilizacja, speed ramp, interpolacja, kadrowanie pionowe.
11. Region Editor: te same operacje ograniczone do zakresu timeline'u, z historią i porównaniem.
12. QC: czarne klatki, flash frames, clipping, LUFS, dryf synchronizacji, zgodność z długością docelową.
13. Eksport: wersje (YouTube 16:9, pionowa 9:16, kwadrat), kodeki, przekazanie do Premiere/AE.

## Modele (dopasowane do 16 GB VRAM)

| Zadanie | Model | Uwagi |
|---|---|---|
| Planowanie, czat, tool calling | Qwen3.5 9B (jest) → później Qwen3.x 14B Q4 lub 30B-A3B | przez Ollama |
| Opis ujęć (VLM) | Qwen3.5 9B vision (jest), Qwen2.5-VL 7B (jest) | próbkowanie klatek, nie całe wideo |
| Wyszukiwanie ujęć | SigLIP / CLIP embeddingi | do pobrania |
| Mowa, napisy | faster-whisper large-v3-turbo (jest) | CUDA float16 |
| Mówcy | pyannote | do pobrania, wymaga jednorazowej akceptacji licencji na HF |
| Maski, śledzenie | SAM 2.1 Hiera Large (jest) | |
| Usuwanie tła | BiRefNet lub RVM | do pobrania |
| Usuwanie obiektów | ProPainter | tylko proxy, krótkie ujęcia |
| Interpolacja, upscaling | RIFE, Real-ESRGAN | przez ComfyUI |
| Obrazy, tła, inpaint | SDXL Base (jest) | przez ComfyUI |
| Szum, separacja, beaty | DeepFilterNet, Demucs, librosa | do pobrania |
| Twarze | InsightFace | do pobrania |
| Stabilizacja | ffmpeg vidstab, Gyroflow | |

Nie obsługujemy lokalnie: RAW (BRAW, R3D, ARRI) – zostaje w Premiere. Dolby Vision. Generatywne wideo w pełnej rozdzielczości.

## Kolejność budowy

Najpierw cienki pionowy przekrój od importu do Premiere, potem pogłębianie.

| Etap | Co powstaje | Test zaliczający |
|---|---|---|
| 0 | Środowisko: venv, PyTorch sm_120, FFmpeg, Ollama, zmienne środowiskowe w `C:\StudioEdit`, git | `torch.cuda.is_available()`, NVENC działa, Ollama ładuje model na GPU |
| 1 | Indeks materiałów: ffprobe, sceny, Whisper, metryki jakości, opisy VLM, embeddingi, SQLite | zapytanie "ujęcia z twarzą, ostre, bez mowy" zwraca sensowną listę z prawdziwego nagrania |
| 2 | Timeline OTIO + podgląd proxy FFmpeg + eksport FCP7 XML | round-trip: timeline → XML → Premiere 2027 → ta sama sekwencja |
| 3 | Agent v1: "zrób rough cut z tych nagrań" end-to-end | podgląd MP4 i XML z prawdziwego materiału, plan z odwołaniami do indeksu |
| 4 | Montaż po transkrypcji, usuwanie ciszy, napisy | gadająca głowa 10 min → skrócona wersja bez ciszy, z SRT |
| 5 | Dźwięk: loudnorm, DeepFilterNet, ducking, beat sync | LUFS w normie, muzyka cichnie pod dialogiem |
| 6 | Kolor: LOG → Rec.709, auto-korekta, shot matching, LUT | dwa ujęcia z różnych kamer dopasowane, LUT otwiera się w Premiere |
| 7 | Efekty przez ComfyUI: SAM 2, tło, stabilizacja, reframe 9:16 | wersja pionowa śledzi główny obiekt |
| 8 | Region Editor, historia, wersje, porównanie przed/po | zmiana w zakresie nie rusza reszty timeline'u |
| 9 | Panel czatu w Premiere, skrypty AE | polecenie z panelu zmienia otwartą sekwencję |
| 10 | QC, wiele wersji eksportu, testy wydajności | pełny film 10 min 4K przechodzi bez ręcznych poprawek technicznych |

## Co przenosimy z `C:\Editing Studio`

Bez pobierania ponownie (ok. 35 GB):

- `AI-Models\Ollama` (17,8 GB: qwen3.5:9b, qwen2.5-vl 7b, wariant vision) → `C:\StudioEdit\AI-Models\Ollama`
- `AI-Models\Whisper` (1,5 GB), `AI-Models\SAM2` (0,8 GB)
- `Tools\FFmpeg` (9.0.2 full, NVENC/ProRes/DNxHR), `Tools\Ollama` (portable 0.40.16), `Tools\SAM2`
- `ComfyUI\ComfyUI_windows_portable` (10,7 GB, z SDXL) – do sprawdzenia wersja PyTorch pod Blackwell
- `Installers\` (2,4 GB) – archiwa źródłowe

Kod do przejrzenia i przepisania pod nową architekturę (nie kopiować 1:1):

- `RenderStudio\premiere_export.py` – FCP7 XML, import do Premiere 2027 potwierdzony
- `RenderStudio\after_effects_export.py` – JSX, uruchomienie w AE 2027 potwierdzone
- `RenderStudio\sam2_video_worker.py`, `sam2_effects.py` – worker SAM 2 na GPU
- `AudioStudio\audio_tools.py` – Whisper, afftdn, loudnorm, ducking
- `MediaAnalyzer\SceneDetector\scene_detector_v2_1.py`, `MotionAnalyzer\camera_motion_analyzer.py`
- `RenderStudio\colorist.py` – bramka profilu koloru (SDR/LOG/HDR), tone mapping libplacebo
- `RenderStudio\comfy_*.py` – klient API ComfyUI

Stary `App\` zostaje w `C:\Editing Studio` jako referencja do czasu zakończenia etapu 3, potem do usunięcia.

## Stan środowiska (2026-11-15, po przeniesieniu)

Zrobione:
- Modele, Tools, ComfyUI, Installers i cache przeniesione z `C:\Editing Studio` do `C:\StudioEdit` (ok. 35 GB).
- Zmienne użytkownika: `OLLAMA_MODELS`, `HF_HOME`, `TORCH_HOME`, `PIP_CACHE_DIR` wskazują na `C:\StudioEdit`;
  `C:\StudioEdit\Tools\FFmpeg\bin` dodany do PATH użytkownika (nowe terminale go widzą).
- Ollama (tray app, port 11434) widzi trzy modele z nowej lokalizacji; `qwen3.5:9b` ładuje się w 100% na GPU.
- ComfyUI portable: Python 3.13, torch 2.14.1+cu130 z `sm_120` — Blackwell obsługiwany.
- Git 2.56 zainstalowany, repo zainicjalizowane (`main`), `.gitignore` wyklucza dane i binaria.

- Venv `App\.venv` (Python 3.12.10): torch 2.14.1+cu130 (sm_120 potwierdzone matmulem na GPU), faster-whisper 1.2.1
  z PyAV 14.2.0 i bibliotekami CUDA 12 z pakietów `nvidia-*` (rejestruje je `App\studio\__init__.py`),
  opentimelineio 0.18.1 + `otio-fcp-adapter` (adapter `fcp_xml`), scenedetect 0.7.3, opencv 5.0, librosa 1.0, ollama 0.6.3.
- `App\check_env.py` sprawdza całość (CUDA, Whisper na GPU, OTIO, FFmpeg, Ollama, zmienne) — uruchamiać po każdej zmianie środowiska.
- Repo: `main`, tożsamość lokalna ustawiona, `.gitattributes` wymusza LF.

Pozostałości:
- W `C:\Editing Studio` zostały: stary kod `App\` (referencja), `Cache\Build` (2,9 GB, PyInstaller, do usunięcia),
  `Exports\Video` (testy syntetyczne), `StudioEditor.exe` (774 MB). Usunąć po etapie 3.

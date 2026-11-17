# StudioEdit — lokalne AI Film Studio

W pełni lokalny (offline), wieloagentowy system postprodukcji wideo dla YouTube, TikToka, Reels, Shorts i Instagrama.
Sterujesz nim z przeglądarki (`http://127.0.0.1:8765`) — czatem po polsku i osią czasu z zaznaczaniem IN/OUT.
Serwer działa w tle albo w oknie CMD. Wszystko (narzędzia, modele, projekty) mieszka w `C:\StudioEdit`.

## Szybki start

| Co | Jak |
|---|---|
| Instalacja / naprawa | `powershell -ExecutionPolicy Bypass -File C:\StudioEdit\scripts\install.ps1` (dodaj `-All` dla ciężkich modeli: Wan 2.2, MusicGen) |
| Start z oknem logów | dwuklik `C:\StudioEdit\StudioEdit.cmd` — otworzy przeglądarkę |
| Start w tle | `C:\StudioEdit\StudioEdit-w-tle.cmd` (logi: `Logs\server.log`) |
| Zatrzymanie | `C:\StudioEdit\StudioEdit-stop.cmd` |
| Modele — stan / doinstalowanie | `.venv\Scripts\python.exe scripts\install_models.py --list` / `--all` |
| Deinstalacja | `scripts\uninstall.ps1` (projekty i nagrania zostają), `-ModelsOnly`, `-Everything` |

## Jak pracować

1. **Pulpit** → *Nowy projekt*: nazwa, skala (mały / średni / masywny), platformy, styl.
2. **Studio** → *Importuj*: wklej ścieżkę pliku/folderu albo przeciągnij pliki. Archiwista sam robi proxy, wykrywa ujęcia,
   ocenia ekspozycję/ostrość/drgania, opisuje ujęcia modelem wizyjnym, a Reżyser Dźwięku robi transkrypcję co do słowa.
   Prześwietlone ujęcia **nie są odrzucane** — dostają flagę `NEEDS_EXPOSURE_RECOVERY` i ratuje je Kolorysta.
3. **Zmontuj film** (lub w czacie: *„Zmontuj dynamiczny film na YouTube, max 4 minuty, ciepłe filmowe kolory”*).
   Uruchamia się graf agentów: analiza → plan narracji → Assembly Cut → reframing → kolor ∥ dźwięk → cięcia do bitu →
   render podglądu (NVENC) → QC z automatycznymi poprawkami. Każdy krok widać na żywo w *Strumieniu pracy agentów*.
4. **Poprawki**: zaznacz myszką fragment na osi (lub klawisze `I` / `O`), a zaznaczenie przypnie się do czatu:
   *„zrób tu slow-motion i przyciemnij niebo”*, *„zmień przebitkę na zbliżenie”*, *„usuń ten kabel w tle”*,
   *„lektor: Witajcie w Tatrach”*, *„wytnij to”*, *„dodaj przejście”*. Przeliczany jest tylko zmieniony fragment.
   `Ctrl+Z` / `Ctrl+Shift+Z` — cofnij/ponów (każda zmiana to nowa wersja `edl_vN.json`), suwak **Przed / Po**.
5. **Eksport** → Final Master (H.264 / H.265 10-bit / AV1 / ProRes / DNxHR, 1080p–4K, 24–60 fps) do `C:\StudioEdit\Exports`.
   Wersje pionowe 9:16 powstają ze smart reframingiem (śledzenie twarzy/obiektu).
6. **Adobe (opcjonalnie)**: *„Wyeksportuj do Premiere / After Effects”* → Premiere XML (ścieżki V1 A-Roll, V2 B-Roll,
   V3 VFX/Napisy, A1–A5, bin Odrzucone_Duble) i skrypt JSX dla After Effects (opcjonalnie uruchamiany w AE).

## Agenci (każdy ma własną pulę wątków i poziom wysiłku low / medium / high / max — zakładka *Agenci*)

| Agent | Moduły | Co robi |
|---|---|---|
| Producent | AI Chat | rozumie polecenia po polsku, deleguje pracę, raportuje |
| Asystent & Archiwista | AI Analysis, ProjectManager | metadane (FPS/HFR, 10-bit, LOG/HDR), proxy NVENC, ujęcia, jakość, Video-VLM, biny |
| Reżyser Montażu | AI Director, AI Montage | hook→kulminacja→zakończenie, selekcja dubli, pacing z oddechem, J/L-cut, cięcie w ruchu, punch-in, slow zoom, speed ramp, B-roll, cięcia do bitu, smart reframe |
| Kolorysta & Lighting | AI Colorist | LOG→Rec.709 (S-Log3, C-Log3, D-Log, V-Log, HLG), ratowanie prześwietleń w float32, balans bieli, shot matching, ochrona skóry, looki LUT, relighting |
| Reżyser Dźwięku | AI Audio, AI Subtitles | STT co do słowa + diaryzacja, synchronizacja, czyszczenie głosu, mikro-przenikania, room tone, vocal chain, ducking, LUFS (−14 / −23), lektor TTS, muzyka, SFX, animowane napisy |
| Artysta VFX | AI Effects, Region Editor | stabilizacja (vidstab), SAM 2.1 + inpainting (usuwanie obiektów), generowanie B-rollu (Wan 2.2 w ComfyUI) — tylko na zaznaczonym fragmencie |
| Nadzorca Jakości | AI QC Supervisor | czarne klatki, zamrożenia, jump cuty, przestery, ciche dialogi, ucięte napisy, synchronizacja → automatyczne poprawki |
| Montażysta Online | Export Studio, Premiere XML, AE JSX, AE runtime | render plastrami (cache, przyrostowo), przejścia, napisy, kodek docelowy |

**VRAMOrchestrator** pilnuje, by na karcie był zawsze tylko jeden model (LLM *albo* Whisper *albo* SAM *albo* ComfyUI);
zadania tego samego modelu są grupowane, a usługi Ollama/ComfyUI budzą się i usypiają same (Auto-Wake / Auto-Sleep).

## Modele (RTX 5060 Ti 16 GB)

| Rola | Model | Uwagi |
|---|---|---|
| Reżyser / czat (domyślny) | `qwen3.6:35b-a3b` (Ollama, Q4, ~24 GB) | MoE, 3B aktywnych — szybki mimo częściowego offloadu do RAM |
| Reżyser — tryb MAX | `qwen3.8:27b` (~18 GB) | najnowszy gęsty model z rozumowaniem |
| Tryb LOW / Video-VLM | `qwen3-vl:8b` (~6 GB) | w całości w VRAM, analiza obrazu |
| STT | Whisper large-v3-turbo (faster-whisper, CUDA fp16) | słowa ze znacznikami czasu |
| Diaryzacja | `microsoft/wavlm-base-plus-sv` | bez tokenów HF |
| Lektor | Piper (pl_PL gosia / darkman) + klonowanie głosu Chatterbox Multilingual | Chatterbox zainstalowany z `--no-deps` (chroni torch cu128) |
| Odszumianie | DeepFilterNet 3 (ONNX, onnxruntime CPU) | Oficjalny eksport ONNX z repozytorium autora, STFT i cechy libDF odtworzone w numpy (`app/studio/audio/dfnet.py`) — bez pakietu `deepfilternet`, który na Pythonie 3.12/Windows się nie instaluje. Pobieranie w panelu Modele (8 MB, suma SHA-256 sprawdzana). Gdy niepobrany: RNNoise (`arnndn`) + `afftdn` |
| Relighting | klasyczny (maska kierunkowa + twarz) | IC-Light / ProPainter wymagają węzłów ComfyUI, których brak — opcjonalne |
| Muzyka | MusicGen stereo medium + biblioteka `Audio-Library\Music` | |
| SFX | synteza proceduralna + `Audio-Library\SFX` | |
| Maski / tracking | SAM 2.1 Hiera-L, YOLO11m, YuNet | |
| Usuwanie obiektów | LaMa (big-lama) | |
| B-roll AI | Wan 2.2 TI2V 5B (ComfyUI) | instalowany z `-All` |

Modele zmieniasz w `config\config.json` (`models.*.name`) albo w UI (*Modele i usługi*, *Ustawienia*).
O AirLLM: ładuje warstwy z dysku po kolei — przy 30B+ to minuty na token, więc tu się nie sprawdza. Zamiast tego
używamy modeli MoE + offloadu warstw do RAM w Ollamie, flash-attention i kwantyzacji cache KV (q8_0).

## Skala montażu

* **Mały** — do ~15 min materiału: najgęstsza analiza (4 klatki VLM na ujęcie).
* **Średni** — do ~2 h.
* **Masywny** — 2 h+: rzadsze próbkowanie, plan hierarchiczny (najpierw selekcja w paczkach, potem plan globalny).

## Ważne

* **Windows Smart App Control** blokuje niektóre niepodpisane pliki `.exe` (np. `ffprobe.exe`). StudioEdit omija to
  bez zmiany ustawień bezpieczeństwa (metadane czyta przez PyAV). Nie aktualizuj ręcznie FFmpeg do dziennych buildów
  — nowa wersja może zostać zablokowana.
* Oryginalne nagrania są tylko do odczytu; „odrzucanie” to przeniesienie do binu `Odrzucone_Duble` (nic nie jest kasowane).
* **AI Developer** (tryb bezpieczny) przed każdą zmianą kodu robi kopię w `Backups\dev\` i wymaga zatwierdzenia.

Dokumentacja techniczna: `docs\ARCHITECTURE.md`, `docs\API.md`.

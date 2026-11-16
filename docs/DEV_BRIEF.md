# Brief dla programistów modułów StudioEdit (wspólne zasady)

Projekt: lokalny wieloagentowy system postprodukcji wideo w `C:\StudioEdit`. Przeczytaj najpierw:
`docs/ARCHITECTURE.md` (kontrakty funkcji!), `docs/API.md`, oraz pliki rdzenia:
`app/studio/paths.py, config.py, events.py, media/ffmpeg.py, gpu.py, vram.py, services.py, llm.py, comfy.py,
project/model.py, project/manager.py, tasks.py`.

## Środowisko
* Windows 11, RTX 5060 Ti 16 GB (Blackwell, sm_120), Core Ultra 7 270K Plus, 32 GB RAM.
* Python: `C:\StudioEdit\.venv\Scripts\python.exe` (torch cu128). Instalacja może jeszcze trwać w tle —
  jeśli pakietu brakuje: `C:\StudioEdit\.venv\Scripts\python.exe -m pip install <pakiet>` i dopisz go do
  `app/requirements.txt` (dopisuj na końcu, nie przepisuj pliku).
* Uruchamianie kodu: cwd `C:\StudioEdit\app`, import `studio.*`; w skryptach testowych najpierw
  `from studio import paths; paths.setup_env()`.
* FFmpeg: `C:\StudioEdit\tools\ffmpeg\bin\ffmpeg.exe` (działa; NVENC, libass, vidstab, arnndn, zscale, lut3d).
  **`ffprobe.exe` jest zablokowany przez Windows Smart App Control** — nigdy nie wołaj ffprobe bezpośrednio,
  używaj `studio.media.ffmpeg.media_info()` / `probe()` (fallback PyAV). Nie próbuj wyłączać zabezpieczeń
  Windows. Nowe niepodpisane .exe z internetu mogą być blokowane — preferuj pakiety Pythona.
* Ollama działa na `http://127.0.0.1:11434` (modele: qwen3-vl:8b, qwen3.6:35b-a3b, qwen3.8:27b — mogą się
  jeszcze pobierać). ComfyUI portable w `C:\StudioEdit\ComfyUI` (python_embeded), start przez
  `studio.services.services.ensure("comfyui")`.
* Modele HF trafiają do `C:\StudioEdit\AI-Models\huggingface` (HF_HOME), wagi plikowe do
  `C:\StudioEdit\AI-Models\weights\<obszar>\`.

## Pliki testowe
* `C:\StudioEdit\test.MP4` — **TYLKO DO ODCZYTU. Nigdy go nie modyfikuj, nie przenoś, nie usuwaj.**
* Wyniki testów zapisuj w `C:\StudioEdit\Cache\dev_<obszar>\`. Możesz generować syntetyczne klipy ffmpeg (lavfi).

## Zasady kodu
* Kompletny, działający kod — bez placeholderów, `TODO`, `pass` w miejscu logiki, „reszta tutaj”.
* Teksty dla użytkownika i logi (`ctx.log`, `events.log`) po polsku.
* Brak opcjonalnego modelu ≠ awaria: degradacja do metody klasycznej + log `level="warn"`.
* GPU tylko w `vram.lease(...)`; modele torch przez `vram.cached(owner, key, loader)`.
* Nie edytuj plików rdzenia, chyba że to ewidentny błąd — wtedy minimalna poprawka i opisz ją w raporcie.
* Nie rób commitów git. Nie dotykaj plików innych obszarów (frontend / color+vfx / audio+napisy / render+eksport+qc /
  analiza+reżyser+serwer).
* Pobieranie modeli twojego obszaru zaimplementuj w `studio/<obszar>/models.py`:
  `CATALOG: list[dict]` (key, name, kind, provider, size_gb, description, used_by) oraz
  `def download(key: str, progress=None) -> None` i `def installed(key: str) -> bool` — użyje tego instalator i UI.
* Testy jednostkowe (pytest) w `app/tests/test_<obszar>.py` — szybkie, bez GPU tam, gdzie się da.

## Raport końcowy (zwięźle)
Pliki, publiczne funkcje z sygnaturami (potwierdź zgodność z ARCHITECTURE.md lub opisz odstępstwa), co
przetestowałeś i z jakim wynikiem, znane ograniczenia, modele (repo HF / URL, rozmiar, ścieżka docelowa),
zmiany w plikach rdzenia (jeśli były).

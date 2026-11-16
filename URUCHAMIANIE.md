# Jak uruchomić StudioEdit

## 1. Codzienne uruchamianie

| Chcę… | Zrób to |
|---|---|
| Uruchomić studio z oknem logów | dwuklik **`C:\StudioEdit\StudioEdit.cmd`** |
| Uruchomić studio w tle (bez okna) | dwuklik **`C:\StudioEdit\StudioEdit-w-tle.cmd`** |
| Zatrzymać studio | dwuklik **`C:\StudioEdit\StudioEdit-stop.cmd`** (albo `Ctrl+C` w oknie CMD) |

Po starcie przeglądarka otworzy się sama na **http://127.0.0.1:8765**.
Jeśli się nie otworzy — wpisz ten adres ręcznie.

* Ollama (modele językowe) i ComfyUI (generowanie wideo) **uruchamiają się same**, gdy są potrzebne — nic nie klikasz.
* Jeśli studio już działa, ponowny dwuklik `StudioEdit.cmd` tylko otworzy przeglądarkę.
* Logi serwera: `C:\StudioEdit\Logs\server.log`.

## 2. Pierwszy film — krok po kroku

1. **Pulpit** → *Nowy projekt* → nazwa, skala (mały / średni / masywny), platformy (YouTube, TikTok…).
2. **Studio** → *Importuj* → wklej ścieżkę pliku lub folderu z nagraniami (np. `D:\Nagrania\Wyjazd`) albo przeciągnij pliki.
   Analiza rusza automatycznie (proxy, ujęcia, transkrypcja, opisy ujęć).
3. Kliknij **Zmontuj film** → opisz, co chcesz (np. *„dynamiczny vlog na YouTube, max 4 minuty, ciepłe kolory, z napisami”*).
4. Obserwuj pracę agentów w dolnym panelu **Strumień pracy**. Po kilku minutach pojawi się podgląd.
5. **Poprawki**: zaznacz myszką fragment na osi czasu (lub klawisze `I` / `O`) i napisz w czacie, np.:
   * *„zrób tu slow motion”*, *„przyciemnij niebo”*, *„zmień przebitkę na zbliżenie”*
   * *„wytnij to”*, *„dodaj przejście”*, *„lektor: Witajcie w Tatrach”*
   * *„zrób też wersję na TikToka”*, *„sprawdź jakość”*
   `Ctrl+Z` cofa zmianę, `Ctrl+Shift+Z` ponawia.
6. **Eksport** → wybierz wersje i kodek → *Renderuj Final Master*. Gotowe pliki: `C:\StudioEdit\Exports\<projekt>\`.
   Do Premiere / After Effects: napisz w czacie *„wyeksportuj do Premiere”*.

## 3. Skróty w Studio

| Klawisz | Działanie |
|---|---|
| `Spacja` | odtwarzaj / pauza |
| `I` / `O` | początek / koniec zaznaczenia |
| `L` | pętla zaznaczenia |
| `←` `→` | ±1 klatka (`Shift` = ±1 s) |
| `Ctrl+Z` / `Ctrl+Shift+Z` | cofnij / ponów |

## 4. Instalacja od zera (nowy komputer / naprawa)

```
git clone https://github.com/JakubSznober/StudioEdit.git C:\StudioEdit
powershell -ExecutionPolicy Bypass -File C:\StudioEdit\scripts\install.ps1 -All
```

Instalator pobiera FFmpeg, Node.js, ComfyUI, środowisko Python z CUDA, buduje interfejs i pobiera modele AI
(ok. 80 GB). Można go uruchamiać ponownie — pominie to, co już jest.

Stan modeli: `C:\StudioEdit\.venv\Scripts\python.exe C:\StudioEdit\scripts\install_models.py --list`

## 5. Gdy coś nie działa

| Objaw | Rozwiązanie |
|---|---|
| `Errno 10048` / „Port 8765 jest zajęty” | studio już działa w tle → otwórz http://127.0.0.1:8765 albo uruchom `StudioEdit-stop.cmd` i wystartuj ponownie; inny program na porcie → `StudioEdit.cmd --port 8766` |
| „Brak środowiska Python” | uruchom `scripts\install.ps1` |
| Interfejs pokazuje „Łączenie z serwerem…” | serwer nie działa → `StudioEdit.cmd` i sprawdź komunikaty w oknie |
| Czat nie odpowiada / wolny | pierwsze polecenie ładuje model (~30–60 s). Zakładka **Modele i usługi** → sprawdź, czy Ollama działa |
| „Zasady kontroli aplikacji zablokowały ten plik” | to Windows Smart App Control — nie podmieniaj ręcznie plików w `tools\`; uruchom `scripts\install.ps1` |
| Mało miejsca | **Modele i usługi** → *Wyczyść cache* (projekty i nagrania nie są ruszane) |
| Kafelek modułu na czerwono | najedź na niego — opis podpowie, czego brakuje; **AI Developer** → *Diagnostyka* |

## 6. Gdzie co jest

| Folder | Zawartość |
|---|---|
| `Projects\` | projekty (oś czasu, wersje, czat, cache) |
| `Exports\` | gotowe filmy, XML Premiere, JSX After Effects |
| `Audio-Library\Music`, `Audio-Library\SFX` | wrzuć tu własną muzykę i efekty — agent będzie z nich korzystał |
| `LUTs\` | własne pliki `.cube` (pojawią się jako looki) |
| `config\config.json` | ustawienia (modele, porty, LUFS, kodeki) — łatwiej zmieniać w zakładce **Ustawienia** |
| `Backups\` | kopie zapasowe (usunięte projekty, zmiany AI Developera) |

Więcej: [README.md](README.md) (opis agentów i modeli), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (technicznie).

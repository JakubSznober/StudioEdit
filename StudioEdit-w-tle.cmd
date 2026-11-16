@echo off
rem ============================================================
rem  StudioEdit - start serwera W TLE (bez okna), logi: Logs\server.log
rem  Zatrzymanie: StudioEdit-stop.cmd
rem ============================================================
cd /d "%~dp0app"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo [!] Brak srodowiska Python. Uruchom najpierw: scripts\install.ps1
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
start "" /b "%~dp0.venv\Scripts\pythonw.exe" -m studio
echo StudioEdit uruchomiony w tle: http://127.0.0.1:8765
timeout /t 3 >nul

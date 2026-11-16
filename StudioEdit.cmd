@echo off
rem ============================================================
rem  StudioEdit - start serwera w oknie CMD (logi na zywo)
rem  Interfejs: http://127.0.0.1:8765 (otwiera sie automatycznie)
rem ============================================================
title StudioEdit - AI Film Studio (serwer)
chcp 65001 >nul
cd /d "%~dp0app"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [!] Brak srodowiska Python. Uruchom najpierw: scripts\install.ps1
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
"%~dp0.venv\Scripts\python.exe" -m studio %*
echo.
echo Serwer zatrzymany.
pause

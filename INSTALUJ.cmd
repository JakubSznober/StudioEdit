@echo off
rem ============================================================
rem  StudioEdit - instalacja na nowym komputerze (dwuklik)
rem  Instaluje wszystko, czego potrzebuja agenci: FFmpeg, Node.js, ComfyUI, Python 3.12
rem  z PyTorch CUDA, Ollama, interfejs i wszystkie modele AI (ok. 100 GB, kilka godzin).
rem  Mozna uruchamiac ponownie - pomija to, co juz jest, i dokancza przerwane pobieranie.
rem ============================================================
title StudioEdit - instalacja
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   StudioEdit - instalacja wszystkich agentow i modeli AI
echo   Folder: %~dp0
echo   Potrzebne: Windows 10/11, karta NVIDIA (najlepiej 16 GB VRAM), ok. 110 GB na dysku, internet.
echo   Po instalacji studio dziala w 100%% offline.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0scripts' | Unblock-File" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" -All %*
set ERR=%ERRORLEVEL%
echo.
if %ERR%==0 (
  echo   Instalacja zakonczona. Uruchom StudioEdit.cmd albo skrot StudioEdit na pulpicie.
) else (
  echo   Instalacja nie jest pelna ^(kod %ERR%^). Przeczytaj komunikaty powyzej i uruchom INSTALUJ.cmd ponownie.
)
pause
exit /b %ERR%

@echo off
rem Zatrzymuje serwer StudioEdit (wraz z uruchomionymi przez niego Ollama/ComfyUI)
powershell -NoProfile -Command "try { Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/system/shutdown -TimeoutSec 5 | Out-Null; 'Zatrzymano StudioEdit.' } catch { $f='%~dp0Logs\server.pid'; if (Test-Path $f) { Stop-Process -Id (Get-Content $f) -Force -ErrorAction SilentlyContinue; 'Zatrzymano proces serwera.' } else { 'Serwer nie dziala.' } }"
timeout /t 2 >nul

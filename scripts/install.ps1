# ==========================================================================================
#  StudioEdit - pelna instalacja (idempotentna, mozna uruchamiac ponownie)
#  Uzycie:  powershell -ExecutionPolicy Bypass -File C:\StudioEdit\scripts\install.ps1 [-All]
#    -All  : rowniez ciezkie modele (Wan 2.2 B-roll ~18 GB, MusicGen, Qwen 3.8 27B)
#  Nic nie jest instalowane poza C:\StudioEdit (poza Ollama, jesli jej brak - instalator Ollama).
# ==========================================================================================
param([switch]$All)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root '.venv\Scripts\python.exe'

Write-Host "`n[1/5] Narzedzia (FFmpeg, Node.js, 7-Zip, ComfyUI)..." -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'install_tools.ps1')
if ($LASTEXITCODE -ne 0) { throw 'install_tools nieudane' }

Write-Host "`n[2/5] Srodowisko Python + PyTorch CUDA..." -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'install_python.ps1')
if ($LASTEXITCODE -ne 0) { throw 'install_python nieudane' }

Write-Host "`n[3/5] Ollama..." -ForegroundColor Cyan
$ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not (Test-Path $ollama) -and -not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host 'Instaluje Ollama przez winget (oficjalny, podpisany instalator)...'
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
}
# modele Ollama trafiaja do C:\StudioEdit\AI-Models\Ollama (StudioEdit uruchamia Ollama z ta sciezka)

Write-Host "`n[4/5] Frontend (React)..." -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build_frontend.ps1')
if ($LASTEXITCODE -ne 0) { throw 'build_frontend nieudane' }

Write-Host "`n[5/5] Modele AI..." -ForegroundColor Cyan
# Ollama musi dzialac, zeby pobierac modele LLM - StudioEdit sam ja uruchomi (Auto-Wake)
$args5 = @((Join-Path $PSScriptRoot 'install_models.py'))
if ($All) { $args5 += '--all' }
& $Py @args5

Write-Host "`nGotowe! Uruchom: C:\StudioEdit\StudioEdit.cmd  (lub StudioEdit-w-tle.cmd)" -ForegroundColor Green

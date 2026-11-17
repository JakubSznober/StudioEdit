# ==========================================================================================
#  StudioEdit - pelna instalacja (idempotentna, mozna uruchamiac ponownie)
#  Uzycie:  powershell -ExecutionPolicy Bypass -File C:\StudioEdit\scripts\install.ps1 [-All]
#    -All  : rowniez ciezkie modele (Wan 2.2 B-roll ~18 GB, MusicGen, Qwen 3.8 27B, Chatterbox)
#  Najprosciej: dwuklik INSTALUJ.cmd w folderze StudioEdit (instaluje wszystko).
#  Wszystko trafia do folderu StudioEdit. Poza nim, tylko gdy ich brakuje: Python 3.12, Ollama i
#  Visual C++ Redistributable (oficjalne instalatory przez winget) + zmienna uzytkownika OLLAMA_MODELS.
# ==========================================================================================
param([switch]$All, [switch]$NoShortcut)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$env:PYTHONIOENCODING = 'utf-8'

function Step($n, $text) { Write-Host "`n[$n/7] $text" -ForegroundColor Cyan }
function Warn($text) { Write-Host "[!] $text" -ForegroundColor Yellow }
function Run-Script($name, [string[]]$extra = @()) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot $name) @extra
    if ($LASTEXITCODE -ne 0) { throw "$name nieudane (kod $LASTEXITCODE)" }
}

# ---------------------------------------------------------------------------------------------
Step 0 'Sprawdzam komputer...'
Write-Host "Folder instalacji: $Root"
$smi = Join-Path $env:WINDIR 'System32\nvidia-smi.exe'
if (Test-Path $smi) {
    try { $gpu = & $smi --query-gpu=name,driver_version,memory.total --format=csv,noheader } catch { $gpu = '?' }
    Write-Host "[ok] GPU: $gpu"
} else {
    Warn 'Nie widze karty NVIDIA (nvidia-smi). Zainstaluj sterownik NVIDIA - bez niego modele AI i NVENC nie zadzialaja.'
}
$need = if ($All) { 110 } else { 60 }
$free = [math]::Round((Get-PSDrive ($Root.Substring(0, 1))).Free / 1GB)
if ($free -lt $need) { Warn "Na dysku jest $free GB wolnego miejsca, instalacja potrzebuje ok. $need GB." }
else { Write-Host "[ok] Wolne miejsce: $free GB (potrzeba ok. $need GB)" }
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Warn 'Brak winget (Instalator aplikacji ze sklepu Microsoft). Jesli brakuje Pythona 3.12 lub Ollama, zainstaluj je recznie.'
}
$vc = @('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll') | Where-Object { -not (Test-Path (Join-Path $env:WINDIR "System32\$_")) }
if ($vc) {
    Write-Host '[..] brak Visual C++ Redistributable (potrzebny PyTorch/ONNX) - instaluje przez winget (Windows zapyta o zgode)'
    winget install --id Microsoft.VCRedist.2015+.x64 -e --accept-source-agreements --accept-package-agreements
} else { Write-Host '[ok] Visual C++ Redistributable' }

# ---------------------------------------------------------------------------------------------
Step 1 'Narzedzia (FFmpeg, Node.js, 7-Zip, ComfyUI)...'
Run-Script 'install_tools.ps1'

# ---------------------------------------------------------------------------------------------
Step 2 'Srodowisko Python 3.12 + PyTorch CUDA + biblioteki agentow...'
Run-Script 'install_python.ps1'

# ---------------------------------------------------------------------------------------------
Step 3 'Ollama (modele jezykowe rezysera, producenta i analizy obrazu)...'
$ollamaDir = Join-Path $Root 'AI-Models\Ollama'
New-Item -ItemType Directory -Force $ollamaDir | Out-Null
$restartOllama = $false
# Modele Ollama maja trafiac do StudioEdit\AI-Models\Ollama rowniez, gdy Ollama startuje sama z zasobnika.
if ([Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User') -ne $ollamaDir) {
    [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $ollamaDir, 'User')
    Write-Host "[ok] OLLAMA_MODELS = $ollamaDir (zmienna uzytkownika)"
    $restartOllama = $true
}
$env:OLLAMA_MODELS = $ollamaDir
$ollamaExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not (Test-Path $ollamaExe) -and -not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host 'Instaluje Ollama przez winget (oficjalny, podpisany instalator)...'
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    $restartOllama = $true
}
if ($restartOllama) {
    # Ollama uruchomiona przed ustawieniem zmiennej czytalaby modele z innego folderu - StudioEdit
    # uruchomi ja sam z wlasciwa sciezka, a przy nastepnym logowaniu zasobnik wezmie juz nowa zmienna.
    Get-Process -Name 'ollama app', 'ollama' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
if (Test-Path $ollamaExe) { Write-Host "[ok] Ollama: $ollamaExe" } else { Warn 'Ollama nie zostala znaleziona - modele jezykowe nie zostana pobrane.' }

# ---------------------------------------------------------------------------------------------
Step 4 'Interfejs (React)...'
Run-Script 'build_frontend.ps1'

# ---------------------------------------------------------------------------------------------
Step 5 'Modele AI dla agentow (najdluzszy krok - dziesiatki GB)...'
$argsModels = @((Join-Path $PSScriptRoot 'install_models.py'))
if ($All) { $argsModels += '--all' }
& $Py @argsModels
$modelsOk = ($LASTEXITCODE -eq 0)
if (-not $modelsOk) { Warn 'Czesc modeli sie nie pobrala - uruchom instalator ponownie, dokonczy od miejsca przerwania.' }

# ---------------------------------------------------------------------------------------------
Step 6 'Skrot na pulpicie...'
if (-not $NoShortcut) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'StudioEdit.lnk'
    if (-not (Test-Path $lnk)) {
        $sh = New-Object -ComObject WScript.Shell
        $s = $sh.CreateShortcut($lnk)
        $s.TargetPath = Join-Path $Root 'StudioEdit.cmd'
        $s.WorkingDirectory = $Root
        $s.Description = 'StudioEdit - lokalne studio montazu AI'
        $s.Save()
        Write-Host "[ok] $lnk"
    } else { Write-Host '[ok] skrot juz jest' }
}

# ---------------------------------------------------------------------------------------------
Step 7 'Sprawdzam agentow i modele...'
& $Py (Join-Path $PSScriptRoot 'verify_install.py')
$verifyOk = ($LASTEXITCODE -eq 0)

if ($modelsOk -and $verifyOk) {
    Write-Host "`nGotowe! Uruchom: $Root\StudioEdit.cmd  (albo skrot StudioEdit na pulpicie)" -ForegroundColor Green
    exit 0
}
Warn 'Instalacja zakonczona z brakami (lista wyzej). Uruchom INSTALUJ.cmd ponownie - pominie to, co juz jest.'
exit 1

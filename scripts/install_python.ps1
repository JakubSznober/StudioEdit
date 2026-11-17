# StudioEdit - srodowisko Python (venv w C:\StudioEdit\.venv)
# PyTorch z CUDA 12.8 (obsluga RTX 50xx / Blackwell sm_120) + zaleznosci backendu.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'

function Find-Python312 {
    try { $p = (& py -3.12 -c "import sys;print(sys.executable)" 2>$null); if ($LASTEXITCODE -eq 0 -and $p) { return $p } } catch {}
    $cands = @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
               (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.12-64\python.exe'),
               (Join-Path $env:ProgramFiles 'Python312\python.exe'))
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $v = & $cmd.Source -c "import sys;print('%d.%d' % sys.version_info[:2])"
            if ($v -eq '3.12') { return $cmd.Source }
        } catch {}  # alias ze sklepu Microsoft zamiast prawdziwego Pythona
    }
    return $null
}

if (-not (Test-Path $Py)) {
    $base = Find-Python312
    if (-not $base) {
        Write-Host '[..] brak Pythona 3.12 - instaluje przez winget (oficjalny instalator python.org, tylko dla uzytkownika)'
        winget install --id Python.Python.3.12 -e --scope user --accept-source-agreements --accept-package-agreements
        $base = Find-Python312
        if (-not $base) { throw 'Nie znaleziono Pythona 3.12. Zainstaluj go z python.org i uruchom instalator ponownie.' }
    }
    Write-Host "[..] tworze venv z $base"
    & $base -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'tworzenie venv nieudane' }
}
& $Py -m pip install --upgrade pip wheel setuptools
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }

# PyTorch CUDA 12.8 - wspoldzieli cuBLAS/cuDNN 12 z CTranslate2 (faster-whisper)
$hasTorch = & $Py -c "import importlib.util;print(bool(importlib.util.find_spec('torch')))"
if ($hasTorch -ne 'True') {
    & $Py -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { throw 'torch install failed' }
}
& $Py -m pip install -r (Join-Path $Root 'app\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'requirements install failed' }

# Klonowanie glosu (Chatterbox): bez zaleznosci, bo metadane wymuszaja torch==2.6 i numpy<2
$hasCb = & $Py -c "import importlib.util;print(bool(importlib.util.find_spec('chatterbox')))"
if ($hasCb -ne 'True') {
    & $Py -m pip install --no-deps -r (Join-Path $Root 'app\requirements-nodeps.txt')
    if ($LASTEXITCODE -ne 0) { throw 'chatterbox install failed' }
}
& $Py -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available(),torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
Write-Host 'PYTHON_DONE'

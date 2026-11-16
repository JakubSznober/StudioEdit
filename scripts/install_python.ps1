# StudioEdit - srodowisko Python (venv w C:\StudioEdit\.venv)
# PyTorch z CUDA 12.8 (obsluga RTX 50xx / Blackwell sm_120) + zaleznosci backendu.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'

if (-not (Test-Path $Py)) {
    $base = $null
    try { $base = (& py -3.12 -c "import sys;print(sys.executable)") } catch {}
    if (-not $base) { $base = (Get-Command python).Source }
    Write-Host "[..] tworze venv z $base"
    & $base -m venv $Venv
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
& $Py -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available(),torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
Write-Host 'PYTHON_DONE'

# Buduje frontend React (frontend\) do app\web przy uzyciu przenosnego Node.js z tools\node
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Node = Join-Path $Root 'tools\node'
if (-not (Test-Path (Join-Path $Node 'node.exe'))) { throw 'Brak tools\node - uruchom scripts\install_tools.ps1' }
$env:PATH = "$Node;$env:PATH"
Push-Location (Join-Path $Root 'frontend')
try {
    if (-not (Test-Path 'node_modules')) { & npm.cmd install --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'npm install failed' } }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'npm run build failed' }
} finally { Pop-Location }
Write-Host 'FRONTEND_DONE'

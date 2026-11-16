# ==========================================================================================
#  StudioEdit - deinstalator
#  Domyslnie usuwa: modele, ComfyUI, narzedzia, venv, cache, frontend build, pobrane pliki.
#  ZACHOWUJE: Projects\, Exports\, Backups\, Audio-Library\, LUTs\, test.MP4 i wszystkie Twoje nagrania.
#  -Everything : usuwa rowniez projekty/eksporty/kopie (wymaga wpisania TAK).
#  -ModelsOnly : usuwa tylko modele AI (AI-Models + modele ComfyUI).
# ==========================================================================================
param([switch]$Everything, [switch]$ModelsOnly)
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot

try { Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/system/shutdown -TimeoutSec 5 | Out-Null; Start-Sleep 2 } catch {}

function Remove-Safe($p) {
    if (Test-Path $p) {
        Get-ChildItem $p -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object { try { $_.Attributes = 'Normal' } catch {} }
        Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue
        Write-Host "usunieto: $p"
    }
}

if ($ModelsOnly) {
    Remove-Safe (Join-Path $Root 'AI-Models')
    Remove-Safe (Join-Path $Root 'ComfyUI\ComfyUI\models\diffusion_models')
    Remove-Safe (Join-Path $Root 'ComfyUI\ComfyUI\models\text_encoders')
    Remove-Safe (Join-Path $Root 'ComfyUI\ComfyUI\models\vae')
    exit 0
}

$targets = 'AI-Models','ComfyUI','tools','.venv','Cache','Downloads','Logs','app\web','frontend\node_modules'
foreach ($t in $targets) { Remove-Safe (Join-Path $Root $t) }

if ($Everything) {
    $ok = Read-Host 'Usunac rowniez Projects, Exports i Backups? Wpisz TAK'
    if ($ok -eq 'TAK') { foreach ($t in 'Projects','Exports','Backups') { Remove-Safe (Join-Path $Root $t) } }
}
Write-Host "`nOdinstalowano komponenty StudioEdit. Twoje projekty i nagrania pozostaly nietkniete (chyba ze uzyto -Everything)."
Write-Host 'Ollama (program) mozna usunac w Ustawienia > Aplikacje.'

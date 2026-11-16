# StudioEdit - instalacja narzedzi (FFmpeg, Node.js portable, 7-Zip, ComfyUI portable)
# Wszystko laduje w C:\StudioEdit\tools i C:\StudioEdit\ComfyUI. Nie zmienia ustawien systemu.
# Skrypt jest idempotentny - mozna go uruchamiac wielokrotnie.
param(
    [switch]$SkipComfy
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root 'tools'
$Dl = Join-Path $Root 'Downloads'
New-Item -ItemType Directory -Force $Tools, $Dl | Out-Null

function Get-File($url, $out) {
    if (Test-Path $out) { Write-Host "[ok] juz pobrane: $out"; return }
    Write-Host "[..] pobieram $url"
    $tmp = "$out.part"
    & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -o $tmp $url
    if ($LASTEXITCODE -ne 0) { throw "Pobieranie nieudane: $url" }
    Move-Item -Force $tmp $out
}

# --- FFmpeg 9.0 (BtbN, GPL: NVENC/NVDEC, libass, vidstab, zimg, rnnoise) ---
$ffDir = Join-Path $Tools 'ffmpeg'
if (-not (Test-Path (Join-Path $ffDir 'bin\ffmpeg.exe'))) {
    $zip = Join-Path $Dl 'ffmpeg.zip'
    Get-File 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n9.0-latest-win64-gpl-9.0.zip' $zip
    $x = Join-Path $Dl 'ffmpeg_x'
    if (Test-Path $x) { Remove-Item -Recurse -Force $x }
    Expand-Archive -Force $zip $x
    $inner = Get-ChildItem $x -Directory | Select-Object -First 1
    if (Test-Path $ffDir) { Remove-Item -Recurse -Force $ffDir }
    Move-Item $inner.FullName $ffDir
    Remove-Item -Recurse -Force $x
}
Write-Host "[ok] FFmpeg: $(& (Join-Path $ffDir 'bin\ffmpeg.exe') -hide_banner -version | Select-Object -First 1)"

# --- RNNoise model dla filtra arnndn (odszumianie bez GPU) ---
$rn = Join-Path $Tools 'rnnoise'
New-Item -ItemType Directory -Force $rn | Out-Null
Get-File 'https://raw.githubusercontent.com/richardpl/arnndn-models/master/std.rnnn' (Join-Path $rn 'std.rnnn')
Get-File 'https://raw.githubusercontent.com/richardpl/arnndn-models/master/sh.rnnn' (Join-Path $rn 'sh.rnnn')

# --- Node.js LTS portable (tylko do budowania frontendu) ---
$nodeDir = Join-Path $Tools 'node'
if (-not (Test-Path (Join-Path $nodeDir 'node.exe'))) {
    $idx = Invoke-RestMethod https://nodejs.org/dist/index.json
    $ver = ($idx | Where-Object { $_.lts } | Select-Object -First 1).version
    $zip = Join-Path $Dl "node-$ver.zip"
    Get-File "https://nodejs.org/dist/$ver/node-$ver-win-x64.zip" $zip
    $x = Join-Path $Dl 'node_x'
    if (Test-Path $x) { Remove-Item -Recurse -Force $x }
    Expand-Archive -Force $zip $x
    $inner = Get-ChildItem $x -Directory | Select-Object -First 1
    if (Test-Path $nodeDir) { Remove-Item -Recurse -Force $nodeDir }
    Move-Item $inner.FullName $nodeDir
    Remove-Item -Recurse -Force $x
}
Write-Host "[ok] Node: $(& (Join-Path $nodeDir 'node.exe') --version)"

# --- 7-Zip standalone (do rozpakowania ComfyUI) ---
$7z = Join-Path $Tools '7zr.exe'
Get-File 'https://www.7-zip.org/a/7zr.exe' $7z

# --- ComfyUI portable (NVIDIA, Blackwell) ---
if (-not $SkipComfy) {
    $comfy = Join-Path $Root 'ComfyUI'
    if (-not (Test-Path (Join-Path $comfy 'ComfyUI\main.py'))) {
        $rel = Invoke-RestMethod https://api.github.com/repos/comfyanonymous/ComfyUI/releases/latest
        $asset = $rel.assets | Where-Object { $_.name -eq 'ComfyUI_windows_portable_nvidia.7z' } | Select-Object -First 1
        $arc = Join-Path $Dl $asset.name
        Get-File $asset.browser_download_url $arc
        $x = Join-Path $Dl 'comfy_x'
        if (Test-Path $x) { Remove-Item -Recurse -Force $x }
        & $7z x $arc "-o$x" -y | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Rozpakowanie ComfyUI nieudane' }
        $inner = Get-ChildItem $x -Directory | Select-Object -First 1
        if (Test-Path $comfy) { Remove-Item -Recurse -Force $comfy }
        Move-Item $inner.FullName $comfy
        Remove-Item -Recurse -Force $x
    }
    Write-Host "[ok] ComfyUI: $comfy"
}
Write-Host 'TOOLS_DONE'

# ============================================================================
#  install_usermap.ps1
#  Instala un usermap (.pk3, .zip, .pak, .bsp o carpeta) de dos maneras:
#    -Mode adb (por defecto): lo envia por ADB a la carpeta de usermaps del
#      movil o Meta Quest 3 sin tener que recompilar el APK.
#    -Mode apk: lo integra en android-app/app/src/main/assets/base/nzp y
#      regenera filelist.txt para incluirlo dentro del APK.
#
#  Ejemplos:
#    powershell -ExecutionPolicy Bypass -File scripts\install_usermap.ps1 "C:\descargas\mi_mapa.pk3"
#    powershell -ExecutionPolicy Bypass -File scripts\install_usermap.ps1 "C:\descargas\mi_mapa.zip" -Mode apk
# ============================================================================

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Source,

    [ValidateSet("adb", "apk")]
    [string]$Mode = "adb"
)

$ErrorActionPreference = "Stop"

$Root = "c:\nazizombiesportable"
if (-not (Test-Path $Root)) {
    $Root = Split-Path -Parent $PSScriptRoot
}

$ResolvedSource = (Resolve-Path $Source).Path
$Item = Get-Item $ResolvedSource

if ($Mode -eq "adb") {
    $AdbCandidates = @(
        "adb",
        "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
        "C:\Users\juani\AppData\Local\Android\Sdk\platform-tools\adb.exe"
    )
    $AdbExe = $null
    foreach ($cand in $AdbCandidates) {
        if (Get-Command $cand -ErrorAction SilentlyContinue) {
            $AdbExe = $cand
            break
        }
        if (Test-Path $cand) {
            $AdbExe = $cand
            break
        }
    }
    if (-not $AdbExe) {
        throw "No se encontro adb.exe. Conecta el dispositivo o usa -Mode apk."
    }

    $RemoteDir = "/sdcard/Android/data/com.nzpteam.nzportable/files/usermaps"
    Write-Host "=== Creando carpeta remota $RemoteDir ===" -ForegroundColor Cyan
    & $AdbExe shell "mkdir -p $RemoteDir"

    Write-Host "=== Enviando $($Item.Name) por ADB ===" -ForegroundColor Cyan
    & $AdbExe push $ResolvedSource "$RemoteDir/"
    if ($LASTEXITCODE -ne 0) {
        throw "Fallo al enviar $ResolvedSource con adb push."
    }

    Write-Host ""
    Write-Host "[OK] Usermap enviado a $RemoteDir/$($Item.Name)" -ForegroundColor Green
    Write-Host "     Abre SOLO -> USER MAPS en el juego y aparecera en la Pagina 1." -ForegroundColor Green
} else {
    $AssetNzp = Join-Path $Root "android-app\app\src\main\assets\base\nzp"
    $MapsDir  = Join-Path $AssetNzp "maps"

    if ($Item.PSIsContainer) {
        Write-Host "=== Copiando carpeta $($Item.Name) a $AssetNzp ===" -ForegroundColor Cyan
        Copy-Item -Path (Join-Path $ResolvedSource "*") -Destination $AssetNzp -Recurse -Force
    } elseif ($Item.Extension -match "^\.(zip|pk3)$") {
        $TmpDir = Join-Path $env:TEMP ("nzp_usermap_" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $TmpDir | Out-Null
        try {
            Expand-Archive -Path $ResolvedSource -DestinationPath $TmpDir -Force
            Copy-Item -Path (Join-Path $TmpDir "*") -Destination $AssetNzp -Recurse -Force
        } finally {
            Remove-Item -Path $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    } elseif ($Item.Extension -match "^\.(bsp|way|nsz|txt|mb2|mbox|hpt)$") {
        $DestFile = Join-Path $MapsDir $Item.Name.ToLowerInvariant()
        Copy-Item -Path $ResolvedSource -Destination $DestFile -Force
    } else {
        throw "Extension no reconocida para -Mode apk: $($Item.Extension)"
    }

    & (Join-Path $Root "scripts\gen_asset_filelist.ps1") -AssetRoot $AssetNzp
    Write-Host "[OK] Usermap integrado en assets/base/nzp y filelist.txt actualizado." -ForegroundColor Green
}

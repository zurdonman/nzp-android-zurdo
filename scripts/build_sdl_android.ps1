# ============================================================================
#  build_sdl_android.ps1
#  Compila las dependencias nativas de Nazi Zombies: Portable para Android.
#
#  Fase 1: SDL2 como libreria COMPARTIDA (libSDL2.so)  -> la usa SDLActivity
#  Fase 2: descarga y extrae el codigo de SDL2_mixer 2.8.2
#
#  Uso:  powershell -ExecutionPolicy Bypass -File .\build_sdl_android.ps1
# ============================================================================

$ErrorActionPreference = 'Stop'

$NDK  = "C:\Users\juani\AppData\Local\Android\Sdk\ndk\28.2.13676358"
$root = "c:\nazizombiesportable\sdl2-src"

$env:ANDROID_NDK_HOME = $NDK
$env:ANDROID_NDK_ROOT = $NDK

# ---------------------------------------------------------------- Fase 1 ----
# SDL2 compartido. La libreria estatica libSDL2.a ya esta en obj/local/ y
# ndk-build no la borra, asi que conservamos ambas variantes.
Write-Host "=== [1/2] SDL2 compartido (libSDL2.so) ===" -ForegroundColor Cyan
Set-Location "$root\SDL-release-2.32.10"

& "$NDK\ndk-build.cmd" `
    NDK_PROJECT_PATH=. `
    APP_BUILD_SCRIPT=Android.mk `
    NDK_APPLICATION_MK=Application.mk `
    APP_MODULES=SDL2 `
    -j8

if ($LASTEXITCODE -ne 0) { throw "SDL2 shared build fallo con codigo $LASTEXITCODE" }

Write-Host "--- libs producidas ---" -ForegroundColor Green
Get-ChildItem -Recurse -Path libs,obj -Filter "libSDL2*.a" -ErrorAction SilentlyContinue |
    ForEach-Object { "  {0}  {1:N2} MB" -f $_.FullName, ($_.Length / 1MB) }
Get-ChildItem -Recurse -Path libs -Filter "libSDL2*.so" -ErrorAction SilentlyContinue |
    ForEach-Object { "  {0}  {1:N2} MB" -f $_.FullName, ($_.Length / 1MB) }

# ---------------------------------------------------------------- Fase 2 ----
Write-Host "=== [2/2] SDL2_mixer 2.8.2 ===" -ForegroundColor Cyan
$zip = "$root\SDL2_mixer-2.8.2.zip"
if (-not (Test-Path $zip)) {
    Write-Host "Descargando SDL_mixer release-2.8.2 ..."
    Invoke-WebRequest -UseBasicParsing `
        "https://github.com/libsdl-org/SDL_mixer/archive/refs/tags/release-2.8.2.zip" `
        -OutFile $zip
}
Write-Host ("Zip: {0:N2} MB" -f ((Get-Item $zip).Length / 1MB))

Expand-Archive -Path $zip -DestinationPath $root -Force

$mixer = Get-ChildItem $root -Directory | Where-Object { $_.Name -like "SDL_mixer*2.8.2*" } | Select-Object -First 1
if (-not $mixer) { throw "No se encontro el directorio extraido de SDL2_mixer" }

Write-Host "Directorio: $($mixer.FullName)" -ForegroundColor Green
Write-Host "--- fuentes en src/ ---"
Get-ChildItem "$($mixer.FullName)\src" -Filter *.c | Select-Object -ExpandProperty Name

Write-Host "=== FASE SDL COMPLETADA ===" -ForegroundColor Green

# ============================================================================
#  build_engine_android.ps1
#  Compila libmain.so (motor NZ:P + plataforma android + GLES shim) para arm64-v8a.
#  Uso:  powershell -ExecutionPolicy Bypass -File scripts\build_engine_android.ps1
# ============================================================================

$ErrorActionPreference = 'Continue'

$ROOT     = "c:\nazizombiesportable"
$ENGINE   = "$ROOT\vril-engine"
$PLATFORM = "$ENGINE\source\platform\android"
$NDK      = "C:\Users\juani\AppData\Local\Android\Sdk\ndk\28.2.13676358"
$LOG      = "$ROOT\scripts\build_engine_android.log"
$APPJNI   = "$ROOT\android-app\app\libs"     # <-- lo que Gradle mete en la APK

Write-Host "=== Build motor Android / arm64-v8a ===" -ForegroundColor Cyan
Write-Host "Plataforma : $PLATFORM"
Write-Host "NDK        : $NDK"
Write-Host "Log        : $LOG"
Write-Host ""

# Regenerar _build_info.h (fecha de build actual)
$bi = "$ENGINE\build\android\_build_info.h"
New-Item -ItemType Directory -Force -Path (Split-Path $bi) | Out-Null
@(
  '#define GIT_HASH "android-port"'
  '#define GIT_BRANCH "android"'
  ('#define BUILD_DATE "' + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') + '"')
) | Set-Content -Encoding ASCII $bi
Write-Host "_build_info.h regenerado:" -ForegroundColor DarkGray
Get-Content $bi | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }

Push-Location $PLATFORM
try {
  & "$NDK\ndk-build.cmd" `
      NDK_PROJECT_PATH=. `
      APP_BUILD_SCRIPT=jni/Android.mk `
      NDK_APPLICATION_MK=jni/Application.mk `
      -j8 2>&1 | Tee-Object -FilePath $LOG
  $code = $LASTEXITCODE
} finally {
  Pop-Location
}

Write-Host ""
if ($code -eq 0) {
  Write-Host "=== BUILD MOTOR OK (exit 0) ===" -ForegroundColor Green
  Get-ChildItem "$PLATFORM\libs" -Recurse -Filter *.so |
    ForEach-Object { "   {0}  {1:N2} MB" -f $_.FullName, ($_.Length/1MB) }

  # ------------------------------------------------------------------
  # Copiar a android-app/app/libs (jniLibs). Sin este paso Gradle empaqueta
  # una libmain.so/libSDL2.so ANTIGUA y los cambios nativos no llegan a la APK.
  # ------------------------------------------------------------------
  Write-Host ""
  Write-Host "=== Copiando nativas a jniLibs ($APPJNI) ===" -ForegroundColor Cyan
  foreach ($abi in @('arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64')) {
    $src = Join-Path "$PLATFORM\libs" $abi
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $APPJNI $abi
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    foreach ($so in Get-ChildItem $src -Filter *.so) {
      Copy-Item $so.FullName $dst -Force
      $info = Get-Item (Join-Path $dst $so.Name)
      "   {0}/{1}  {2:N2} MB  {3}" -f $abi, $so.Name, ($info.Length/1MB), $info.LastWriteTime.ToString('HH:mm:ss') |
        Write-Host -ForegroundColor DarkGray
    }
  }
} else {
  Write-Host "=== BUILD MOTOR FALLO (exit $code) ===" -ForegroundColor Red
  Write-Host "Errores:"
  Select-String -Path $LOG -Pattern "error:" | ForEach-Object { $_.Line } | Select-Object -Unique
}

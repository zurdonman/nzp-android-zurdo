# ============================================================================
#  build_apk.ps1
#  Reempaqueta la APK debug con las librerias nativas ya copiadas en jniLibs.
#  Uso: powershell -ExecutionPolicy Bypass -File scripts\build_apk.ps1
# ============================================================================

$ErrorActionPreference = 'Stop'

$ROOT = "c:\nazizombiesportable"
$APP  = "$ROOT\android-app"
$APK  = "$APP\app\build\outputs\apk\debug\app-debug.apk"

$env:JAVA_HOME        = 'C:\Users\juani\jdk17\jdk-17.0.13+11'
$env:ANDROID_HOME     = 'C:\Users\juani\AppData\Local\Android\Sdk'
$env:ANDROID_SDK_ROOT = 'C:\Users\juani\AppData\Local\Android\Sdk'

Write-Host "=== Gradle assembleDebug ===" -ForegroundColor Cyan
Push-Location $APP
try {
  & "$APP\gradlew.bat" assembleDebug --console=plain
  $code = $LASTEXITCODE
} finally {
  Pop-Location
}

Write-Host ""
if ($code -eq 0 -and (Test-Path $APK)) {
  $f = Get-Item $APK
  Write-Host "=== APK OK ===" -ForegroundColor Green
  Write-Host ("   {0}" -f $f.FullName)
  Write-Host ("   {0:N1} MB   {1}" -f ($f.Length/1MB), $f.LastWriteTime)
} else {
  Write-Host "=== APK FALLO (exit $code) ===" -ForegroundColor Red
  exit 1
}

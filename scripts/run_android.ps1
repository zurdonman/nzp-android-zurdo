# ============================================================================
#  run_android.ps1
#  Instala la APK, la lanza, espera y guarda el logcat filtrado del arranque.
#  Uso: powershell -ExecutionPolicy Bypass -File scripts\run_android.ps1
# ============================================================================

param(
  [int]$WaitSeconds = 15,
  [switch]$SkipInstall
)

$ErrorActionPreference = 'Continue'

$ROOT    = "c:\nazizombiesportable"
$ADB     = "C:\Users\juani\AppData\Local\Android\Sdk\platform-tools\adb.exe"
$APK     = "$ROOT\android-app\app\build\outputs\apk\debug\app-debug.apk"
$PKG     = "com.nzpteam.nzportable"
$ACTIVITY = "org.libsdl.app.SDLActivity"
$LOGOUT  = "$ROOT\android-debug-log.txt"

if (-not (Test-Path $ADB))     { Write-Host "No encuentro adb en $ADB" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $APK))     { Write-Host "No encuentro la APK en $APK  (lanza antes el build)" -ForegroundColor Red; exit 1 }

Write-Host "=== Dispositivos ===" -ForegroundColor Cyan
& $ADB devices

if (-not $SkipInstall) {
  Write-Host "`n=== Instalando APK ===" -ForegroundColor Cyan
  "   APK: {0:N1} MB  {1}" -f ((Get-Item $APK).Length/1MB), (Get-Item $APK).LastWriteTime.ToString('HH:mm:ss') | Write-Host -ForegroundColor DarkGray
  & $ADB install -r $APK
  if ($LASTEXITCODE -ne 0) { Write-Host "Instalacion fallida" -ForegroundColor Red; exit 1 }
}

Write-Host "`n=== Logcat limpio ===" -ForegroundColor Cyan
& $ADB logcat -c | Out-Null

Write-Host "=== Lanzando $PKG/$ACTIVITY ===" -ForegroundColor Cyan
& $ADB shell am start -W -n "$PKG/$ACTIVITY"
Start-Sleep -Seconds $WaitSeconds

Write-Host "`n=== Proceso vivo? ===" -ForegroundColor Cyan
$ps = & $ADB shell pidof $PKG
if ($ps) {
  Write-Host "   SIGUE VIVO, pid=$ps  <-- BUENA SENAL" -ForegroundColor Green
} else {
  Write-Host "   NO hay proceso (se ha cerrado)" -ForegroundColor Yellow
}

Write-Host "`n=== Volcando log a $LOGOUT ===" -ForegroundColor Cyan
& $ADB logcat -d > $LOGOUT 2>$null

$patterns = @(
  'nzportable', 'libmain', 'SDL', 'nzpdata', 'basedir', 'asset',
  'SIGSEGV', 'SIGABRT', 'FATAL', 'Fatal signal', 'AndroidRuntime',
  'RuntimeException', 'UnsatisfiedLinkError', 'ClassNotFoundException',
  'Unable to start activity', 'NoSuchMethod', 'GLES', 'OpenGL', 'shader'
)

Write-Host "`n=== Lineas relevantes (filtradas) ===" -ForegroundColor Cyan
$hits = Select-String -Path $LOGOUT -Pattern $patterns -SimpleMatch:$false
$hits | ForEach-Object { $_.Line } | Write-Host

Write-Host "`n=== Resumen ===" -ForegroundColor Cyan
Write-Host ("   lineas relevantes : {0}" -f $hits.Count)
Write-Host ("   log completo      : {0}" -f $LOGOUT)
if ($ps) { Write-Host "   estado            : ARRANCADO" -ForegroundColor Green }
else     { Write-Host "   estado            : SE CIERRA" -ForegroundColor Yellow }

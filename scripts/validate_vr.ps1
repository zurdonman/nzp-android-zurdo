# ============================================================================
#  validate_vr.ps1
#  Validacion post-begin del fix VR (swapchains creados DESPUES de
#  xrBeginSession, commit 0406088). Instala, despierta el visor, lanza en
#  modo VR, y resume el estado: proceso vivo / log vr_log.txt / crash buffer.
#  Uso:  powershell -ExecutionPolicy Bypass -File scripts\validate_vr.ps1
#        powershell -ExecutionPolicy Bypass -File scripts\validate_vr.ps1 -SkipInstall
# ============================================================================

param(
  [int]$WaitSeconds = 35,
  [switch]$SkipInstall
)

$ErrorActionPreference = 'Continue'

$ROOT     = "c:\nazizombiesportable"
$ADB      = "C:\Users\juani\AppData\Local\Android\Sdk\platform-tools\adb.exe"
$APK      = "$ROOT\android-app\app\build\outputs\apk\debug\app-debug.apk"
$PKG      = "com.nzpteam.nzportable"
$ACTIVITY = "org.libsdl.app.SDLActivity"
$SERIAL   = "2G0YC5ZG7C00RJ"

if (-not (Test-Path $ADB)) { Write-Host "No encuentro adb en $ADB" -ForegroundColor Red; exit 1 }

# --- dispositivo ---
$devs = & $ADB devices | Select-String -Pattern 'device$|unauthorized'
if (-not $devs) {
  Write-Host "NO hay dispositivo conectado. Conecta el Quest por USB (y acepta el dialogo en el visor)." -ForegroundColor Red
  exit 1
}
Write-Host "=== Dispositivos ===" -ForegroundColor Cyan
& $ADB devices

if (-not $SkipInstall) {
  if (-not (Test-Path $APK)) { Write-Host "No encuentro la APK: $APK (lanza antes build_apk.ps1)" -ForegroundColor Red; exit 1 }
  Write-Host "`n=== Instalando APK ===" -ForegroundColor Cyan
  ("   APK: {0:N1} MB  {1}" -f ((Get-Item $APK).Length/1MB), (Get-Item $APK).LastWriteTime.ToString('MM-dd HH:mm:ss')) | Write-Host -ForegroundColor DarkGray
  & $ADB install -r $APK
  if ($LASTEXITCODE -ne 0) { Write-Host "Instalacion fallida" -ForegroundColor Red; exit 1 }
}

# --- despertar visor + matar app + limpiar logcat ---
Write-Host "`n=== Despertando visor ===" -ForegroundColor Cyan
& $ADB shell input keyevent 224 | Out-Null
Start-Sleep -Seconds 2
& $ADB shell dumpsys power | Select-String -Pattern 'mWakefulness=' | ForEach-Object { "   $_" }

& $ADB shell am force-stop $PKG | Out-Null
& $ADB logcat -c | Out-Null

# --- lanzar en modo VR ---
Write-Host "=== Lanzando intent VR ===" -ForegroundColor Cyan
& $ADB shell "am start -a android.intent.action.MAIN -c com.oculus.intent.category.VR -n $PKG/$ACTIVITY"
Write-Host "   esperando $WaitSeconds s (swapchains post-begin = primer WaitFrame -> SYNCHRONIZED)..." -ForegroundColor DarkGray
Start-Sleep -Seconds $WaitSeconds

# --- 1) proceso vivo? ---
Write-Host "`n=== 1) Proceso ===" -ForegroundColor Cyan
$appPid = (& $ADB shell pidof $PKG) -join ''
if ($appPid) { Write-Host "   VIVO pid=$appPid  <-- si no hay crash debajo, el fix aguanta" -ForegroundColor Green }
else      { Write-Host "   MUERTO (se ha cerrado)" -ForegroundColor Red }

# --- 2) crash buffer ---
Write-Host "`n=== 2) Crash buffer ===" -ForegroundColor Cyan
$crash = & $ADB logcat -d -b crash 2>$null | Select-String -Pattern 'Fatal signal|fault addr|libvrapiimpl|libmain'
if ($crash) { $crash | Select-Object -First 12 | ForEach-Object { Write-Host "   $_" -ForegroundColor Red } }
else        { Write-Host "   (vacio: sin SIGSEGV) <-- BUENA SENAL" -ForegroundColor Green }

# --- 3) vr_log.txt en el sandbox de la app ---
Write-Host "`n=== 3) files/vr_log.txt (marcadores clave) ===" -ForegroundColor Cyan
$log = & $ADB shell "run-as $PKG grep -e 'swapchains AHORA' -e 'Reintento swapchains' -e 'Swapchain ojo' -e 'FBOs ojo' -e 'LAYERDUMP' -e 'Acciones' -e 'HB frames' -e 'estado=' -e fallo -e 'xrBeginSession' -e 'xrEndFrame' files/vr_log.txt" 2>$null
if ($log) { $log | Select-Object -Last 30 | ForEach-Object { "   $_" } }
else { Write-Host "   (sin lineas clave; volcando tail crudo)" -ForegroundColor Yellow
     & $ADB shell "run-as $PKG tail -n 30 files/vr_log.txt" 2>$null | ForEach-Object { "   $_" } }

# --- mtime del log (evita fiarse de log rancio) ---
Write-Host "`n=== 4) mtime/size vr_log.txt ===" -ForegroundColor Cyan
& $ADB shell "run-as $PKG ls -la files/vr_log.txt" 2>$null | ForEach-Object { "   $_" }
& $ADB shell date 2>$null | ForEach-Object { "   hora dispositivo: $_" }

Write-Host "`nInterpretacion: exito = 'Intento de swapchains #N (estado=3/4/5, post-begin)' -> 'FBOs ojo listos' -> 'LAYERDUMP n=2' -> 'HB frames' subiendo, proceso VIVO, crash buffer vacio." -ForegroundColor Cyan
Write-Host "Si todo verde: pregunta al usuario -> ¿se ve el mundo en 3D estereo? ¿gira la camara al mover la cabeza?" -ForegroundColor Cyan

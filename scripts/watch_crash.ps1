# ============================================================================
#  watch_crash.ps1
#  Captura CONTINUA de logcat con simbolizacion de crashes via ndk-stack.
#  Cuando el juego muere (SIGSEGV/SIGABRT), crash-watch.log contiene el
#  backtrace con nombres de funcion y linea de codigo.
#
#  Uso:  powershell -ExecutionPolicy Bypass -File scripts\watch_crash.ps1
#  Para: Ctrl+C (o se mata el proceso adb logcat)
#  Log:  c:\nazizombiesportable\crash-live.log  (logcat crudo threadtime)
#  Despues del crash: scripts\sim_crash.ps1 (simboliza con ndk-stack)
# ============================================================================

$ROOT      = "c:\nazizombiesportable"
$ADB       = "C:\Users\juani\AppData\Local\Android\Sdk\platform-tools\adb.exe"
$OUT       = "$ROOT\crash-live.log"

$ps = & $ADB shell pidof com.nzpteam.nzportable
Write-Host "=== Vigilancia de crash activa (v2: streaming crudo) ===" -ForegroundColor Cyan
Write-Host "   proceso juego : $(if ($ps) { "vivo (pid $ps)" } else { "NO arrancado" })"
Write-Host "   log salida    : $OUT"
Write-Host "   Juega y reproduce el crash. Despues avisame y corro la simbolizacion." -ForegroundColor Yellow

& $ADB logcat -c
# Stream crudo a disco con cmd.exe (PowerShell es lento con streams infinitos;
# ndk-stack en el pipe bufferiza y no escribe nada en vivo, se simboliza despues).
cmd /c "`"$ADB`" logcat -v threadtime > `"$OUT`" 2>&1"

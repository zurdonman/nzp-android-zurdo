# ============================================================================
#  sim_crash.ps1
#  Se ejecuta DESPUES de un crash (una vez el juego se ha cerrado solo).
#  1. Detecta el bloque tombstone/Fatal signal en crash-live.log.
#  2. Corre ndk-stack -sym sobre el log completo para simbolizar backtraces.
#  3. Guarda el resultado en crash-symbolicated.txt.
#
#  Uso:  powershell -ExecutionPolicy Bypass -File scripts\sim_crash.ps1
# ============================================================================

$ROOT      = "c:\nazizombiesportable"
$ADB       = "C:\Users\juani\AppData\Local\Android\Sdk\platform-tools\adb.exe"
$NDK       = "C:\Users\juani\AppData\Local\Android\Sdk\ndk\28.2.13676358"
$NDK_STACK = "$NDK\ndk-stack.cmd"
$SYM       = "$ROOT\vril-engine\source\platform\android\obj\local\arm64-v8a"
$LOG       = "$ROOT\crash-live.log"
$OUT       = "$ROOT\crash-symbolicated.txt"

if (-not (Test-Path $LOG)) { Write-Host "No existe $LOG - arranca antes watch_crash.ps1" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $NDK_STACK)) { Write-Host "No encuentro ndk-stack en $NDK_STACK" -ForegroundColor Red; exit 1 }
if (-not (Test-Path "$SYM\libmain.so")) { Write-Host "No encuentro libmain.so con simbolos en $SYM" -ForegroundColor Red; exit 1 }

# 1. Descarta un dump de logcat fresco (por si el stream perdio lineas al morir adb)
$tail = & $ADB logcat -d -v threadtime -t 4000 2>$null
if ($tail) { $tail | Set-Content "$ROOT\crash-live-tail.txt" -Encoding UTF8 }

# 2. Filtra solo lo relevante: senales fatales, backtraces y mensajes del motor
$pattern = 'Fatal signal|signal \d+ \(|backtrace|#[0-9]+ pc|Tombstone|tombstoned|libmain\.so|DEBUG|libSDL2|SIGABRT|SIGSEGV|SIGBUS|libnzp|Build fingerprint|Abort message'
$filtered = Get-Content $LOG | Select-String -Pattern $pattern
$filtered | Set-Content "$ROOT\crash-filtered.txt" -Encoding UTF8
Write-Host "Lineas relevantes: $($filtered.Count)" -ForegroundColor Cyan

# 3. Simboliza el log completo (ndk-stack ignora lineas sin backtrace)
$ndkstack = "$NDK_STACK"
$ndkstack = $ndkstack -replace '/', '\'
cmd /c "`"$ndkstack`" -sym `"$SYM`" -i `"$LOG`" > `"$OUT`" 2>&1"
Write-Host "Log simbolizado en $OUT" -ForegroundColor Cyan
Write-Host "Filtrado en $ROOT\crash-filtered.txt" -ForegroundColor Cyan

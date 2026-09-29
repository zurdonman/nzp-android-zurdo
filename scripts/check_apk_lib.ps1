# ============================================================================
#  check_apk_lib.ps1
#  Comprueba que la libmain.so dentro de la APK coincide con la recien compilada.
# ============================================================================
$ErrorActionPreference = 'Stop'

$engine = 'c:\nazizombiesportable\vril-engine\source\platform\android\libs\arm64-v8a\libmain.so'
$applib = 'c:\nazizombiesportable\android-app\app\libs\arm64-v8a\libmain.so'
$apk    = 'c:\nazizombiesportable\android-app\app\build\outputs\apk\debug\app-debug.apk'

function Md5Of([string]$path) {
  return (Get-FileHash $path -Algorithm MD5).Hash
}

$md5Engine = Md5Of $engine
$md5App    = Md5Of $applib

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($apk)
$entry = $zip.Entries | Where-Object { $_.FullName -eq 'lib/arm64-v8a/libmain.so' }
if (-not $entry) {
  Write-Host 'NO hay lib/arm64-v8a/libmain.so en la APK' -ForegroundColor Red
  $zip.Dispose(); exit 1
}
$ms = New-Object System.IO.MemoryStream
$s = $entry.Open()
$s.CopyTo($ms)
$s.Close()
$zip.Dispose()

$md5inApk = [BitConverter]::ToString(
  [System.Security.Cryptography.MD5]::Create().ComputeHash($ms.ToArray())
) -replace '-',''

Write-Host ('engine  : {0,10:N0}  md5={1}' -f (Get-Item $engine).Length, $md5Engine)
Write-Host ('app libs: {0,10:N0}  md5={1}' -f (Get-Item $applib).Length, $md5App)
Write-Host ('APK     : {0,10:N0}  md5={1}' -f $ms.Length, $md5inApk)
Write-Host ''
if ($md5Engine -eq $md5inApk) {
  Write-Host 'COINCIDE: la APK lleva la libmain.so nueva.' -ForegroundColor Green
} else {
  Write-Host 'DISTINTA: la APK lleva una libmain.so vieja.' -ForegroundColor Red
  exit 1
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip2 = [System.IO.Compression.ZipFile]::OpenRead($apk)
$nzpEntries = @($zip2.Entries | Where-Object { $_.FullName -like 'assets/base/nzp/*' })
$filelist = @($zip2.Entries | Where-Object { $_.FullName -eq 'assets/base/nzp/filelist.txt' })
$zip2.Dispose()
Write-Host ''
Write-Host ('Entradas assets/base/nzp/*: {0}' -f $nzpEntries.Count)
if ($filelist.Count -gt 0) {
  Write-Host ('filelist.txt en la APK: SI ({0:N0} bytes)' -f $filelist[0].Length) -ForegroundColor Green
} else {
  Write-Host 'filelist.txt en la APK: NO' -ForegroundColor Red
  exit 1
}

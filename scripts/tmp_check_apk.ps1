$apk = "c:\nazizombiesportable\android-app\app\build\outputs\apk\debug\app-debug.apk"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::OpenRead($apk)
$e = $z.Entries | Where-Object { $_.FullName -eq "lib/arm64-v8a/libmain.so" }
$s = $e.Open()
$ms = New-Object System.IO.MemoryStream
$s.CopyTo($ms)
$s.Close()
$z.Dispose()
$bytes = $ms.ToArray()
$txt = [System.Text.Encoding]::ASCII.GetString($bytes)
Write-Host "APK libmain.so bytes = $($bytes.Length)"
Write-Host ("contains 'pre-begin'            : " + $txt.Contains("pre-begin"))
Write-Host ("contains 'swapchains ya listos'  : " + $txt.Contains("swapchains ya listos"))
Write-Host ("contains 'post-begin'            : " + $txt.Contains("post-begin"))
Write-Host ("contains 'LOOP %d active'        : " + $txt.Contains("LOOP %d active"))
$sha = [System.Security.Cryptography.SHA256]::Create()
$h1 = ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") }) -join ''
$libBytes = [System.IO.File]::ReadAllBytes("c:\nazizombiesportable\android-app\app\libs\arm64-v8a\libmain.so")
$h2 = ($sha.ComputeHash($libBytes) | ForEach-Object { $_.ToString("x2") }) -join ''
Write-Host "APK  sha256 = $($h1.Substring(0,24))"
Write-Host "libs sha256 = $($h2.Substring(0,24))"
Write-Host ("IGUALES: " + ($h1 -eq $h2))

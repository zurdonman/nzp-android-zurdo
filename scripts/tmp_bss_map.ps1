$ndk = "C:\Users\juani\AppData\Local\Android\Sdk\ndk\28.2.13676358\toolchains\llvm\prebuilt\windows-x86_64\bin"
$so  = "c:\nazizombiesportable\vril-engine\source\platform\android\obj\local\arm64-v8a\libmain.so"
$lines = & "$ndk\llvm-nm.exe" -n -S $so 2>$null
foreach ($l in $lines) {
    if ($l -match '^([0-9a-fA-F]{16})\s+([0-9a-fA-F]{16})?\s*([A-Za-z])\s+(.+)$') {
        $addr = [Convert]::ToUInt64($Matches[1], 16)
        $size = if ($Matches[2]) { [Convert]::ToUInt64($Matches[2], 16) } else { 0 }
        $type = $Matches[3]
        $name = $Matches[4]
        # .bss: 'B' grande, 'b' estatico; rango bajo game_build_date (0x1fea50)
        if ($addr -ge 0x1fc000 -and $addr -le 0x1fea58 -and ($type -eq 'B' -or $type -eq 'b')) {
            $over = $addr + $size
            $mark = if ($over -gt 0x1fea50 -and $addr -lt 0x1fea58) { "  <<< SOLAPA game_build_date" } else { "" }
            "{0}  sz={1,-8} {2} {3}{4}" -f $Matches[1], $size, $type, $name, $mark
        }
    }
}

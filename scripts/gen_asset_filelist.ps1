# ============================================================================
#  gen_asset_filelist.ps1
#  Genera el indice de assets que consume el motor en Android:
#     android-app/app/src/main/assets/base/nzp/filelist.txt
#
#  Android descarta SILENCIOSAMENTE los ficheros de assets que empiezan por
#  punto (".filelist"), por eso el indice DEBE tener un nombre normal.
#
#  Ademas, AAssetDir_getNextFileName() en Android solo devuelve los ficheros
#  del nivel pedido: NO devuelve los subdirectorios, asi que la recursion nativa
#  no puede descubrir el arbol. Este indice es la unica via fiable.
#
#  Uso: powershell -ExecutionPolicy Bypass -File scripts\gen_asset_filelist.ps1
# ============================================================================

$ErrorActionPreference = 'Stop'

$ROOT     = 'c:\nazizombiesportable'
$ASSETS   = "$ROOT\android-app\app\src\main\assets\base\nzp"
$FILELIST = "$ASSETS\filelist.txt"
$LEGACY   = "$ASSETS\.filelist"

if (-not (Test-Path $ASSETS)) {
  Write-Host "No existe $ASSETS" -ForegroundColor Red
  exit 1
}

# Rutas relativas a base/nzp, con separador '/', ordenadas.
$lines = Get-ChildItem -Path $ASSETS -Recurse -File -Force |
  Where-Object { $_.Name -ne 'filelist.txt' -and $_.Name -ne '.filelist' } |
  ForEach-Object { $_.FullName.Substring($ASSETS.Length + 1).Replace('\', '/') } |
  Sort-Object

# UTF8 sin BOM y con salto de linea LF (nada de \r, que el motor interpretaria
# como parte del nombre del fichero).
$text = ($lines -join "`n") + "`n"
[System.IO.File]::WriteAllText($FILELIST, $text, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "=== Indice de assets generado ===" -ForegroundColor Green
Write-Host ("   {0}" -f $FILELIST)
Write-Host ("   {0} entradas   {1:N0} bytes" -f $lines.Count, (Get-Item $FILELIST).Length)

# Elimina el indice antiguo (inutil: Android no lo empaqueta).
if (Test-Path $LEGACY) {
  Remove-Item $LEGACY -Force
  Write-Host "   eliminado .filelist (Android no lo empaqueta)" -ForegroundColor Yellow
}

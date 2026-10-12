@echo off
REM ============================================================================
REM  INSTALAR_USERMAP.bat
REM  Arrastra y suelta un .pk3, .zip o .bsp sobre este archivo para enviarlo
REM  directamente al movil / Meta Quest 3 por ADB (sin recompilar el APK).
REM ============================================================================
setlocal
cd /d "%~dp0"

if "%~1"=="" (
    echo Uso: Arrastra un archivo .pk3, .zip, .bsp o carpeta sobre INSTALAR_USERMAP.bat
    echo   o ejecuta: INSTALAR_USERMAP.bat ruta\al\mapa.pk3 [adb^|apk]
    pause
    exit /b 1
)

set MODE=%~2
if "%MODE%"=="" set MODE=adb

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_usermap.ps1" -Source "%~1" -Mode %MODE%
pause

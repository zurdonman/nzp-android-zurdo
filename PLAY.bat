@echo off
REM ============================================================
REM  Nazi Zombies: Portable - Lanzador (build propio)
REM  Binario: vril-engine\build\sdl\nzportable.exe
REM  Datos  : game\nzp\
REM ============================================================
setlocal
cd /d "%~dp0"

REM Necesitamos las DLLs de MSYS2 UCRT64 (SDL2, SDL2_mixer, libgcc)
set "PATH=C:\msys64\ucrt64\bin;%PATH%"

REM basedir = carpeta que CONTIENE "nzp\"  ->  game
set "BASEDIR=game"
set "EXE=vril-engine\build\sdl\nzportable.exe"

if not exist "%EXE%" (
    echo [ERROR] No existe el binario: %EXE%
    echo         Ejecuta primero BUILD.bat
    pause
    exit /b 1
)

echo Iniciando Nazi Zombies: Portable...
echo   Binario : %EXE%
echo   Datos   : %BASEDIR%\nzp
echo.

REM %* pasa argumentos extra (ej: +map nzp_warehouse)
"%EXE%" -basedir "%BASEDIR%" %*

endlocal

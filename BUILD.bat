@echo off
REM ============================================================
REM  Recompilar el motor Vril desde el codigo fuente
REM  Requiere MSYS2 con UCRT64 (gcc, make, SDL2)
REM ============================================================
setlocal
cd /d "%~dp0"

echo Compilando Vril Engine...
C:\msys64\usr\bin\bash.exe -lc "export PATH=/ucrt64/bin:/usr/bin:$PATH && cd /c/nazizombiesportable/vril-engine && make -f Makefile.sdl 2>&1 | tail -15"

if exist "vril-engine\build\sdl\nzportable.exe" (
    echo.
    echo [OK] Compilado: vril-engine\build\sdl\nzportable.exe
) else (
    echo.
    echo [ERROR] No se genero el binario
)

endlocal

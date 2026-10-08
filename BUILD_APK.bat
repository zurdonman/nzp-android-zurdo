@echo off
REM ============================================================
REM  BUILD_APK.bat -- Compila el motor nativo y genera el APK
REM  Requiere: Android SDK + NDK 28.2.13676358 + JDK 17
REM  Salida:   android-app\app\build\outputs\apk\debug\app-debug.apk
REM ============================================================
setlocal
cd /d "%~dp0"

set SDK=%LOCALAPPDATA%\Android\Sdk
set NDK=%SDK%\ndk\28.2.13676358
set GRADLE=android-app\gradlew.bat

echo === [1/4] Regenerando _build_info.h ===
REM system.c incluye "_build_info.h" desde vril-engine\build\android (que esta
REM en .gitignore). Si no existe, ndk-build falla con "file not found".
if not exist vril-engine\build\android mkdir vril-engine\build\android
set BI=vril-engine\build\android\_build_info.h
>  "%BI%" echo #define GIT_HASH "android-port"
>> "%BI%" echo #define GIT_BRANCH "android"
>> "%BI%" echo #define BUILD_DATE "%DATE% %TIME%"
type "%BI%"

echo.
echo === [2/4] Compilando motor nativo (ndk-build, arm64-v8a) ===
pushd vril-engine\source\platform\android
call "%NDK%\ndk-build.cmd" NDK_PROJECT_PATH=. APP_BUILD_SCRIPT=jni/Android.mk NDK_APPLICATION_MK=jni/Application.mk -j 8
if errorlevel 1 ( popd & echo [ERROR] Fallo ndk-build & exit /b 1 )
popd

echo.
echo === [3/4] Copiando librerias nativas al proyecto Android ===
if not exist android-app\app\libs\arm64-v8a mkdir android-app\app\libs\arm64-v8a
copy /Y vril-engine\source\platform\android\libs\arm64-v8a\libmain.so android-app\app\libs\arm64-v8a\ >nul
copy /Y vril-engine\source\platform\android\libs\arm64-v8a\libSDL2.so android-app\app\libs\arm64-v8a\ >nul

echo.
echo === [4/4] Generando APK (gradle assembleDebug) ===
pushd android-app
call %GRADLE% assembleDebug
if errorlevel 1 ( popd & echo [ERROR] Fallo gradle & exit /b 1 )
popd

echo.
echo ============================================================
echo  [OK] APK generado:
echo    android-app\app\build\outputs\apk\debug\app-debug.apk
echo ============================================================

endlocal

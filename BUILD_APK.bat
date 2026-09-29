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

echo === [1/3] Compilando motor nativo (ndk-build, arm64-v8a) ===
pushd vril-engine\source\platform\android
call "%NDK%\ndk-build.cmd" NDK_PROJECT_PATH=. APP_BUILD_SCRIPT=jni/Android.mk NDK_APPLICATION_MK=jni/Application.mk -j 8
if errorlevel 1 ( popd & echo [ERROR] Fallo ndk-build & exit /b 1 )
popd

echo.
echo === [2/3] Copiando librerias nativas al proyecto Android ===
if not exist android-app\app\libs\arm64-v8a mkdir android-app\app\libs\arm64-v8a
copy /Y vril-engine\source\platform\android\libs\arm64-v8a\libmain.so android-app\app\libs\arm64-v8a\ >nul
copy /Y vril-engine\source\platform\android\libs\arm64-v8a\libSDL2.so android-app\app\libs\arm64-v8a\ >nul

echo.
echo === [3/3] Generando APK (gradle assembleDebug) ===
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

# Checklist VR Quest 3

## Antes de tocar codigo

- Confirmar si el problema es de OpenXR, render, input o gameplay.
- Leer `vr_log.txt` y comprobar su fecha.
- No confiar en un APK solo porque Gradle dice `BUILD SUCCESSFUL`.
- Revisar si `libmain.so` fue recompilado despues del cambio.

## Build

```powershell
Remove-Item vril-engine/source/platform/android/obj -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item vril-engine/source/platform/android/libs -Recurse -Force -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File scripts/build_engine_android.ps1
powershell -ExecutionPolicy Bypass -File scripts/build_apk.ps1
```

Auditar el APK:

```powershell
$apk = 'android-app/app/build/outputs/apk/debug/app-debug.apk'
$tmp = Join-Path $env:TEMP 'nzp-apk-audit'
Expand-Archive -LiteralPath $apk -DestinationPath $tmp -Force
$so = Join-Path $tmp 'lib/arm64-v8a/libmain.so'
$text = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($so))
$text.Contains('marcador-de-la-funcion')
```

## Instalacion

```powershell
$adb = 'C:\Users\juani\AppData\Local\Android\Sdk\platform-tools\adb.exe'
$d = '10.19.2.99:5555'
& $adb connect $d
& $adb -s $d install -r android-app/app/build/outputs/apk/debug/app-debug.apk
& $adb -s $d shell am start -a android.intent.action.MAIN -c com.oculus.intent.category.VR -n com.nzpteam.nzportable/org.libsdl.app.SDLActivity
```

Comprobar:

- `install -r` devuelve `Success`.
- `dumpsys package` muestra una hora posterior.
- `pidof com.nzpteam.nzportable` devuelve PID.
- El hash del APK local coincide con el APK remoto si se extrae mediante la ruta actual de `pm path`.

## Diagnostico runtime

Los filtros con comillas anidadas de PowerShell suelen romperse. Para consultas sencillas, usar comandos separados:

```powershell
& $adb -s $d shell run-as com.nzpteam.nzportable ls -l files/vr_log.txt
& $adb -s $d shell run-as com.nzpteam.nzportable grep MOVE files/vr_log.txt
```

Buscar:

- sesion `FOCUSED`;
- acciones creadas y adjuntas;
- `MOVE body/headw/gun/d`;
- `LASER start/hit/len`;
- errores `xrEndFrame`, `XR_ERROR_LAYER_INVALID` y `RUNTIME_FAILURE`.

## Regresiones frecuentes

- HUD desaparece: quitar transformaciones `glPushMatrix/glScalef` alrededor de `HUD_Draw()` en GLES Quest; probar primero matriz 2D directa.
- Menu estrecho: ajustar el lienzo virtual del modo menu, no la resolucion fisica del swapchain.
- Arma y laser desalineados: compartir pose/rayo y revisar offsets locales.
- Movimiento cambia al apuntar abajo: normalizar diferencia de yaw y evitar `atan2` cuando la proyeccion horizontal es casi cero.
- Pantalla negra: revisar contexto EGL ES3, ciclo de swapchain, FOV y liberacion de imagenes.

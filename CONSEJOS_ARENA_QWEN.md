# Consejos de Arena.ai para Qwen — NZ:P Android + Meta Quest 3

> Documento de contexto para el agente que trabaja en modo autónomo desde VS Code.
> Leer este archivo antes de modificar código.

## Ubicación del repositorio

- Raíz del proyecto: `/home/user/nzp-android-zurdo`
- Rama de trabajo de esta sesión: `arena/755f10cb-nzp-android-zurdo`
- No cambiar de rama ni crear otra rama.
- No borrar, renombrar ni mover la raíz del repositorio ni `.git`.

En Windows, el mismo proyecto se documenta habitualmente como:

```text
C:\nazizombiesportable
```

## Objetivo

Mantener y terminar el port de Nazi Zombies: Portable para Android arm64 y Meta Quest 3 mediante:

```text
SDL2 + OpenGL ES + OpenXR + Android NDK + Gradle
```

La prioridad actual es validar el render estéreo real en Quest 3, el head tracking y los mandos Touch, sin romper el modo táctil Android existente.

## Documentación que hay que leer primero

Orden recomendado:

1. `README.md` — visión general del proyecto.
2. `PROGRESO.md` — estado técnico y bloqueos conocidos.
3. `ROADMAP_ANDROID_VR.md` — hoja de ruta VR.
4. Este archivo — reglas prácticas para el trabajo autónomo.
5. `vril-engine/source/platform/android/vr/vr_openxr.c` y `.h` — implementación OpenXR.
6. `vril-engine/source/render/r_screen.c` — render por ojo.
7. `vril-engine/source/platform/android/sys_sdl.c` — ciclo principal Android/VR.
8. `vril-engine/source/platform/android/jni/Android.mk` y `Application.mk` — build nativo.

## Estado conocido al iniciar

- El juego táctil Android ya está implementado.
- Los assets jugables están en `android-app/app/src/main/assets/base/nzp/`.
- El código OpenXR ya contiene inicialización, sesión, swapchains, FBOs, render estéreo, head tracking y acciones de los mandos.
- Según `PROGRESO.md`, Quest 3 llegó hasta `xrBeginSession`.
- Falta validar con el casco puesto que se vea correctamente el render de ambos ojos y que los controles funcionen.
- El árbol de trabajo puede tener cambios hechos por otro agente: comprobar siempre `git status` antes de editar.

## Reglas antes de tocar código

1. Ejecutar:

   ```bash
   git status --short --branch
   git log --oneline -3
   ```

2. Leer el contexto del código que se va a modificar.
3. No sobrescribir cambios ajenos.
4. Hacer cambios pequeños, reversibles y bien localizados.
5. No introducir binarios grandes, APKs, logs ni builds en Git salvo que se solicite expresamente.
6. No desactivar el modo táctil para arreglar VR.
7. No inventar resultados de pruebas en Quest: separar siempre “compilado”, “instalado” y “validado dentro del visor”.
8. Después de cada cambio, revisar:

   ```bash
   git diff --check
   git diff --stat
   ```

## Prioridad de validación VR

Comprobar en este orden:

1. `xrInitializeLoaderKHR` devuelve OK.
2. `xrCreateInstance` y `xrGetSystem` encuentran el runtime.
3. `xrCreateSession` funciona con el contexto OpenGL ES correcto.
4. `xrBeginSession` llega a estado iniciado.
5. Se crean las dos swapchains.
6. Cada ojo adquiere y libera su imagen correctamente.
7. `xrEndFrame` entrega una capa de proyección con las dos vistas.
8. El Quest muestra imagen en ambos ojos.
9. El head tracking modifica yaw/pitch sin invertir ejes.
10. Los controles funcionan:
    - Stick izquierdo: movimiento.
    - Stick derecho: cámara.
    - Trigger derecho: disparo.
    - Grip: acción secundaria/granada según el mapeo.
    - A/B/X/Y: acciones del juego.
    - Menú izquierdo: menú.

## Diagnóstico

Usar primero los logs persistentes de VR y después `adb logcat`. Buscar especialmente:

```text
xrInitializeLoaderKHR
xrCreateInstance
xrGetSystem
xrCreateSession
xrBeginSession
xrCreateSwapchain
xrAcquireSwapchainImage
xrWaitSwapchainImage
xrEndFrame
Sesion INICIADA
FALLO
```

Un código OpenXR negativo debe anotarse con su nombre, no solo con el número. No ocultar un error haciendo que el juego continúe en negro.

## Compilación Android

El build principal está pensado para Windows y usa:

```text
BUILD_APK.bat
```

Los scripts de `scripts/` contienen rutas históricas de Windows y pueden requerir adaptación si se ejecutan desde Linux/WSL. El build necesita:

- JDK 17.
- Android SDK.
- Android NDK `28.2.13676358`.
- OpenXR SDK/loader en `third_party/OpenXR-SDK`.
- Fuentes SDL2 y SDL2_mixer en las rutas esperadas por `Android.mk`.

No afirmar que el APK está generado si no se ha encontrado físicamente en:

```text
android-app/app/build/outputs/apk/debug/app-debug.apk
```

## Qué hacer si falta OpenXR SDK

El `Android.mk` espera referencias bajo:

```text
vril-engine/third_party/OpenXR-SDK/
```

Comprobar primero si existe. Si falta:

1. No crear headers o bibliotecas falsas.
2. No commitear binarios generados sin autorización.
3. Informar del bloqueo y preparar una solución reproducible/documentada.
4. Mantener la compilación 2D/táctil intacta si es posible.

## Criterios de aceptación

Un cambio VR solo se considera terminado cuando:

- Compila `arm64-v8a` sin errores.
- Genera el APK.
- El APK se instala en Quest 3.
- Hay evidencia de logs OpenXR.
- El visor muestra imagen estéreo.
- Head tracking y al menos disparo/movimiento funcionan.
- El modo táctil Android sigue compilando y no se rompe.

## Formato de informe al terminar una tarea

Responder siempre con:

1. Archivos modificados.
2. Qué problema se resolvió.
3. Comandos ejecutados.
4. Resultado real de cada comando.
5. Qué no se pudo probar.
6. Próximo bloqueo o siguiente paso.

No indicar “validado en Quest 3” salvo que se haya ejecutado realmente en un Quest 3.

## Nota para coordinación entre agentes

Si otro agente está trabajando simultáneamente, no reformatear archivos completos ni hacer cambios masivos. Revisar `git diff` y coordinar los cambios por archivo. Este documento es una guía de contexto; no sustituye la documentación de OpenXR ni los logs reales del dispositivo.

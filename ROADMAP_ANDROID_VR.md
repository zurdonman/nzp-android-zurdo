# 🗺️ Hoja de Ruta: Nazi Zombies: Portable → Android + VR

**Fecha:** 2026-10-06 (actualizado)
**Proyecto:** NZ:P Team — *Vril Engine* (fork mejorado del motor Quake)
**Estado actual:** ✅ Motor compilado y **JUGABLE en Windows** (`PLAY.bat`) · ✅ **Port Android JUGABLE con controles táctiles** (APK `com.nzpteam.nzportable` en móvil y Quest 3) · 🔶 **VR (OpenXR en Quest 3) en fase final**: sesión FOCUSED + acciones de mandos + head tracking verificados en casco; fix raíz del crash `xrEndFrame` (swapchains post-begin) desplegado, pendiente de validación visual estéreo (swapchains/FBOs 1680x1760 por ojo)

---

## 📌 Resumen ejecutivo (estado 2026-10-06)

| Pregunta | Respuesta verificada |
|---|---|
| ¿Existe ya un port Android? | **SÍ, terminado.** `source/platform/android/` + SDL2 compilado `arm64-v8a` + APK Gradle. El juego arranca, menú y gameplay táctil jugables (stick virtual, look por arrastre, 10 botones, sprint). |
| ¿Cuál es la mejor base para VR? | Confirmada: SDL2 + **OpenXR nativo in-process** (`vr_openxr.c`), GLES 1.1 + FBOs OES por ojo, swapchains ES3 compartido. `injector.cpp` (opción B) descartado: no sirve en Quest. |
| ¿Se compila hoy en esta máquina? | **Sí.** PC: `BUILD.bat`. Android: `scripts\build_engine_android.ps1` + `scripts\build_apk.ps1` (NDK 28.2, JDK 17). |
| ¿Dónde están los datos jugables? | `game\nzp\` (1152 archivos / 105 MB del nightly) empaquetados en `android-app/app/src/main/assets/base/nzp/` y extraídos al almacenamiento interno en el primer arranque. |
| ¿VR funcionando en Quest 3? | **A un paso.** Verificado en casco: sesión hasta FOCUSED(5), swapchains+FBOs 1680x1760 por ojo, poses de cabeza vivas, "Acciones de mandos listas". Bloqueador histórico (SIGSEGV en `xrEndFrame` con capas) diagnosticado con evidencia forense: los swapchains deben crearse DESPUÉS de `xrBeginSession` (el compositor de Meta no registra imágenes creadas pre-begin). Fix desplegado (commit `0406088`), falta validación visual con casco. |

---

## 🧩 Estructura del proyecto (real)

```
c:\nazizombiesportable\
├── ROADMAP_ANDROID_VR.md        ← este documento
├── PLAY.bat                     ← ✅ lanzador de un clic (juego jugable)
├── BUILD.bat                    ← ✅ recompila el motor (MSYS2 + make)
├── injector.cpp                 ← esqueleto DLL OpenXR (VR, prototipo PC)
├── game\nzp\                    ← ✅ DATOS JUGABLES COMPLETOS (1152 archivos / 105 MB)
│   ├── progs.dat                ← ⚠️ viene del release nightly, NO de assets
│   ├── maps\*.bsp               ← ⚠️ compilados, solo existen en nightly
│   └── maps\*.nsz, gfx\, models\, sounds\, textures\…
├── nightly\                     ← release oficial de referencia (para comparar A/B)
├── quakec\                      ← fuentes QuakeC (progs/*.src) — requiere fteqcc para compilar
└── vril-engine\                 ← motor clonado (github.com/nzp-team/vril-engine)
    ├── Makefile.sdl             ← build de PC  (gcc + SDL2)
    ├── Makefile.psp / .psp2 / .ctr / .nx / .nspire   ← builds de consolas
    ├── source/
    │   ├── platform/sdl/        ← ★ BASE para Android y VR
    │   ├── platform/psp/, nx/…  ← referencia de cómo se añade una plataforma
    │   └── menu/, render/, qcvm/…
    ├── build/sdl/nzportable.exe ← ✅ binario compilado y probado
    └── graphify-out/            ← índice del código (graph.json, GRAPH_REPORT.md)
```

### Cómo ejecutar (lo que ya funciona)
```bat
BUILD.bat   :: recompila  → vril-engine\build\sdl\nzportable.exe
PLAY.bat    :: juega      → añade C:\msys64\ucrt64\bin al PATH y usa -basedir game
```
Comando equivalente manual:
```powershell
$env:PATH = "C:\msys64\ucrt64\bin;$env:PATH"
.\vril-engine\build\sdl\nzportable.exe -basedir game
```

### Diagnóstico rápido si algo falla
Añade `-condebug`: escribe `game/nzp/condebug.log` línea a línea. Es la vía más rápida
para localizar el fallo (así se detectaron las causas de los cierres anteriores).

### Cómo se añade una plataforma nueva (patrón del propio proyecto)
El motor está diseñado por *directorios de plataforma*. Cada plataforma aporta:
- `source/platform/<nombre>/platform_def.h` — definiciones propias
- `source/platform/<nombre>/sys_*.c` — arranque, ventana, tiempo
- `source/platform/<nombre>/snd_*.c`, `in_*.c`, `net_*.c`, `music.h`
- Un `Makefile.<nombre>` en la raíz

> 👉 Para Android usaremos `PLATFORM_DIRECTORY=android` y reutilizaremos tanto código SDL como sea posible.

---

## 🅰️ FASE 1 — Port a Android ✅ COMPLETADA (2026-10-02/04)

### Objetivo
Generar un `libnzportable.so` (o `.apk`) que arranque el motor en un teléfono Android.

### 1.1 Prerrequisitos (SDKs y herramientas)

| Herramienta | Para qué | Cómo obtenerla |
|---|---|---|
| **Android NDK** (r25b o superior) | Compilador cruzado clang para Android (`aarch64-linux-android`) | `winget install Google.AndroidStudio` o descargar NDK suelto de developer.android.com |
| **Android SDK + platform-tools** | Empaquetar APK, `adb`, `aapt2` | Viene con Android Studio |
| **CMake** o **ndk-build** | Sistema de build recomendado por Google | Incluido en el NDK |
| **SDL2 para Android** | Capa de ventana/audio/input | Compilar desde fuente (ver 1.3) o usar `SDL2-*-android.zip` oficial |
| **JDK 17** | Firma y empaquetado del APK | `winget install Microsoft.OpenJDK.17` |

### 1.2 Estrategia (de menos a más esfuerzo)

1. **Reutilizar SDL2**: compilar SDL2 para Android y enlazarlo. El motor ya usa SDL2 (`SDL_Init`, `SDL_CreateWindow`, `SDL_GLContext`), así que la mayor parte funciona tal cual.
2. **Adaptar arranque**: Android no tiene `main()` normal; SDL2 lo resuelve con `SDL_main` + `android_main`. En `Makefile.sdl` el flag `-Dmain=SDL_main` se elimina para SDL en Android (SDL lo gestiona).
3. **Capa Android**: crear `source/platform/android/` copiando la estructura de `source/platform/sdl/` y ajustando:
   - `sys_android.c` (arranque, ciclo de vida `APP_CMD_*`)
   - `gl/gl_vidandroid.c` (ventana EGL + contexto GLES)
   - `in_android.c` (táctil + gamepad)
   - `snd_android.c`, `music.h`
4. **Makefile.android** propio (basado en `Makefile.sdl`) apuntando al NDK.

### 1.3 Comandos concretos (cuando el NDK esté instalado)

```bash
# Compilar SDL2 para Android (una vez)
cd SDL2-<ver>
./build-scripts/androidbuild.sh --arch arm64

# Compilar el motor (desde vril-engine/)
export NDK=$ANDROID_HOME/ndk/<ver>
export TOOLCHAIN=$NDK/toolchains/llvm/prebuilt/windows-x86_64
export CC=$TOOLCHAIN/bin/aarch64-linux-android24-clang
make -f Makefile.android CC=$CC
```

### 1.4 Dataset (assets del juego) — ✅ RESUELTO

⚠️ **Corrección de un supuesto inicial:** `nzp-team/assets` **no** contiene el juego jugable.
Contiene mapas en formato **fuente** (`.map` de TrenchBroom, `.way`) y texturas. Los recursos
compilados que el motor necesita en tiempo de ejecución **solo existen en el release `nightly`**:

| Recurso | ¿En `assets`? | ¿En `nightly`? | Tamaño |
|---|---|---|---|
| `progs.dat` (bytecode QuakeC) | ❌ | ✅ | 747.826 bytes |
| `maps/*.bsp` (19 mapas compilados) | ❌ (solo `.map`) | ✅ | — |
| `maps/*.nsz` (10 navegaciones zombie) | ❌ | ✅ | — |
| `gfx/`, `models/`, `sounds/`, `textures/` | parcial (fuentes) | ✅ | — |

**Receta aplicada (ya hecha en `game/nzp/`):**
```powershell
Invoke-WebRequest "https://github.com/nzp-team/nzportable/releases/download/nightly/nzportable-win64.zip" -OutFile nightly-win64.zip
Expand-Archive nightly-win64.zip -DestinationPath nightly
Copy-Item nightly\nzp\* game\nzp\ -Recurse -Force
```
Resultado: `game\nzp\` = **1152 archivos / 105 MB**, idéntico al release
(verificado con `Compare-Object` → 0 diferencias).

> Los `.map`/`.way` del repo `assets` sirven si en el futuro quieres **recompilar** mapas con
> `q3map2`/`bspc`, pero no son necesarios para jugar.

### 1.4b Empaquetado en Android
En Android esto se traduce en meter esos 105 MB en `assets/` del APK (o descargarlos del CDN
al primer arranque, recomendado para no superar los límites del Play Store).

### 1.5 Entregable de la fase
- [x] `nzportable` compilado para `arm64-v8a` (`libmain.so` 1,06 MB vía ndk-build + `Android.mk`)
- [x] APK instalable que arranca hasta el menú (Gradle, `android-app/`, Java 17)
- [x] Con assets: juego jugable con controles táctiles (stick+look+10 botones+sprint+edición de layout; ver PROGRESO.md "Táctil v2")
- [x] Bonus: coop LAN, menú COOPERATIVE, tag v2.0.0

### 1.6 ⚠️ Obstáculos ya detectados (evitar perder tiempo)

Durante la puesta en marcha en PC aparecieron 3 cierres seguidos. Todos tenían la misma
naturaleza y **se repetirán en Android**:

| Síntoma | Causa real | Solución |
|---|---|---|
| `exit=-1073741819` (0xC0000005) al arrancar | `nzp.rc` ejecutaba `startdemos fakedemo` y **`fakedemo.dem` no existe** | eliminar/comentar esa línea en `nzp.rc` |
| Sigue cerrando tras lo anterior | **`progs.dat` ausente** → `COM_LoadHunkFile` da fatal (`pr_edict.c:1159`) | copiar `progs.dat` del nightly |
| Sigue cerrando | **faltan los `.bsp` compilados** de los mapas | copiar `nightly\nzp\maps\*.bsp` |

**Método de diagnóstico que funcionó:** lanzar con `-condebug` y leer `nzp/condebug.log`.
Las últimas líneas indican exactamente dónde murió (no buscar a ciegas en el código).

---

## 🥽 FASE 2 — Modo VR (OpenXR en Quest 3) 🔶 EN FASE FINAL

### Estado real (2026-10-06, verificado en casco)

| Hito | Estado | Evidencia (vr_log.txt) |
|---|---|---|
| Loader + instance + system | ✅ | `xrInitializeLoaderKHR OK`, runtime Meta detectado |
| Sesión GLES (ES3 auxiliar compartido) | ✅ | `xrCreateSession` OK (fix -50: `xrGetOpenGLESGraphicsRequirementsKHR` + config offscreen con PBUFFER_BIT) |
| Máquina de estados hasta FOCUSED | ✅ | `Estado de sesion: 1→2→3→4→5` (fix clave: `xrWaitFrame` SIEMPRE con sesión iniciada — Meta solo emite SYNCHRONIZED tras el primer Wait; + `fwi.type` obligatorio) |
| Swapchains estéreos | ✅ | `Swapchain ojo 0/1: 3 imagenes` GL_RGBA8 1680x1760 · ⚠️ deben crearse POST-begin (ver crash abajo) |
| FBOs por imagen | ✅ | `FBOs ojo 0/1 listos (3)` |
| Head tracking (poses vivas) | ✅ | `head pos=(…) yaw=… pitch=…` cambia al girar la cabeza |
| Acciones mandos (XrActions) | ✅ | `Acciones de mandos listas` (fix: `localizedActionName` no vacío + `xrAttachSessionActionSets` explícito) |
| Render estéreo visible | 🔶 **EN VALIDACIÓN** | Crash SIGSEGV en `xrEndFrame` con capas → **causa raíz: swapchains creados ANTES de `xrBeginSession`** (compositor Meta no registra sus imágenes → deref NULL+0x23/0x24 dentro de libvrapiimpl; con 0 capas funciona). Fix post-begin desplegado commit `0406088`, APK instalada |
| Controles responden en juego | ⬜ | Pendiente de probar tras imagen estéreo (bindings thumbstick/trigger/grip/haptics ya creados) |
| Menú VR + pulido | ⬜ | Paso 6 del plan |

### Diagnóstico del crash `xrEndFrame` (resumen para el historial)
- Firma: SIGSEGV determinista `fault addr 0x23` (capa proyección, 2 vistas) / `0x24` (capa quad, 1 vista) en `libvrapiimpl.so+0x7d4568` ← `xrEndFrame+80`. Con `layerCount=0` no peta.
- El volcado `LAYERDUMP` de la capa mostraba handles/rects/fov/poses **válidos** → descartado mal formato de capa, descartado mismatch de structs (loader y app: mismo header 1.1.63), descartado release prematuro de imagen (ya se libera tras `xrEndFrame`), descartado contexto de binding (probado con ES3 current + `glFinish`).
- Conclusión (coherente con hello_xr y con la spec — `xrCreateSwapchain` no exige running, pero el runtime de Meta sí necesita registrar las imágenes al presentar): **crear los swapchains con la sesión RUNNING**.

### 2.4 `injector.cpp` (DESCARTADO)
Es un **esqueleto** para la Opción B (prototipo PC). No aplica: en Android/Quest no se pueden inyectar DLLs. La integración real es `vril-engine/source/platform/android/vr/vr_openxr.c` (Opción A ✅ en marcha).

### 2.5 Entregable de la fase
- [x] Motor con ciclo OpenXR in-process en Android (`vr_openxr.c`, Opción A)
- [x] Sesión hasta FOCUSED + swapchains + FBOs + head tracking + acciones (verificados en casco)
- [ ] Imagen estéreo visible en ambos ojos (fix post-begin desplegado, en validación)
- [ ] Controles VR mapeados y respondiendo en juego
- [ ] (Opcional) XR_EXT_hp_mixed_reality_controller para poses de mandos + haptics en gameplay

---

## 🔧 Infraestructura ya montada en esta máquina

| Elemento | Estado | Ruta / Comando |
|---|---|---|
| Toolchain C (gcc/make) | ✅ | MSYS2: `C:\msys64\usr\bin\bash.exe -lc "export PATH=/ucrt64/bin:/usr/bin:$PATH && …"` |
| SDL2 + SDL2_mixer | ✅ instalado en MSYS2 UCRT64 | `pacman -S mingw-w64-ucrt-x86_64-SDL2 -SDL2_mixer` |
| Build PC del motor | ✅ | `BUILD.bat` → `vril-engine/build/sdl/nzportable.exe` (3.557.000 bytes) |
| Datos jugables | ✅ | `game\nzp\` — 1152 archivos / 105 MB (origen: release `nightly`) |
| Lanzador | ✅ | `PLAY.bat` (añade `C:\msys64\ucrt64\bin` al PATH) |
| Índice graphify | ✅ | `%USERPROFILE%\.local\bin\graphify.exe` — 36.800 nodos (motor + SDL + OpenXR-SDK + quakevr) |
| uv (gestor Python) | ✅ | winget `astral-sh.uv` |
| Android NDK | ✅ | `28.2.13676358` (SDK en `%LOCALAPPDATA%\Android\Sdk`, platform-tools con adb) |
| SDL2 + SDL2_mixer arm64 | ✅ | `SDL-release-2.32.10` + `SDL_mixer-release-2.8.2` compilados a `.a`/`.so` arm64-v8a |
| OpenXR SDK | ✅ | `third_party\OpenXR-SDK` 1.1.63 — loader prebuilt `build-android\src\loader\libopenxr_loader.so` |
| APK debug | ✅ | `android-app\app\build\outputs\apk\debug\app-debug.apk` (BUILD_APK.bat / scripts\build_apk.ps1) |
| Quest 3 conectado | ✅ | serial `2G0YC5ZG7C00RJ`, package `com.nzpteam.nzportable` |

### Cómo usar el índice para ahorrar tiempo/tokens
```powershell
graphify explain "gl_vidsdl"      # ver cómo encaja el render
graphify query "how is the frame loop structured?"
graphify path "Host_Frame" "gl_swap"
```
El grafo está unificado en `graphify-out/` (raíz del workspace, cubre motor + QC + scripts; 
24088 nodos, 68568 aristas, 768 comunidades).

---

## 📅 Orden de trabajo sugerido (para AI-driver)

| Orden | Tarea | Dificultad | Estado |
|---|---|---|---|
| 1 | Reunir datos jugables y **ver el juego en PC** | ⭐ Fácil | ✅ **HECHO** |
| 2 | Instalar Android Studio (SDK+NDK+JDK) | ⭐ Fácil | ✅ **HECHO** (NDK 28.2 per-user, sin admin) |
| 3 | Compilar SDL2 para Android | ⭐⭐ Media | ✅ **HECHO** (2.32.10 estático+compartido, mixer 2.8.2) |
| 4 | Crear `source/platform/android/` + build ndk-build | ⭐⭐⭐ Media-alta | ✅ **HECHO** (`libmain.so` 1,06 MB) |
| 5 | Arrancar motor en dispositivo | ⭐⭐⭐ | ✅ **HECHO** (móvil MIUI + Quest 3) |
| 6 | Empaquetar APK con assets | ⭐⭐⭐ | ✅ **HECHO** (105 MB en assets, extracción primer arranque) |
| 7 | ✅→🔶 ~~Integrar OpenXR~~ → OpenXR en Quest standalone | ⭐⭐⭐⭐⭐ | 🔶 **90%**: sesión/acciones/poses/FBOs verificados; fix crash xrEndFrame desplegado, validar estéreo con casco |
| 8 | Controles responden + pulido (menú VR, recenter, haptics) | ⭐⭐⭐ | ⬜ tras validación visual |

---

## ✅ Próximo paso inmediato

**Validar el fix de swapchains post-begin con el casco puesto** (APK ya instalada, commit `0406088`):
```powershell
# Despertar visor + lanzar en modo VR:
adb shell input keyevent 224
adb shell "am start -a android.intent.action.MAIN -c com.oculus.intent.category.VR -n com.nzpteam.nzportable/org.libsdl.app.SDLActivity"
# A los ~30 s, leer log y comprobar que el proceso sigue vivo:
adb shell "ps -A | grep nzportable"
adb shell run-as com.nzpteam.nzportable grep -e 'post-begin' -e FBO -e LAYERDUMP -e fallo -e HB files/vr_log.txt
```
Éxito = `Intento de swapchains #N (estado=3/4/5, post-begin)` → `FBOs ojo listos` → `LAYERDUMP n=2` → **HB frames subiendo sin crash** → mundo en estéreo.

Si el crash persistiera en el mismo offset: siguiente hipótesis = registrar las imágenes vía `XR_KHR_swapchain_usage_input_attachment_bit`/formato sRGB, o fallback a `XR_EXT_win32_appcontainer_compatible`-style debugging con validation layer del loader.

Después: mapear stick/trigger a comandos del juego (código ya escrito, falta prueba táctil), menú VR, y opcionalmente `XR_EXT_hp_mixed_reality_controller` para poses de mando.

---

*Documento basado en el análisis real del repositorio `nzp-team/vril-engine` (compilación verificada) e índice `graphify`.*

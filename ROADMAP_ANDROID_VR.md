# 🗺️ Hoja de Ruta: Nazi Zombies: Portable → Android + VR

**Fecha:** 2026-09-28
**Proyecto:** NZ:P Team — *Vril Engine* (fork mejorado del motor Quake)
**Estado actual:** ✅ Motor compilado y **JUGABLE en Windows** (`PLAY.bat`) · ✅ Índice de código generado · ✅ Datos jugables completos · ⏳ Port Android/VR pendiente

---

## 📌 Resumen ejecutivo (qué hemos averiguado, con datos reales)

| Pregunta | Respuesta verificada |
|---|---|
| ¿Existe ya un port Android? | **No.** Solo existe la *constante* `PLATFORM_AND` usada para mostrar cadenas de texto en el menú QuakeC. **No hay** un build nativo Android. |
| ¿Cuál es la mejor base para Android? | El backend **SDL2** (`source/platform/sdl/`). SDL2 tiene soporte oficial de Android, así que es el camino más directo. |
| ¿Cuál es la mejor base para VR? | El mismo backend SDL2 + **OpenXR** (capa/integración), porque el render ya es OpenGL. |
| ¿Se compila hoy en esta máquina? | **Sí.** `vril-engine/build/sdl/nzportable.exe` (3.557.000 bytes) se genera y **el juego es jugable**. |
| ¿Dónde están los datos jugables? | **⚠️ Hallazgo clave:** el repo `nzp-team/assets` contiene solo **fuentes** (`.map`, `.way`, texturas). Los **`.bsp` compilados** y **`progs.dat`** NO están ahí → hay que sacarlos del **release `nightly`** (`nzportable-win64.zip`). |
| ¿Cómo se lanza? | ✅ `PLAY.bat` (un clic). Compila con `BUILD.bat`. |

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

## 🅰️ FASE 1 — Port a Android

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
- [ ] `nzportable` compilado para `arm64-v8a`
- [ ] APK instalable que arranca hasta el menú (sin assets aún OK)
- [ ] Con assets: juego jugable con controles táctiles/gamepad

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

## 🥽 FASE 2 — Modo VR (OpenXR)

### Objetivo
Añadir soporte de visor VR al motor OpenGL existente.

### 2.1 Decisión de arquitectura: ¿"inyección" o integración nativa?

> ⚠️ **Aclaración importante:** la "inyección de DLL" es útil para *prototipar* en PC, pero **no es el camino recomendado** para un producto final ni para Android/Quest (donde el proceso es sandbox y no puedes inyectar DLLs por seguridad). El camino real y mantenible es **integrar OpenXR** en el render.

Dos opciones:

| Opción | Ventaja | Inconveniente | Recomendada para |
|---|---|---|---|
| **A. OpenXR in-process** (integrar en `gl_vidsdl.c`) | Nativo, funciona en Quest, mantenible | Hay que tocar el render (matrices, doble vista) | ✅ **Producción / Android** |
| **B. DLL inyectada** | Prueba rápida en PC sin tocar el motor | Frágil, no sirve en Android/Quest | Prototipo en PC |

### 2.2 SDK necesario
- **Khronos OpenXR SDK** (headers + loader) → https://github.khronos.org/OpenXR-SDK/
  - Clave: `openxr_loader`, `openxr.h`, `openxr_platform.h`
- **Validation Layers** (opcional, para depurar)
- Para Quest: **Meta XR SDK / OpenXR mobile** (viene con el NDK de Meta)

### 2.3 Qué hay que implementar en el motor (puntos de anclaje reales)
Según el código (`graphify explain`):

1. **Ventana/contexto** → `source/platform/sdl/gl/gl_vidsdl.c`
   - Cambiar `SDL_CreateWindow` por sesión OpenXR + swapchain.
   - `XrGraphicsBindingOpenGL` enlaza el contexto GL existente.
2. **Cámara y proyección** → `source/render/r_screen.c`, `source/view.c`
   - Renderizar **una vez por ojo** con matrices de proyección/vista del visor.
3. **Input** → `source/input.c`, `source/platform/sdl/in_sdl.c`
   - Ya existen `IN_GetAnalogStick` y soporte gyro/rumble (`PLATFORM_SUPPORTS_GYRO`, `PLATFORM_SUPPORTS_RUMBLE`) → mapear a acciones OpenXR.
4. **Bucle principal** → `source/host.c` (`Host_Init`, `Host_Frame`)
   - Insertar `xrWaitFrame` / `xrBeginFrame` / `xrEndFrame`.

### 2.4 `injector.cpp` (incluido en este repo)
Es un **esqueleto** para la Opción B (prototipo PC). Contiene el punto de entrada `DllMain` y `InitXR()` vacío. Sirve para experimentar rápido, **no** para el producto final.

### 2.5 Entregable de la fase
- [ ] Motor renderiza en estereoscópico en un visor PC (Opción A)
- [ ] Controles VR mapeados
- [ ] (Opcional Quest) APK con OpenXR mobile

---

## 🔧 Infraestructura ya montada en esta máquina

| Elemento | Estado | Ruta / Comando |
|---|---|---|
| Toolchain C (gcc/make) | ✅ | MSYS2: `C:\msys64\usr\bin\bash.exe -lc "export PATH=/ucrt64/bin:/usr/bin:$PATH && …"` |
| SDL2 + SDL2_mixer | ✅ instalado en MSYS2 UCRT64 | `pacman -S mingw-w64-ucrt-x86_64-SDL2 -SDL2_mixer` |
| Build PC del motor | ✅ | `BUILD.bat` → `vril-engine/build/sdl/nzportable.exe` (3.557.000 bytes) |
| Datos jugables | ✅ | `game\nzp\` — 1152 archivos / 105 MB (origen: release `nightly`) |
| Lanzador | ✅ | `PLAY.bat` (añade `C:\msys64\ucrt64\bin` al PATH) |
| Índice graphify | ✅ | `%USERPROFILE%\.local\bin\graphify.exe` |
| uv (gestor Python) | ✅ | winget `astral-sh.uv` |
| Android NDK | ❌ falta | ver 1.1 |
| OpenXR SDK | ❌ falta | ver 2.2 |

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
| 2 | Instalar Android Studio (SDK+NDK+JDK) | ⭐ Fácil | ⏳ siguiente |
| 3 | Compilar SDL2 para Android | ⭐⭐ Media | ⏳ |
| 4 | Crear `source/platform/android/` + `Makefile.android` | ⭐⭐⭐ Media-alta | ⏳ |
| 5 | Arrancar motor en emulador (sin assets) | ⭐⭐⭐ | ⏳ |
| 6 | Empaquetar APK con assets | ⭐⭐⭐ | ⏳ |
| 7 | Integrar OpenXR en PC (Opción A) | ⭐⭐⭐⭐ Alta | ⏳ |
| 8 | (Opcional) OpenXR en Quest | ⭐⭐⭐⭐⭐ Muy alta | ⏳ |

---

## ✅ Próximo paso inmediato

**Paso 1 — ✅ COMPLETADO.** El juego arranca en PC desde el build propio:
`vril-engine\build\sdl\nzportable.exe` + `game\nzp\` (1152 archivos), lanzado con `PLAY.bat`.
Proceso vivo y estable (≈150 MB RAM, sin cierres).

**Paso 2 — Android (siguiente).** Instalar el NDK y preparar `Makefile.android`:
1. Descargar **Android Studio** (incluye SDK, NDK, JDK) → https://developer.android.com/studio
2. Verificar: `%LOCALAPPDATA%\Android\Sdk\ndk\<versión>\toolchains\llvm\prebuilt\windows-x86_64\bin\aarch64-linux-android24-clang.exe`
3. Compilar SDL2 para `arm64-v8a` (fuente: https://github.com/libsdl-org/SDL/releases — `SDL2-2.*.zip`)
4. Crear `source/platform/android/` tomando `source/platform/sdl/` como plantilla
5. Usar `PLATFORM_DIRECTORY=android` y `PLATFORM_RENDERER=gles` en `Makefile.android`

---

*Documento basado en el análisis real del repositorio `nzp-team/vril-engine` (compilación verificada) e índice `graphify`.*

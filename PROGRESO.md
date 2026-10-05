# 📋 PROGRESO DEL PROYECTO — Nazi Zombies: Portable (Android + VR)

**Última actualización:** 2026-10-05 (v11: **PASOS 3-5 programados** — render estereo + head tracking + controles XrActions escritos; sesion OpenXR verificada en Quest 3 hasta `xrBeginSession` OK; root cause del -2 en swapchains corregido (config offscreen PBUFFER+ES3 para el pbuffer del contexto ES3 auxiliar); falta validacion final CON CASCO PUESTO porque el compositor pausa la app 2D al hacer begin de la sesion VR. Ver seccion "Pasos 3-5 en curso".)  
**Estado actual:** ✅ JUGANDO (v8 táctil validado) + 🥽 **FASE VR INICIADA**. El juego base (táctil, coop LAN, triggerbot, MOVE SPEED) está entregado y publicado como release v2.0.0 en GitHub. Ahora se empieza la integración VR **paso a paso** (ver plan en la sección "🥽 FASE VR" al final de este documento).
> **Workspace:** `c:\nazizombiesportable`
> **Usuario:** AI-driver (no programador) — el trabajo lo ejecuta el agente
> **Documento hermano:** `ROADMAP_ANDROID_VR.md` (la hoja de ruta técnica detallada)

## ✅ Estado actual verificado

- **Video:** GLES 1.1 fixed pipeline, 2441x1102 (sensorLandscape forzado en AndroidManifest). Menú y juego renderizan. Capturas: `screen_landscape.png` (menú completo visible).
- **Audio:** unificado por `SDL_audio.c` callback PostMix, 44100 Hz, sonidos 8-bit cargados OK.
- **Táctil (v2, `sys_sdl.c`):**
  - Stick virtual (izquierda, fx<0.5): movimiento **pixel-normalizado** contra radio del stick (fix strafe muerto: antes se escalaba en espacio fracción ×2.0 que ignoraba el aspect 1:2 → strafe max ~23%).
  - Look por arrastre (mitad derecha): `yaw -= dx*0.12`, `pitch += dy*0.12` (fix inversión: config `m_pitch "0"` hacia que el branch `m_pitch.value>0` eligiera signo -1). Clamp pitch -70..80.
  - 10 botones: FIRE/AIM/RLD/USE/JMP/SW/GR/KNIFE/MENU/CONS, cada uno con cmd down/up.
  - **Sprint stick (v8):** stick a tope adelante = `impulse 23`, bajar de 0.85 o soltar = `impulse 24` (edge-trigger con histéresis; `NZP_TouchUpdateSprint` en `sys_sdl.c`). Disparar/cuchillo desarma y re-arma el sprint al soltar.
  - **Modo edición:** `touch_editmode` en consola → arrastra botones a otra posición → suelta → guarda en cvar `touch_layout` (`"i:cx,cy;..."`, persistido en config.cfg). `touch_resetlayout` restaura.
  - Menú: tap mueve cursor + activa ítem (`Menu_MouseMove` + `Menu_ButtonPress`).
  - Loadscreen: tap en cualquier sitio = K_ENTER down+up (resuelve "press ENTER" sin teclado).
  - BACK físico: `SDLK_AC_BACK → K_ESCAPE` (abre/cierra menú opciones).
- **Bloqueo de validación automática:** MIUI niega `INJECT_EVENTS` a adb (`input tap` → SecurityException) y `sendevent` no puede escribir en `/dev/input/event6` (Permission denied, uid shell). **La validación táctil es manual por el usuario.**
- **Proceso de build verificado:**

```powershell
# 1. Motor nativo (ndk-build) + copia a jniLibs
powershell -ExecutionPolicy Bypass -File "c:\nazizombiesportable\scripts\build_engine_android.ps1"
# 2. APK (gradle) + instalación + lanzamiento
powershell -ExecutionPolicy Bypass -File "c:\nazizombiesportable\scripts\build_apk.ps1"
adb install -r "c:\nazizombiesportable\android-app\app\build\outputs\apk\debug\app-debug.apk"
powershell -ExecutionPolicy Bypass -File "c:\nazizombiesportable\scripts\run_android.ps1" -WaitSeconds 60
# 3. Verificación runtime (logcat → archivo)
Select-String -Path "c:\nazizombiesportable\android-debug-log.txt" -Pattern "nzportable-stdout|nzportable-stderr" | Select-Object -Last 60
# 4. Captura de pantalla
adb shell "cmd statusbar collapse"; adb exec-out screencap -p > screen_landscape.png
```

---

## 🔄 Cómo actualizar `PROGRESO.md` y hacer `graphify`

Cuando quieras dejar constancia del estado actual o refrescar el índice del proyecto, ejecuta esto:

```powershell
cd C:\nazizombiesportable
graphify . --ignore-file .graphifyignore --output-dir graphify-out
```

Y luego actualiza este documento con:

- Qué ha salido bien.
- Qué todavía falla.
- Qué comando se ha ejecutado para verificarlo.
- Qué bloqueador queda.

Importante:

- No indexar `game/` ni `nightly/`.
- No incluir `graphify-out/` dentro del propio índice.
- Hacer una pasada de `graphify` cada cierto tiempo para no perder trazabilidad del proyecto y ahorrar tokens de contexto.

---

---

## ⏸️ PUNTO DE PARADA (leer esto primero si retomas la sesión)

**Hoy se ha conseguido el hito grande: `libmain.so` (el motor completo) compila para Android
ARM64.** Ya no hay bloqueos de compilación pendientes. Lo próximo, en orden, es:

1. Terminar la **capa de datos** de Android (`sys_android_data.c` — extraer los 105 MB de los
   assets del APK al almacenamiento interno en el primer arranque). **A medio escribir.**
2. Crear el **proyecto Gradle** (`android-app/`) que empaqueta el `.so` + los datos en un APK.
3. `gradlew assembleDebug` → APK.
4. Probar en emulador o móvil y depurar con `adb logcat`.

**Comando para recompilar el motor ahora mismo** (funciona ya):
```powershell
cd C:\nazizombiesportable
powershell -ExecutionPolicy Bypass -File scripts\build_engine_android.ps1
```

---

## 🎯 Objetivo del proyecto

Conseguir que **Nazi Zombies: Portable** (motor *Vril Engine*, fork de Quake del equipo NZ:P) se pueda:

1. **Jugar en Android** (móvil/tablet) — descargable como APK.
2. **Jugar en VR** con visor (OpenXR).

Requisito del usuario: la vía **más fácil y directa** posible, guiando paso a paso, sin necesidad de ser programador.

---

## 📊 Panel de estado

| Fase | Estado | Notas |
|---|---|---|
| 🔍 Investigación de repositorios | ✅ **Hecho** | Confirmadas las limitaciones reales |
| 🛠️ Toolchain de compilación en Windows | ✅ **Hecho** | MSYS2 + gcc 16.2.0 + make |
| 🎮 **Compilar el motor en PC** | ✅ **Hecho** | Binario de 3.557.000 bytes funcionando |
| 📦 **Datos jugables completos** | ✅ **Hecho** | 1151 archivos / 105 MB |
| 🚀 **Juego jugable en PC** | ✅ **HECHO** | `PLAY.bat` — probado, ~150 MB RAM |
| 🗺️ Índice de código (graphify) | ✅ **Hecho** | 5775 nodos, 14852 aristas, 257 comunidades |
| 🧪 Análisis de dificultad del port GLES | ✅ **Hecho** | Ver "Hallazgo clave" — es viable |
| 📱 Herramientas Android (SDK+NDK) | ✅ **RESUELTO** | Android CLI — per-user, **sin admin**. NDK 28.2 + clang 19 ARM64 |
| 📱 Carpeta `source/platform/android/` | ✅ **Hecho** | Copia de `sdl/` + shim GLES |
| 📱 **Shader de compatibilidad GLES 1.1** | ✅ **HECHO** | `gl_gles.h` + `gl_gles.c` escritos a mano (18 funciones) |
| 📱 **Compilar SDL2 para `arm64-v8a`** | ✅ **Hecho** | Estático 10,90 MB + compartido **1,62 MB** |
| 📱 **Compilar SDL2_mixer 2.8.2** | ✅ **Hecho** | Estático, WAV + MP3 (minimp3) |
| 📱 **`Android.mk` + `Application.mk`** | ✅ **Hecho** | 3 módulos: SDL2 prebuilt, mixer, motor |
| 📱 **COMPILAR EL MOTOR (`libmain.so`)** | ✅ **HECHO** | **1,06 MB** — arranca el 1º compilado ARM64 |
| 📱 Capa de datos (assets → disco) | ⏳ **A MEDIAS** | `sys_android_data.c` escrito, sin probar en ejecución real |
| 📱 Proyecto Gradle + APK | ✅ **HECHO** | APK debug generada con Java 17 + SDK Android |
| 📱 Arrancar motor en emulador/móvil | ⏳ **SIGUIENTE PASO** | instalar y probar en un dispositivo/emulador |
| 🥽 Integrar OpenXR en PC | ❌ Pendiente | |
| 🥽 OpenXR en Quest standalone | ❌ Pendiente | |

**Leyenda:** ✅ hecho y verificado · ⏳ en curso/bloqueado · ❌ no empezado

---

## ✅ Lo que ya está hecho (verificado, con datos)

### 1. El juego funciona en PC desde código fuente propio

Esto es el hito más importante hasta ahora: **no usamos el ejecutable oficial**, sino uno
compilado por nosotros desde el código del motor.

| Concepto | Valor real medido |
|---|---|
| Binario | `vril-engine\build\sdl\nzportable.exe` |
| Tamaño | **3.557.000 bytes** |
| Datos | `game\nzp\` — **1151 archivos / 105 MB** |
| Prueba de vida | Proceso vivo, **~150 MB RAM**, sin cerrarse |
| Comparación | `game\nzp` == `nightly\nzp` → **0 diferencias** |

### 2. Descubrimiento crítico sobre los datos del juego

⚠️ **El repositorio `nzp-team/assets` NO contiene el juego jugable.**

Contiene solo **fuentes**: mapas en formato editable (`.map` de TrenchBroom), navegaciones
(`.way`) y texturas. Los archivos que el motor necesita **en tiempo de ejecución** solo
existen en el **release `nightly`**:

| Archivo | ¿En `assets`? | ¿En `nightly`? | Tamaño |
|---|---|---|---|
| `progs.dat` (bytecode del juego) | ❌ | ✅ | 747.826 bytes |
| `maps/*.bsp` (19 mapas **compilados**) | ❌ (solo `.map`) | ✅ | — |
| `maps/*.nsz` (10 navegaciones zombie) | ❌ | ✅ | — |
| `gfx/`, `models/`, `sounds/`, `textures/` | parcial | ✅ | — |

**Origen usado:**
```
https://github.com/nzp-team/nzportable/releases/download/nightly/nzportable-win64.zip
```

### 3. Los 3 fallos que tuvimos y cómo se resolvieron

El juego se cerraba con `exit=-1073741819` (error 0xC0000005). Fueron **tres causas en cadena**:

| # | Síntoma | Causa real | Solución aplicada |
|---|---|---|---|
| 1 | Cierre al arrancar | `nzp.rc` ejecutaba `startdemos fakedemo` y ese archivo **no existe** | Comentada esa línea en `game/nzp/nzp.rc` |
| 2 | Seguía cerrando | Faltaba **`progs.dat`** (fatal en `qcvm/pr_edict.c:1159`) | Copiado desde el release nightly |
| 3 | Seguía cerrando | Faltaban los **`.bsp` compilados** de los mapas | Copiados desde `nightly\nzp\maps\` |

> 💡 **Método de diagnóstico que funcionó** (y que sirve para el futuro):
> lanzar con el argumento `-condebug`. Escribe `game/nzp/condebug.log` línea a línea.
> Las **últimas líneas** de ese log indican exactamente dónde murió el proceso.
> Es muchísimo más rápido que buscar a ciegas en el código.

### 4. 🔑 Hallazgo clave para Android: el port es MUCHO más viable de lo esperado

Investigué si el motor puede usar **OpenGL ES** (el OpenGL de móviles). Resultado:

**El motor tiene 6 backends de plataforma**, y dos de ellos (`psp2` = PS Vita y `ctr` = Nintendo 3DS)
**ya renderizan con OpenGL ES** — y usan **código prácticamente idéntico** al de escritorio:

| Archivo del renderer | SDL (escritorio) vs PSP2 (PS Vita/GLES) |
|---|---|
| `gl_rsurf.c` | **IDÉNTICO** |
| `gl_mesh.c` | **IDÉNTICO** |
| `gl_draw.c` | difiere solo 45 bytes |
| `gl_warp.c` | difiere solo 34 bytes |
| `gl_hyena.c` | difiere solo 167 bytes |
| `gl_rmain.c` | 61 líneas diferentes (de 1783) |

**¿Por qué funciona?** Porque PSP2 usa **`vitaGL`**, una librería que *emula OpenGL de función
fija sobre OpenGL ES 2.0*. El motor no se toca: el shim traduce.

**Consecuencia para nosotros:** en Android queremos lo mismo. Opciones:
- **`gl4es`** — proyecto equivalente a vitaGL pero para Android (mapea GL 1.x → GLES).
- **GLES 1.1 nativo** — tiene matrices, `glTexEnv`, `glShadeModel` (todo eso **sí** existe en GLES 1.1).

**Números exactos del port (esto reduce el trabajo de días a horas):**

| Elemento a adaptar | Cantidad | Esfuerzo |
|---|---|---|
| `glBegin`/`glEnd` (no existen en GLES) | **solo 31 llamadas** | Medio (conversión a vertex arrays) |
| `gluPerspective` | **1 llamada** (`gl_rmain.c:1531`) | Trivial (10 líneas de `glFrustum`) |
| `glPolygonMode` (no existe en GLES) | **1 llamada** | Trivial (borrar) |
| Matrices, client-state, `glTexEnv`, `glShadeModel` | **~70 llamadas** | ✅ Ya funcionan en GLES 1.1 |
| `#include <GL/glu.h>` | 1 | Sustituir por `GLES/gl.h` |

### 5. Infraestructura montada en la máquina

| Elemento | Estado | Detalle |
|---|---|---|
| MSYS2 | ✅ | `C:\msys64` |
| gcc 16.2.0 / GNU Make 4.4.1 | ✅ | vía MSYS2 UCRT64 |
| SDL2 2.32.10 + SDL2_mixer 2.8.2 | ✅ | con `pkgconf` |
| graphify 0.9.70 | ✅ | `C:\Users\juani\.local\bin\graphify.exe` |
| uv 0.12.19 | ✅ | gestor de Python |
| Repos clonados | ✅ | `vril-engine`, `quakec` |
| **Android CLI 1.0.16406183** | ✅ | `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.AndroidCLI_...\android.exe` |
| **Android SDK** | ✅ | `C:\Users\juani\AppData\Local\Android\Sdk` |
| adb (platform-tools) | ✅ | v37.0.1 (ya venía instalado) |

### 5b. 📱 Cómo se desbloqueó Android (sin permisos de administrador)

**El problema:** el instalador de **Android Studio** (859 MB) se descargó correctamente y su
SHA256 coincidía con el oficial, pero al instalarlo se necesita aprobación de **administrador
(UAC)** — y eso **no se puede automatizar** con seguridad.

**La solución encontrada:** en 2026 Google publicó **Android CLI** (`android.exe`), una
herramienta de línea de comandos que **se instala por usuario, sin admin**, y que sabe
descargar el SDK completo. Es la vía moderna y la que vamos a usar.

| Paso | Comando | Resultado |
|---|---|---|
| 1. Instalar Android CLI | `winget install --id Google.AndroidCLI` | ✅ Instalado, crea el comando `android` |
| 2. Inicializar | `android init` | ✅ Instala skills para agentes IA |
| 3. Instalar NDK | `android sdk install "ndk/28.2.13676358"` | ✅ **NDK 28.2.13676358** (~1 GB) |
| 4. Instalar SDK | `android sdk install "platforms/android-36" "build-tools/36.1.0" "cmdline-tools/latest"` | ✅ Completado |
| 5. Probar compilador | `aarch64-linux-android21-clang --version` | ✅ **clang 19.0.1**, target `aarch64-unknown-linux-android21` |

**Ubicación del SDK:** `C:\Users\juani\AppData\Local\Android\Sdk` (carpeta del usuario, no de sistema).
**Compilador ARM64:** `…\Sdk\ndk\28.2.13676358\toolchains\llvm\prebuilt\windows-x86_64\bin\aarch64-linux-android21-clang.cmd`

**Paquetes instalados (verificado con `android sdk list`):**

| Paquete | Versión |
|---|---|
| `ndk/28.2.13676358` | 28.2.13676358 |
| `platforms/android-36` | 2.0.0 |
| `build-tools/36.1.0` | 36.1.0 |
| `cmdline-tools/latest` | 23.0.0 |

Plus: adb (`platform-tools`) v37.0.1 ya estaba instalado de antes.

> ⚠️ **Nota:** al instalar varios paquetes en un solo comando de `android sdk install`, solo se
> aplica el **primero** (los demás se ignoran). Hay que instalarlos **de uno en uno**. Ese fue
> el motivo de que el primer intento solo dejara el NDK.

> ✅ **Ventaja importante:** al estar en la carpeta del usuario, **no hace falta Android Studio
> ni permisos de administrador** para compilar. El NDK trae su propio compilador clang para
> ARM, con el que construiremos el motor. **Ya no es un bloqueo.**

> ⚠️ Si algún día se quiere el entorno gráfico (el IDE), sí habría que pulsar "Sí" en el UAC.

---

### 6. 📱 EL MOTOR YA COMPILA PARA ANDROID (`libmain.so`)

**Este es el hito central de esta sesión.** Todos los ficheros C del motor (72 del núcleo +
la plataforma `android` + el renderer GLQUAKE + el shim GLES) compilan y **enlazan** para
Android ARM64 sin errores.

| Artículo | Tamaño | Ruta |
|---|---|---|
| `libmain.so` (el motor) | **1,06 MB** | `source/platform/android/libs/arm64-v8a/libmain.so` |
| `libSDL2.so` (prebuilt) | **1,62 MB** | `source/platform/android/prebuilt/arm64-v8a/libSDL2.so` |

**Comprobar que está bien** (símbolos exportados, verificado):
```
$ llvm-nm -D --defined-only libmain.so | Select-String "SDL_main"
00000000000e13c4 T SDL_main          ← el punto de entrada que busca SDLActivity.java
$ llvm-nm -D --defined-only libmain.so | Select-String "glBegin|glEnd|glVertex3f"
00000000000e3a90 T glBegin           ← el shim GLES está enlazado
00000000000eb24c T MYgluPerspective
```

### 6b. 🔑 El problema de GLES y cómo se resolvió (shim escrito a mano)

**El problema:** el motor es *GLQUAKE* (Quake 1), que usa **modo inmediato**
(`glBegin`/`glVertex3f`/`glEnd`). **OpenGL ES 1.1 no tiene modo inmediato**, y además le
faltan cosas que el motor usa: `GL_QUADS`, `GL_POLYGON`, `GLdouble`, `glOrtho`, `gluPerspective`…

**Investigación previa (medida, no supuesta):** se probó a compilar un fichero de prueba con
el compilador del NDK y salieron **4 errores** (`glBegin`, `glTexCoord2f`, `glVertex3f`,
`glEnd` undeclared). Se buscó `GL_QUADS` en la cabecera `gl.h` del NDK: **no existe**.
Solo hay `GL_TRIANGLES` (0x0004), `GL_TRIANGLE_STRIP` (0x0005), `GL_TRIANGLE_FAN` (0x0006).

**Decisión:** en lugar de añadir una librería externa (`gl4es`, que son ~2 MB más y una
dependencia extra), se escribió **a mano un shim de ~200 líneas**. Solo hacían falta
**18 funciones exactas**, que se localizaron contando los puntos de llamada reales:

| Función que falta en GLES | Dónde se usa | Cómo se resuelve |
|---|---|---|
| `glBegin`/`glEnd`/`glVertex2f`/`glVertex3f`/`glVertex3fv` | 17 sitios | **Recolector (batcher)** → vertex arrays |
| `glTexCoord2f`/`glTexCoord2fv` | idem | Se guarda la UV pendiente |
| `glColor3f`/`glColor4fv` | idem | Se guarda el color pendiente |
| `glClearDepth`/`glDepthRange` | 2 | `#define` → `glClearDepthf`/`glDepthRangef` |
| `glFrustum`/`glOrtho` | 2 | `#define` → `glFrustumf`/`glOrthof` |
| `glFogi` | 1 | `#define` → `glFogx` (versión entera de GLES) |
| `glPolygonMode` | 1 | `#define` → nada (GLES siempre rellena) |
| `glDrawBuffer`/`glReadBuffer` | 6 | `#define` → nada (camino muerto en el motor) |
| `gluPerspective` | 1 | Reusa `MYgluPerspective` que **ya existía** en `gl_rmain.c` |
| `GL_QUADS` / `GL_POLYGON` | 17 sitios | Enums privados (0x0007/0x0009) → `GL_TRIANGLE_FAN` |

**Ficheros nuevos:**
- `source/platform/android/gl/gl_gles.h` — cabecera del shim (enums falsos, renames, declaraciones)
- `source/platform/android/gl/gl_gles.c` — **el recolector**: acumula vértices en un array
  intercalado (`xyz[3] + uv[2] + rgba[4]`) y los dibuja de golpe con
  `glVertexPointer` + `glTexCoordPointer` + `glColorPointer` + `glDrawArrays`.
- `source/platform/android/gl/gl_main.h` — parcheado: `#ifdef __ANDROID__ → #include "gl_gles.h"`

> 💡 **Por qué un `GL_QUADS` de 4 vértices se puede dibujar como `GL_TRIANGLE_FAN`:**
> un *fan* de 4 vértices recorre 0-1-2 y 0-2-3, que es **exactamente** el mismo
> cuadrilátero con el mismo sentido de giro. Y se comprobó en `gl_hyena.c` que el motor
> **nunca** le pasa `GL_QUADS` a una función real de GLES (solo a `glBegin`), así que los
> valores 0x0007+ están libres y no pueden confundir a GLES.

### 6c. Los 3 errores de compilación que aparecieron y su causa

Se compiló en **6 intentos**. Los fallos y su causa real (esto ahorra tiempo si vuelve a pasar):

| # | Error | Causa real | Solución |
|---|---|---|---|
| 1 | `LOCAL_SRC_FILES points to a missing file` | El NDK **no tiene** la librería `.so` de SDL2 todavía | Copiar `libSDL2.so` a `prebuilt/arm64-v8a/` |
| 2 | `No rule to make target 'jni/jni/../../../../source/chase.c'` | `$(call my-dir)` devuelve una ruta **relativa** (`jni`), y ndk-build la vuelve a prefijar → `jni/jni/...` | `LOCAL_PATH := $(abspath $(call my-dir))` |
| 3 | `'SDL.h' file not found` | `NZP_ROOT` subía **un nivel de más** (`..\..` en vez de `..`) → apuntaba a `C:\` | `NZP_ROOT := $(VRIL_ROOT)/..` |
| 4 | `unknown type name 'GLdouble'` | GLES 1.1 **no tiene** doble precisión | `typedef float GLdouble;` en `gl_gles.h` |
| 5 | `undefined symbol: gethostid` (al enlazar) | **bionic (Android) no exporta `gethostid()`** | Usar la misma vía que Windows: `gethostname`+`gethostbyname` |

### 6d. Estructura de compilación Android creada

```
vril-engine\source\platform\android\
├── jni\
│   ├── Android.mk        ← 3 módulos: SDL2 (prebuilt), SDL2_mixer (estático), main
│   └── Application.mk    ← APP_ABI=arm64-v8a, APP_PLATFORM=android-21, defines del motor
├── prebuilt\arm64-v8a\libSDL2.so     ← 1,62 MB
├── libs\arm64-v8a\libmain.so         ← 1,06 MB  ✅ COMPILADO
├── libs\arm64-v8a\libSDL2.so         ← copia que va dentro del APK
├── gl\gl_gles.h / gl_gles.c          ← el shim GLES
└── sys_android_data.c                ← ⏳ extracción de assets (a medias)
```

**Script de compilación:** `scripts\build_engine_android.ps1` (+ log en `scripts\build_engine_android.log`)

**Incógnita resuelta — ¿cómo entra el juego en Android?**
`SDL_main.h` de SDL2 define, cuando compila para Android, `#define main SDL_main` y además
`SDLActivity.java` busca ese símbolo **dentro de `libmain.so`** (`getLibraries()` devuelve
`{"SDL2","main"}`). Por eso **no hace falta** el módulo `SDL2_main` ni ninguna clase Java extra:
el motor se enlaza como `libmain.so` y SDL lo llama.

### 7. ¿Por qué los datos del juego no pueden ir "tal cual" en el APK?

Detalle importante para la parte que queda. En Android:
- El **directorio de trabajo del proceso es `/`** y es de **solo lectura** → el motor no puede
  abrir `nzp/progs.dat` con `fopen()` porque esa ruta no existe.
- Los ficheros dentro del APK **no son ficheros de verdad** (viven en un ZIP), así que
  `fopen()` / `stat()` / `readdir()` **no los ven**. Solo se pueden leer por la API `AAssetManager`
  o vía `SDL_RWops`.
- Y `progs.dat` **necesita un descriptor de fichero real** (`Sys_FileOpenRead`), así que **no
  vale** leerlo como asset.

**Solución diseñada (dos niveles):**
1. **Almacenamiento externo efímero** (`/sdcard/Android/data/<pkg>/files/nzp/progs.dat`):
   si el usuario ya ha volcado los datos ahí (o un instalador posterior), se usa tal cual.
   Permite **mods y actualizar datos sin recompilar el APK**.
2. **Extracción desde los assets del APK**: la primera vez se copia
   `assets/base/nzp/**` → `<interno>/nzpdata/nzp/**` (~105 MB, unos 10-20 s) y se deja un
   marcador `.complete` para no repetirlo.

Esto implica que el **APK pesará ~110 MB** (no 5 MB), a cambio de ser **autocontenido**.
Implementado en `source/platform/android/sys_android_data.c` (⏳ pendiente de probar).

**Y un detalle crítico ya resuelto:** `source/system.c` incluye `_build_info.h`, que el
`Makefile.sdl` genera al vuelo. Para Android se ha creado `vril-engine\build\android\_build_info.h`
y el script lo regenera en cada compilación (con la fecha actual).

---

## 🚧 Lo que queda por hacer

### A) Compilar para Android (EN CURSO — el motor ya compila ✅)

✅ **Las herramientas están resueltas** (ver 5b) y ✅ **el motor ya compila** (ver 6).
Lo que queda, en orden:

| Paso | Estado | Detalle |
|---|---|---|
| 1. Compilar SDL2 `arm64-v8a` | ✅ **Hecho** | Estático 10,90 MB + compartido 1,62 MB |
| 2. Crear `source/platform/android/` | ✅ **Hecho** | 35 archivos + shim GLES |
| 3. `Android.mk` + `Application.mk` | ✅ **Hecho** | 3 módulos |
| 4. Adaptar GLES (las 18 funciones) | ✅ **Hecho** | `gl_gles.h` / `gl_gles.c` |
| 5. **Compilar el motor** | ✅ **HECHO** | `libmain.so` 1,06 MB |
| 6. **Capa de datos** (`sys_android_data.c`) | ⏳ **A medias** | Escrito, sin compilar ni probar |
| 7. Parchear `main()` para inyectar `-basedir` | ⏳ **A medias** | Escrito en `sys_sdl.c` |
| 8. **Proyecto Gradle + APK** | ❌ Pendiente | JDK 21 ✅ ya instalado |
| 9. Probar en emulador o móvil | ❌ Pendiente | `adb logcat` para depurar |

### A2) Lo que hay que hacer al retomar (paso a paso)

**Paso 6-7: terminar la capa de datos**
- `sys_android_data.c` necesita: que el build genere el índice `assets/base/nzp/.filelist`
  (lista de los 1151 ficheros) para poder recorrerlos con el `AAssetManager`.
- `ndk-build` no incluye el fichero nuevo automáticamente por el `$(wildcard)`: ya lo recoge
  (`$(PLATFORM_DIR)/*.c`), así que se compilará solo.
- Recompilar con el script y corregir los errores que salgan.

**Paso 8: proyecto Gradle** (carpeta nueva `android-app/`)

| Configuración | Valor |
|---|---|
| `namespace` / package | `com.nzpteam.nzportable` |
| `compileSdkVersion` / `targetSdkVersion` | 34 |
| `minSdkVersion` | 21 |
| `abiFilters` | **solo `arm64-v8a`** |
| `externalNativeBuild.ndkBuild.path` | `../vril-engine/source/platform/android/jni/Android.mk` |
| `sourceSets.main.jniLibs.srcDir` | `../vril-engine/source/platform/android/libs` |
| `assets` | `assets/base/nzp/**` ← los 105 MB de datos, más `.filelist` |
| `ndkVersion` | `28.2.13676358` |
| JDK | `C:\Users\juani\jdk21\jdk-21.0.12.1+1` |

Plantilla de partida (ya descargada con SDL2): `sdl2-src\SDL-release-2.32.10\android-project\`
(contiene `SDLActivity.java`, `AndroidManifest.xml`, `gradlew`, el wrapper de Gradle 8.1.1, etc.).

Comando previsto:
```powershell
$env:JAVA_HOME = "C:\Users\juani\jdk21\jdk-21.0.12.1+1"
cd android-app
.\gradlew.bat assembleDebug --console=plain
```

**Paso 9: probar**
```powershell
adb devices                                    # ¿hay móvil o emulador conectado?
adb install -r android-app\app\build\outputs\apk\debug\app-debug.apk
adb logcat -s SDL:V nzportable:V AndroidRuntime:E
```
Si no hay móvil: instalar `emulator` + una imagen de sistema con `android sdk install`
(**¡de uno en uno!**, ver el aviso de la sección 5b).

### B) OpenXR / VR

- `injector.cpp` (ya creado) es **solo un prototipo para PC**. La inyección de DLL **no funciona
  en Android/Quest** (el sistema lo bloquea por seguridad).
- El camino real: integrar OpenXR **dentro** del motor, en `gl_vidsdl.c` (ventana→sesión XR),
  `r_screen.c`/`view.c` (render por ojo) y `host.c` (bucle `xrWaitFrame`/`xrEndFrame`).
- SDK necesario: **Khronos OpenXR SDK** (+ Meta XR SDK si el objetivo es Quest).

### C) Mejoras opcionales

- Recompilar los mapas desde las fuentes `.map` de `assets` con `q3map2` (no necesario para jugar).
- Recompilar el QuakeC (`quakec/progs/*.src`) con `fteqcc` (actualmente usamos el `progs.dat` del nightly).
- Sustituir el shim GLES por **`gl4es`** *solo si* aparecen artefactos visuales que no se
  puedan arreglar a mano (el shim actual es más ligero: 0 dependencias extra).

---

## ▶️ Comandos de uso diario

### Jugar
```bat
PLAY.bat
```
Equivalente manual:
```powershell
$env:PATH = "C:\msys64\ucrt64\bin;$env:PATH"
.\vril-engine\build\sdl\nzportable.exe -basedir game
```

### Recompilar el motor (tras cambiar código C)
```bat
BUILD.bat
```
Equivalente manual:
```powershell
C:\msys64\usr\bin\bash.exe -lc "export PATH=/ucrt64/bin:/usr/bin:$PATH && cd /c/nazizombiesportable/vril-engine && make -f Makefile.sdl"
```

### Depurar un cierre inesperado
```powershell
$env:PATH = "C:\msys64\ucrt64\bin;$env:PATH"
.\vril-engine\build\sdl\nzportable.exe -basedir game -condebug
# luego:
Get-Content .\game\nzp\condebug.log | Select-Object -Last 20
```

### 📱 Recompilar el motor para ANDROID (funciona ya)
```powershell
cd C:\nazizombiesportable
powershell -ExecutionPolicy Bypass -File scripts\build_engine_android.ps1
```
Genera `vril-engine\source\platform\android\libs\arm64-v8a\libmain.so`.
Log completo en `scripts\build_engine_android.log`.

### 📱 Comprobar los símbolos del motor Android
```powershell
$nm = "C:\Users\juani\AppData\Local\Android\Sdk\ndk\28.2.13676358\toolchains\llvm\prebuilt\windows-x86_64\bin\llvm-nm.exe"
& $nm -D --defined-only vril-engine\source\platform\android\libs\arm64-v8a\libmain.so |
    Select-String "SDL_main|glBegin|MYgluPerspective"
```

### 📱 Ver qué dispositivos Android hay conectados
```powershell
& "C:\Users\juani\AppData\Local\Android\Sdk\platform-tools\adb.exe" devices
```

### 📱 Instalar SDK de Android adicional (¡de UNO en UNO!)
```powershell
$android = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Google.AndroidCLI_Microsoft.Winget.Source_8wekyb3d8bbwe\android.exe"
& $android sdk install emulator
& $android sdk install "system-images;android-36;google_apis;arm64-v8a"
```

---

## 🧠 Cómo usar graphify (índice de código)

graphify convierte el proyecto en un **grafo de conocimiento consultable**. Sirve para que el
agente (y tú) encontréis cosas **sin leer archivos enteros** → **ahorra muchísimos tokens**.

### Índice actual
| Dato | Valor |
|---|---|
| Ubicación | `c:\nazizombiesportable\graphify-out\` |
| Nodos | **5775** |
| Aristas | **14852** |
| Comunidades | **257** |
| CLI | `C:\Users\juani\.local\bin\graphify.exe` |
| Fichero de exclusión | `.graphifyignore` (excluye `game/`, `nightly/`) |

### Comandos útiles

```powershell
# Preguntar cualquier cosa sobre el código (lo más usado)
graphify query "cómo se inicializa el vídeo"
graphify query "dónde se carga progs.dat"

# Explicar un símbolo concreto en lenguaje llano
graphify explain "Host_Init"
graphify explain "GL_Init"

# Ver qué símbolos dependen de otro (impacto de un cambio)
graphify affected "GL_Init" --depth 3

# Ruta más corta entre dos conceptos
graphify path "Host_Init" "SDL_GL_SwapWindow"

# Los "nodos dios" (los más conectados = piezas centrales del motor)
graphify god-nodes --top 15
```

**Nodos centrales del motor (para orientarse rápido):**
1. `Con_Printf()` — 374 conexiones (el sistema de consola)
2. `Hunk_AllocName()` — 172 (gestor de memoria)
3. `Cmd_Argv()` — 94 (sistema de comandos)
4. `Cmd_Argc()` — 78
5. `Cvar_RegisterVariable()` — 57 (variables de configuración)

### 🔄 Actualizar el índice (hacer de vez en cuando)

**Cuándo:** después de escribir código nuevo, o si las consultas devuelven cosas raras.

```powershell
# Opción rápida: re-extrae SOLO los archivos que cambiaron (no necesita API key)
cd C:\nazizombiesportable
graphify update .

# Opción completa: reconstruir desde cero (tarda ~1 min, 16 workers)
graphify extract . --code-only --out "C:\nazizombiesportable\graphify-out"

# Regenerar el informe y las comunidades tras actualizar
graphify cluster-only . --no-label
```

> ⚠️ **Ojo con el flag `--out`:** al usar `graphify extract . --out <ruta>`, graphify añade
> `graphify-out/` a la ruta indicada. Si se pasa `--out "C:\...\graphify-out"` se crea
> `graphify-out\graphify-out\`. **Solución:** ejecutar `graphify extract .` desde la raíz y
> **sin** `--out` (por defecto ya escribe en `./graphify-out/`), o mover los archivos después.

### Ficheros que genera
| Archivo | Qué es |
|---|---|
| `graphify-out/graph.json` | El grafo (datos para consultas) |
| `graphify-out/GRAPH_REPORT.md` | Informe legible con comunidades y hallazgos |
| `graphify-out/graph.html` | Visualización interactiva (abrir en navegador) |
| `graphify-out/.graphify_root` | Recuerda la carpeta raíz indexada |

---

## 📝 Cómo mantener este PROGRESO.md

Cuando se avance en el trabajo, actualizar este documento así:

1. **Cambiar la fecha** de "Última actualización" arriba.
2. **Mover filas** en el *Panel de estado*: `❌` → `⏳` → `✅`.
3. **Actualizar el "PUNTO DE PARADA"** (al principio) con los 2-3 próximos pasos concretos,
   porque es lo primero que se lee al retomar la sesión.
4. **Añadir lo nuevo** a la sección *"Lo que ya está hecho"* con **datos medidos reales**
   (tamaños, recuentos, líneas exactas) — no descripciones vagas.
5. **Si aparece un fallo nuevo**, añadirlo a la tabla correspondiente (los 3 del PC, o los 5
   de la compilación Android de la sección 6c) con su **causa real** y su solución.
6. **Anotar bloqueos** del usuario (como el UAC de Android Studio) de forma explícita.
7. **Actualizar los números del índice** de graphify si se ha reconstruido.

**Regla de oro del proyecto:** este documento debe permitir que **cualquier sesión nueva**
entienda el estado completo sin releer todo el código. Datos concretos, no impresiones.

---

## 🗂️ Estructura del proyecto

```
c:\nazizombiesportable\
├── PROGRESO.md                  ← este documento (estado + cómo usar graphify)
├── ROADMAP_ANDROID_VR.md        ← hoja de ruta técnica detallada (fases, SDKs)
├── PLAY.bat                     ← ▶️ lanzar el juego (PC)
├── BUILD.bat                    ← 🔨 recompilar el motor (PC)
├── injector.cpp                 ← esqueleto DLL OpenXR (solo prototipo PC)
├── .graphifyignore              ← exclusiones del índice
├── scripts\                     ← 🛠️ scripts de compilación
│   ├── build_sdl_android.ps1    ←    compila SDL2 + SDL2_mixer para ARM64
│   ├── build_engine_android.ps1 ←    compila el motor → libmain.so
│   ├── build_engine_android.log ←    log de la última compilación
│   └── engine_sources.txt       ←    censo de los 72 ficheros .c del motor
├── game\nzp\                    ← 📦 DATOS JUGABLES (1151 archivos / 105 MB)
│   ├── progs.dat                ←    bytecode del juego (del release nightly)
│   ├── maps\*.bsp               ←    19 mapas compilados
│   ├── maps\*.nsz               ←    10 navegaciones zombie
│   └── gfx\ models\ sounds\ textures\ ...
├── nightly\                     ← release oficial de referencia (para comparar)
├── quakec\                      ← fuentes del juego (progs/*.src, requiere fteqcc)
├── sdl2-src\                    ← SDL2 2.32.10 + SDL2_mixer 2.8.2 (fuente + build ARM64)
│   ├── SDL-release-2.32.10\
│   │   ├── libs\arm64-v8a\libSDL2.so      ← 1,62 MB
│   │   ├── obj\local\arm64-v8a\libSDL2.a  ← 10,90 MB
│   │   └── android-project\     ← plantilla Gradle de partida para el APK
│   └── SDL_mixer-release-2.8.2\
├── third_party\                 ← 🥽 dependencias VR (clonadas 2026-10-02)
│   ├── OpenXR-SDK\              ← Khronos OpenXR (headers + loader + loader sources)
│   └── quakevr\                 ← mod VR de Quake (vittorioromeo) — REFERENCIA de arquitectura
├── graphify-out\                ← 🧠 índice de código (36.768 nodos / 103.867 edges / 1.014 comunidades, 2026-10-02)
└── vril-engine\                 ← el motor
    ├── Makefile.sdl             ← build de PC
    ├── Makefile.psp2 / .ctr / .nx / .psp / .nspire
    ├── build\sdl\nzportable.exe ← ✅ binario PC compilado y probado (3,557,000 bytes)
    ├── build\android\_build_info.h  ← fecha/hash generados para el build Android
    └── source\platform\
        ├── sdl\                 ← ★ BASE para Android (plantilla)
        ├── psp2\  ctr\          ← ★ ya usan OpenGL ES (referencia GLES)
        ├── nx\ psp\ nspire\     ← consolas
        └── android\             ← 📱 NUEVO — plataforma Android
            ├── jni\Android.mk        ← 3 módulos (SDL2, mixer, main)
            ├── jni\Application.mk    ← arm64-v8a, android-21, defines del motor
            ├── gl\gl_gles.h/.c       ← shim GLES 1.1 (modo inmediato → vertex arrays)
            ├── prebuilt\arm64-v8a\   ← libSDL2.so (entrada al enlazado)
            ├── libs\arm64-v8a\       ← ✅ libmain.so (1,06 MB) + libSDL2.so
            └── sys_android_data.c    ← ⏳ localiza/extrae los datos del juego
```

---

## 🥽 FASE VR (iniciada 2026-10-02)

### Qué se ha hecho hoy (v9)

1. **Clonado en `third_party\`:**
   - `third_party\OpenXR-SDK\` — headers + loader de Khronos (fuente: github.com/KhronosGroup/OpenXR-SDK).
   - `third_party\quakevr\` — mod VR de Quake de vittorioromeo (REFERENCIA, no se compila).
2. **Codegrafo actualizado** (`graphify . --update --code-only`, sin API key):
   - 36.768 nodos / 103.867 edges / 1.014 comunidades (antes: 5.775 nodos).
   - `graph.html` regenerado (vista agregada por comunidades, >5.000 nodos).
   - Nota: `--cluster-only` a secas falla si hay docs pendientes; usar `--code-only` en el update.
3. **Arquitectura de quakevr analizada** (hallazgos clave):
   - Es QuakeSpasm + **OpenVR (SteamVR)**, NO OpenXR → hay que traducir el enfoque, no copiar código.
   - Render: FBO por ojo + submit al compositor; el patrón es portable a OpenXR (xrWaitFrame → xrLocateViews → render 2 ojos → xrEndFrame).
   - Input: acciones (locomotion/fire/grab/haptics) + head tracking sumado a cl.viewangles; separa viewangles (mirar) de aimangles (apuntar).
   - Punto de enganche del render: `SCR_UpdateScreen()` desvía a `VR_UpdateScreenContent()` si `vr_enabled`.
   - OJO: quakevr modifica el protocolo de red y el QuakeC → nosotros NO lo haremos (mantener compat).
   - GLES 1.1 no tiene FBOs en core (necesita `GL_OES_framebuffer_object`) y no hay glBlitFramebuffer.

### Plan de integración VR paso a paso (Vril + OpenXR + Android)

| Paso | Qué | Estado |
|---|---|---|
| 0 | Clonar SDKs + indexar + analizar referencia | ✅ hecho (hoy) |
| 1 | Compilar el **loader OpenXR** para Android (arm64) y enlazarlo en Android.mk | ✅ hecho (libopenxr_loader.so 0,72 MB en jniLibs) |
| 2 | Módulo `vr_openxr.c` mínimo: instance/session/swapchains + xrWaitFrame/Begin/End (sin render aún, solo log de poses) | ✅ **HECHO Y VERIFICADO** (ver detalle abajo) |
| 3 | Render estéreo: FBO por ojo (GLES + OES_framebuffer_object) → xrEndFrame; mirror opcional | 🔶 código completo; swapchains pendientes de validar en casco |
| 4 | Head tracking → cl.viewangles (pitch/yaw del HMD sumado al look táctil) | 🔶 código completo (delta de pose en VR_BeginFrame → in_sdl.c) |
| 5 | Controles: XrActions (thumbsticks, trigger, grip, haptics) mapeados a los mismos bits que el táctil | 🔶 código completo (acciones + sticks + menú VR); requiere sesión activa |
| 6 | Menú VR (opciones vr_enabled, aim mode, recenter) + pulido | ⬜ |

### 🔶 Pasos 3-5 en curso (2026-10-05) — estado real en Quest 3

**Verificado en el dispositivo (vr_log.txt):**
- `xrCreateSession` GLES OK (fix -50: llamar `xrGetOpenGLESGraphicsRequirementsKHR` + contexto ES3 auxiliar en el binding).
- Máquina de estados respetada: IDLE(1) → READY(2) → `xrBeginSession` estereo OK ("Sesion INICIADA").
- Contexto ES3 auxiliar con **config offscreen propio** (id=9, PBUFFER+ES3) — el config de la ventana del juego (id=7) NO tiene PBUFFER_BIT, por eso antes fallaba `eglMakeCurrent` y los swapchains se creaban con GL 1.1 → `XR_ERROR_RUNTIME_FAILURE (-2)`.
- Resolución por ojo detectada: 1680x1760; formato GL_RGBA8 (0x8058) preferido.

**Hipótesis raíz del -2 (corregida, pendiente de validación final):**
1. El pbuffer ES3 se creaba con el config de ventana (sin PBUFFER_BIT) → `vr_es3_surf` inválido → ES3 nunca current → swapchains desde GL 1.1 → runtime los rechaza. Fix: `eglChooseConfig` offscreen dedicado (ya loguea "Config offscreen ES3: id=9 / Pbuffer ES3 16x16 OK").
2. Ahora `VR_CreateSwapchains` loguea `GL_VERSION`/`GL_RENDERER` bajo ES3, la lista completa de formats, limpia swapchains parciales en fallo, y reintenta con backoff (10 intentos, cada 60 frames).

**Bloqueador actual (no es código):** con el casco SIN poner, `xrBeginSession` hace que el compositor VR tome el display y la app 2D se PAUSA (SDL congela el hilo principal) → el bucle del motor no llega a ejecutar el intento de swapchains. Validar requiere casco puesto + mandos encendidos. Además, tras cada reinstalación aparece el diálogo de sistema `LaunchCheckControllerRequiredDialogActivity` que SOLO se acepta dentro del casco (uiautomator/keyevents adb no lo alcanzan — el diálogo vive en el compositor VR, `mCurrentFocus=null`).

**Heartbeat añadido:** cada 300 frames el motor loguea `HB frames=N estado=S running=R sc=B` en vr_log.txt — permite distinguir "loop congelado" de "swapchain falla".

**Cuando el usuario pueda (procedimiento de 1 paso):**
```powershell
# Con Quest 3 por USB, casco puesto y mandos en mano:
powershell -ExecutionPolicy Bypass -File c:\nazizombiesportable\scripts\run_android.ps1 -WaitSeconds 60
# Aceptar el dialogo de mandos DENTRO del casco (una vez).
# Luego leer el log:
adb shell run-as com.nzpteam.nzportable cat files/vr_log.txt
```
Éxito = `Intento de swapchains #1` → `Swapchain ojo 0/1: N imagenes` → `FBOs ojo listos` → HMD con imagen estereo.

**Decisiones de diseño (fijadas):**
- VR como capa opcional (`vr_enabled 0/1`): el juego normal sigue funcionando igual.
- Sin cambios en protocolo de red ni QuakeC (a diferencia de quakevr) → compat total con progs.dat actual.
- OpenXR con loader dinámico en Android (dlopen de libxr_loader / runtime del sistema) según patrón oficial de Khronos.
- GLES 1.1 se mantiene; el estéreo se hace con FBOs OES, no migrando a GLES3 (por ahora).

### ✅ Paso 2 completado (2026-10-02) — verificado en logcat

**Archivos nuevos/modificados:**
- `vril-engine/source/platform/android/vr/vr_openxr.c` + `.h` (nuevo, ~530 líneas): ciclo OpenXR completo (loader→instance→system→session→spaces→swapchains→WaitFrame/BeginFrame/EndFrame), fallo elegante si no hay runtime.
- `sys_sdl.c`: `VR_Init()` tras `Host_Init`, `VR_BeginFrame/VR_EndFrame` en el bucle principal, `VR_Shutdown()` al salir.
- `Android.mk`: fuentes `vr/*.c`, includes OpenXR, `-lEGL`, módulo prebuilt `openxr_loader`.
- `Application.mk`: `-DNZP_VR_OPENXR`.

**Verificación en el móvil (logcat, PID 32764):**
```
nzp-vr  : xrInitializeLoaderKHR OK
nzportable-stderr: Error [GENERAL | xrCreateInstance | OpenXR-Loader] : RuntimeManifestFile::FindManifestFiles - failed to determine active runtime file path
nzp-vr  : FALLO xrCreateInstance(&ici, &vr_instance) -> OTHER (-51)   ← XR_ERROR_RUNTIME_UNAVAILABLE
nzportable-stdout: ========Nazi Zombies Portable Initialized=========   ← el juego sigue OK
```
**Conclusión:** el loader funciona; el móvil (MIUI/MediaTek) no tiene runtime OpenXR → fallo elegante correcto, juego 2D intacto. El paso 2 solo se puede validar al 100% en un dispositivo CON runtime (Meta Quest 3 vía AppScene/Link USB).

**Lecciones del build (5 fallos corregidos):**
1. `XR_ERROR_PERMISSION_INSUFFICIENT` (no SYSTEM_PERMISSIONS) + `<jni.h>` necesario.
2. Definir `XR_USE_PLATFORM_ANDROID` + `XR_USE_GRAPHICS_API_OPENGL_ES` ANTES de los headers OpenXR; JNI en C es `(*env)->GetJavaVM(env,&vm)`.
3. `<jni.h>` y `<EGL/egl.h>` deben incluirse ANTES de `openxr_platform.h` (patrón hello_xr).
4. Enlace: `-lEGL` en LDLIBS + módulo PREBUILT_SHARED_LIBRARY para el loader.
5. El módulo prebuilt va DESPUÉS del `BUILD_SHARED_LIBRARY` de libmain (si no, ndk-build error "already defined").

### Bloqueadores / riesgos VR conocidos

- El móvil necesita un runtime OpenXR (Horizon OS lo trae; un móvil normal sin runtime no podrá VR → probar en el dispositivo y ver qué pasa con xrInitializeLoader).
- GLES 1.1 + FBO OES: comprobar extensiones del GPU (Adreno/Mali) antes del paso 3.
- Rendimiento: 2 ojos × 2441x1102 es inviable; se renderizará a resolución recomendada por el runtime (~1024-1600 px/ojo).

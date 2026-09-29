# Nazi Zombies: Portable — Android Port (zurdo)

Port **no oficial de la comunidad** de [Nazi Zombies: Portable](https://github.com/nzp-team/nzportable) a **Android (arm64)**, con controles táctiles adaptados para jugador zurdo, stick izquierdo completo, sprint y botón de cuchillo.

> 🥽 **Próximamente: soporte para Meta Quest 3** — el motor (Vril, fork de Quake) ya compila para arm64 y el roadmap de VR está en marcha.

---

## ⚠️ Aviso

Este proyecto es un **fork** del original [NZ:P (`nzportable`)](https://github.com/nzp-team/nzportable), respetando su licencia **GPL-2.0**. Todo el mérito del juego, assets y diseño original es de la comunidad NZ:P. Este repositorio solo añade:

- Build nativo para Android vía **ndk-build + Gradle** (libmain.so + SDL2 2.32.10 + SDL2_mixer 2.8.2)
- Controles táctiles rediseñados (10 botones configurables, posiciones para zurdos)
- Sprint con el stick (impulsos QC 23/24 con histéresis) y botón KNIFE
- Corrección de un crash de memoria en la pantalla GAME OVER (`game_build_date` corrompible)
- Pipeline de captura y simbolización de crashes (`scripts/watch_crash.ps1` + `scripts/sim_crash.ps1` con `ndk-stack`)

---

## 📱 Requisitos

- Android 5.0+ (arm64-v8a)
- ~90 MB de espacio

## 🚀 Instalación (usuarios)

1. Descarga `nzp-android-zurdo.V1.apk` desde la sección **Releases** de este repo
2. Activa "Instalar apps de origen desconocido" si te lo pide Android
3. Instala el APK y juega

## 🛠️ Compilación (desarrolladores)

### En Windows

```bat
BUILD_APK.bat
```

Esto compila el motor nativo con `ndk-build` y empaqueta el APK debug en:

```
android-app\app\build\outputs\apk\debug\app-debug.apk
```

### Requisitos

| Herramienta | Versión |
|---|---|
| Android SDK | Cualquiera reciente |
| Android NDK | 28.2.13676358 |
| JDK | 17 |
| SDL2 | 2.32.10 (incluido en `sdl2-src/`) |
| SDL2_mixer | 2.8.2 (incluido en `sdl2-src/`) |

## 🎮 Controles táctiles

| Botón | Acción |
|---|---|
| FIRE | Disparar |
| AIM | Apuntar |
| RLD | Recargar |
| USE | Usar/comprar |
| JMP | Saltar |
| SW | Cambiar arma |
| GR | Granada |
| KNIFE | Cuchillo (botón dedicado) |
| CONS | Consola |
| MENU | Menú |

Stick izquierdo completo con **sprint automático** al empujar hacia delante a fondo (con histéresis para evitar parpadeo).

---

## 🧠 Desarrollo asistido por IA

Este port ha sido desarrollado mediante **ingeniería de prompts y dirección de agentes de IA**, con el ser humano actuando como *prompt engineer* y *AI driver*, y la implementación realizada por agentes como **DeepSeek V4 Flash** y **GLM 5.3 Flash**.

## 📄 Licencia

- Motor y juego: **GPL-2.0** (heredado de NZ:P y Quake)
- Consulta `vril-engine/LICENSE` y `quakec/LICENSE`
- Assets originales: propiedad de la comunidad NZ:P

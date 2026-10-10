# Arquitectura VR de NZP

## Flujo principal

`VR_BeginFrame()` prepara el frame OpenXR.

`SCR_UpdateScreenVR()` dibuja uno o dos ojos:

- `VR_BeginEye(eye)` adquiere y vincula el framebuffer.
- `V_RenderView()` dibuja el mundo y el viewmodel.
- `GL_Set2D()` dibuja HUD, consola y menus.
- `VR_EndEye(eye)` libera el ojo.

`VR_EndFrame()` presenta la capa de proyeccion al runtime.

## Archivos propietarios del comportamiento

| Archivo | Responsabilidad |
|---|---|
| `vril-engine/source/platform/android/vr/vr_openxr.c` | Instance, sesion, acciones, poses, conversion de coordenadas, cvars VR y menu quad. |
| `vril-engine/source/platform/android/vr/vr_openxr.h` | API publica VR y cvars compartidos. |
| `vril-engine/source/render/r_screen.c` | Render por ojo, HUD 2D, menu VR y puntero. |
| `vril-engine/source/platform/android/gl/gl_rmain.c` | Proyeccion, culling VR, viewmodel y laser. |
| `vril-engine/source/view.c` | Posicion y orientacion del viewmodel en primera persona. |
| `vril-engine/source/cl_input.c` | Movimiento enviado al servidor y correccion de marco cabeza/arma. |
| `vril-engine/source/platform/android/in_sdl.c` | Stick de movimiento y giro horizontal. |
| `vril-engine/source/menu/menu_controls.c` | Pantalla de opciones y sliders VR. |
| `vril-engine/source/menu/menu.c` | Registro del estado `m_vr_options`. |

## Cvars de ajuste

- `vr_vm_scale`: tamano del modelo del arma.
- `vr_vm_gain`: amplificacion del desplazamiento del mando.
- `vr_camera_right`: offset lateral de la camara de primera persona.
- `vr_hand_right`, `vr_hand_up`, `vr_hand_forward`: offset local del arma.
- `vr_hand_yaw`, `vr_hand_pitch`: giro adicional del arma.
- `vr_hud_scale`: escala interna del HUD.
- `vr_laser_muzzle`: distancia del laser desde el origen del modelo hasta la boca.
- `vr_world_scale`: unidades Quake por metro.
- `vr_fov_mult`: debe permanecer en `1.0` salvo una prueba especifica.

Los sliders de pose y HUD viven en `Configuration -> Controls -> VR Options`.

## Regla de consistencia

Si se cambia la pose visual del arma, revisar siempre estas rutas juntas:

1. `VR_GetHandWorldMatrix()` para el modelo.
2. `VR_GetHandAim()` para disparo y angulos.
3. `CL_SendMove()` para el marco de movimiento.
4. Laser en `gl_rmain.c`.

Una correccion que solo modifica el modelo suele producir un arma visualmente alineada pero disparos o laser desfasados.

## Build fiable

El build Android tiene artefactos incrementales. Cuando se cambian fuentes de menu o VR y el APK parece viejo, borrar solo artefactos generados:

- `vril-engine/source/platform/android/obj/`
- `vril-engine/source/platform/android/libs/`
- opcionalmente `android-app/app/build/`

Luego ejecutar:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_engine_android.ps1
powershell -ExecutionPolicy Bypass -File scripts/build_apk.ps1
```

Antes de instalar, inspeccionar `libmain.so` dentro del APK y buscar un marcador de la funcionalidad cambiada. Los APK y `.so` estan ignorados por Git por su tamano.

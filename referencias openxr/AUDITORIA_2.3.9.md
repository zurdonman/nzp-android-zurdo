# Auditoria del APK de Quest 3 en la 2.3.9

Revision del codigo que ha producido `NZP-Android-2.3.9-quest3-arm64.apk`
(commit `3543522`) contra los documentos de esta carpeta.

Resultado: **sin hallazgos bloqueantes**. Lo marcado como PENDIENTE son mejoras
del roadmap (`GTA_SA_VR_PATRONES.md`), no defectos.

## OPENXR_QUEST3.md

### Perfil y acciones

| Comprobacion | Estado | Donde |
|---|---|---|
| Perfil `/interaction_profiles/oculus/touch_controller` | OK | `vr_openxr.c:1018` |
| Acciones boolean con `countSubactionPaths = 1` | OK | `vr_openxr.c:931,944,957,970,983,1006` |

### Orden del ciclo (los 10 pasos del doc)

| Paso | Estado | Donde |
|---|---|---|
| 1. Instance + SystemId | OK | `VR_Init` |
| 2. Binding grafico Android GLES | OK | `XrGraphicsBindingOpenGLESAndroidKHR` en `vr_openxr.c:511-523` |
| 3. Sesion y espacios | OK | espacios VIEW (cabeza) + LOCAL |
| 4. Action set y acciones una vez por instancia | OK | `VR_InitActions`, guard `vr_actions_built` |
| 5. Sugerir bindings Touch | OK | `xrSuggestInteractionProfileBindings` en `vr_openxr.c:1046` |
| 6. Attach una sola vez por sesion | OK | guard `if (!vr_actions_attached)` en `vr_openxr.c:890` |
| 7. `xrWaitFrame` -> `xrBeginFrame` -> `xrLocateViews` -> `xrSyncActions` | OK | `1779`, `1793`, `1831`, `1294` |
| 8. Adquirir y dibujar cada imagen | OK | `VR_BeginEye` en `vr_openxr.c:2676` |
| 9. Liberar imagenes ANTES de `xrEndFrame` | OK | `vr_openxr.c:2020` |
| 10. Capa presentada como array de punteros | OK | `fei.layers = layer_ptrs` en `vr_openxr.c:2038-2041` |

El punto 6 es el que causaba el "gatillo muerto tras dormir el casco"
(apk18/19/20): la sesion se reutiliza entre suenyo y vigilia, y re-adjuntar el
action set sobre una sesion que ya lo tenia devolvia `XR_ERROR_ACTIONSET_NOT_ATTACHED`
y dejaba botones y puntero muertos. Ahora el attach esta guardado y el motivo
esta documentado en el propio codigo.

### Cosas que rompen Quest 3

| Riesgo del doc | Estado | Detalle |
|---|---|---|
| Declarar ES2 cuando el binding necesita ES3 | **Corregido** | `AndroidManifest.xml:13` -> `glEsVersion 0x00030000 required=false`. Antes estaba en `0x00020000 required=true`, de ahi el `ImportTextureResourcesGLES: external memory object extensions are not found` -> swapchain -2 / capa -23. Solo hay un manifest en el proyecto (sin libreria SDL que lo pise), asi que la fusion lo respeta. |
| Swapchains con el contexto EGL equivocado | OK | contexto ES3 dedicado creado con `EGL_OPENGL_ES3_BIT` (`vr_openxr.c:491`) y `VR_RestoreGameContext` para devolverle al juego su contexto tras `xrEndFrame`. |
| Liberar la imagen despues de `xrEndFrame` | OK | se libera antes; el codigo documenta el estado READY que exige Meta. |
| Pasar `&layer` en `XrFrameEndInfo::layers` | OK | array `const XrCompositionLayerBaseHeader *layer_ptrs[1]`. |
| Re-adjuntar el action set al despertar | OK | ver punto 6. |
| FOV distinto al del compositor | OK | `proj_views[i].fov = vr_eye_view[eye].fov` (`vr_openxr.c:1901`) y `vr_fov_mult` a `1.0` (`vr_openxr.c:182`). |
| Confiar en logs antiguos | N/A en build | `validate_vr.ps1` y el propio log llevan marca de tiempo. |

### Poses y coordenadas

- Conversion centralizada en `VR_PoseToWorldQuake()` (`vr_openxr.c:2478`), usada
  por las tres rutas de mando (`2564`, `2601`, `2633`).
- El movimiento sigue a las **gafas**, no al arma: `CL_SendMove` gira
  forward/side por el angulo entre la mira del arma y la cabeza
  (`cl_input.c:677-680`). El delta de cabeza no se suma dos veces a
  `cl.viewangles`.

## NZP_VR_ARQUITECTURA.md

Flujo `VR_BeginFrame` -> `SCR_UpdateScreenVR` (`VR_BeginEye` / `V_RenderView` /
`GL_Set2D` / `VR_EndEye`) -> `VR_EndFrame`: presente y completo.

Los nueve ficheros de la tabla de responsabilidades estan en la rama y llegan
al APK de Quest: `vr_openxr.c/.h`, `r_screen.c`, `gl_rmain.c`, `view.c`,
`cl_input.c`, `in_sdl.c`, `menu_controls.c` y `menu.c`.

Regla de consistencia (modelo / disparo / movimiento / laser): las cuatro rutas
comparten `VR_PoseToWorldQuake`, y el laser de `gl_rmain.c` usa el mismo rayo
que el disparo con el offset de boca `vr_laser_muzzle`.

`vr_fov_mult` permanece en `1.0`.

## CHECKLIST_VR.md

- **Build**: el de CI parte siempre de un checkout limpio, asi que no aplica el
  problema de artefactos incrementales de `obj/` y `libs/`.
- **"No confiar en BUILD SUCCESSFUL"**: el paso *Comprobar que la variante lleva
  lo que tiene que llevar* se ha endurecido. Ya no basta con que
  `libopenxr_loader.so` este al lado del ejecutable; ahora exige que
  `libmain.so` contenga `xrCreateInstance` y el perfil
  `/interaction_profiles/oculus/touch_controller`. Si alguien rompe
  `-DNZP_VR_OPENXR`, el enlazador **no** falla (todo el codigo VR va bajo
  `#ifdef`) y el APK saldria sin VR pero en verde; estos marcadores lo delatan.
  La variante de telefono comprueba lo contrario: cero rastro de VR.
- **Instalacion y diagnostico**: sin cambios, sigue `scripts/validate_vr.ps1`.

## GTA_SA_VR_PATRONES.md -- estado de portabilidad

| Idea | Estado |
|---|---|
| 3. Snapshot de pose comun (modelo, laser, disparo, melee) | HECHO parcial: `VR_PoseToWorldQuake` compartido por las tres rutas. |
| 4. Opciones de confort | PARCIAL: giro suave con `vr_turn_speed`. Sin snap turn ni recentrado. |
| 5. Diagnosticos con timestamp y estado de acciones | HECHO: `VR_LOG` a `files/vr_log.txt`. |
| 1. Persistencia de offsets por arma | PENDIENTE: hoy son cvars globales. |
| 2. Mirilla con proximidad + alineacion del rayo | PENDIENTE. |

## Conclusion

El APK de Quest 3 de la 2.3.9 esta construido conforme a las referencias: ciclo
OpenXR correcto, binding ES3, capas como array de punteros, action set adjunto
una sola vez y FOV del compositor. Lo unico que quedaba mal era la declaracion
de ES2 en el manifest, corregido en esta version.

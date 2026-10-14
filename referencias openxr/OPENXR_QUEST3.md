# OpenXR en Quest 3

## Perfil de controladores

El perfil usado por Quest Touch es:

`/interaction_profiles/oculus/touch_controller`

Rutas importantes:

| Control fisico | Ruta OpenXR | Tipo |
|---|---|---|
| Stick izquierdo | `/user/hand/left/input/thumbstick` | `vector2f` |
| Stick derecho | `/user/hand/right/input/thumbstick` | `vector2f` |
| Click stick izquierdo | `/user/hand/left/input/thumbstick/click` | `boolean` |
| Click stick derecho | `/user/hand/right/input/thumbstick/click` | `boolean` |
| Gatillo izquierdo | `/user/hand/left/input/trigger/value` | `float` |
| Gatillo derecho | `/user/hand/right/input/trigger/value` | `float` |
| Grip izquierdo | `/user/hand/left/input/squeeze/value` | `float` |
| Grip derecho | `/user/hand/right/input/squeeze/value` | `float` |
| X/Y | `/user/hand/left/input/x/click`, `y/click` | `boolean` |
| A/B | `/user/hand/right/input/a/click`, `b/click` | `boolean` |
| Menu | `/user/hand/left/input/menu/click` | `boolean` |
| Pose de apuntado | `/user/hand/<left|right>/input/aim/pose` | `pose` |
| Vibracion | `/user/hand/<left|right>/output/haptic` | `vibration` |

Los botones booleanos deben crearse con `countSubactionPaths=1` y su ruta de mano correspondiente si despues se consultan con `subactionPath`.

## Orden del ciclo

1. Crear `XrInstance` y obtener `XrSystemId`.
2. Crear el binding grafico Android OpenGL ES.
3. Crear la sesion y los espacios.
4. Crear el action set y las acciones una sola vez por instancia.
5. Sugerir bindings para el perfil Touch.
6. Adjuntar el action set una sola vez por sesion.
7. En cada frame: `xrWaitFrame`, `xrBeginFrame`, localizar vistas y sincronizar acciones.
8. Adquirir y dibujar cada imagen de swapchain.
9. Liberar las imagenes antes de `xrEndFrame` cuando el runtime de Meta lo requiera.
10. Presentar una capa de proyeccion con punteros a capas, no un puntero a una estructura de capa.

## Poses y coordenadas

- OpenXR usa Y arriba; Quake/NZP usa Z arriba.
- La conversion local a Quake esta centralizada en `VR_PoseToWorldQuake()`.
- El mundo debe usar la pose de cada ojo en la camara, no sumar el delta de cabeza otra vez a `cl.viewangles`.
- El arma, la mira, el laser y el movimiento deben tomar la misma epoca de pose siempre que sea posible.
- El pitch positivo del motor Quake apunta hacia abajo. No invertirlo dos veces.

## Cosas que rompen Quest 3

- Declarar ES2 en el manifest cuando el binding grafico necesita ES3.
- Crear swapchains con el contexto EGL equivocado o con una configuracion distinta.
- Liberar una imagen de swapchain despues de `xrEndFrame` cuando el runtime exige que este libre antes.
- Pasar `&layer` en `XrFrameEndInfo::layers`; debe ser un array de punteros a capas.
- Re-adjuntar el action set cada vez que el visor despierta.
- Usar FOV diferente al del compositor: produce deformacion tipo goma.
- Confiar en logs antiguos despues de reinstalar: borrar `files/vr_log.txt` y comprobar su fecha.

## Fuentes oficiales

- OpenXR: https://www.khronos.org/openxr/
- Especificacion: https://registry.khronos.org/OpenXR/specs/1.0/html/xrspec.html
- OpenXR SDK: https://github.com/KhronosGroup/OpenXR-SDK
- Meta OpenXR: https://developer.oculus.com/documentation/native/android/mobile-openxr/

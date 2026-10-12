# Patrones observados en GTA SA VR Quest

Referencia local consultada:

`C:\gts sa vr\gta-sa-vr-quest\native\src`

Archivos relevantes:

- `PhysicalWeapon.cpp`
- `Calib.cpp`
- `ScopeAim.cpp`
- `Locomotion.cpp`
- `GestureMove.cpp`
- `VrFire.cpp`
- `VrCamera.cpp`
- `Weapon.cpp`

Este documento resume ideas de arquitectura. No copia codigo ni assets de esa referencia.

## Armas fisicas

`PhysicalWeapon` separa varios conceptos que conviene mantener separados en NZP:

- pose base del arma;
- pose visual final;
- mano principal;
- mano de apoyo;
- armas en funda o soltadas;
- velocidad de mano para lanzar o recoger;
- bloqueo de interacciones durante menus.

La leccion para NZP es no recalcular la calibracion sobre la pose ya transformada cada frame. Guardar una pose base evita que una rotacion de apoyo se acumule.

## Calibracion

`Calib` mantiene perfiles por arma y por conjunto de modelos. El usuario puede corregir offset y rotacion sin cambiar el codigo. NZP ya tiene cvars globales y sliders VR; el siguiente paso natural es persistirlos por arma si se necesita precision por modelo.

Separar campos:

- offset lateral, vertical y de profundidad;
- pitch, yaw y roll;
- offset de mira;
- offset de mano de apoyo;
- escala del modelo.

## Mirilla

`ScopeAim` no activa la vista de mira solo porque se pulse un boton. Comprueba una envolvente de proximidad a la cabeza y la alineacion entre:

- direccion de la cabeza;
- rayo real calibrado del cañon;
- posicion del mando respecto al ojo.

Para NZP, esta es la referencia correcta para implementar apuntado por mirilla: el disparo debe seguir usando el rayo del arma y el zoom solo debe entrar cuando ambos estan alineados.

## Disparo y laser

`VrFire` toma un rayo fisico del arma y lo comparte entre hitscan, proyectiles y efectos. La recomendacion para NZP es que el laser use exactamente el mismo origen y direccion que el disparo, con un offset configurable de boca (`vr_laser_muzzle`) solo para evitar que atraviese visualmente el modelo.

## Locomocion

`Locomotion` separa:

- modo de movimiento relativo a cabeza o cuerpo;
- giro suave o snap;
- sensibilidad;
- opciones de confort;
- bindings configurables L3/R3;
- persistencia de ajustes.

NZP mantiene ahora movimiento relativo a cabeza y giro suave horizontal. Los controles de Quest estan en OpenXR, mientras que las acciones del juego se inyectan como comandos Quake o impulsos QC.

## Movimiento gestual

`GestureMove` filtra velocidad de manos, ignora tracking perdido y usa histéresis para evitar activaciones por ruido. No conviene usar la posicion absoluta sin filtro para activar sprint, salto o nado.

## Que portar primero a NZP

1. Persistencia de offsets por arma.
2. Mirilla con proximidad + alineacion del rayo.
3. Un snapshot de pose comun para modelo, laser, disparo y melee.
4. Opciones de confort: snap turn, sensibilidad y recentrado.
5. Diagnosticos con timestamp y estado de acciones.

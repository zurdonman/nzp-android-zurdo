# PLANO DE LA PRISION

**Regla de trabajo:** primero este plano, luego el codigo que lo replica, y
despues se comprueba que cada linea del plano esta en el `.bsp`. Nada se
publica sin pasar el checklist del final.

---

## 1. Vista general (planta baja, Z 0..144)

Visto desde arriba, +Y es norte (arriba), +X es este (derecha).

```
      X-1500        X-1100              X0              X1100        X1500
Y+2300  +---------------------------------------------------------------+
        |                      GALERIA NORTE                            |
Y+1900  |          +===========O====O===========O====O=========+        |
        |          #                                           #        |
        |          #        PATIO  (al aire, Z 0..400)         #        |
Y+1400  |          #                                           #        |
        |          #                  [caja misteriosa]        #        |
Y+900   |          +======O=====O=====+=========O=====O=========+        |
        |          #                  |                         #        |
        |          #     COMEDOR      D      ENFERMERIA         #        |
Y+550   |          #   X-1100..0      |       X0..1100          #        |
        |          #                  D                         #        |
Y+200   |          +=========O========O=========O===============+        |
        |          #                                            #        |
        |          #    BLOQUE DE CELDAS (2 plantas, Z 0..288)  #        |
Y-350   |          #   [celda][celda]  PASILLO  [celda][celda]  #        |
        |          #                                            #        |
Y-900   |          +===========O=========O===========O==========+        |
        |          #                                            #        |
        |          #       RECEPCION / SPAWN  Y-1300..-900      #        |
Y-1300  |          +=====================O======================+        |
        |                      GALERIA SUR                               |
Y-1700  +---------------------------------------------------------------+
```

## 2. Planta alta (Z 144..288) — solo el bloque de celdas

```
      X-1500        X-1100              X0              X1100        X1500
Y+200   |           +----------------------------------------------+
        |           #  [celda][celda]  PASILLO  [celda][celda]     #
Y-350   |           #        (forjado con hueco sobre el pasillo)  #
Y-900   |           +----------------------------------------------+
```

La galeria **no** tiene planta alta: es un anillo de una sola altura (0..400)
que rodea el edificio. Va techada porque el juego no trae textura de cielo.

---

## 3. Leyenda

| Simbolo | Significado |
|---------|-------------|
| `#` | muro exterior del edificio, macizo de suelo a techo |
| `=` | muro exterior **con hueco de madera** (barricada) |
| `O` | `item_barricade` — hueco tapado con tablas, a ras de suelo |
| `D` | `func_door_nzp` — puerta comprable que abre una zona |
| `[ ]` | recinto interior o prop |

---

## 4. Recintos (cotas exactas)

| Recinto | X | Y | Z | Zona |
|---|---|---|---|---|
| Recepcion | -1100..1100 | -1300..-900 | 0..144 | z_recep |
| Celdas | -1100..1100 | -900..200 | 0..288 | z_cells |
| — pasillo central | -100..100 | -900..200 | 0..288 | |
| — celdas oeste | -1080..-100 | ver tabla | 0 / 144 | |
| — celdas este | 100..1080 | ver tabla | 0 / 144 | |
| Comedor | -1100..0 | 200..900 | 0..144 | z_mess |
| Enfermeria | 0..1100 | 200..900 | 0..144 | z_infirm |
| Patio | -1100..1100 | 900..1900 | 0..400 | z_yard |
| Galeria | -1500..1500 | -1700..2300 | 0..400 | (exterior) |

## 5. Las 8 celdas

| # | Lado | cy0 | cy1 | Hueco de paso (Y) |
|---|---|---|---|---|
| 0 | oeste | -860 | -620 | -740..-660 |
| 1 | oeste | -600 | -360 | -480..-400 |
| 2 | oeste | -340 | -100 | -220..-140 |
| 3 | oeste | -80 | 160 | 40..120 |
| 4 | este | -860 | -620 | -740..-660 |
| 5 | este | -600 | -360 | -480..-400 |
| 6 | este | -340 | -100 | -220..-140 |
| 7 | este | -80 | 160 | 40..120 |

---

## 6. Barricadas de madera (el cambio importante)

**Ya no son ventanas.** Eran huecos a 40..112 del suelo y los zombies no
trepaban. Ahora son **huecos de puerta a ras de suelo** (0..112) tapados con
tablas: el zombie rompe la madera y **entra andando**, sin trepar.

Cada barricada lleva: `item_barricade` en el hueco + `spawn_zombie` en la
galeria + `path_corner` para que llegue tranquilo hasta la madera.

| ID | Muro | Hueco | Posicion barricada | angle | Zona | Nace zombie en |
|----|------|-------|--------------------|-------|------|----------------|
| b_r1 | O recepcion (x=-1100) | y -1240..-1144 | (-1100, -1192, 8) | 90 | z_recep | (-1250, -1192) |
| b_r2 | E recepcion (x=1100) | y -1240..-1144 | (1100, -1192, 8) | 270 | z_recep | (1250, -1192) |
| b_r3 | S recepcion (y=-1300) | x -48..48 | (0, -1300, 8) | 0 | z_recep | (0, -1450) |
| b_c1 | O celdas (x=-1100) | y -800..-704 | (-1100, -752, 8) | 90 | z_cells | (-1250, -752) |
| b_c2 | E celdas (x=1100) | y -800..-704 | (1100, -752, 8) | 270 | z_cells | (1250, -752) |
| b_c3 | O celdas (x=-1100) | y -280..-184 | (-1100, -232, 8) | 90 | z_cells | (-1250, -232) |
| b_c4 | E celdas (x=1100) | y -280..-184 | (1100, -232, 8) | 270 | z_cells | (1250, -232) |
| b_m1 | O comedor (x=-1100) | y 400..496 | (-1100, 448, 8) | 90 | z_mess | (-1250, 448) |
| b_m2 | O comedor (x=-1100) | y 700..796 | (-1100, 748, 8) | 90 | z_mess | (-1250, 748) |
| b_m3 | N comedor (y=900) | x -820..-724 | (-772, 900, 8) | 180 | z_yard | (-772, 1060) |
| b_i1 | E enfermeria (x=1100) | y 400..496 | (1100, 448, 8) | 270 | z_infirm | (1250, 448) |
| b_i2 | E enfermeria (x=1100) | y 700..796 | (1100, 748, 8) | 270 | z_infirm | (1250, 748) |
| b_i3 | N enfermeria (y=900) | x 400..496 | (448, 900, 8) | 180 | z_yard | (448, 1060) |

**Regla de zonas:** el `spawn_zombie` de una barricada pertenece a la zona
del recinto al que da. Como las zonas solo se activan al abrir su puerta,
**los zombies solo salen en salas ya desbloqueadas**.

## 7. Puertas comprables

| ID | Coste | Abre | Posicion | wayTarget |
|----|-------|------|----------|-----------|
| d_cells | 750 | z_cells | (-120..120, -900) | w_recep |
| d_yard | 1000 | z_yard | (-120..120, 900) | w_cells |
| d_infirm | 500 | z_infirm | (0, 400..520) | w_mess |

## 8. Barrotes de las celdas

Los actuales son 4 cajas sueltas de 12x8 que no tapan nada: **falsos e
inservibles**. Se sustituyen por una reja de una pieza por celda, hecha con
`func_wall` y textura `m_metal_darkBlu`, que cubre todo el hueco de paso
de suelo a techo salvo una rendija por la que se ve el interior.

---

## 9. CHECKLIST DE VERIFICACION (obligatorio antes de publicar)

1. [ ] **Cerrado**: ningun rayo disparado desde el interior de cada recinto
       sale al exterior por sitio que no sea una barricada o una puerta.
2. [ ] **Hueco real**: en cada una de las 13 barricadas hay un agujero
       practicable de 96 de ancho x 112 de alto, y la madera esta centrada.
3. [ ] **Suelo continuo**: el suelo a Z=36 llega a todos los recintos y a
       la galeria, incluido detras de cada barricada (si no, el zombie cae).
4. [ ] **Orientacion**: el `angle` de cada barricada es perpendicular a su
       muro (muro en X -> 90/270; muro en Y -> 0/180), no al reves.
5. [ ] **Spawn por barricada**: cada `item_barricade` tiene su
       `spawn_zombie` y su `path_corner`, y el zombie nace en la galeria.
6. [ ] **Zonas**: ningun `spawn_zombie` pertenece a una zona cuya puerta
       no este comprada.
7. [ ] **Arbol de nodos**: las cotas del arbol cubren la geometria
       (+-2600 frente a modelo +-2500).
8. [ ] **Presupuesto**: < 2500 caras de mundo, < 30 luces, ratio
       nodos/caras entre 0,60 y 0,90.
9. [ ] **Texturas**: 0 inexistentes.

#!/usr/bin/env python3
"""
build_prison_map.py
===================
Genera "prison" (penal) para NZ:P Android.

A diferencia del anterior Tranzit, este mapa sigue las convenciones de los
mapas del juego que SI rinden (4all, hangar, ndu, town):

  - El mundo es solo el cascaron: paredes, suelos y techos. Sin hornar
    detalles en world brushes.
  - Los props van como `place_model` / `misc_model` con .mdl, que es como
    lo hacen los mapas de referencia (70-156 modelos frente a los 27 de
    Tranzit).
  - Luces contadas (~30, no 286).
  - Los zombies NO salen al azar: se reparten en `spawn_zone` con
    zone_name / zone_target / adjacent_zones / door_way_targets, y cada
    `spawn_zombie` apunta por targetname a su zona y por target al primer
    path_corner. Tranzit no tenia ni un spawn_zone y por eso salian en
    cualquier sitio.
  - Interiores: las paredes tapan lo que hay detras, asi que el PVS
    funciona y el mapa no se viene abajo.

Uso:  python3 scripts/build_prison_map.py
"""
import os
import struct
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_tranzit_map import (  # noqa: E402
    BoxBrush,
    PointLight,
    compile_bsp30,
    export_map_file,
    validate_textures,
    write_txt_file,
    write_way_file,
)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ASSET_NZP_DIR = os.path.join(ROOT_DIR, "android-app", "app", "src", "main",
                             "assets", "base", "nzp")
MAPS_SRC_DIR = os.path.join(ROOT_DIR, "maps_src")

# ---------------------------------------------------------------------------
# Texturas (todas presentes en el WAD de town, las valida validate_textures)
# ---------------------------------------------------------------------------
T_WALL = "bricks_ak_white"
T_WALL2 = "wall_br_red"
T_CONC = "con_rN"
T_FLOOR = "3tiles_grey1"
T_FLOOR2 = "conc_road_D2"
T_CEIL = "ceilings_te1"
T_METAL = "metal_stB"
T_DOOR = "doors"
T_WOOD = "w_wood_dark_64"
T_WOODTOP = "wood_t1"
T_ROOF = "roof_green"
T_GLASS = "window_lit"
T_DARK = "m_metal_darkBlu"

WALL_H = 144          # altura de planta (dos plantas = 288)
CELL_W = 160          # ancho de celda
CORRIDOR = 200        # ancho del pasillo central
WIN_Z0 = 40.0         # franja del muro que ocupa una ventana
WIN_Z1 = 112.0


class World:
    """Recolector de geometria, entidades, luces y waypoints."""

    def __init__(self):
        self.brushes: List[BoxBrush] = []
        self.submodels: List[Tuple[str, List[BoxBrush], dict]] = []
        self.ents: List[dict] = []
        self.lights: List[PointLight] = []
        self.ways: List[dict] = []

    # -- geometria ----------------------------------------------------------
    def brush(self, x0, y0, z0, x1, y1, z1, tex=None, top=None, bottom=None,
              face_tex=None, skip=None):
        tex = tex or T_WALL
        self.brushes.append(BoxBrush(
            float(min(x0, x1)), float(min(y0, y1)), float(min(z0, z1)),
            float(max(x0, x1)), float(max(y0, y1)), float(max(z0, z1)),
            top or tex, bottom or tex, tex, face_tex or {}, skip or set()))

    def floor(self, x0, y0, x1, y1, z, tex=T_FLOOR, thick=16):
        self.brush(x0, y0, z - thick, x1, y1, z, tex=tex, top=tex)

    def ceiling(self, x0, y0, x1, y1, z, tex=T_CEIL, thick=16):
        self.brush(x0, y0, z, x1, y1, z + thick, tex=tex, bottom=tex)

    def wall_x(self, y, x0, x1, z0, z1, tex=T_WALL, thick=16, gaps=()):
        """Muro paralelo al eje X, en Y=y. gaps = lista de (xa, xb)."""
        segs = self._segs(x0, x1, gaps)
        for a, b in segs:
            if b - a > 1:
                self.brush(a, y - thick / 2, z0, b, y + thick / 2, z1, tex=tex)

    def wall_y(self, x, y0, y1, z0, z1, tex=T_WALL, thick=16, gaps=()):
        """Muro paralelo al eje Y, en X=x. gaps = lista de (ya, yb)."""
        segs = self._segs(y0, y1, gaps)
        for a, b in segs:
            if b - a > 1:
                self.brush(x - thick / 2, a, z0, x + thick / 2, b, z1, tex=tex)

    @staticmethod
    def _segs(a, b, gaps):
        """Parte [a,b] quitando los huecos."""
        cuts = sorted((max(a, g[0]), min(b, g[1])) for g in gaps)
        out, cur = [], a
        for ga, gb in cuts:
            if ga > cur:
                out.append((cur, ga))
            cur = max(cur, gb)
        if cur < b:
            out.append((cur, b))
        return out

    def shell(self, x0, y0, x1, y1, z0, z1, tex=T_WALL, floor_t=T_FLOOR,
              ceil_t=T_CEIL, doors=(), windows=()):
        """Caja cerrada: suelo, techo y 4 paredes con huecos.

        doors   = (lado, a, b) hueco de puerta, de suelo a techo.
        windows = (lado, a, b) hueco de ventana: solo se abre la franja
                  WIN_Z0..WIN_Z1, el resto del muro sigue en pie.
        lado en 'N','S','E','W'.
        """
        self.floor(x0, y0, x1, y1, z0, tex=floor_t)
        self.ceiling(x0, y0, x1, y1, z1, tex=ceil_t)
        sides = ("N", "S", "E", "W")
        dg = {s: [g[1:] for g in doors if g[0] == s] for s in sides}
        vg = {s: [g[1:] for g in windows if g[0] == s] for s in sides}

        def band(mk, holes, wins):
            """Tres franjas si la pared tiene ventanas, una sola si no."""
            if wins:
                mk(z0, WIN_Z0, [])                 # antepecho
                mk(WIN_Z0, WIN_Z1, holes + wins)   # la propia ventana
                mk(WIN_Z1, z1, [])                 # dintel
            else:
                mk(z0, z1, holes)

        band(lambda a, b, g: self.wall_x(y1, x0, x1, a, b, tex=tex, gaps=g),
             dg["N"], vg["N"])
        band(lambda a, b, g: self.wall_x(y0, x0, x1, a, b, tex=tex, gaps=g),
             dg["S"], vg["S"])
        band(lambda a, b, g: self.wall_y(x1, y0, y1, a, b, tex=tex, gaps=g),
             dg["E"], vg["E"])
        band(lambda a, b, g: self.wall_y(x0, y0, y1, a, b, tex=tex, gaps=g),
             dg["W"], vg["W"])

    # -- entidades ----------------------------------------------------------
    def ent(self, classname, origin=None, **kv):
        e = {"classname": classname}
        if origin is not None:
            e["origin"] = "%g %g %g" % (origin[0], origin[1], origin[2])
        for k, v in kv.items():
            e[k] = v if isinstance(v, str) else str(v)
        self.ents.append(e)

    def light(self, x, y, z, r=255, g=230, b=200, radius=320):
        self.lights.append(PointLight(float(x), float(y), float(z),
                                      float(r), float(g), float(b),
                                      float(radius)))

    def prop(self, model, x, y, z, angles="0 0 0"):
        self.ent("place_model", (x, y, z), model=model, angles=angles)

    def submodel(self, classname, brushes, **kv):
        self.submodels.append(
            (classname, brushes, {k: str(v) for k, v in kv.items()}))

    def way(self, wid, x, y, z, targets):
        self.ways.append({"id": wid, "origin": (float(x), float(y), float(z)),
                          "special": 0, "targets": list(targets)})


# ===========================================================================
#  LAYOUT: PENAL
#  Planta baja  Z[0..144]      Planta alta Z[144..288]
#
#        +---------------------------+
#        |        PATIO              |   Y[+900..+1900]
#        +------------+--------------+
#        |  COMEDOR   |  ENFERMERIA  |   Y[+200..+900]
#        +------------+--------------+
#        |   BLOQUE DE CELDAS (x8)   |   Y[-900..+200]
#        +------------+--------------+
#        |       RECEPCION           |   Y[-1300..-900]
#        +---------------------------+
#           X[-1100 .. +1100]
# ===========================================================================
X0, X1 = -1100.0, 1100.0


def build_prison_world() -> World:
    w = World()
    Z0, Z1 = 0.0, WALL_H           # planta baja
    Z2, Z3 = WALL_H, WALL_H * 2    # planta alta

    # ------------------------------------------------------------------
    # VENTANAS CON TABLAS
    # Cada ventana es un trio: item_barricade en el hueco, un path_corner
    # en el exterior y un spawn_zombie detras. El zombie pertenece a la
    # zona indicada, llega al hueco, rompe las maderas y entra: asi es
    # como se "hacen puntos" matandolos mientras rompen.
    # ------------------------------------------------------------------
    n_bar = [0]

    def window(name, x, y, zone, ox, oy, ang):
        """Hueco con tablas en (x,y); el zombie nace fuera, en (ox,oy)."""
        n_bar[0] += 1
        pc = "pc_bar%d" % n_bar[0]
        w.ent("item_barricade", (x, y, WIN_Z0 + 8), angles=ang,
              targetname=name, model="models/misc/window.mdl",
              oldmodel="sounds/misc/barricade.wav",
              aistatus="sounds/misc/barricade_destroy.wav")
        w.ent("path_corner", (ox, oy, 40), targetname=pc, target=name)
        w.ent("spawn_zombie", (ox, oy, 40), angles="0 0 0",
              targetname=zone, target=pc, spawnflags="0")

    # ------------------------------------------------------------------
    # GALERIA EXTERIOR: anillo cerrado alrededor del edificio
    # (X[-1500..1500] Y[-1700..2300] menos la planta X[-1100..1100]
    # Y[-1300..1900]). Es donde viven los zombies que asaltan las
    # ventanas. Va cerrado con techo porque no hay textura de cielo.
    # ------------------------------------------------------------------
    for ax0, ay0, ax1, ay1 in ((-1500.0, -1700.0, 1500.0, -1300.0),   # sur
                               (-1500.0, 1900.0, 1500.0, 2300.0),    # norte
                               (-1500.0, -1300.0, -1100.0, 1900.0),  # oeste
                               (1100.0, -1300.0, 1500.0, 1900.0)):   # este
        w.floor(ax0, ay0, ax1, ay1, 0.0, tex=T_FLOOR2)
        w.ceiling(ax0, ay0, ax1, ay1, 400.0, tex=T_CEIL)
    w.wall_x(2300.0, -1500.0, 1500.0, 0.0, 400.0, tex=T_CONC)
    w.wall_x(-1700.0, -1500.0, 1500.0, 0.0, 400.0, tex=T_CONC)
    w.wall_y(-1500.0, -1700.0, 2300.0, 0.0, 400.0, tex=T_CONC)
    w.wall_y(1500.0, -1700.0, 2300.0, 0.0, 400.0, tex=T_CONC)

    # ------------------------------------------------------------------
    # RECEPCION / SPAWN  Y[-1300..-900]
    # ------------------------------------------------------------------
    ry0, ry1 = -1300.0, -900.0
    w.shell(X0, ry0, X1, ry1, Z0, Z1, tex=T_WALL2, floor_t=T_FLOOR2,
            doors=[("N", -120, 120)],
            windows=[("W", -1240, -1160), ("W", -1040, -960),
                     ("E", -1240, -1160), ("E", -1040, -960)])
    # mostrador de entrada
    w.brush(-260, -1120, Z0, 260, -1080, 44, tex=T_WOOD, top=T_WOODTOP)
    w.ent("info_player_start", (-600, -1100, 40), angles="0 0 0")
    w.ent("info_player_1_spawn", (-600, -1100, 40), angles="0 0 0")
    w.ent("info_player_2_spawn", (-500, -1150, 40), angles="0 20 0")
    w.ent("info_player_3_spawn", (-700, -1150, 40), angles="0 340 0")
    w.ent("info_player_4_spawn", (-600, -1200, 40), angles="0 0 0")
    for x in (-800, -400, 0, 400, 800):
        w.light(x, -1100, 120, radius=300)
    w.ent("weapon_wall", (-1040, -1290, 40), targetname="w_pistol",
          angle="0", sequence="1")
    w.ent("buy_weapon", (-1040, -1290, 40), target="w_pistol", weapon="2",
          cost="500", cost2="250")

    # ------------------------------------------------------------------
    # BLOQUE DE CELDAS  Y[-900..+200], pasillo central en X[-100..100]
    # ------------------------------------------------------------------
    by0, by1 = -900.0, 200.0
    w.shell(X0, by0, X1, by1, Z0, Z3, tex=T_CONC, floor_t=T_FLOOR,
            doors=[("S", -120, 120), ("N", -120, 120)],
            windows=[("W", -800, -740), ("W", -280, -220),
                     ("E", -800, -740), ("E", -280, -220)])
    # forjado de la planta alta con hueco sobre el pasillo
    w.brush(X0, by0, Z1, -CORRIDOR / 2, by1, Z1 + 16, tex=T_CONC)
    w.brush(CORRIDOR / 2, by0, Z1, X1, by1, Z1 + 16, tex=T_CONC)

    # 8 celdas: 4 al oeste (X[-1100..-100]) y 4 al este (X[100..1100])
    cells = []
    for side in (-1, 1):
        for i in range(4):
            cy0 = -860.0 + i * 260.0
            cy1 = cy0 + 240.0
            if side < 0:
                cx0, cx1 = -1080.0, -CORRIDOR / 2
            else:
                cx0, cx1 = CORRIDOR / 2, 1080.0
            cells.append((side, i, cx0, cy0, cx1, cy1))
            for z0, z1 in ((Z0, Z1), (Z2, Z3)):
                # muro exterior de la celda: hueco de paso + ventana
                wx = cx0 if side < 0 else cx1
                paso = (cy1 - 120, cy1 - 40)
                # solo las celdas 0 y 2 de la planta baja dan al exterior
                vg = [(cy0 + 60, cy0 + 120)] if (z0 == Z0 and i in (0, 2)) else []
                if vg:
                    w.wall_y(wx, cy0, cy1, z0, WIN_Z0, tex=T_CONC,
                             gaps=[paso])
                    w.wall_y(wx, cy0, cy1, WIN_Z0, WIN_Z1, tex=T_CONC,
                             gaps=[paso] + vg)
                    w.wall_y(wx, cy0, cy1, WIN_Z1, z1, tex=T_CONC,
                             gaps=[paso])
                else:
                    w.wall_y(wx, cy0, cy1, z0, z1, tex=T_CONC, gaps=[paso])
                w.wall_x(cy0, cx0, cx1, z0, z1, tex=T_CONC)
                w.wall_x(cy1, cx0, cx1, z0, z1, tex=T_CONC)
                # rejas de la celda (barrotes finos, pocos brushes)
                gy = cy1 - 120 if side < 0 else cy1 - 120
                for b in range(4):
                    bx = (cx1 - 20) if side < 0 else (cx0 + 20)
                    w.brush(bx - 6, gy + b * 22, z0, bx + 6, gy + b * 22 + 8,
                            z1 - 40, tex=T_DARK)
            w.light(cx0 + (cx1 - cx0) / 2, cy0 + 120, Z1 - 12, radius=200)

    # pasillo central: 2 plantas
    w.light(0, -350, Z1 - 12, radius=340)
    w.light(0, 30, Z1 - 12, radius=340)
    w.light(0, -350, Z3 - 12, radius=340)

    # ------------------------------------------------------------------
    # COMEDOR  X[-1100..0] Y[200..900]
    # ------------------------------------------------------------------
    w.shell(X0, 200.0, 0.0, 900.0, Z0, Z1, tex=T_WALL, floor_t=T_FLOOR,
            doors=[("S", -120, 120)],
            windows=[("W", 400, 460), ("W", 700, 760),
                     ("N", -820, -740), ("N", -420, -340)])
    for i in range(4):
        mx = -950.0 + i * 260.0
        w.brush(mx, 350, Z0, mx + 200, 420, 34, tex=T_WOOD, top=T_WOODTOP)
        w.brush(mx, 620, Z0, mx + 200, 690, 34, tex=T_WOOD, top=T_WOODTOP)
    w.light(-550, 520, 120, radius=420)
    w.ent("perk_revive", (-1040, 300, 40), angles="0 90 0", spawnflags="8",
          cost="500", cost2="1500",
          oldmodel="sounds/machines/perk_drink.wav",
          weapon2model="models/machines/v_perk.mdl",
          weapon2_animduration="22",
          model="models/machines/quake_scale/quick_revive.mdl")
    w.ent("weapon_wall", (-1080, 760, 40), targetname="w_shotgun",
          angle="90", sequence="8")
    w.ent("buy_weapon", (-1080, 760, 40), target="w_shotgun", weapon="8",
          cost="1200", cost2="600")

    # ------------------------------------------------------------------
    # ENFERMERIA  X[0..1100] Y[200..900]
    # ------------------------------------------------------------------
    w.shell(0.0, 200.0, X1, 900.0, Z0, Z1, tex=T_WALL2, floor_t=T_FLOOR,
            doors=[("S", 60, 180), ("W", 400, 520)],
            windows=[("E", 400, 460), ("E", 700, 760),
                     ("N", 400, 480), ("N", 800, 880)])
    for i in range(3):
        w.brush(120 + i * 300, 320, Z0, 260 + i * 300, 480, 36,
                tex=T_METAL, top=T_FLOOR)
    w.light(550, 550, 120, radius=400)
    w.ent("perk_speed", (1040, 300, 40), angles="0 270 0", spawnflags="8",
          cost="3000", cost2="3000",
          oldmodel="sounds/machines/perk_drink.wav",
          model="models/machines/quake_scale/speed_cola.mdl")

    # ------------------------------------------------------------------
    # PATIO  Y[900..1900] (exterior amurallado)
    # ------------------------------------------------------------------
    py0, py1 = 900.0, 1900.0
    w.floor(X0, py0, X1, py1, Z0, tex=T_FLOOR2)
    w.wall_x(py1, X0, X1, Z0, 400, tex=T_CONC)      # muro norte alto
    w.wall_y(X0, py0, py1, Z0, 400, tex=T_CONC)
    w.wall_y(X1, py0, py1, Z0, 400, tex=T_CONC)
    w.wall_x(py0, X0, X1, Z0, 400, tex=T_CONC, gaps=[(-120, 120)])
    # torres de vigilancia
    for tx in (-900.0, 900.0):
        for tz0, tz1 in ((Z0, 260),):
            w.brush(tx - 150, py1 - 150, tz0, tx + 150, py1 + 10, tz1,
                    tex=T_CONC)
    w.light(0, 1400, 200, 210, 220, 255, radius=700)
    #Props del patio
    w.prop("models/props/Barrel_m.mdl", -900, 1050, 16)
    w.prop("models/props/Barrel_m.mdl", -840, 1090, 16)
    w.prop("models/props/Kino_boxes3.mdl", 820, 1080, 16)
    w.prop("models/props/Kino_box.mdl", 760, 1120, 16)
    w.prop("models/props/MopBucket.mdl", -1000, 1700, 16)
    w.prop("models/props/bodybag_flat.mdl", 400, 1500, 16)
    # props del bloque de celdas
    w.prop("models/props/bed.mdl", -980, -820, 16)
    w.prop("models/props/bed.mdl", 980, -820, 16)
    w.prop("models/props/Kino_table.mdl", -980, -560, 16)
    w.prop("models/props/bath.mdl", 980, -560, 16)
    w.prop("models/props/bodybag_head.mdl", -980, -300, 16)

    # ------------------------------------------------------------------
    # VENTANAS CON TABLAS: 4 por sala (16 en total)
    # ------------------------------------------------------------------
    for i, wy in enumerate((-1200, -1000)):                     # recepcion
        window("bar_r%d_w" % i, -1100, wy, "z_cells", -1250, wy, "0 90 0")
        window("bar_r%d_e" % i, 1100, wy, "z_cells", 1250, wy, "0 270 0")
    for i, cy in enumerate((-770, -250)):                       # celdas
        window("bar_c%d_w" % i, -1100, cy, "z_cells", -1250, cy, "0 90 0")
        window("bar_c%d_e" % i, 1100, cy, "z_cells", 1250, cy, "0 270 0")
    for i, my in enumerate((430, 730)):                         # comedor
        window("bar_m%d_w" % i, -1100, my, "z_mess", -1250, my, "0 90 0")
    for i, mx in enumerate((-780, -380)):                       # comedor->patio
        window("bar_m%d_n" % i, mx, 900, "z_yard", mx, 1060, "0 180 0")
    for i, ny in enumerate((430, 730)):                         # enfermeria
        window("bar_i%d_e" % i, 1100, ny, "z_infirm", 1250, ny, "0 270 0")
    for i, nx in enumerate((430, 830)):                         # enf->patio
        window("bar_i%d_n" % i, nx, 900, "z_yard", nx, 1060, "0 180 0")

    # ------------------------------------------------------------------
    # ARMA DE PARED EN CADA SALA (IDs numericos, como los mapas del juego)
    # ------------------------------------------------------------------
    def wallgun(name, wid, cost, x, y, ang):
        w.ent("weapon_wall", (x, y, 40), targetname=name, angle=ang,
              sequence=str(wid))
        w.ent("buy_weapon", (x, y, 40), target=name, weapon=str(wid),
              cost=str(cost), cost2=str(cost // 2))

    wallgun("wg_cells_a", 5, 600, -1080, -500, "90")     # bloque de celdas
    wallgun("wg_cells_b", 21, 1200, 1080, -500, "270")
    wallgun("wg_mess_a", 7, 2000, -1080, 460, "90")      # comedor
    wallgun("wg_infirm_a", 23, 1500, 1080, 460, "270")   # enfermeria
    wallgun("wg_infirm_b", 3, 1000, 1080, 760, "270")
    wallgun("wg_yard_a", 11, 2000, -1080, 1500, "90")    # patio

    # ------------------------------------------------------------------
    # MOBILIARIO (todos los .mdl existen en models/props)
    # ------------------------------------------------------------------
    # recepcion
    w.prop("models/props/shelf.mdl", 1000, -1240, 16, "0 270 0")
    w.prop("models/props/trash_con.mdl", -1000, -1240, 16)
    w.prop("models/props/radio.mdl", -240, -1100, 44, "0 90 0")
    # celdas: literas y enseres
    for i, py in enumerate((-800, -540, -280, -20)):
        w.prop("models/props/bed.mdl", -1000, py, 16, "0 90 0")
        w.prop("models/props/bed.mdl", 1000, py, 16, "0 270 0")
        w.prop("models/props/toilet.mdl", -1020, py + 90, 16, "0 90 0")
        w.prop("models/props/metal_chair.mdl", 1020, py + 60, 16, "0 270 0")
    # pasillo
    w.prop("models/props/sandbags.mdl", -60, -700, 16, "0 90 0")
    w.prop("models/props/sandbags.mdl", -60, 60, 16, "0 90 0")
    w.prop("models/props/container.mdl", 60, -820, 16)
    # comedor
    for i in range(4):
        mx = -950.0 + i * 260.0
        w.prop("models/props/metal_chair.mdl", mx + 40, 300, 16, "0 180 0")
        w.prop("models/props/metal_chair.mdl", mx + 40, 740, 16, "0 0 0")
    w.prop("models/props/fridge.mdl", -60, 820, 16, "0 270 0")
    w.prop("models/props/oven.mdl", -60, 700, 16, "0 270 0")
    w.prop("models/props/trash_con.mdl", -1020, 820, 16)
    # enfermeria
    w.prop("models/props/dentist_chair.mdl", 300, 700, 16, "0 0 0")
    w.prop("models/props/dentist_chair.mdl", 700, 700, 16, "0 0 0")
    w.prop("models/props/dentist_chair.mdl", 300, 400, 16, "0 180 0")
    w.prop("models/props/table_sq.mdl", 500, 550, 16)
    w.prop("models/props/MopBucket.mdl", 1040, 820, 16)
    w.prop("models/props/bodybag_flat.mdl", 200, 240, 16, "0 90 0")
    # patio
    w.prop("models/props/sandbags.mdl", -900, 1200, 16, "0 0 0")
    w.prop("models/props/sandbags.mdl", -780, 1200, 16, "0 0 0")
    w.prop("models/props/Kino_boxes4.mdl", 900, 1300, 16)
    w.prop("models/props/Kino_boxes2.mdl", 840, 1420, 16)
    w.prop("models/props/teddy.mdl", 950, 1600, 16)

    # ------------------------------------------------------------------
    # PUERTAS COMPRABLES (abren zonas de zombies)
    # ------------------------------------------------------------------
    def door(name, zone, cost, x0, y0, z0, x1, y1, z1, way_target):
        b = BoxBrush(float(min(x0, x1)), float(min(y0, y1)), float(min(z0, z1)),
                     float(max(x0, x1)), float(max(y0, y1)), float(max(z0, z1)),
                     T_DOOR, T_DOOR, T_DOOR, {}, set())
        w.submodel("func_door_nzp", [b], wayTarget=way_target, target=zone,
                   cost=cost, speed="120", wait="4", lip="8", sounds="2",
                   angles="0 90 0", spawnflags="32")

    door("d_cells", "z_cells", 750, -120, -900, 0, 120, -884, 120, "w_recep")
    door("d_yard", "z_yard", 1000, -120, 884, 0, 120, 900, 120, "w_cells")
    door("d_infirm", "z_infirm", 500, -16, 400, 0, 0, 520, 120, "w_mess")

    # ------------------------------------------------------------------
    # ZONAS DE SPAWN DE ZOMBIS (spawn_zone + cadena de path_corner)
    # Esto es lo que Tranzit NO tenia y hacia que salieran al azar.
    # ------------------------------------------------------------------
    zones = {
        "z_cells": dict(adjacent="z_yard", doors="d_cells",
                        path=[(0, -700, 40), (0, -400, 40), (0, -100, 40)],
                        spawns=[(-900, -820, 40), (900, -820, 40),
                                (-900, -300, 40)]),
        "z_yard": dict(adjacent="z_cells, z_mess", doors="d_yard",
                       path=[(0, 1000, 40), (0, 1300, 40), (0, 1600, 40)],
                       spawns=[(-900, 1700, 40), (900, 1700, 40),
                               (0, 1800, 40)]),
        "z_mess": dict(adjacent="z_yard, z_infirm", doors="d_infirm",
                       path=[(550, 300, 40), (550, 550, 40), (550, 800, 40)],
                       spawns=[(-900, 800, 40), (1040, 800, 40)]),
        "z_infirm": dict(adjacent="z_mess", doors="d_infirm",
                         path=[(-550, 300, 40), (-550, 550, 40),
                               (-550, 800, 40)],
                         spawns=[(-900, 250, 40), (900, 250, 40)]),
    }
    wid = 0
    for zname, z in zones.items():
        w.ent("spawn_zone", zone_name=zname, zone_target=zname,
              adjacent_zones=z["adjacent"], door_way_targets=z["doors"])
        # cadena de path_corner: cada nodo apunta al siguiente
        names = [f"pc_{zname}_{i}" for i in range(len(z["path"]))]
        for i, (px, py, pz) in enumerate(z["path"]):
            nxt = names[i + 1] if i + 1 < len(names) else names[0]
            w.ent("path_corner", (px, py, pz), targetname=names[i],
                  target=nxt)
        for i, (sx, sy, sz) in enumerate(z["spawns"]):
            w.ent("spawn_zombie", (sx, sy, sz), angles="0 0 0",
                  targetname=zname, target=names[0], spawnflags="0")
        # waypoints de IA encadenados por la ruta de la zona
        base = wid
        for i, (px, py, pz) in enumerate(z["path"]):
            w.way(base + i, px, py, pz,
                  [base + i - 1] if i else [base + len(z["path"]) - 1])
        for i, (sx, sy, sz) in enumerate(z["spawns"]):
            w.way(base + len(z["path"]) + i, sx, sy, sz, [base])
        wid += len(z["path"]) + len(z["spawns"])

    # waypoints del spawn de jugadores hacia las zonas
    w.way(wid, -600, -1100, 40, [0])
    w.way(wid + 1, 0, -700, 40, [wid, 1])
    w.way(wid + 2, 0, 1000, 40, [3])

    # ------------------------------------------------------------------
    # EXTRAS: caja misteriosa, interruptor de luz, barricadas
    # ------------------------------------------------------------------
    w.ent("mystery_box", (0, 1500, 40), angles="0 0 0")
    w.ent("power_switch", (-1040, 820, 40), angles="0 90 0")
    w.ent("item_barricade", (-1040, 500, 40), angles="0 90 0",
          model="models/misc/window.mdl",
          oldmodel="sounds/misc/barricade.wav",
          aistatus="sounds/misc/barricade_destroy.wav")
    w.ent("path_corner", (-1040, 420, 40), targetname="pc_bar1",
          target="bar1")
    w.ent("spawn_zombie", (-1040, 340, 40), angles="0 0 0",
          target="pc_bar1", spawnflags="0")

    return w


def main():
    w = build_prison_world()
    sub_brushes = sum(len(b) for _c, b, _k in w.submodels)
    print(f"[INFO] Geometria: {len(w.brushes)} brushes de mundo, "
          f"{len(w.submodels)} submodelos, "
          f"{len(w.brushes) + sub_brushes} brushes totales")
    print(f"[INFO] Luces: {len(w.lights)}   Entidades: {len(w.ents)}   "
          f"Props: {sum(1 for e in w.ents if e['classname'] == 'place_model')}")

    validate_textures(w.brushes, w.submodels)

    os.makedirs(MAPS_SRC_DIR, exist_ok=True)
    map_path = os.path.join(MAPS_SRC_DIR, "prison.map")
    export_map_file(map_path, w.brushes, w.submodels, w.ents)
    print(f"[OK] Fuente .map para TrenchBroom: {map_path}")

    bsp_path = os.path.join(ASSET_NZP_DIR, "maps", "prison.bsp")
    compile_bsp30(bsp_path, w.brushes, w.submodels, w.ents, w.lights)

    way_path = os.path.join(ASSET_NZP_DIR, "maps", "prison.way")
    write_way_file(way_path, w.ways)

    txt_path = os.path.join(ASSET_NZP_DIR, "maps", "prison.txt")
    write_txt_file(txt_path)


if __name__ == "__main__":
    main()

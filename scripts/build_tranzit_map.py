#!/usr/bin/env python3
"""
build_tranzit_map.py
====================
Generador y compilador BSP30 completo del mapa BO2 Tranzit (Green Run) a escala
para Nazi Zombies: Portable (Android / Meta Quest 3).

Genera:
  1. maps_src/tranzit.map (fuente en formato Quake/Half-Life editable en TrenchBroom)
  2. android-app/app/src/main/assets/base/nzp/maps/tranzit.bsp (BSP v30 con texturas WAD3,
     lightmaps RGB, arboles de colision Hulls 0..3 y entidades)
  3. android-app/app/src/main/assets/base/nzp/maps/tranzit.way (red de waypoints de IA)
  4. android-app/app/src/main/assets/base/nzp/maps/tranzit.txt (metadatos del menu)
  5. android-app/app/src/main/assets/base/nzp/gfx/menu/custom/tranzit.png (480x272)
  6. android-app/app/src/main/assets/base/nzp/gfx/lscreen/tranzit.png (512x256)
"""

import bisect
import math
import os
import struct
import sys
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ASSET_NZP_DIR = os.path.join(ROOT_DIR, "android-app", "app", "src", "main", "assets", "base", "nzp")
MAPS_SRC_DIR = os.path.join(ROOT_DIR, "maps_src")

CONTENTS_EMPTY = -1
CONTENTS_SOLID = -2
TEX_SPECIAL = 1
MAX_MAP_CLIPNODES = 32767

# Texturas que emiten luz propia: su lightmap se rellena con un color fijo
# (el motor NZ:P no soporta el rango fullbright de la paleta WAD3 en Half-Life).
FULLBRIGHT_TEXTURES: Dict[str, Tuple[int, int, int]] = {
    "lava_cracks": (255, 150, 52),
    "br_lightGL": (255, 240, 200),
    "reactor_blue": (66, 176, 255),
    "window_lit": (255, 202, 124),
    "danger_stripe": (150, 132, 48),
    "bus_headlight": (255, 250, 226),
    "bus_tail": (255, 70, 50),
}


@dataclass
class BoxBrush:
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float
    tex_top: str
    tex_bottom: str
    tex_sides: str
    face_tex: Dict[str, str] = field(default_factory=dict)
    skip_faces: set = field(default_factory=set)

    def get_tex(self, face_tag: str) -> str:
        if face_tag in self.face_tex:
            return self.face_tex[face_tag]
        if face_tag == "+z":
            return self.tex_top
        if face_tag == "-z":
            return self.tex_bottom
        return self.tex_sides


@dataclass
class QuadFace:
    axis: int          # 0=X, 1=Y, 2=Z
    dist: float        # coordinate on axis
    side: int          # 0 if outward normal is +axis, 1 if outward normal is -axis
    u0: float
    v0: float
    u1: float
    v1: float
    tex_name: str
    fullbright: Optional[Tuple[int, int, int]] = None


@dataclass
class PointLight:
    x: float
    y: float
    z: float
    r: float
    g: float
    b: float
    radius: float


def load_wad3_textures_from_town() -> Dict[str, bytes]:
    """Extrae las texturas WAD3 embebidas en town.bsp y anade las custom de Tranzit."""
    town_bsp = os.path.join(ASSET_NZP_DIR, "maps", "town.bsp")
    with open(town_bsp, "rb") as f:
        data = f.read()

    ofs, sz = struct.unpack_from("<ii", data, 4 + 8 * 2)
    numtex = struct.unpack_from("<i", data, ofs)[0]
    offs = [struct.unpack_from("<i", data, ofs + 4 + 4 * i)[0] for i in range(numtex)]

    raw_blobs: Dict[str, bytes] = {}
    for i, tofs in enumerate(offs):
        if tofs < 0:
            continue
        abs_p = ofs + tofs
        name = data[abs_p : abs_p + 16].split(b"\x00", 1)[0].decode("latin1")
        w, h = struct.unpack_from("<II", data, abs_p + 16)
        m0, m1, m2, m3 = struct.unpack_from("<IIII", data, abs_p + 24)
        end_mip3 = abs_p + m3 + (w // 8) * (h // 8)
        colors_used = struct.unpack_from("<H", data, end_mip3)[0]
        total_len = (end_mip3 - abs_p) + 2 + colors_used * 3 + 2
        raw_blobs[name] = data[abs_p : abs_p + total_len]

    # Texturas procedimentales exclusivas de Green Run (rotulos, asfalto con
    # linea central, aceras, lona del autobus, reactor, tejas, heno, ...)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tranzit_textures

    for name, blob in tranzit_textures.build_custom_textures().items():
        raw_blobs[name] = blob
    return raw_blobs


# ============================================================================
# DISENO ARQUITECTONICO FIEL DE BO2 TRANZIT (GREEN RUN)
# ============================================================================

def build_tranzit_world():
    """
    Construye toda la geometria, submodelos, luces, entidades y waypoints de
    Green Run (BO2 Tranzit) en un circuito cerrado de [-2400, 2400] x [-2400, 2400].
    """
    world_brushes: List[BoxBrush] = []
    submodels: List[Tuple[str, List[BoxBrush], dict]] = []
    point_entities: List[dict] = []
    lights: List[PointLight] = []
    waypoints: List[dict] = []

    def add_brush(
        xmin, ymin, zmin, xmax, ymax, zmax,
        tex_sides="conc_road_D2", tex_top=None, tex_bottom=None,
        face_tex=None, skip_faces=None
    ):
        world_brushes.append(
            BoxBrush(
                float(xmin), float(ymin), float(zmin),
                float(xmax), float(ymax), float(zmax),
                tex_top or tex_sides,
                tex_bottom or tex_sides,
                tex_sides,
                face_tex or {},
                skip_faces or set(),
            )
        )

    def add_submodel(classname: str, brushes: List[BoxBrush], kv: dict):
        submodels.append((classname, brushes, kv))

    def add_ent(classname: str, origin: Optional[Tuple[float, float, float]] = None, **kwargs):
        d = {"classname": classname}
        if origin is not None:
            d["origin"] = f"{int(origin[0])} {int(origin[1])} {int(origin[2])}"
        for k, v in kwargs.items():
            d[k] = str(v)
        point_entities.append(d)

    def add_light(x, y, z, r, g, b, radius=420.0):
        lights.append(PointLight(float(x), float(y), float(z), float(r), float(g), float(b), float(radius)))
        add_ent("light", (x, y, z), _light=f"{int(r)} {int(g)} {int(b)} {int(radius)}")

    def add_wall_weapon(
        name_id: str, origin: Tuple[float, float, float], yaw: int,
        weapon_id: int, cost: int, ammo_cost: int,
        bx0: float, by0: float, bz0: float, bx1: float, by1: float, bz1: float
    ):
        add_ent(
            "weapon_wall",
            origin,
            targetname=name_id,
            sequence=weapon_id - 1,
            frame=weapon_id,
            angles=f"0 {yaw} 0",
        )
        trig_brush = BoxBrush(bx0, by0, bz0, bx1, by1, bz1, "doors", "doors", "doors")
        add_submodel(
            "buy_weapon",
            [trig_brush],
            {
                "target": name_id,
                "weapon": str(weapon_id),
                "cost": str(cost),
                "cost2": str(ammo_cost),
                "ammo": "120",
                "angles": "0 0 0",
            },
        )

    def add_buyable_door(
        way_target: str, unlock_target: str, cost: int,
        xmin: float, ymin: float, zmin: float, xmax: float, ymax: float, zmax: float,
        tex="ver_metal_door", spawnflags=32
    ):
        b = BoxBrush(xmin, ymin, zmin, xmax, ymax, zmax, tex, tex, tex)
        kv = {
            "wayTarget": way_target,
            "cost": str(cost),
            "speed": "120",
            "wait": "4",
            "lip": "8",
            "sounds": "2",
            "spawnflags": str(spawnflags),
            "angles": "0 90 0",
        }
        if unlock_target:
            kv["target"] = unlock_target
        add_submodel("func_door_nzp", [b], kv)

    def add_lava_pit(xmin: float, ymin: float, xmax: float, ymax: float):
        # Superficie visual incandescente ligeramente elevada (+2u, <18u step)
        add_brush(
            xmin, ymin, 0, xmax, ymax, 2,
            tex_sides="lava_cracks", tex_top="lava_cracks", tex_bottom="lava_cracks",
            skip_faces={"-z"}
        )
        # Trigger_hurt que dana al jugador (10 HP/s) pero no mata a la IA (spawnflags=1)
        tb = BoxBrush(xmin, ymin, 0, xmax, ymax, 20, "lava_cracks", "lava_cracks", "lava_cracks")
        add_submodel(
            "trigger_hurt",
            [tb],
            {"dmg": "10", "spawnflags": "1", "message": "^1Lava Burn!"},
        )
        cx, cy = 0.5 * (xmin + xmax), 0.5 * (ymin + ymax)
        add_light(cx, cy, 44, 255, 115, 30, 380)
        add_ent("place_model", (cx, cy, 4), mdl="models/props/flame.mdl", angles="0 0 0", spawnflags="4")

    def add_prop(model_path: str, origin: Tuple[float, float, float], yaw: int = 0):
        add_ent("place_model", origin, mdl=model_path, angles=f"0 {yaw} 0")

    # --- Utilidades arquitectonicas de alto nivel ---------------------------

    def add_floor(x0, y0, x1, y1, tex, z0=0.0, z1=2.0):
        """Loseta de suelo interior/exterior (cara superior a Z=z1)."""
        add_brush(x0, y0, z0, x1, y1, z1, tex_sides=tex, tex_top=tex, tex_bottom=tex, skip_faces={"-z"})

    def add_road(x0, y0, x1, y1, tex="road_stripe"):
        """Calzada: 1u de grosor (por debajo del escalon de la acera)."""
        add_floor(x0, y0, x1, y1, tex, 0.0, 1.0)

    def add_walk(x0, y0, x1, y1, tex="sidewalk", top=5.0):
        """Aceras y bordillos (5u de alto, subibles sin saltar)."""
        add_floor(x0, y0, x1, y1, tex, 0.0, top)

    def add_column(cx, cy, z0, z1, size=16.0, tex="brick_pillar"):
        """Pilar / columna cuadrada centrada en (cx, cy)."""
        h = size * 0.5
        add_brush(cx - h, cy - h, z0, cx + h, cy + h, z1, tex_sides=tex, tex_top=tex, tex_bottom=tex)

    def add_beam(x0, y0, x1, y1, z0, z1, tex="metal_stB"):
        """Viga horizontal (dinteles, marquesinas, vigas de tejado)."""
        add_brush(x0, y0, z0, x1, y1, z1, tex_sides=tex, tex_top=tex, tex_bottom=tex)

    def add_panel(x0, y0, z0, x1, y1, z1, tex, faces):
        """Panel plano (rotulo, ventana, persiana) con solo las caras dadas visibles."""
        add_brush(
            x0, y0, z0, x1, y1, z1,
            tex_sides="conc_road_D2",
            face_tex={f: tex for f in faces},
        )

    def add_steps(x0, y0, x1, y1, z_top, step_h=6.0, tex="sidewalk", dir_x=True, count=3):
        """Escalera de `count` peldanos de `step_h` unidades."""
        for i in range(count):
            z0 = z_top - (count - i) * step_h
            if dir_x:
                sx0 = x0 + i * (x1 - x0) / count
                add_floor(sx0, y0, x1, y1, tex, 0.0, z0 + step_h)
            else:
                sy0 = y0 + i * (y1 - y0) / count
                add_floor(x0, sy0, x1, y1, tex, 0.0, z0 + step_h)

    def add_rail(x0, y0, x1, y1, z0, z1, tex="metal_grate", thick=6.0):
        """Barandilla sencilla (travesano superior)."""
        add_brush(
            min(x0, x1) - thick, min(y0, y1) - thick, z0,
            max(x0, x1) + thick, max(y0, y1) + thick, z1,
            tex_sides=tex, tex_top=tex, tex_bottom=tex,
        )

    def add_window_row(a0, a1, z0, z1, face, wall_a, spacing=140.0, tex="window_lit", depth=8.0, inset=2.0):
        """Fila de ventanas iluminadas sobre la cara interior de una pared.

        `face` es la normal de la cara visible (lado desde el que se mira) y
        `wall_a` es la coordenada de la cara interior de la pared. El panel se
        coloca siempre DENTRO de la sala, separado `inset` unidades de la pared
        para evitar z-fighting.
        """
        horizontal = face in ("-y", "+y")
        if face == "-y":
            b0, b1 = wall_a - inset - depth, wall_a - inset
        elif face == "+y":
            b0, b1 = wall_a + inset, wall_a + inset + depth
        elif face == "-x":
            b0, b1 = wall_a - inset - depth, wall_a - inset
        else:
            b0, b1 = wall_a + inset, wall_a + inset + depth

        a = a0 + spacing * 0.5
        while a + spacing * 0.5 <= a1:
            c0, c1 = a - 20.0, a + 20.0
            if horizontal:
                add_brush(c0, b0, z0, c1, b1, z1, tex_sides=tex, face_tex={face: tex})
            else:
                add_brush(b0, c0, z0, b1, c1, z1, tex_sides=tex, face_tex={face: tex})
            a += spacing

    def add_street_lamp(x, y, z=0.0):
        """Farola de calle: poste + luminaria de luz calida."""
        add_column(x, y, z, z + 190, size=8.0, tex="m_metal_darkBlu")
        add_brush(x - 16, y - 16, z + 190, x + 16, y + 16, z + 204, tex_sides="br_lightGL", tex_top="m_metal_darkBlu", tex_bottom="br_lightGL")
        add_light(x, y, z + 176, 235, 205, 150, 340)

    def add_ceiling_lamp(x, y, z, tex="br_lightGL"):
        """Luminaria de techo empotrada."""
        add_brush(x - 26, y - 26, z, x + 26, y + 26, z + 8, tex_sides=tex, tex_top="conc_road_D2", tex_bottom=tex)
        add_light(x, y, z - 24, 225, 210, 168, 300)

    def add_truss(x0, y0, x1, y1, z, tex="metal_stB"):
        """Cercha metalica de refuerzo en tejados y marquesinas."""
        add_beam(x0, y0, x1, y1, z, z + 12, tex)

    # Luz de luna difusa: rejilla de focos muy suaves en altura que evita que
    # las zonas exteriores alejadas de las estaciones queden completamente negras.
    for mx in range(-2100, 2400, 700):
        for my in range(-2100, 2400, 700):
            add_light(mx, my, 420, 78, 88, 108, 1000)

    # ------------------------------------------------------------------------
    # 0. CAJA DE CIELO SELLADA Y SUELO BASE DE GREEN RUN
    # ------------------------------------------------------------------------
    # Suelo general en Z=[-64..0] (tierra/maleza de Green Run)
    add_brush(-2400, -2400, -64, 2400, 2400, 0, tex_sides="ground_dirt", tex_top="ground_dirt", tex_bottom="ground_dirt")
    # Techo de cielo nocturno en Z=[512..544]
    add_brush(-2400, -2400, 512, 2400, 2400, 544, tex_sides="sky_night", tex_top="sky_night", tex_bottom="sky_night")
    # 4 muros perimetrales exteriores (Z=[0..512])
    add_brush(-2464, -2464, -64, -2400, 2464, 544, tex_sides="con_rN")
    add_brush(2400, -2464, -64, 2464, 2464, 544, tex_sides="con_rN")
    add_brush(-2400, -2464, -64, 2400, -2400, 544, tex_sides="con_rN")
    add_brush(-2400, 2400, -64, 2400, 2464, 544, tex_sides="con_rN")

    # ------------------------------------------------------------------------
    # BLOQUES SEPARADORES DE VISIBILIDAD (COLINAS / BOSQUE / ROCA)
    # Crean el anillo de carretera y el pasillo del maizal hacia Nacht
    # ------------------------------------------------------------------------
    # Cuadrante Suroeste interior (entre Bus Depot, Tunnel, Town y Nacht)
    add_brush(-1150, -1150, 0, -350, -250, 384, tex_sides="con_rN", tex_top="roof_green")
    # Cuadrante Noroeste interior (entre Tunnel, Diner y Maizal)
    add_brush(-1150, -150, 0, -350, 1150, 384, tex_sides="corn_wall", tex_top="roof_green")
    # Cuadrante Noreste interior (entre Diner, Farm y Maizal)
    add_brush(350, -150, 0, 1150, 1150, 384, tex_sides="corn_wall", tex_top="roof_green")
    # Cuadrante Sureste interior (entre Farm, Power Station, Town y Nacht)
    add_brush(350, -1150, 0, 1150, -250, 384, tex_sides="con_rN", tex_top="roof_green")
    # Muro sur de Nacht (separa Nacht de la carretera sur de Town, obligando a entrar por el Norte del maizal)
    add_brush(-350, -1150, 0, 350, -450, 384, tex_sides="corn_wall", tex_top="roof_green")

    # ------------------------------------------------------------------------
    # 0.B RED VIARIA DE GREEN RUN: CALZADAS, ACERAS, BORDILLOS Y PASOS
    # ------------------------------------------------------------------------
    # Anillo de autopista por el que circula el autobus de Tranzit:
    #   Tramo Oeste  : X[-1550..-1220], Y[-1800..2400]
    #   Tramo Norte  : Y[1020..1420],   X[-1550..1620]
    #   Tramo Este   : X[1020..1420],   Y[-1800..1420]
    #   Tramo Sur    : Y[-1800..-1500], X[-1550..1420]
    add_road(-1550, -1800, -1220, 2400, "road_stripe")
    add_road(-1550, 1020, 1620, 1420, "road_stripe")
    add_road(1020, -1800, 1420, 1420, "road_stripe")
    add_road(-1550, -1800, 1420, -1500, "road_stripe")

    # Aceras y bordillos a ambos lados de cada tramo de autopista
    add_walk(-1620, -1800, -1550, 2400)     # Bordillo oeste del tramo oeste
    add_walk(-1220, -1800, -1150, 2400)     # Bordillo este del tramo oeste
    add_walk(-1620, 1420, 1620, 1490)       # Bordillo norte del tramo norte
    add_walk(-1620, 950, 1620, 1020)        # Bordillo sur del tramo norte
    add_walk(1420, -1800, 1490, 1420)       # Bordillo este del tramo este
    add_walk(950, -1800, 1020, 1420)        # Bordillo oeste del tramo este
    add_walk(-1550, -1870, -650, -1800)     # Bordillo sur del tramo sur (oeste)
    add_walk(650, -1870, 1420, -1800)       # Bordillo sur del tramo sur (este)
    add_walk(-650, -1820, 650, -1800)       # Aceron delante del Banco
    add_walk(-1550, -1500, -550, -1480)     # Bordillo norte del tramo sur (oeste)
    add_walk(250, -1500, 1420, -1480)       # Bordillo norte del tramo sur (este)
    add_walk(-550, -1500, 250, -1480)       # Aceron delante del Bar

    # Pasos de peatones en las 4 esquinas del circuito
    for (px, py, horiz) in (
        (-1400, -1790, True), (-1400, -1440, True),
        (1120, -1790, True), (1120, -1440, True),
        (-1300, 1150, False), (-1000, 1150, False),
        (1100, 1150, False), (1400, 1150, False),
    ):
        if horiz:
            for i in range(6):
                add_road(px - 120 + i * 44, py - 30, px - 120 + i * 44 + 26, py + 30, "sidewalk")
        else:
            for i in range(6):
                add_road(px - 30, py - 120 + i * 44, px + 30, py - 120 + i * 44 + 26, "sidewalk")

    # Plataformas de hormigon de las paradas del autobus (8 paradas), encaradas
    # al lateral sur del vehiculo para poder subir andando (desnivel de 6u).
    for (sx, sy) in (
        (-1500, -1720), (-1500, 200), (-1500, 1240),
        (0, 1240), (1240, 1240), (1240, -180), (1240, -1720), (0, -1720),
    ):
        add_walk(sx - 40, sy - 104, sx + 200, sy, top=6.0)

    # ========================================================================
    # ESTACION 1: BUS DEPOT (SUR-OESTE: X[-2300..-1150], Y[-2300..-1150])
    # ========================================================================
    # Edificio del Bus Depot: X[-2250..-1550], Y[-2200..-1550], Z[0..208]
    # Techo del Depot
    add_brush(-2250, -2200, 208, -1550, -1550, 232, tex_sides="bricks_ak_white", tex_top="roof_green", tex_bottom="ceilings_te1")
    # Pared Oeste (con hueco de ventana barricada en Y[-1920..-1824])
    add_brush(-2250, -2200, 0, -2218, -1920, 208, tex_sides="bricks_ak_white")
    add_brush(-2250, -1920, 0, -2218, -1824, 36, tex_sides="bricks_ak_white")
    add_brush(-2250, -1920, 128, -2218, -1824, 208, tex_sides="bricks_ak_white")
    add_brush(-2250, -1824, 0, -2218, -1550, 208, tex_sides="bricks_ak_white")
    # Pared Sur (con hueco de ventana barricada en X[-1960..-1864])
    add_brush(-2218, -2200, 0, -1960, -2168, 208, tex_sides="bricks_ak_white")
    add_brush(-1960, -2200, 0, -1864, -2168, 36, tex_sides="bricks_ak_white")
    add_brush(-1960, -2200, 128, -1864, -2168, 208, tex_sides="bricks_ak_white")
    add_brush(-1864, -2200, 0, -1550, -2168, 208, tex_sides="bricks_ak_white")
    # Pared Norte del Depot
    add_brush(-2250, -1582, 0, -1550, -1550, 208, tex_sides="bricks_ak_white")
    # Pared Este del Depot (con puerta comprable en Y[-1940..-1780])
    add_brush(-1582, -2168, 0, -1550, -1940, 208, tex_sides="wall_br_red")
    add_brush(-1582, -1940, 160, -1550, -1780, 208, tex_sides="wall_br_red")
    add_brush(-1582, -1780, 0, -1550, -1582, 208, tex_sides="wall_br_red")
    # Mostrador de billetes interior
    add_brush(-2100, -1720, 0, -1920, -1670, 44, tex_sides="w_wood_dark_64", tex_top="wood_t1")

    # Puerta comprable del Bus Depot (750 pts)
    add_buyable_door("door_depot", "z_depot_ext", 750, -1578, -1940, 0, -1554, -1780, 160, tex="doors")

    # Spawns de jugadores dentro de Bus Depot
    #
    # ALTURA DEL SUELO MEDIDA EN EL BSP COMPilado: la loseta del Bus Depot
    # tiene la cara superior en Z=2 y el techo de la nave esta en Z=208, asi
    # que el hueco libre va de Z=2 a Z=208. El jugador mide 72u (mins -32,
    # maxs +40) y apoya con el ORIGEN en Z=34 (suelo + 32).
    #
    # Se coloca el spawn en Z=96, muy por encima del suelo (Z=2) y con 112u
    # libres hasta el techo: asi el jugador nace SIEMPRE dentro del volumen
    # jugable y, si el droptofloor del motor falla, aun asi cae de pie sobre
    # el suelo en vez de aparecer por debajo de la loseta.
    add_ent("info_player_start", (-1950, -1880, 96), angles="0 0 0")
    add_ent("info_player_1_spawn", (-1950, -1880, 96), angles="0 0 0")
    add_ent("info_player_2_spawn", (-1950, -1980, 96), angles="0 15 0")
    add_ent("info_player_3_spawn", (-2060, -1880, 96), angles="0 0 0")
    add_ent("info_player_4_spawn", (-2060, -1980, 96), angles="0 20 0")

    # Quick Revive en Bus Depot
    add_ent(
        "perk_revive", (-2175, -1635, 36),
        angles="0 270 0", spawnflags="8", cost="500", cost2="1500",
        oldmodel="sounds/machines/perk_drink.wav",
        weapon2model="models/machines/v_perk.mdl",
        weapon2_animduration="22",
        model="models/machines/quake_scale/quick_revive.mdl"
    )
    # Armas iniciales en Bus Depot: Gewehr/M14 (10) y Olympia/DB (8)
    add_wall_weapon("ww_depot_m14", (-1720, -2162, 58), 90, 10, 500, 250, -1752, -2168, 16, -1688, -2136, 96)
    add_wall_weapon("ww_depot_db", (-1720, -1588, 58), 270, 8, 500, 250, -1752, -1614, 16, -1688, -1582, 96)

    # Barricadas de Ronda 1 y sus spawners en Bus Depot
    add_ent("item_barricade", (-2222, -1872, 36), targetname="bar_depot_w", angles="0 0 0", model="models/misc/window.mdl", oldmodel="sounds/misc/barricade.wav", aistatus="sounds/misc/barricade_destroy.wav")
    add_ent("path_corner", (-2290, -1872, 36), targetname="pc_depot_w", target="bar_depot_w")
    add_ent("spawn_zombie", (-2345, -1872, 40), angles="0 0 0", target="pc_depot_w", spawnflags="0")

    add_ent("item_barricade", (-1912, -2172, 36), targetname="bar_depot_s", angles="0 90 0", model="models/misc/window.mdl", oldmodel="sounds/misc/barricade.wav", aistatus="sounds/misc/barricade_destroy.wav")
    add_ent("path_corner", (-1912, -2250, 36), targetname="pc_depot_s", target="bar_depot_s")
    add_ent("spawn_zombie", (-1912, -2320, 40), angles="0 90 0", target="pc_depot_s", spawnflags="0")

    # Spawner interior de suelo en Bus Depot (garantiza accion inmediata desde Ronda 1)
    add_ent("spawn_zombie", (-2150, -2100, 40), angles="0 45 0", spawnflags="4")

    # Explanada exterior de Bus Depot: Mystery Box inicial, grieta de lava y Farola Verde #1
    add_ent("mystery_box", (-1480, -2120, 36), angles="0 90 0", spawnflags="2")
    add_lava_pit(-1420, -1740, -1260, -1520)
    add_ent("tranzit_lamp_tp", (-1480, -1460, 40), targetname="lamp_depot", target="lamp_diner", message="DINER & GARAGE")
    add_light(-1480, -1460, 110, 60, 255, 90, 320)
    add_light(-1880, -1880, 175, 255, 210, 145, 480)
    add_light(-1320, -1880, 190, 210, 170, 120, 450)

    # --- Detalle arquitectonico del Bus Depot -------------------------------
    # Solado interior de la terminal (baldosas grises)
    add_floor(-2222, -2172, -1578, -1578, "3tiles_grey1")
    # Andenes de hormigon y vias de los autobuses al sur de la parada del bus
    add_walk(-1550, -2180, -1240, -1900, top=8.0)
    for by in (-2120, -2040, -1960):
        add_rail(-1540, by - 6, -1250, by + 6, 8, 22, "conc_road_D2")
    # Marquesina de la entrada este (X[-1582..-1380], Y[-1990..-1730])
    add_beam(-1582, -1990, -1380, -1730, 176, 192, "conS6C")
    add_column(-1440, -1966, 0, 176, size=14.0, tex="metal_stB")
    add_column(-1440, -1754, 0, 176, size=14.0, tex="metal_stB")
    add_column(-1568, -1972, 0, 176, size=12.0, tex="metal_stB")
    add_column(-1568, -1748, 0, 176, size=12.0, tex="metal_stB")
    # Rotulos BUS DEPOT en las fachadas este y sur (por encima del tejado)
    add_brush(-1556, -1960, 234, -1540, -1810, 266,
              tex_sides="conc_road_D2", face_tex={"-x": "sign_depot", "+x": "sign_depot"})
    add_brush(-2080, -2216, 234, -1930, -2200, 266,
              tex_sides="conc_road_D2", face_tex={"-y": "sign_depot", "+y": "sign_depot"})
    # Taquilla de billetes: mampara de cristal sobre el mostrador
    add_brush(-2100, -1694, 44, -1920, -1678, 132, tex_sides="g_glass_", tex_top="m_metal_stG", tex_bottom="g_glass_")
    add_beam(-2104, -1698, -1916, -1674, 132, 140, "w_wood_dark_64")
    # Panel de salidas en la pared norte del vestibulo
    add_brush(-2200, -1590, 96, -2060, -1578, 168, tex_sides="metal_stB", face_tex={"-y": "sign_depot"})
    # Bancos de la sala de espera (2 filas de 2 bancos)
    for bx in (-2180, -2060):
        for by in (-2060, -1960):
            add_brush(bx, by, 0, bx + 120, by + 34, 20, tex_sides="w_wood_dark_64", tex_top="wood_t1", tex_bottom="w_wood_dark_64")
            add_brush(bx, by + 34, 0, bx + 120, by + 42, 52, tex_sides="w_wood_dark_64")
    # Luminarias empotradas en el techo de la terminal
    for lx in (-2140, -1980, -1820, -1660):
        for ly in (-2100, -1900, -1700):
            add_ceiling_lamp(lx, ly, 200)
    # Ventanillas de cristal en las fachadas oeste (x=-2218) y sur (y=-2168)
    add_window_row(-2214, -1590, 60, 120, "+y", -2168, spacing=150.0)
    add_window_row(-2164, -1590, 60, 120, "+x", -2218, spacing=150.0)
    # Pilares de ladrillo del portico de la entrada este
    for py in (-2000, -1720):
        add_column(-1520, py, 0, 208, size=16.0, tex="brick_pillar")

    # Spawners exteriores de Bus Depot (se desbloquean al abrir door_depot)
    add_ent("spawn_zombie", (-1250, -2150, 40), targetname="z_depot_ext", spawnflags="5")
    add_ent("spawn_zombie", (-1250, -1350, 40), targetname="z_depot_ext", spawnflags="5")

    # ========================================================================
    # SECTOR 1.5: HIGHWAY TUNNEL (OESTE: X[-2250..-1150], Y[-950..+850])
    # ========================================================================
    # Muro oeste y techo abovedado del Tunel (Y[-650..+550])
    add_brush(-2250, -650, 0, -2150, 550, 280, tex_sides="conc_road_D2")
    add_brush(-2250, -650, 240, -1150, 550, 280, tex_sides="conc_road_D2", tex_top="roof_green", tex_bottom="con_linesB")
    # Pilares centrales divisorios del Tunel
    for py in (-450, -150, 150, 400):
        add_brush(-1720, py - 40, 0, -1660, py + 40, 240, tex_sides="con_linesB")
    # Grieta de lava en el Tunel
    add_lava_pit(-2050, -60, -1740, 80)
    # --- Detalle arquitectonico del Highway Tunnel --------------------------
    # Solado de asfalto continuo por el interior y las bocas del tunel
    add_floor(-2250, -900, -1150, 900, "asphalt_line")
    # Aceras y bordillos a ambos lados de la calzada del tunel
    add_walk(-2150, -900, -2070, 900)
    add_walk(-1230, -900, -1150, 900)
    # Alicatado blanco de tunel en el zocalo del muro oeste
    add_brush(-2150, -650, 0, -2140, 550, 104, tex_sides="tunnel_tile", face_tex={"+x": "tunnel_tile"})
    # Cornisa de hormigon sobre el alicatado
    add_brush(-2150, -650, 104, -2144, 550, 116, tex_sides="con_linesB", face_tex={"+x": "con_linesB"})
    # Barandillas metalicas sobre las aceras
    add_rail(-2140, -900, -2140, 900, 5, 42, "metal_grate")
    add_rail(-1160, -900, -1160, 900, 5, 42, "metal_grate")
    # Porticos de hormigon en las dos bocas del tunel (arco escalonado)
    for (pya, pyb, face) in ((-700, -650, "+y"), (550, 600, "-y")):
        add_brush(-2250, pya, 0, -2150, pyb, 280, tex_sides="con_linesB")      # Pilar oeste
        add_brush(-1250, pya, 0, -1150, pyb, 280, tex_sides="con_linesB")      # Pilar este
        add_brush(-2250, pya, 280, -1150, pyb, 320, tex_sides="con_linesB")    # Dintel superior
        add_brush(-2180, pya, 240, -1220, pyb, 280, tex_sides="con_linesB")    # Arco escalon 1
        add_brush(-2120, pya, 200, -1280, pyb, 240, tex_sides="con_linesB")    # Arco escalon 2
    # Rotulo del tunel sobre la boca sur
    add_brush(-2080, -716, 322, -1820, -700, 356, tex_sides="conc_road_D2",
              face_tex={"-y": "sign_tunnel", "+y": "sign_tunnel"})
    # Tiras de luminarias en el techo del tunel
    for ly in range(-560, 520, 160):
        add_brush(-2200, ly - 8, 236, -1200, ly + 8, 244,
                  tex_sides="br_lightGL", tex_top="conc_road_D2", tex_bottom="br_lightGL")
        add_light(-1700, ly, 214, 250, 226, 178, 420)
    # Barreras New Jersey continuas que separan los dos carriles del tunel
    for bx in (-2050, -1850):
        add_brush(bx - 10, -620, 0, bx + 10, 520, 30, tex_sides="con_linesB",
                  tex_top="con_linesB", tex_bottom="con_linesB")
    add_light(-1500, -560, 200, 210, 190, 170, 400)
    add_light(-1500, 460, 200, 210, 190, 170, 400)
    add_light(-1900, 600, 120, 190, 175, 150, 380)
    add_light(-1300, -750, 120, 190, 175, 150, 380)

    # Arma de pared del Tunel: M16 / STG-44 (weapon=6, coste 1200)
    add_wall_weapon("ww_tunnel_stg", (-2142, 0, 58), 0, 6, 1200, 600, -2150, -32, 16, -2112, 32, 96)
    add_light(-1850, -300, 210, 255, 165, 95, 460)
    add_light(-1850, 250, 210, 255, 165, 95, 460)
    add_ent("spawn_zombie", (-1980, -480, 40), targetname="z_depot_ext", spawnflags="5")
    add_ent("spawn_zombie", (-1980, 380, 40), targetname="z_depot_ext", spawnflags="5")

    # ========================================================================
    # ESTACION 2: DINER & GARAGE (NOROESTE: X[-2250..-450], Y[+1150..+2300])
    # ========================================================================
    # Marquesina de la Gasolinera (Canopy): X[-1450..-950], Y[+1280..+1520], Z[192..216]
    add_brush(-1450, 1280, 192, -950, 1520, 216, tex_sides="metal_stB", tex_top="roof_green", tex_bottom="ceilings_sign5")
    add_brush(-1410, 1380, 0, -1370, 1420, 192, tex_sides="metal_stB")
    add_brush(-1030, 1380, 0, -990, 1420, 192, tex_sides="metal_stB")

    # Edificio del Restaurante DINER: X[-1550..-700], Y[+1680..+2250], Z[0..208]
    add_brush(-1550, 1680, 208, -700, 2250, 232, tex_sides="bricks_red2", tex_top="roof_green", tex_bottom="ceilings_te1")
    add_brush(-1550, 2218, 0, -700, 2250, 208, tex_sides="bricks_red2")  # Norte
    add_brush(-732, 1680, 0, -700, 2218, 208, tex_sides="bricks_red2")   # Este
    add_brush(-1550, 1680, 0, -1518, 2218, 208, tex_sides="bricks_red2") # Oeste
    # Fachada Sur del Diner con puerta comprable en X[-1200..-1040]
    add_brush(-1518, 1680, 0, -1200, 1712, 208, tex_sides="wall_br_red")
    add_brush(-1200, 1680, 160, -1040, 1712, 208, tex_sides="wall_br_red")
    add_brush(-1040, 1680, 0, -732, 1712, 208, tex_sides="wall_br_red")
    # Barra del Diner
    add_brush(-1380, 1960, 0, -900, 2010, 42, tex_sides="3tiles_grey1", tex_top="t_floor_blue")

    add_buyable_door("door_diner", "z_diner", 750, -1200, 1684, 0, -1040, 1708, 160, tex="doors")

    # Speed Cola dentro del Diner + MP5K (weapon=29)
    add_ent(
        "perk_speed", (-820, 2140, 36),
        angles="0 180 0", spawnflags="4", cost="3000",
        oldmodel="sounds/machines/perk_drink.wav",
        weapon2model="models/machines/v_perk.mdl",
        weapon2_animduration="22",
        model="models/machines/quake_scale/speed_cola.mdl"
    )
    add_wall_weapon("ww_diner_mp5", (-1490, 1950, 58), 0, 29, 1000, 500, -1518, 1918, 16, -1480, 1982, 96)

    # Taller / Garaje del Diner: X[-2250..-1680], Y[+1600..+2220], Z[0..216]
    add_brush(-2250, 1600, 216, -1680, 2220, 240, tex_sides="conS6C", tex_top="roof_green", tex_bottom="metal_floor")
    add_brush(-2250, 2188, 0, -1680, 2220, 216, tex_sides="conS6C")
    add_brush(-2250, 1600, 0, -2218, 2188, 216, tex_sides="conS6C")
    add_brush(-1712, 1600, 0, -1680, 2188, 216, tex_sides="conS6C")
    # Fachada Sur del Garaje con puerta comprable en X[-2040..-1880]
    add_brush(-2218, 1600, 0, -2040, 1632, 216, tex_sides="conS6C")
    add_brush(-2040, 1600, 160, -1880, 1632, 216, tex_sides="conS6C")
    add_brush(-1880, 1600, 0, -1712, 1632, 216, tex_sides="conS6C")

    add_buyable_door("door_garage", "z_diner", 750, -2040, 1604, 0, -1880, 1628, 160, tex="ver_metal_door")
    add_ent("mystery_box_tp_spot", (-1960, 2100, 36), angles="0 270 0")
    add_wall_weapon("ww_garage_trench", (-2210, 1900, 58), 0, 23, 1500, 750, -2218, 1868, 16, -2180, 1932, 96)

    # Farola Verde #2 en Diner + grieta de lava + luces + spawners
    add_ent("tranzit_lamp_tp", (-1580, 1380, 40), targetname="lamp_diner", target="lamp_farm", message="FARM")
    add_light(-1580, 1380, 110, 60, 255, 90, 320)
    add_lava_pit(-700, 1260, -480, 1480)
    add_light(-1120, 1920, 180, 180, 235, 255, 480)
    add_light(-1960, 1900, 180, 255, 200, 140, 440)
    add_light(-1200, 1400, 175, 255, 220, 160, 500)
    # --- Detalle arquitectonico del Diner -----------------------------------
    # Suelo de damero blanco y negro tipico de los diners americanos
    add_floor(-1522, 1716, -728, 2222, "diner_tile")
    # Escalones de acceso a la puerta principal del Diner
    add_floor(-1240, 1640, -1000, 1712, "sidewalk", 0.0, 5.0)
    # Reservados (booths) a lo largo del muro oeste
    for by in (1800, 1900, 2000, 2100):
        add_brush(-1518, by - 40, 0, -1440, by + 40, 20, tex_sides="carpet_64_red", tex_top="carpet_64_red", tex_bottom="carpet_64_red")
        add_brush(-1518, by - 40, 20, -1506, by + 40, 78, tex_sides="carpet_64_red")
        add_brush(-1470, by - 40, 0, -1410, by + 40, 20, tex_sides="carpet_64_red")
        add_brush(-1470, by - 40, 20, -1458, by + 40, 78, tex_sides="carpet_64_red")
        add_brush(-1470, by - 30, 40, -1410, by + 30, 46, tex_sides="w_wood_dark_64", tex_top="wood_t1", tex_bottom="w_wood_dark_64")
    # Barra americana: sobre elevado, repisa trasera y cafetera
    add_brush(-1380, 1960, 42, -900, 2010, 48, tex_sides="m_metal_stG", tex_top="t_floor_blue", tex_bottom="m_metal_stG")
    add_brush(-1380, 2010, 0, -900, 2030, 132, tex_sides="w_wood_dark_64", tex_top="wood_t1", tex_bottom="w_wood_dark_64")
    add_brush(-1360, 2030, 132, -920, 2038, 152, tex_sides="metal_stB")
    # Ventanas iluminadas en la fachada sur y oeste del Diner
    add_window_row(-1500, -760, 64, 128, "+y", -1712, spacing=110.0)
    add_window_row(-2120, -1770, 64, 128, "+x", -1518, spacing=110.0)
    # Luminarias de techo del comedor
    for lx in (-1420, -1250, -1080, -910):
        for ly in (1830, 1970, 2110):
            add_ceiling_lamp(lx, ly, 200)
    # Gran rotulo DINER en el tejado (visto desde la carretera sur)
    add_brush(-1400, 1664, 234, -1000, 1680, 268, tex_sides="conc_road_D2",
              face_tex={"-y": "sign_diner", "+y": "sign_diner"})
    add_brush(-1420, 1660, 268, -980, 2252, 276, tex_sides="metal_stB", tex_top="shingle_roof", tex_bottom="metal_stB")

    # --- Gasolinera bajo la marquesina --------------------------------------
    # Islas con surtidores (por encima del techo del autobus: Z<=148)
    for px in (-1300, -1060):
        add_brush(px - 60, 1400, 0, px + 60, 1500, 6, tex_sides="sidewalk", tex_top="sidewalk", tex_bottom="sidewalk")
        add_brush(px - 26, 1418, 6, px + 26, 1482, 56, tex_sides="metal_stB", tex_top="m_metal_stG", tex_bottom="metal_stB")
        add_brush(px - 30, 1410, 6, px + 30, 1418, 12, tex_sides="danger_stripe", tex_top="danger_stripe", tex_bottom="danger_stripe")
        add_brush(px - 30, 1482, 6, px + 30, 1490, 12, tex_sides="danger_stripe", tex_top="danger_stripe", tex_bottom="danger_stripe")
        add_light(px, 1450, 74, 230, 210, 160, 260)
    # Rotulo de la gasolinera en el faldon sur de la marquesina
    add_brush(-1430, 1264, 200, -1000, 1280, 232, tex_sides="metal_stB",
              face_tex={"-y": "sign_diner", "+y": "sign_diner"})
    # Pilares adicionales de la marquesina (4 en total)
    add_column(-1300, 1340, 0, 192, size=12.0, tex="metal_stB")
    add_column(-1060, 1340, 0, 192, size=12.0, tex="metal_stB")
    # Luminarias bajo la marquesina
    for lx in (-1300, -1150, -1000):
        add_ceiling_lamp(lx, 1450, 208)

    # --- Taller / Garaje del Diner ------------------------------------------
    add_floor(-2222, 1636, -1708, 2192, "metal_grate")
    # Persiana enrollable sobre el hueco de la puerta del garaje
    add_brush(-2040, 1600, 160, -1880, 1632, 204, tex_sides="conS6C",
              face_tex={"-y": "garage_door", "+y": "garage_door"})
    add_brush(-2040, 1600, 204, -1880, 1632, 216, tex_sides="conS6C")
    # Banco de trabajo, armarios de herramientas y estanterias
    add_brush(-2218, 1700, 0, -2060, 1750, 40, tex_sides="metal_stB", tex_top="metal_stB", tex_bottom="metal_stB")
    add_brush(-2218, 1770, 0, -2060, 1820, 40, tex_sides="metal_stB", tex_top="metal_stB", tex_bottom="metal_stB")
    add_brush(-2218, 1840, 0, -2060, 1890, 40, tex_sides="metal_stB", tex_top="metal_stB", tex_bottom="metal_stB")
    add_brush(-2050, 2000, 0, -1960, 2180, 96, tex_sides="metal_stB", tex_top="metal_stB", tex_bottom="metal_stB")
    add_brush(-1900, 2000, 0, -1810, 2180, 96, tex_sides="metal_stB", tex_top="metal_stB", tex_bottom="metal_stB")
    # Pilas de neumaticos apilados
    for tx in (-2180, -2140, -2100):
        for tz in (0, 14, 28):
            add_brush(tx - 18, 2100 - 18, tz, tx + 18, 2100 + 18, tz + 14,
                      tex_sides="m_metal_darkBlu", tex_top="m_metal_darkBlu", tex_bottom="m_metal_darkBlu")
    # Luminarias industriales del taller
    for lx in (-2150, -1980, -1810):
        for ly in (1720, 1900, 2080):
            add_ceiling_lamp(lx, ly, 208)
    # Zocalo de ladrillo visto, estanterias y elevador de vehiculos
    add_brush(-2218, 1636, 0, -2210, 2184, 96, tex_sides="brick_pillar", face_tex={"+x": "brick_pillar"})
    add_brush(-1716, 1636, 0, -1708, 2184, 96, tex_sides="brick_pillar", face_tex={"-x": "brick_pillar"})
    add_brush(-2210, 2184, 0, -1708, 2176, 96, tex_sides="brick_pillar", face_tex={"-y": "brick_pillar"})
    for shz in (0, 48, 96, 144):
        add_beam(-2214, 2050, -2060, 2060, shz, shz + 8, "w_wood_dark_64")
    add_brush(-2050, 2050, 0, -1960, 2060, 96, tex_sides="w_wood_dark_64", tex_top="wood_t1", tex_bottom="w_wood_dark_64")
    add_brush(-1900, 2050, 0, -1810, 2060, 96, tex_sides="w_wood_dark_64", tex_top="wood_t1", tex_bottom="w_wood_dark_64")
    # Elevador hidraulico de dos columnas
    for lx2 in (-1900, -1780):
        add_brush(lx2 - 14, 1660, 0, lx2 + 14, 1688, 200, tex_sides="metal_stB", tex_top="m_metal_stG", tex_bottom="metal_stB")
    add_beam(-1914, 1652, -1766, 1696, 196, 212, "danger_stripe")
    # Cartel de peligro y extintor en la pared del taller
    add_brush(-2210, 1750, 120, -2202, 1830, 168, tex_sides="danger_stripe", face_tex={"+x": "danger_stripe"})
    add_light(-1900, 1850, 120, 210, 200, 170, 320)
    # Rotulo GARAGE en la fachada sur del taller
    add_brush(-2200, 1584, 218, -1900, 1600, 252, tex_sides="conS6C",
              face_tex={"-y": "sign_garage", "+y": "sign_garage"})
    add_light(-1120, 1600, 150, 200, 185, 165, 340)
    add_light(-1700, 1560, 150, 190, 180, 160, 340)

    add_ent("spawn_zombie", (-1120, 2120, 40), targetname="z_diner", spawnflags="5")
    add_ent("spawn_zombie", (-1960, 2040, 40), targetname="z_diner", spawnflags="5")
    add_ent("spawn_zombie", (-1620, 1250, 40), targetname="z_depot_ext", spawnflags="5")

    # ========================================================================
    # SECTOR CENTRAL (MAIZAL): RUINAS DE NACHT DER UNTOTEN (X[-350..+350], Y[-450..+1150])
    # ========================================================================
    # Bunker Nacht en el claro central X[-260..+260], Y[-380..+120], Z[0..192]
    add_brush(-260, -380, 192, 260, 120, 216, tex_sides="con_rN", tex_top="con_rN", tex_bottom="con_rN")
    add_brush(-260, -380, 0, 260, -348, 192, tex_sides="con_rN")
    add_brush(-260, -348, 0, -228, 120, 192, tex_sides="con_rN")
    add_brush(228, -348, 0, 260, 120, 192, tex_sides="con_rN")
    # Entrada abierta por el Norte con dos pilares de hormigon
    add_brush(-228, 88, 0, -80, 120, 192, tex_sides="con_rN")
    add_brush(80, 88, 0, 228, 120, 192, tex_sides="con_rN")
    # Armas en Nacht Bunker: Kar98k Scoped (11) y Sawed-Off (21)
    add_wall_weapon("ww_nacht_sniper", (-220, -140, 58), 0, 11, 1500, 750, -228, -172, 16, -190, -108, 96)
    add_wall_weapon("ww_nacht_sawnoff", (220, -140, 58), 180, 21, 1200, 600, 190, -172, 16, 228, -108, 96)
    add_light(0, -130, 160, 220, 195, 150, 420)

    # --- Detalle del maizal y del bunker de Nacht ---------------------------
    # Sendero de tierra batida que cruza el maizal de sur a norte
    add_floor(-70, -460, 70, 1150, "debris")
    # Seis hileras densas de maiz (muros de 16u x 96u de alto) a cada lado
    for cxr in (-300, -180, -60, 60, 180, 300):
        add_brush(cxr - 8, 170, 0, cxr + 8, 1140, 96,
                  tex_sides="corn_wall", tex_top="corn_wall", tex_bottom="corn_wall")
    # Espolones transversales que rompen la monotonia del maizal
    for (sx, sy) in ((-230, 380), (-110, 760), (110, 300), (230, 900), (-240, 1020), (240, 560)):
        add_brush(sx - 60, sy - 8, 0, sx + 60, sy + 8, 96,
                  tex_sides="corn_wall", tex_top="corn_wall", tex_bottom="corn_wall")
    # Farolas de sendero (postes de luz calida) para no dejar el maizal a oscuras
    for ly in (-200, 120, 440, 760, 1040):
        add_street_lamp(-56, ly)
        add_light(0, ly, 120, 150, 165, 120, 300)
    # Escombros y cascotes alrededor del bunker
    for (rx, ry, rs) in ((-180, 200, 40), (200, 240, 34), (-300, -60, 30), (300, 40, 38), (-150, -300, 26)):
        add_brush(rx - rs, ry - rs, 0, rx + rs, ry + rs, 8 + rs // 4,
                  tex_sides="debris", tex_top="debris", tex_bottom="debris")
    # Rotulo NACHT sobre la entrada norte del bunker
    add_floor(-228, -348, 228, 120, "debris")
    add_brush(-140, 124, 218, 140, 140, 252, tex_sides="con_rN",
              face_tex={"+y": "sign_nacht", "-y": "sign_nacht"})
    # Ventanas enrejadas en las fachadas laterales del bunker
    add_window_row(-320, 80, 64, 120, "+x", -228, spacing=110.0, tex="der_riese_windo")
    add_window_row(-320, 80, 64, 120, "-x", 228, spacing=110.0, tex="der_riese_windo")
    # Antena de radio en el tejado del bunker
    add_column(200, -300, 216, 360, size=8.0, tex="m_metal_darkBlu")
    add_brush(184, -308, 300, 216, -292, 306, tex_sides="m_metal_darkBlu")
    add_light(200, -300, 330, 220, 60, 50, 220)
    # Cajas de munición y sacos terreros apilados en el interior
    add_brush(-210, -330, 0, -170, -290, 32, tex_sides="w_wood_dark_64", tex_top="w_wood_dark_64", tex_bottom="w_wood_dark_64")
    add_brush(-150, -340, 0, -110, -300, 32, tex_sides="w_wood_dark_64", tex_top="w_wood_dark_64", tex_bottom="w_wood_dark_64")
    add_brush(-150, -250, 0, -110, -210, 32, tex_sides="w_wood_dark_64", tex_top="w_wood_dark_64", tex_bottom="w_wood_dark_64")
    add_brush(150, -330, 0, 190, -290, 32, tex_sides="w_wood_dark_64", tex_top="w_wood_dark_64", tex_bottom="w_wood_dark_64")
    add_light(0, -60, 150, 170, 160, 130, 360)

    add_ent("spawn_zombie", (0, -260, 40), targetname="z_depot_ext", spawnflags="5")

    # ========================================================================
    # ESTACION 3: FARM (GRANJA - NORESTE: X[+1150..+2300], Y[+850..+2300])
    # ========================================================================
    # Muro y verja de entrada a la Granja a lo largo de X=1520 (Y[+850..+2250])
    add_brush(1500, 850, 0, 1532, 1380, 160, tex_sides="wall_Owood3")
    add_brush(1500, 1380, 152, 1532, 1540, 192, tex_sides="wall_Owood3")
    add_brush(1500, 1540, 0, 1532, 2250, 160, tex_sides="wall_Owood3")
    add_buyable_door("door_farm", "z_farm", 750, 1504, 1380, 0, 1528, 1540, 152, tex="old_doors")

    # El Granero Rojo (Barn): X[+1650..+2250], Y[+900..+1420], Z[0..240]
    add_brush(1650, 900, 240, 2250, 1420, 264, tex_sides="bricks_red2", tex_top="roof_green", tex_bottom="w_wood_dark_64")
    add_brush(1650, 900, 0, 2250, 932, 240, tex_sides="bricks_red2")
    add_brush(2218, 932, 0, 2250, 1420, 240, tex_sides="bricks_red2")
    add_brush(1650, 1388, 0, 2218, 1420, 240, tex_sides="bricks_red2")
    # Frente oeste del Granero con gran porton abierto en Y[+1060..+1260]
    add_brush(1650, 932, 0, 1682, 1060, 240, tex_sides="bricks_red2")
    add_brush(1650, 1060, 176, 1682, 1260, 240, tex_sides="bricks_red2")
    add_brush(1650, 1260, 0, 1682, 1388, 240, tex_sides="bricks_red2")

    # Double Tap Root Beer dentro del Granero + M1A1 Carbine (weapon=13)
    add_ent(
        "perk_double", (2150, 1160, 36),
        angles="0 180 0", spawnflags="16", cost="2000",
        oldmodel="sounds/machines/perk_drink.wav",
        weapon2model="models/machines/v_perk.mdl",
        weapon2_animduration="22",
        model="models/machines/quake_scale/double_tap.mdl"
    )
    add_wall_weapon("ww_barn_m1a1", (1950, 940, 58), 90, 13, 600, 300, 1918, 932, 16, 1982, 964, 96)

    # Casa de la Granja (Farmhouse): X[+1680..+2250], Y[+1620..+2200], Z[0..208]
    add_brush(1680, 1620, 208, 2250, 2200, 232, tex_sides="wall_Owood", tex_top="roof_green", tex_bottom="w_wood_floor_YB")
    add_brush(1680, 2168, 0, 2250, 2200, 208, tex_sides="wall_Owood")
    add_brush(2218, 1620, 0, 2250, 2168, 208, tex_sides="wall_Owood")
    add_brush(1680, 1620, 0, 2218, 1652, 208, tex_sides="wall_Owood")
    # Fachada oeste de la Casa con entrada abierta en Y[+1820..+1980]
    add_brush(1680, 1652, 0, 1712, 1820, 208, tex_sides="wall_Owood")
    add_brush(1680, 1820, 160, 1712, 1980, 208, tex_sides="wall_Owood")
    add_brush(1680, 1980, 0, 1712, 2168, 208, tex_sides="wall_Owood")

    # --- Detalle del Granero Rojo (Barn) ------------------------------------
    # Solado de tierra prensada y pajar con heno
    add_floor(1686, 936, 2214, 1384, "debris")
    # Cumbrera a dos aguas sobre el forjado del granero
    add_brush(1630, 1040, 264, 2270, 1280, 276, tex_sides="shingle_roof", tex_top="shingle_roof", tex_bottom="shingle_roof")
    add_brush(1630, 1080, 276, 2270, 1240, 288, tex_sides="shingle_roof", tex_top="shingle_roof", tex_bottom="shingle_roof")
    add_brush(1630, 1120, 288, 2270, 1200, 300, tex_sides="shingle_roof", tex_top="shingle_roof", tex_bottom="shingle_roof")
    add_brush(1630, 1150, 300, 2270, 1170, 312, tex_sides="shingle_roof", tex_top="shingle_roof", tex_bottom="shingle_roof")
    # Ventilador cupular sobre la cumbrera
    add_brush(1930, 1140, 312, 1970, 1180, 356, tex_sides="wall_Owood", tex_top="shingle_roof", tex_bottom="wall_Owood")
    # Viga del pajar y riel de la puerta corredera
    add_beam(1682, 1140, 2218, 1180, 176, 184, "w_wood_dark_64")
    add_beam(1650, 1050, 1682, 1270, 176, 188, "m_metal_darkBlu")
    # Pacas de heno apiladas en el pajar
    for (hx, hy, hz) in ((1760, 1000, 0), (1760, 1000, 40), (1830, 1000, 0), (1760, 1080, 0)):
        add_brush(hx - 36, hy - 22, hz, hx + 36, hy + 22, hz + 40,
                  tex_sides="hay_wall", tex_top="hay_wall", tex_bottom="hay_wall")
    for (hx, hy) in ((2120, 1300), (2180, 1300), (2120, 1240)):
        add_brush(hx - 36, hy - 22, 0, hx + 36, hy + 22, 40,
                  tex_sides="hay_wall", tex_top="hay_wall", tex_bottom="hay_wall")
    # Luminarias colgadas de la cumbrera
    for lx in (1800, 2000, 2150):
        add_ceiling_lamp(lx, 1160, 232)
    add_light(1950, 1160, 96, 210, 190, 150, 380)
    # Pesebres y separaciones de las cuadras del granero
    for sy2 in (1010, 1090, 1170, 1250):
        add_brush(2040, sy2 - 6, 0, 2200, sy2 + 6, 56, tex_sides="w_wood_dark_64", tex_top="wood_t1", tex_bottom="w_wood_dark_64")
    add_brush(2040, 1330, 0, 2200, 1342, 90, tex_sides="w_wood_dark_64", tex_top="wood_t1", tex_bottom="w_wood_dark_64")
    # Escalera de mano al pajar y aperos colgados
    for r in range(9):
        add_brush(1740, 1330, r * 18, 1760, 1346, r * 18 + 6, tex_sides="w_wood_dark_64", tex_top="w_wood_dark_64", tex_bottom="w_wood_dark_64")
    add_brush(1712, 1320, 20, 1720, 1356, 22, tex_sides="m_metal_darkBlu", tex_top="m_metal_darkBlu", tex_bottom="m_metal_darkBlu")
    add_brush(1780, 1320, 20, 1788, 1356, 22, tex_sides="m_metal_darkBlu", tex_top="m_metal_darkBlu", tex_bottom="m_metal_darkBlu")
    add_brush(1720, 1330, 150, 1780, 1338, 156, tex_sides="metal_stB", tex_top="metal_stB", tex_bottom="metal_stB")
    add_brush(1720, 1330, 120, 1780, 1338, 126, tex_sides="metal_stB", tex_top="metal_stB", tex_bottom="metal_stB")

    # --- Silo metalico de la granja -----------------------------------------
    add_brush(2280, 980, 0, 2380, 1080, 320, tex_sides="silo_wall", tex_top="silo_wall", tex_bottom="silo_wall")
    add_brush(2284, 984, 320, 2376, 1076, 336, tex_sides="silo_wall", tex_top="silo_wall", tex_bottom="silo_wall")
    add_brush(2300, 1000, 336, 2360, 1060, 356, tex_sides="silo_wall", tex_top="shingle_roof", tex_bottom="silo_wall")
    add_brush(2312, 1012, 356, 2348, 1048, 372, tex_sides="silo_wall", tex_top="shingle_roof", tex_bottom="silo_wall")
    for sz in (60, 160, 260):
        add_brush(2276, 980, sz, 2384, 1084, sz + 6, tex_sides="silo_wall", face_tex={"-x": "silo_wall", "+x": "silo_wall", "-y": "silo_wall", "+y": "silo_wall"})

    # --- Detalle de la Casa de la Granja (Farmhouse) -------------------------
    add_floor(1716, 1656, 2214, 2164, "w_wood_floor_YB")
    # Porche de entrada con escalones, postes y barandilla
    add_floor(1620, 1780, 1716, 2020, "w_wood_dark_64", 0.0, 8.0)
    add_steps(1570, 1790, 1620, 2010, 8.0, step_h=3.0, tex="w_wood_dark_64", dir_x=True, count=3)
    add_beam(1610, 1770, 1690, 2030, 112, 124, "shingle_roof")
    for (px2, py2) in ((1626, 1796), (1626, 2004), (1674, 1796), (1674, 2004)):
        add_column(px2, py2, 8, 112, size=10.0, tex="wall_Owood")
    add_rail(1620, 1804, 1620, 1996, 40, 48, "w_wood_dark_64")
    add_rail(1630, 1770, 1670, 1770, 40, 48, "w_wood_dark_64")
    # Chimenea de ladrillo sobre el tejado
    add_brush(2080, 2100, 232, 2140, 2160, 330, tex_sides="brick_pillar", tex_top="brick_pillar", tex_bottom="brick_pillar")
    # Ventanas iluminadas en la fachada oeste de la casa
    add_window_row(1700, 2120, 64, 128, "+x", 1712, spacing=120.0)
    add_window_row(1740, 2140, 64, 128, "-x", 2218, spacing=140.0)
    # Luminarias del salon y del porche
    for (lx, ly) in ((1900, 1800), (1900, 2000), (2100, 1900)):
        add_ceiling_lamp(lx, ly, 200)
    add_light(1650, 1900, 100, 235, 200, 150, 240)

    # --- Patio de la granja: valla, abrevadero y pacas ----------------------
    for (fx0, fy0, fx1, fy1) in (
        (1560, 2350, 2400, 2362), (1560, 860, 1560, 1450), (1560, 1620, 1560, 2380),
    ):
        add_brush(min(fx0, fx1) - 6, min(fy0, fy1) - 6, 0, max(fx0, fx1) + 6, max(fy0, fy1) + 6, 88,
                  tex_sides="fence_wood", tex_top="fence_wood", tex_bottom="fence_wood")
    # Solado de tierra del patio (evita que se vea la hierva base en la granja)
    add_floor(1560, 900, 1650, 2350, "debris")
    add_floor(1650, 1420, 2250, 1620, "debris")
    add_floor(2250, 900, 2400, 2350, "debris")
    add_floor(1560, 2200, 2400, 2350, "debris")
    add_floor(1650, 900, 2250, 940, "debris")
    # Abrevadero de madera y pacas sueltas en el patio
    add_brush(1780, 1500, 0, 1900, 1560, 28, tex_sides="w_wood_dark_64", tex_top="w_wood_dark_64", tex_bottom="w_wood_dark_64")
    for (bx2, by2) in ((1600, 1250), (1600, 1330), (1660, 1290)):
        add_brush(bx2 - 34, by2 - 22, 0, bx2 + 34, by2 + 22, 40,
                  tex_sides="hay_wall", tex_top="hay_wall", tex_bottom="hay_wall")
    # Rotulo FARM sobre el porton de entrada a la granja
    add_brush(1478, 1410, 192, 1494, 1510, 224, tex_sides="wall_Owood3",
              face_tex={"-x": "sign_farm", "+x": "sign_farm"})
    add_light(1600, 1460, 120, 190, 180, 150, 320)
    add_light(2050, 2100, 120, 180, 170, 140, 300)

    add_wall_weapon("ww_farm_thomp", (2210, 1900, 58), 180, 3, 1200, 600, 2180, 1868, 16, 2218, 1932, 96)
    add_ent("mystery_box_tp_spot", (1980, 2110, 36), angles="0 270 0")

    # Farola Verde #3 en Farm + grieta de lava + luces + spawners
    add_ent("tranzit_lamp_tp", (1380, 1620, 40), targetname="lamp_farm", target="lamp_power", message="POWER STATION")
    add_light(1380, 1620, 110, 60, 255, 90, 320)
    add_lava_pit(1220, 950, 1440, 1120)
    add_light(1950, 1160, 190, 255, 190, 120, 460)
    add_light(1960, 1900, 180, 255, 210, 150, 460)
    add_ent("spawn_zombie", (1960, 1260, 40), targetname="z_farm", spawnflags="5")
    add_ent("spawn_zombie", (1960, 1780, 40), targetname="z_farm", spawnflags="5")
    add_ent("spawn_zombie", (1320, 1820, 40), targetname="z_depot_ext", spawnflags="5")

    # ========================================================================
    # ESTACION 4: POWER STATION (ESTE: X[+1150..+2300], Y[-1150..+250])
    # ========================================================================
    # Laboratorio de la Central Electrica: X[+1580..+2260], Y[-1050..+150], Z[0..232]
    add_brush(1580, -1050, 232, 2260, 150, 256, tex_sides="facility_wall_l", tex_top="roof_green", tex_bottom="ceilings_te1")
    add_brush(1580, 118, 0, 2260, 150, 232, tex_sides="facility_wall_l")
    add_brush(2228, -1050, 0, 2260, 118, 232, tex_sides="facility_wall_l")
    add_brush(1580, -1050, 0, 2228, -1018, 232, tex_sides="facility_wall_l")
    # Fachada Oeste del Laboratorio con puerta comprable en Y[-240..-80]
    add_brush(1580, -1018, 0, 1612, -240, 232, tex_sides="facility_contro")
    add_brush(1580, -240, 160, 1612, -80, 232, tex_sides="facility_contro")
    add_brush(1580, -80, 0, 1612, 118, 232, tex_sides="facility_contro")

    add_buyable_door("door_power", "z_power", 750, 1584, -240, 0, 1608, -80, 160, tex="ver_metal_door")

    # --- Detalle de la Central Electrica ------------------------------------
    # Solado tecnico de rejilla metalica
    add_floor(1616, -1014, 2224, 114, "metal_floor")
    # Rotulo POWER en la fachada oeste, sobre la puerta comprable
    add_brush(1564, -300, 234, 1580, -20, 266, tex_sides="facility_contro",
              face_tex={"-x": "sign_power", "+x": "sign_power"})
    # Escalones de acceso a la puerta de la central
    add_floor(1500, -260, 1580, -60, "sidewalk", 0.0, 5.0)

    # Nucleo del Reactor en el centro del Laboratorio + Interruptor de Electricidad (power_switch)
    add_brush(1880, -620, 0, 2000, -500, 232, tex_sides="facility_contro",
              face_tex={"-x": "reactor_blue", "+x": "reactor_blue",
                        "-y": "reactor_blue", "+y": "reactor_blue"})
    add_ent("power_switch", (1860, -560, 44), angles="0 180 0")
    # Pasarela metalica y barandilla alrededor del reactor
    for (px0, py0, px1, py1) in (
        (1840, -660, 1880, -460), (2000, -660, 2040, -460),
        (1880, -660, 2000, -620), (1880, -500, 2000, -460),
    ):
        add_brush(px0, py0, 96, px1, py1, 104, tex_sides="metal_grate", tex_top="metal_grate", tex_bottom="metal_grate")
    add_rail(1832, -664, 2048, -664, 104, 146, "metal_grate")
    add_rail(1832, -456, 2048, -456, 104, 146, "metal_grate")
    add_rail(1832, -664, 1832, -456, 104, 146, "metal_grate")
    add_rail(2048, -664, 2048, -456, 104, 146, "metal_grate")
    # Escalera de acceso a la pasarela del reactor
    for i in range(6):
        add_brush(2050 + i * 22, -560, i * 18, 2072 + i * 22, -480, i * 18 + 18,
                  tex_sides="metal_grate", tex_top="metal_grate", tex_bottom="metal_grate")
    # Consolas de control a lo largo del muro sur
    for cx2 in range(1700, 2150, 90):
        add_brush(cx2, -1000, 0, cx2 + 70, -940, 48,
                  tex_sides="facility_contro", tex_top="m_metal_stG", tex_bottom="facility_contro")
        add_brush(cx2, -1006, 48, cx2 + 70, -1000, 54,
                  tex_sides="danger_stripe", tex_top="danger_stripe", tex_bottom="danger_stripe")
        add_brush(cx2, -1000, 96, cx2 + 70, -986, 132,
                  tex_sides="reactor_blue", tex_top="m_metal_stG", tex_bottom="reactor_blue")
    # Conductos y bandejas de cables en el techo
    for py2 in (-880, -700, -520, -340, -200):
        add_beam(1620, py2 - 8, 2220, py2 + 8, 216, 224, "metal_stB")
    # Luminarias industriales del laboratorio
    for lx2 in (1720, 1900, 2080):
        for ly2 in (-900, -700, -500, -300, -100, 60):
            add_ceiling_lamp(lx2, ly2, 224)
    add_light(1940, -560, 150, 90, 180, 255, 520)
    add_light(1700, -900, 140, 110, 170, 240, 400)
    add_light(2150, -200, 140, 110, 170, 240, 400)

    # --- Dos torres de refrigeracion al norte del laboratorio ---------------
    for (tx2, ty2) in ((1780, 420), (2120, 420)):
        for (z0, z1, half_a, half_b) in ((0, 90, 116, 78), (90, 210, 100, 66), (210, 320, 84, 54)):
            add_brush(tx2 - half_a, ty2 - half_b, z0, tx2 + half_a, ty2 + half_b, z1,
                      tex_sides="cooling_tower", tex_top="cooling_tower", tex_bottom="cooling_tower")
            add_brush(tx2 - half_b, ty2 - half_a, z0, tx2 + half_b, ty2 + half_a, z1,
                      tex_sides="cooling_tower", tex_top="cooling_tower", tex_bottom="cooling_tower")
        add_brush(tx2 - 92, ty2 - 92, 320, tx2 + 92, ty2 + 92, 340,
                  tex_sides="cooling_tower", tex_top="cooling_tower", tex_bottom="cooling_tower")
        add_brush(tx2 - 70, ty2 - 70, 340, tx2 + 70, ty2 + 70, 352,
                  tex_sides="cooling_tower", tex_top="cooling_tower", tex_bottom="cooling_tower")
        add_light(tx2, ty2, 300, 120, 190, 240, 420)
        add_light(tx2, ty2 - 190, 120, 150, 200, 235, 420)
        add_light(tx2, ty2 + 190, 120, 150, 200, 235, 420)
    # Torres de iluminacion del patio norte de la central
    for (fx2, fy2) in ((1620, 700), (1900, 760), (2180, 700), (1750, 950), (2050, 950)):
        add_street_lamp(fx2, fy2)
        add_light(fx2, fy2, 60, 160, 180, 210, 420)
    # Vallado y apilamiento de material del patio de la central
    add_brush(1500, 620, 0, 1506, 1000, 88, tex_sides="fence_wood", tex_top="fence_wood", tex_bottom="fence_wood")
    for (qx, qy) in ((1600, 620), (1650, 640), (1620, 660)):
        add_brush(qx - 30, qy - 20, 0, qx + 30, qy + 20, 40, tex_sides="w_wood_dark_64", tex_top="wood_t1", tex_bottom="w_wood_dark_64")

    # Transformadores y groupos electrogenos del exterior
    for (ex, ey) in ((1500, 180), (1500, 380), (1500, 580)):
        add_brush(ex - 40, ey - 30, 0, ex + 40, ey + 30, 72,
                  tex_sides="metal_stB", tex_top="m_metal_stG", tex_bottom="metal_stB")
        add_brush(ex - 44, ey - 34, 72, ex + 44, ey + 34, 80,
                  tex_sides="danger_stripe", tex_top="danger_stripe", tex_bottom="danger_stripe")
        add_light(ex, ey, 96, 140, 170, 220, 260)

    # Perks en Power Station: Stamin-Up (2000) y Mule Kick (4000) + BAR (weapon=5)
    add_ent(
        "perk_staminup", (2170, -940, 36),
        angles="0 180 0", spawnflags="16", cost="2000",
        oldmodel="sounds/machines/perk_drink.wav",
        weapon2model="models/machines/v_perk.mdl",
        weapon2_animduration="22",
        model="models/machines/quake_scale/staminup.mdl"
    )
    add_ent(
        "perk_mule", (2170, 40, 36),
        angles="0 180 0", spawnflags="4", cost="4000",
        oldmodel="sounds/machines/perk_drink.wav",
        weapon2model="models/machines/v_perk.mdl",
        weapon2_animduration="22",
        model="models/machines/quake_scale/mulekick.mdl"
    )
    add_wall_weapon("ww_power_bar", (1920, -1010, 58), 90, 5, 1800, 900, 1888, -1018, 16, 1952, -984, 96)

    # Farola Verde #4 en Power Station + grieta de lava + luces azules de reactor + spawners
    add_ent("tranzit_lamp_tp", (1420, -420, 40), targetname="lamp_power", target="lamp_town", message="TOWN")
    add_light(1420, -420, 110, 60, 255, 90, 320)
    add_lava_pit(1220, 260, 1480, 420)
    add_light(1920, -450, 190, 110, 195, 255, 520)
    add_light(1750, -820, 180, 140, 210, 255, 420)
    add_ent("spawn_zombie", (1750, -850, 40), targetname="z_power", spawnflags="5")
    add_ent("spawn_zombie", (2100, -250, 40), targetname="z_power", spawnflags="5")
    add_ent("spawn_zombie", (1340, -780, 40), targetname="z_depot_ext", spawnflags="5")

    # ========================================================================
    # ESTACION 5: TOWN (EL PUEBLO CENTRAL - SUR: X[-950..+1050], Y[-2300..-1150])
    # ========================================================================
    # 1) EL BAR DE TOWN (Norte de la calle sur: X[-550..+250], Y[-1480..-1150], Z[0..208])
    add_brush(-550, -1480, 208, 250, -1150, 232, tex_sides="bricks_r64", tex_top="roof_green", tex_bottom="w_wood_dark_64")
    add_brush(-550, -1480, 0, -518, -1150, 208, tex_sides="bricks_r64")
    add_brush(218, -1480, 0, 250, -1150, 208, tex_sides="bricks_r64")
    # Fachada Sur del Bar con puerta comprable en X[-220..-60]
    add_brush(-518, -1480, 0, -220, -1448, 208, tex_sides="wall_br_red")
    add_brush(-220, -1480, 160, -60, -1448, 208, tex_sides="wall_br_red")
    add_brush(-60, -1480, 0, 218, -1448, 208, tex_sides="wall_br_red")

    # Muro norte del Bar (con hueco de ventana barricada en X[-260..-120])
    add_brush(-550, -1182, 0, -260, -1150, 208, tex_sides="bricks_r64")
    add_brush(-260, -1182, 0, -120, -1150, 36, tex_sides="bricks_r64")
    add_brush(-260, -1182, 128, -120, -1150, 208, tex_sides="bricks_r64")
    add_brush(-120, -1182, 0, 250, -1150, 208, tex_sides="bricks_r64")

    add_buyable_door("door_bar", "z_town", 1000, -220, -1476, 0, -60, -1452, 160, tex="doors_dark")
    # Ventanas iluminadas del Bar vistas desde el norte (patio trasero)
    add_window_row(-500, 220, 140, 180, "-y", -1182, spacing=110.0)

    # Juggernog y PhD Flopper dentro del Bar
    add_ent(
        "perk_juggernog", (160, -1240, 36),
        angles="0 180 0", spawnflags="2", cost="2500",
        oldmodel="sounds/machines/perk_drink.wav",
        weapon2model="models/machines/v_perk.mdl",
        weapon2_animduration="22",
        model="models/machines/quake_scale/juggernog.mdl"
    )
    add_ent(
        "perk_flopper", (-450, -1240, 36),
        angles="0 0 0", spawnflags="16", cost="2000",
        oldmodel="sounds/machines/perk_drink.wav",
        weapon2model="models/machines/v_perk.mdl",
        weapon2_animduration="22",
        model="models/machines/quake_scale/flopper.mdl"
    )

    # 2) EL BANCO Y CAMARA DEL PACK-A-PUNCH (Sur de la calle: X[-650..+650], Y[-2350..-1820], Z[0..224])
    add_brush(-650, -2350, 224, 650, -1820, 248, tex_sides="bricks_ak_white", tex_top="roof_green", tex_bottom="ceilings_te1")
    add_brush(-650, -2350, 0, 650, -2318, 224, tex_sides="bricks_ak_white")
    add_brush(-650, -2318, 0, -618, -1820, 224, tex_sides="bricks_ak_white")
    add_brush(618, -2318, 0, 650, -1820, 224, tex_sides="bricks_ak_white")
    # Fachada Norte del Banco con puerta principal en X[-180..-20]
    add_brush(-618, -1852, 0, -180, -1820, 224, tex_sides="bricks_ak_white")
    add_brush(-180, -1852, 160, -20, -1820, 224, tex_sides="bricks_ak_white")
    add_brush(-20, -1852, 0, 618, -1820, 224, tex_sides="bricks_ak_white")
    # Muro interior de la Camara Acorazada (Vault) en X=180 con puerta de 1500 pts en Y[-2160..-2000]
    add_brush(160, -2318, 0, 192, -2160, 224, tex_sides="metal_stB")
    add_brush(160, -2160, 160, 192, -2000, 224, tex_sides="metal_stB")
    add_brush(160, -2000, 0, 192, -1852, 224, tex_sides="metal_stB")

    add_buyable_door("door_bank", "z_town", 1000, -180, -1848, 0, -20, -1824, 160, tex="ver_metal_door")
    add_buyable_door("door_vault", "z_town", 1500, 164, -2160, 0, 188, -2000, 160, tex="ver_metal_door")

    # Pack-a-Punch dentro de la Camara Acorazada + Deadshot Daiquiri en el lobby del Banco
    add_ent("perk_pap", (480, -2080, 42), angles="0 180 0", light="80")
    add_ent(
        "perk_deadshot", (-520, -2220, 36),
        angles="0 0 0", spawnflags="16", cost="1500", cost2="1000",
        oldmodel="sounds/machines/perk_drink.wav",
        weapon2model="models/machines/v_perk.mdl",
        weapon2_animduration="22",
        model="models/machines/hl_scale/deadshot.mdl"
    )
    add_ent("mystery_box_tp_spot", (-520, -1960, 36), angles="0 0 0")

    # --- Detalle del Bar de Town --------------------------------------------
    # Solado de madera del salon y tarima de la barra
    add_floor(-522, -1452, 222, -1146, "w_wood_dark_64")
    add_brush(-480, -1310, 0, -280, -1250, 44,
              tex_sides="w_wood_brown_re", tex_top="wood_t1", tex_bottom="w_wood_brown_re")
    add_brush(-480, -1250, 44, -280, -1258, 48, tex_sides="m_metal_stG", tex_top="t_floor_blue", tex_bottom="m_metal_stG")
    add_brush(-480, -1318, 0, -280, -1310, 132, tex_sides="w_wood_dark_64", tex_top="wood_t1", tex_bottom="w_wood_dark_64")
    for bx3 in range(-460, -300, 34):
        add_brush(bx3, -1322, 132, bx3 + 8, -1306, 152, tex_sides="g_glass_", tex_top="g_glass_", tex_bottom="g_glass_")
    # Cornisa y linea de forjado de la fachada de dos plantas
    for (bz0, bz1, btex) in ((196, 212, "brick_pillar"), (110, 124, "bricks_ak_white")):
        add_brush(-566, -1496, bz0, 266, -1480, bz1, tex_sides=btex, tex_top=btex, tex_bottom=btex)
        add_brush(-566, -1496, bz0, -550, -1134, bz1, tex_sides=btex, tex_top=btex, tex_bottom=btex)
        add_brush(250, -1496, bz0, 266, -1134, bz1, tex_sides=btex, tex_top=btex, tex_bottom=btex)
    # Ventanas iluminadas de la planta alta (vistas desde la calle)
    wx = -480.0
    while wx <= 190.0:
        add_brush(wx - 20, -1500, 140, wx + 20, -1484, 180,
                  tex_sides="window_lit", face_tex={"-y": "window_lit"})
        wx += 110.0
    # Porche de entrada: escalones, postes y toldo
    add_floor(-270, -1500, -10, -1482, "w_wood_dark_64", 0.0, 6.0)
    for px3 in (-250, -30):
        add_column(px3, -1466, 6, 176, size=10.0, tex="w_wood_dark_64")
    add_beam(-266, -1500, -14, -1470, 176, 188, "metal_stB")
    # Rotulo BAR sobre la puerta del bar
    add_brush(-260, -1516, 192, -20, -1500, 224, tex_sides="conc_road_D2",
              face_tex={"-y": "sign_bar", "+y": "sign_bar"})
    # Luminarias y luces del interior del bar
    for (lx3, ly3) in ((-440, -1240), (-180, -1240), (60, -1240), (-180, -1380)):
        add_ceiling_lamp(lx3, ly3, 200)
    add_light(-150, -1300, 140, 235, 190, 120, 380)

    # --- Detalle del Banco (fachada, portico y camara acorazada) -------------
    add_floor(-622, -2322, 622, -1848, "floor2_gr3x3")
    add_floor(196, -2322, 618, -1848, "metal_grate")
    # Mostrador de caja con barrotes verticales
    add_brush(-500, -2100, 0, 500, -2050, 44, tex_sides="m_metal_stG", tex_top="t_floor_blue", tex_bottom="m_metal_stG")
    add_brush(-500, -2100, 44, 500, -2092, 48, tex_sides="m_metal_stG", tex_top="t_floor_blue", tex_bottom="m_metal_stG")
    bx4 = -470.0
    while bx4 <= 470.0:
        add_brush(bx4, -2100, 48, bx4 + 6, -2094, 132, tex_sides="metal_stB", tex_top="metal_stB", tex_bottom="metal_stB")
        bx4 += 60.0
    # Portico: escalones, 4 columnas y fronton escalonado
    add_floor(-290, -1820, 90, -1802, "sidewalk", 0.0, 6.0)
    for cx3 in (-250, -200, 0, 50):
        add_column(cx3, -1812, 6, 200, size=20.0, tex="brick_pillar")
    add_brush(-290, -1820, 200, 90, -1804, 212, tex_sides="bricks_ak_white", tex_top="bricks_ak_white", tex_bottom="bricks_ak_white")
    add_brush(-250, -1820, 212, 50, -1804, 224, tex_sides="bricks_ak_white", tex_top="bricks_ak_white", tex_bottom="bricks_ak_white")
    add_brush(-210, -1820, 224, 10, -1804, 236, tex_sides="bricks_ak_white", tex_top="bricks_ak_white", tex_bottom="bricks_ak_white")
    # Rotulo BANK sobre la puerta principal del banco
    add_brush(-180, -1824, 168, -20, -1808, 200, tex_sides="bricks_ak_white",
              face_tex={"-y": "sign_bank", "+y": "sign_bank"})
    # Ventanas de las fachadas oeste y este del banco
    wy = -2290.0
    while wy <= -1890.0:
        add_brush(-666, wy - 20, 64, -650, wy + 20, 128, tex_sides="window_lit", face_tex={"-x": "window_lit"})
        add_brush(650, wy - 20, 64, 666, wy + 20, 128, tex_sides="window_lit", face_tex={"+x": "window_lit"})
        wy += 140.0
    # Cornisa superior del banco
    for (bz0, bz1) in ((226, 240), (130, 144)):
        add_brush(-666, -2366, bz0, 666, -2350, bz1, tex_sides="bricks_ak_white", tex_top="bricks_ak_white", tex_bottom="bricks_ak_white")
        add_brush(-666, -2366, bz0, -650, -1810, bz1, tex_sides="bricks_ak_white", tex_top="bricks_ak_white", tex_bottom="bricks_ak_white")
        add_brush(650, -2366, bz0, 666, -1810, bz1, tex_sides="bricks_ak_white", tex_top="bricks_ak_white", tex_bottom="bricks_ak_white")
    # Luminarias del vestibulo y de la camara
    for (lx4, ly4) in ((-450, -1950), (-100, -1950), (300, -1950), (-450, -2250), (400, -2250)):
        add_ceiling_lamp(lx4, ly4, 216)
    add_light(400, -2080, 140, 190, 210, 255, 400)

    # --- Alumbrado publico de la calle principal de Town --------------------
    for lx5 in (-1300, -1000, -800):
        add_street_lamp(lx5, -1490)
    for lx5 in (450, 750, 1050):
        add_street_lamp(lx5, -1490)
    for lx5 in (-1300, -1000, -800, 800, 1050, 1300):
        add_street_lamp(lx5, -1830)
    for lx6 in (-1200, -800, -400, 0, 400, 800, 1200):
        add_light(lx6, -1660, 200, 195, 186, 140, 520)
        add_light(lx6, -1790, 140, 170, 168, 132, 420)
    add_light(-150, -1600, 90, 230, 195, 130, 420)

    # Grietas centrales de lava en Town + Farola Verde #5 + luces + spawners
    add_lava_pit(700, -1740, 900, -1560)
    add_lava_pit(-880, -1720, -680, -1520)
    add_ent("tranzit_lamp_tp", (420, -1520, 40), targetname="lamp_town", target="lamp_depot", message="BUS DEPOT")
    add_light(420, -1520, 110, 60, 255, 90, 320)
    add_light(-140, -1300, 180, 255, 195, 130, 420)
    add_light(-220, -2080, 185, 255, 225, 175, 450)
    add_light(420, -2080, 185, 255, 130, 60, 420)
    add_ent("spawn_zombie", (-380, -1300, 40), targetname="z_town", spawnflags="5")
    add_ent("spawn_zombie", (-380, -2180, 40), targetname="z_town", spawnflags="5")
    add_ent("spawn_zombie", (380, -2180, 40), targetname="z_town", spawnflags="5")
    add_ent("spawn_zombie", (620, -1620, 40), targetname="z_depot_ext", spawnflags="5")

    # ========================================================================
    # AUTOBUS EN MOVIMIENTO DE TRANZIT (func_tranzit_bus) Y RUTA DE 8 PARADAS
    # ========================================================================
    # Autobus de Tranzit (T.E.D.D.) a escala real en coordenadas locales
    # [0..160] x [0..112] x [0..148]: piso a 14u (escalon de 8u desde el
    # anden), ventanillas corridas, techo a 136u, cabina, quitanieve y faros.
    BUS_FLOOR = 14.0
    BUS_BELT = 52.0        # Cintura inferior de las ventanillas
    BUS_ROOF = 136.0
    SOLID_X = ((0.0, 30.0), (56.0, 104.0), (130.0, 160.0))   # Tramos de chapa
    PILLAR_X = ((0.0, 12.0), (18.0, 30.0), (56.0, 68.0),
                (92.0, 104.0), (130.0, 142.0), (148.0, 160.0))

    bus_brushes: List[BoxBrush] = []
    # Piso y rodadura
    bus_brushes.append(BoxBrush(0, 0, 0, 160, 112, BUS_FLOOR, "metal_floor", "m_metal_darkBlu", "bus_metal"))
    # Panel de cola y mampara delantera de la cabina
    bus_brushes.append(BoxBrush(0, 0, BUS_FLOOR, 8, 112, BUS_ROOF, "bus_side", "bus_side", "bus_side"))
    bus_brushes.append(BoxBrush(148, 0, BUS_FLOOR, 160, 112, BUS_ROOF, "bus_side", "bus_side", "bus_side"))
    # Laterales: chapa inferior, cintura de ventanillas, montantes y larguero
    for (sx0, sx1) in SOLID_X:
        for (sy0, sy1) in ((0.0, 6.0), (106.0, 112.0)):
            bus_brushes.append(BoxBrush(sx0, sy0, BUS_FLOOR, sx1, sy1, BUS_BELT, "bus_side", "bus_side", "bus_side"))
            bus_brushes.append(BoxBrush(sx0, sy0, BUS_BELT, sx1, sy1, BUS_BELT + 6, "bus_metal", "bus_metal", "bus_metal"))
    for (sx0, sx1) in PILLAR_X:
        for (sy0, sy1) in ((0.0, 6.0), (106.0, 112.0)):
            bus_brushes.append(BoxBrush(sx0, sy0, BUS_BELT + 6, sx1, sy1, BUS_ROOF, "bus_metal", "bus_metal", "bus_metal"))
    # Techo (con luz interior) y escotilla de evacuacion
    bus_brushes.append(BoxBrush(6, 6, BUS_ROOF, 154, 106, 148, "bus_metal", "bus_metal", "bus_metal"))
    bus_brushes.append(BoxBrush(70, 46, 148, 90, 66, 156, "bus_metal", "bus_metal", "bus_metal"))
    # Capo delantero, quitanieve escalonado y parachoques trasero
    bus_brushes.append(BoxBrush(144, 8, BUS_FLOOR, 152, 104, 44, "bus_side", "bus_side", "bus_side"))
    bus_brushes.append(BoxBrush(152, 2, 0, 157, 110, 32, "bus_metal", "bus_metal", "bus_metal"))
    bus_brushes.append(BoxBrush(157, 10, 0, 160, 102, 22, "bus_metal", "bus_metal", "bus_metal"))
    bus_brushes.append(BoxBrush(0, 2, 0, 5, 110, 30, "bus_metal", "bus_metal", "bus_metal"))
    # Faros delanteros y pilotos traseros (texturas emisivas)
    for by in (16.0, 84.0):
        bus_brushes.append(BoxBrush(156, by, 34, 160, by + 14, 48, "bus_headlight", "bus_headlight", "bus_headlight"))
        bus_brushes.append(BoxBrush(0, by, 34, 4, by + 14, 48, "bus_tail", "bus_tail", "bus_tail"))
    # Bancos corridos y respaldos del habitaculo (a ambos lados del pasillo)
    for bxx in (34.0, 58.0, 84.0, 108.0):
        bus_brushes.append(BoxBrush(bxx, 8, BUS_FLOOR, bxx + 20, 32, BUS_FLOOR + 18,
                                    "carpet_64_red", "carpet_64_red", "carpet_64_red"))
        bus_brushes.append(BoxBrush(bxx, 8, BUS_FLOOR + 18, bxx + 20, 13, BUS_FLOOR + 44,
                                    "carpet_64_red", "carpet_64_red", "carpet_64_red"))
        bus_brushes.append(BoxBrush(bxx, 80, BUS_FLOOR, bxx + 20, 104, BUS_FLOOR + 18,
                                    "carpet_64_red", "carpet_64_red", "carpet_64_red"))
        bus_brushes.append(BoxBrush(bxx, 99, BUS_FLOOR + 18, bxx + 20, 104, BUS_FLOOR + 44,
                                    "carpet_64_red", "carpet_64_red", "carpet_64_red"))
    # Barra longitudinal interior para agarrarse
    bus_brushes.append(BoxBrush(14, 54, 118, 146, 58, 124, "metal_stB", "metal_stB", "metal_stB"))
    add_submodel(
        "func_tranzit_bus",
        bus_brushes,
        {"target": "bus_stop_1", "speed": "210", "dmg": "500"},
    )

    # Nodos path_corner del circuito rectangular del Autobus de Tranzit
    # (el autobus se traslada sin girar, asi que las paradas forman un rectangulo
    #  despejado: pasa bajo el tunel y bajo la marquesina de la gasolinera)
    bus_stops = [
        ("bus_stop_1", "bus_stop_2", (-1500, -1720, 2), 25, "BUS DEPOT"),
        ("bus_stop_2", "bus_stop_3", (-1500, 200, 2), 8, "HIGHWAY TUNNEL"),
        ("bus_stop_3", "bus_stop_4", (-1500, 1240, 2), 25, "DINER & GARAGE"),
        ("bus_stop_4", "bus_stop_5", (0, 1240, 2), 8, "CORNFIELD CROSSROADS"),
        ("bus_stop_5", "bus_stop_6", (1240, 1240, 2), 25, "FARM"),
        ("bus_stop_6", "bus_stop_7", (1240, -180, 2), 25, "POWER STATION"),
        ("bus_stop_7", "bus_stop_8", (1240, -1720, 2), 6, "SOUTH HIGHWAY"),
        ("bus_stop_8", "bus_stop_1", (0, -1720, 2), 25, "TOWN (BANK & BAR)"),
    ]
    for tname, next_t, org, wait_s, msg in bus_stops:
        add_ent("path_corner", org, targetname=tname, target=next_t, wait=wait_s, message=msg)

    # Director exclusivo de Tranzit (gestiona spawns por proximidad y avisos de zona)
    add_ent("func_tranzit_director", (0, 0, 100))

    # ========================================================================
    # PROPS 3D (.MDL) REPARTIDOS POR TODAS LAS ESTACIONES DE GREEN RUN
    # ========================================================================
    # 1. Bus Depot: bancos de espera, contenedor de basura, barriles y maletas
    add_prop("models/props/Kino_couch.mdl", (-2150, -1750, 20), 0)
    add_prop("models/props/trash_con.mdl", (-1510, -1620, 20), 90)
    add_prop("models/props/Barrel_m.mdl", (-1510, -2060, 20), 0)
    add_prop("models/props/Kino_boxes2.mdl", (-2160, -2120, 20), 45)

    # 2. Highway Tunnel: jeep militar abandonado y barriles
    add_prop("models/props/jeep.mdl", (-1960, -240, 20), 80)
    add_prop("models/props/Barrel_m.mdl", (-2110, 220, 20), 0)
    add_prop("models/props/sandbags.mdl", (-1700, -320, 16), 90)

    # 3. Diner & Garage: mesas de cafeteria, nevera, horno, jeep en gasolinera y cajas en taller
    add_prop("models/props/table_dinner.mdl", (-1320, 1820, 20), 0)
    add_prop("models/props/table_dinner.mdl", (-960, 1820, 20), 0)
    add_prop("models/props/fridge.mdl", (-760, 1980, 28), 180)
    add_prop("models/props/oven.mdl", (-760, 1860, 24), 180)
    add_prop("models/props/jeep.mdl", (-1220, 1340, 20), 15)
    add_prop("models/props/Barrel_m.mdl", (-2180, 1720, 20), 0)
    add_prop("models/props/Kino_boxes3.mdl", (-1760, 2120, 20), 180)

    # 4. Cornfield & Nacht Bunker: arboles flanqueando el sendero, sacos terreros y radio
    add_prop("models/props/treeTH.mdl", (-290, 820, 16), 0)
    add_prop("models/props/treeSL.mdl", (290, 680, 16), 90)
    add_prop("models/props/treeTH.mdl", (-290, 340, 16), 45)
    add_prop("models/props/sandbags.mdl", (-140, 95, 16), 0)
    add_prop("models/props/sandbags.mdl", (140, 95, 16), 0)
    add_prop("models/props/radio.mdl", (0, -320, 28), 90)
    add_prop("models/props/derped/ammo_can.mdl", (-160, -310, 16), 45)

    # 5. Farm (Granja): el tractor clasico en el patio, barriles y muebles en la casa
    add_prop("models/props/derped/tractor.mdl", (1740, 1520, 24), 210)
    add_prop("models/props/Barrel_m.mdl", (1710, 980, 20), 0)
    add_prop("models/props/table_sq.mdl", (1960, 1920, 20), 0)
    add_prop("models/props/bed.mdl", (2150, 2110, 20), 180)
    add_prop("models/props/treeSL.mdl", (1420, 1980, 16), 0)

    # 6. Power Station: contenedor industrial, barriles y consolas
    add_prop("models/props/container.mdl", (1340, -620, 32), 90)
    add_prop("models/props/Barrel_m.mdl", (1660, -960, 20), 0)
    add_prop("models/props/Kino_boxes4.mdl", (2160, -680, 20), 180)

    # 7. Town (Bar & Banco): piano, gramofono y sofa en el Bar; mesas y jeep en la calle
    add_prop("models/props/piano.mdl", (-460, -1380, 24), 0)
    add_prop("models/props/gramophone.mdl", (160, -1380, 24), 180)
    add_prop("models/props/Kino_couch.mdl", (-150, -1210, 20), 270)
    add_prop("models/props/table_sq.mdl", (-360, -2100, 20), 0)
    add_prop("models/props/jeep.mdl", (340, -1680, 20), 165)
    add_prop("models/props/metal_chair.mdl", (-420, -1180, 20), 90)
    add_prop("models/props/metal_chair.mdl", (-370, -1200, 20), 45)
    add_prop("models/props/table_dinner.mdl", (-330, -1250, 20), 0)
    add_prop("models/props/table_dinner.mdl", (60, -1250, 20), 0)
    add_prop("models/props/radio.mdl", (-500, -1290, 34), 270)
    add_prop("models/props/vanity_table.mdl", (-100, -2080, 20), 90)
    add_prop("models/props/shelf.mdl", (-560, -1980, 20), 0)
    add_prop("models/props/shelf.mdl", (-560, -2060, 20), 0)
    add_prop("models/props/container.mdl", (900, -1300, 32), 0)
    add_prop("models/props/trash_con.mdl", (-1000, -1700, 20), 45)
    add_prop("models/props/Barrel_m.mdl", (-1150, -1700, 20), 0)
    add_prop("models/props/treeSL.mdl", (-1250, -1600, 16), 0)
    add_prop("models/props/treeTH.mdl", (1150, -1700, 16), 40)

    # 8. Bus Depot (extras): maletas, cajas y carretilla en el anden
    add_prop("models/props/Kino_boxes3.mdl", (-1460, -2000, 10), 20)
    add_prop("models/props/Kino_boxes4.mdl", (-1300, -2060, 10), 70)
    add_prop("models/props/MopBucket.mdl", (-2180, -1660, 20), 0)
    add_prop("models/props/stand.mdl", (-2050, -1660, 20), 180)
    add_prop("models/props/sandbags.mdl", (-1420, -1960, 8), 0)

    # 9. Highway Tunnel (extras): neumaticos, bidones y senalizacion
    add_prop("models/props/Barrel_m.mdl", (-1600, -420, 20), 0)
    add_prop("models/props/Barrel_m.mdl", (-1600, -360, 20), 30)
    add_prop("models/props/rebar.mdl", (-2090, 300, 16), 20)
    add_prop("models/props/derped/ammo_can.mdl", (-2050, 420, 16), 0)
    add_prop("models/props/jeep_hl.mdl", (-1980, -520, 20), 15)

    # 10. Diner & Garage (extras): sillas, neveras y herramientas
    add_prop("models/props/metal_chair.mdl", (-1240, 1880, 20), 180)
    add_prop("models/props/metal_chair.mdl", (-1040, 1880, 20), 180)
    add_prop("models/props/table_dinner.mdl", (-760, 1900, 20), 90)
    add_prop("models/props/flame.mdl", (-1180, 1450, 8), 0)
    add_prop("models/props/derped/pumpkin_1.mdl", (-1000, 1280, 8), 0)
    add_prop("models/props/derped/pumpkin_2.mdl", (-960, 1270, 8), 90)
    add_prop("models/props/derped/pumpkin_3.mdl", (-920, 1285, 8), 200)
    add_prop("models/props/Kino_boxes2.mdl", (-2160, 1660, 20), 0)
    add_prop("models/props/Barrel_m.mdl", (-1760, 1700, 20), 0)
    add_prop("models/props/flag_usa.mdl", (-1520, 2200, 20), 270)
    add_prop("models/props/radiator.mdl", (-2120, 2150, 20), 90)

    # 11. Maizal y Nacht (extras): arboles y restos
    add_prop("models/props/tree_ch.mdl", (-250, 1000, 16), 0)
    add_prop("models/props/tree_ch.mdl", (250, 1000, 16), 180)
    add_prop("models/props/treeSL.mdl", (300, -100, 16), 90)
    add_prop("models/props/bodybag_flat.mdl", (-60, 60, 10), 30)
    add_prop("models/props/dummy.mdl", (180, -80, 20), 200)
    add_prop("models/props/derped/ammo_can.mdl", (-180, -60, 16), 120)

    # 12. Granja (extras): aperos, muebles y animales disecados
    add_prop("models/props/oven.mdl", (2180, 1700, 24), 180)
    add_prop("models/props/fridge.mdl", (1730, 2100, 28), 0)
    add_prop("models/props/table_sq.mdl", (1900, 1760, 10), 0)
    add_prop("models/props/metal_chair.mdl", (1900, 1720, 10), 180)
    add_prop("models/props/metal_chair.mdl", (1900, 1800, 10), 0)
    add_prop("models/props/derped/pumpkin_1.mdl", (1620, 1700, 8), 0)
    add_prop("models/props/derped/pumpkin_2.mdl", (1640, 1720, 8), 90)
    add_prop("models/props/treeSL.mdl", (2320, 1300, 16), 0)
    add_prop("models/props/treeTH.mdl", (1200, 1050, 16), 45)
    add_prop("models/props/radiator.mdl", (2200, 1680, 20), 270)

    # 13. Central Electrica (extras): consolas, bidones y cajas fuertes
    add_prop("models/props/mainframe_pad.mdl", (1700, -960, 32), 0)
    add_prop("models/props/mainframe_pad.mdl", (1820, -960, 32), 0)
    add_prop("models/props/Barrel_m.mdl", (2160, -880, 20), 0)
    add_prop("models/props/Barrel_m.mdl", (2160, -820, 20), 25)
    add_prop("models/props/Kino_boxes4.mdl", (1660, -180, 20), 90)
    add_prop("models/props/dentist_chair.mdl", (2050, -980, 20), 180)

    # ========================================================================
    # 14. VEHICULOS ABANDONADOS, ALUMBRADO DEL CIRCUITO, VALLADOS Y SENALES
    # ========================================================================
    # Utilidades de vehiculos: trabajan en coordenadas locales (u = largo,
    # v = ancho) y se orientan en el mundo con `along_x`.

    def add_bus_wreck(x0: float, y0: float, along_x: bool = True):
        """Autobus siniestrado: 300 x 116, piso a 18, techo a 132."""
        L, W = 300.0, 116.0

        def B(u0, v0, z0, u1, v1, z1, **kw):
            if along_x:
                add_brush(x0 + u0, y0 + v0, z0, x0 + u1, y0 + v1, z1, **kw)
            else:
                add_brush(x0 + v0, y0 + u0, z0, x0 + v1, y0 + u1, z1, **kw)

        B(0, 0, 8, L, W, 18, tex_sides="bus_metal", tex_top="metal_floor", tex_bottom="bus_metal")
        B(0, 0, 126, L, W, 132, tex_sides="bus_metal", tex_top="bus_metal", tex_bottom="bus_metal")
        # Laterales con hueco de puerta central (u[120..200]) en el lado v=0
        B(0, 0, 18, 120, 8, 126, tex_sides="bus_side", tex_top="bus_side", tex_bottom="bus_side")
        B(200, 0, 18, L, 8, 126, tex_sides="bus_side", tex_top="bus_side", tex_bottom="bus_side")
        B(0, W - 8, 18, L, W, 126, tex_sides="bus_side", tex_top="bus_side", tex_bottom="bus_side")
        # Frontal y trasera
        B(0, 0, 18, 8, W, 126, tex_sides="bus_side", tex_top="bus_side", tex_bottom="bus_side")
        B(L - 8, 0, 18, L, W, 126, tex_sides="bus_side", tex_top="bus_side", tex_bottom="bus_side")
        # Franjas de ventanas (cristal roto) a ambos lados
        for i in range(5):
            u = 22 + i * 56
            B(u, -3, 58, u + 40, 3, 104, tex_sides="g_glass_", tex_top="g_glass_", tex_bottom="g_glass_")
            B(u, W - 3, 58, u + 40, W + 3, 104, tex_sides="g_glass_", tex_top="g_glass_", tex_bottom="g_glass_")
        # Faros delanteros y pilotos traseros
        for v0 in (16, W - 34):
            B(-2, v0, 40, 2, v0 + 18, 56, tex_sides="bus_tail", tex_top="bus_tail", tex_bottom="bus_tail")
            B(L - 2, v0, 40, L + 2, v0 + 18, 56, tex_sides="bus_headlight", tex_top="bus_headlight", tex_bottom="bus_headlight")
        # Ruedas
        for (wu, wv) in ((40, -6), (40, W - 22), (L - 68, -6), (L - 68, W - 22)):
            B(wu, wv, 0, wu + 28, wv + 28, 18,
              tex_sides="m_metal_darkBlu", tex_top="m_metal_darkBlu", tex_bottom="m_metal_darkBlu")

    def add_car_wreck(x0: float, y0: float, along_x: bool = True, tex: str = "m_metal_darkBlu"):
        """Coche abandonado: 180 x 88, chasis a 44, cabina a 82."""
        L, W = 180.0, 88.0

        def B(u0, v0, z0, u1, v1, z1, **kw):
            if along_x:
                add_brush(x0 + u0, y0 + v0, z0, x0 + u1, y0 + v1, z1, **kw)
            else:
                add_brush(x0 + v0, y0 + u0, z0, x0 + v1, y0 + u1, z1, **kw)

        B(0, 0, 14, L, W, 46, tex_sides=tex, tex_top=tex, tex_bottom=tex)
        B(38, 6, 46, 130, W - 6, 84, tex_sides=tex, tex_top=tex, tex_bottom=tex)
        # Parabrisas y luneta trasera
        B(132, 10, 50, 138, W - 10, 74, tex_sides="g_glass_", tex_top="g_glass_", tex_bottom="g_glass_")
        B(34, 10, 50, 40, W - 10, 74, tex_sides="g_glass_", tex_top="g_glass_", tex_bottom="g_glass_")
        # Ventanillas laterales
        B(56, 4, 54, 122, 10, 74, tex_sides="g_glass_", tex_top="g_glass_", tex_bottom="g_glass_")
        B(56, W - 10, 54, 122, W - 4, 74, tex_sides="g_glass_", tex_top="g_glass_", tex_bottom="g_glass_")
        # Ruedas
        for (wu, wv) in ((24, -8), (24, W - 20), (L - 52, -8), (L - 52, W - 20)):
            B(wu, wv, 0, wu + 28, wv + 28, 22,
              tex_sides="m_metal_darkBlu", tex_top="m_metal_darkBlu", tex_bottom="m_metal_darkBlu")

    def add_fence_run(x0: float, y0: float, x1: float, y1: float, height: float = 88.0,
                      tex: str = "fence_wood", step: float = 110.0, rail_z=0.0):
        """Vallado: travesano continuo mas postes cada `step` unidades."""
        if abs(x1 - x0) >= abs(y1 - y0):
            xa, xb = min(x0, x1), max(x0, x1)
            yc = 0.5 * (y0 + y1)
            add_brush(xa, yc - 4, rail_z, xb, yc + 4, height,
                      tex_sides=tex, tex_top=tex, tex_bottom=tex)
            a = xa
            while a <= xb:
                add_column(a, yc, rail_z, height + 14, size=12.0, tex="w_wood_dark_64")
                a += step
        else:
            ya, yb = min(y0, y1), max(y0, y1)
            xc = 0.5 * (x0 + x1)
            add_brush(xc - 4, ya, rail_z, xc + 4, yb, height,
                      tex_sides=tex, tex_top=tex, tex_bottom=tex)
            a = ya
            while a <= yb:
                add_column(xc, a, rail_z, height + 14, size=12.0, tex="w_wood_dark_64")
                a += step

    # Gran cementerio de vehiculos al norte de la autopista, en la franja de
    # tierra entre el Diner y la Granja (X[-620..340], Y[1560..2020]).
    add_bus_wreck(-560, 1620, along_x=True)
    add_bus_wreck(-560, 1780, along_x=True)
    add_bus_wreck(-200, 1600, along_x=True)
    add_car_wreck(-200, 1780, along_x=True, tex="metal_stB")
    add_car_wreck(120, 1800, along_x=False)
    add_car_wreck(-40, 1900, along_x=True, tex="w_wood_dark_64")
    add_fence_run(-620, 1560, -620, 2020, height=80.0, tex="fence_wood")
    add_fence_run(-620, 1560, 340, 1560, height=80.0, tex="fence_wood")
    add_fence_run(-620, 2020, 340, 2020, height=80.0, tex="fence_wood")
    for (gx, gy) in ((-560, 1600), (-560, 1980), (-200, 1580), (300, 1900)):
        add_street_lamp(gx, gy)
    for (nlx, nly) in ((-400, 1700), (0, 1700), (-200, 1900), (200, 1860), (-100, 1620)):
        add_light(nlx, nly, 140, 190, 198, 215, 540)

    # Vehiculos aparcados en el patio de las torres de refrigeracion
    add_car_wreck(2050, 600, along_x=True, tex="w_wood_dark_64")
    add_car_wreck(2280, 600, along_x=False, tex="metal_stB")

    # Luces de apoyo en el patio de las torres de refrigeracion y en los accesos
    # al maizal, para que ninguna zona transitable quede por debajo del umbral.
    for (elx, ely) in ((1560, 700), (1900, 700), (2200, 760), (2400, 700), (1700, 560)):
        add_light(elx, ely, 170, 150, 195, 245, 780)
    for (wlx, wly) in ((-400, 1620), (0, 1620), (200, 1760), (-300, 1900), (280, 1620)):
        add_light(wlx, wly, 150, 185, 200, 215, 640)
    for (clx, cly) in ((-450, -300), (-450, 100), (-450, 500), (-450, 900)):
        add_light(clx, cly, 130, 165, 180, 135, 560)

    # --- Alumbrado del circuito de la autopista ------------------------------
    for ly in (-1700, -1300, -900, -500, -100, 300, 700, 1100, 1500, 1900, 2300):
        add_street_lamp(-1185, ly)
    for lx in (-1400, -1000, -600, -200, 200, 600, 1000, 1400):
        add_street_lamp(lx, 985)
    for ly2 in (-1700, -1300, -900, -500, -100, 300, 700, 1100):
        add_street_lamp(985, ly2)
    for lx2 in (-1400, -1000, 800, 1200):
        add_street_lamp(lx2, -1490)

    # --- Vallado perimetral del maizal (con hueco para el sendero) -----------
    add_fence_run(-380, -480, -380, 1180, height=96.0, tex="fence_wood")
    add_fence_run(380, -480, 380, 1180, height=96.0, tex="fence_wood")
    add_fence_run(-380, -480, -80, -480, height=96.0, tex="fence_wood")
    add_fence_run(80, -480, 380, -480, height=96.0, tex="fence_wood")
    add_fence_run(-380, 1180, -80, 1180, height=96.0, tex="fence_wood")
    add_fence_run(80, 1180, 380, 1180, height=96.0, tex="fence_wood")

    # --- Sefiales de carretera del circuito ----------------------------------
    for (sgx, sgy, face, tex) in (
        (-1180, -400, "+x", "sign_tunnel"),
        (-1180, 1700, "+x", "sign_diner"),
        (985, 0, "-x", "sign_power"),
        (985, 1300, "-x", "sign_farm"),
        (-700, 990, "+y", "sign_depot"),
        (700, 990, "+y", "sign_town"),
        (-1180, -1700, "+x", "sign_depot"),
        (985, -1700, "-x", "sign_town"),
    ):
        add_brush(sgx - 6, sgy - 6, 0, sgx + 6, sgy + 6, 150,
                  tex_sides="m_metal_darkBlu", tex_top="m_metal_darkBlu", tex_bottom="m_metal_darkBlu")
        if face == "+x":
            add_brush(sgx + 6, sgy - 60, 110, sgx + 12, sgy + 60, 172,
                      tex_sides="metal_stB", face_tex={"+x": tex})
        elif face == "-x":
            add_brush(sgx - 12, sgy - 60, 110, sgx - 6, sgy + 60, 172,
                      tex_sides="metal_stB", face_tex={"-x": tex})
        elif face == "+y":
            add_brush(sgx - 60, sgy + 6, 110, sgx + 60, sgy + 12, 172,
                      tex_sides="metal_stB", face_tex={"+y": tex})
        else:
            add_brush(sgx - 60, sgy - 12, 110, sgx + 60, sgy - 6, 172,
                      tex_sides="metal_stB", face_tex={"-y": tex})

    # ========================================================================
    # RED DE WAYPOINTS (.WAY) CONECTANDO LAS 6 ZONAS Y SUS PUERTAS COMPRABLES
    # ========================================================================
    # Definimos los nodos (id 1..N) y sus enlaces bidireccionales + wayTarget en puertas
    wp_nodes: Dict[int, Tuple[int, int, int, str, List[int]]] = {
        # Bus Depot Interior (1..3) -> Puerta Depot (4) -> Exterior Bus Depot (5..6)
        1: (-1980, -1960, 36, "", [2, 3]),
        2: (-1980, -1760, 36, "", [1, 3]),
        3: (-1680, -1860, 36, "", [1, 2, 4]),
        4: (-1560, -1860, 36, "door_depot", [3, 5]),
        5: (-1360, -1860, 36, "", [4, 6, 26]),
        6: (-1360, -1320, 36, "", [5, 7]),
        # Highway Tunnel (7..9)
        7: (-1820, -780, 36, "", [6, 8]),
        8: (-1820, 0, 36, "", [7, 9]),
        9: (-1820, 780, 36, "", [8, 10]),
        # Diner Exterior (10..11), Diner Interior (12..13), Garage Interior (14..15)
        10: (-1960, 1500, 36, "", [9, 11, 14]),
        11: (-1120, 1500, 36, "", [10, 12, 16]),
        12: (-1120, 1696, 36, "door_diner", [11, 13]),
        13: (-1120, 1920, 36, "", [12]),
        14: (-1960, 1616, 36, "door_garage", [10, 15]),
        15: (-1960, 1920, 36, "", [14]),
        # Carretera Norte y Desvio al Maizal / Nacht Bunker (16..18)
        16: (0, 1360, 36, "", [11, 17, 19]),
        17: (0, 520, 36, "", [16, 18]),
        18: (0, -120, 36, "", [17]),
        # Farm Exterior (19) -> Puerta Farm (20) -> Patio Farm (21), Entrada Granero (34) y Barn Interior (22)
        19: (1340, 1460, 36, "", [16, 20, 23]),
        20: (1516, 1460, 36, "door_farm", [19, 21]),
        21: (1590, 1460, 36, "", [20, 34]),
        34: (1590, 1160, 36, "", [21, 22]),
        22: (1960, 1160, 36, "", [34]),
        # Carretera Este y Power Station (23..25, 33, 35)
        23: (1340, 480, 36, "", [19, 24]),
        24: (1360, -160, 36, "", [23, 25, 35]),
        25: (1596, -160, 36, "door_power", [24, 33]),
        33: (1820, -420, 36, "", [25]),
        35: (1360, -1380, 36, "", [24, 26]),
        # Town (Calle central 26..27, Bar 28..29, Banco 30..31, Vault Pack-a-Punch 32)
        26: (520, -1580, 36, "", [35, 27]),
        27: (-120, -1620, 36, "", [5, 26, 28, 30]),
        28: (-140, -1464, 36, "door_bar", [27, 29]),
        29: (-140, -1300, 36, "", [28]),
        30: (-100, -1836, 36, "door_bank", [27, 31]),
        31: (-100, -2080, 36, "", [30, 32]),
        32: (176, -2080, 36, "door_vault", [31]),
    }

    for wid in sorted(wp_nodes.keys()):
        ox, oy, oz, spec, targets = wp_nodes[wid]
        waypoints.append(
            {
                "id": wid,
                "origin": (ox, oy, oz),
                "special": spec,
                "targets": targets[:8],
            }
        )

    return world_brushes, submodels, point_entities, lights, waypoints


# ============================================================================
# COMPILADOR BSP30 (GEOMETRIA, ARBOLES KD/BSP, HULLS 0..3, LIGHTMAPS RGB)
# ============================================================================

def brush_to_quads(b: BoxBrush, max_span: float = 768.0) -> List[QuadFace]:
    """Genera las caras de un BoxBrush subdivididas para que extents <= 240."""
    quads: List[QuadFace] = []

    def subdivide_rect(axis: int, dist: float, side: int, u0: float, v0: float, u1: float, v1: float, tex: str):
        fullbright = FULLBRIGHT_TEXTURES.get(tex)
        step = max_span
        nu = max(1, int(math.ceil((u1 - u0) / step)))
        nv = max(1, int(math.ceil((v1 - v0) / step)))
        du = (u1 - u0) / nu
        dv = (v1 - v0) / nv
        for iu in range(nu):
            for iv in range(nv):
                quads.append(
                    QuadFace(
                        axis=axis,
                        dist=dist,
                        side=side,
                        u0=u0 + iu * du,
                        v0=v0 + iv * dv,
                        u1=u0 + (iu + 1) * du,
                        v1=v0 + (iv + 1) * dv,
                        tex_name=tex,
                        fullbright=fullbright,
                    )
                )

    if "-x" not in b.skip_faces:
        subdivide_rect(0, b.xmin, 1, b.ymin, b.zmin, b.ymax, b.zmax, b.get_tex("-x"))
    if "+x" not in b.skip_faces:
        subdivide_rect(0, b.xmax, 0, b.ymin, b.zmin, b.ymax, b.zmax, b.get_tex("+x"))
    if "-y" not in b.skip_faces:
        subdivide_rect(1, b.ymin, 1, b.xmin, b.zmin, b.xmax, b.zmax, b.get_tex("-y"))
    if "+y" not in b.skip_faces:
        subdivide_rect(1, b.ymax, 0, b.xmin, b.zmin, b.xmax, b.zmax, b.get_tex("+y"))
    if "-z" not in b.skip_faces:
        subdivide_rect(2, b.zmin, 1, b.xmin, b.ymin, b.xmax, b.ymax, b.get_tex("-z"))
    if "+z" not in b.skip_faces:
        subdivide_rect(2, b.zmax, 0, b.xmin, b.ymin, b.xmax, b.ymax, b.get_tex("+z"))
    return quads


def quad_vertices(q: QuadFace) -> List[Tuple[float, float, float]]:
    if q.axis == 0:
        pts = [(q.dist, q.u0, q.v0), (q.dist, q.u1, q.v0), (q.dist, q.u1, q.v1), (q.dist, q.u0, q.v1)]
        nout = (-1.0, 0.0, 0.0) if q.side else (1.0, 0.0, 0.0)
    elif q.axis == 1:
        pts = [(q.u0, q.dist, q.v0), (q.u1, q.dist, q.v0), (q.u1, q.dist, q.v1), (q.u0, q.dist, q.v1)]
        nout = (0.0, -1.0, 0.0) if q.side else (0.0, 1.0, 0.0)
    else:
        pts = [(q.u0, q.v0, q.dist), (q.u1, q.v0, q.dist), (q.u1, q.v1, q.dist), (q.u0, q.v1, q.dist)]
        nout = (0.0, 0.0, -1.0) if q.side else (0.0, 0.0, 1.0)

    v0, v1, v2 = pts[0], pts[1], pts[2]
    ux, uy, uz = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    vx, vy, vz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    dot = cx * nout[0] + cy * nout[1] + cz * nout[2]
    if dot > 0:
        pts = [pts[0], pts[3], pts[2], pts[1]]
    return pts


def export_map_file(
    out_map_path: str,
    world_brushes: List[BoxBrush],
    submodels: List[Tuple[str, List[BoxBrush], dict]],
    point_entities: List[dict],
):
    """Escribe el archivo fuente .map estandar para TrenchBroom / J.A.C.K."""
    os.makedirs(os.path.dirname(out_map_path), exist_ok=True)

    def fmt_brush(b: BoxBrush) -> str:
        x0, y0, z0, x1, y1, z1 = int(b.xmin), int(b.ymin), int(b.zmin), int(b.xmax), int(b.ymax), int(b.zmax)
        t_top = b.get_tex("+z")
        t_bot = b.get_tex("-z")
        t_side = b.tex_sides
        return "\n".join(
            [
                "{",
                f"( {x0} {y0} {z1} ) ( {x0} {y1} {z1} ) ( {x1} {y1} {z1} ) {t_top} 0 0 0 1 1",
                f"( {x0} {y1} {z0} ) ( {x0} {y0} {z0} ) ( {x1} {y0} {z0} ) {t_bot} 0 0 0 1 1",
                f"( {x0} {y0} {z1} ) ( {x0} {y0} {z0} ) ( {x0} {y1} {z0} ) {t_side} 0 0 0 1 1",
                f"( {x1} {y1} {z1} ) ( {x1} {y1} {z0} ) ( {x1} {y0} {z0} ) {t_side} 0 0 0 1 1",
                f"( {x1} {y0} {z1} ) ( {x1} {y0} {z0} ) ( {x0} {y0} {z0} ) {t_side} 0 0 0 1 1",
                f"( {x0} {y1} {z1} ) ( {x0} {y1} {z0} ) ( {x1} {y1} {z0} ) {t_side} 0 0 0 1 1",
                "}",
            ]
        )

    with open(out_map_path, "w", encoding="utf-8") as f:
        f.write('{\n"classname" "worldspawn"\n"mapversion" "220"\n"sky" "gfx/env/CloudyNightSky.png"\n"fog" "220 1450 42 46 48"\n')
        for b in world_brushes:
            f.write(fmt_brush(b) + "\n")
        f.write("}\n")
        for cname, b_list, kv in submodels:
            f.write('{\n"classname" "' + cname + '"\n')
            for k, v in kv.items():
                f.write(f'"{k}" "{v}"\n')
            for b in b_list:
                f.write(fmt_brush(b) + "\n")
            f.write("}\n")
        for ent in point_entities:
            f.write("{\n")
            for k, v in ent.items():
                f.write(f'"{k}" "{v}"\n')
            f.write("}\n")


def compile_bsp30(
    out_bsp_path: str,
    world_brushes: List[BoxBrush],
    submodels: List[Tuple[str, List[BoxBrush], dict]],
    point_entities: List[dict],
    lights: List[PointLight],
):
    """Compila el mapa completo a formato binario Half-Life / NZ:P BSP v30."""
    tex_blobs = load_wad3_textures_from_town()
    tex_names = sorted(tex_blobs.keys())
    tex_index = {name: i for i, name in enumerate(tex_names)}

    # 1. Construir LUMP_TEXTURES
    num_tex = len(tex_names)
    tex_header_size = 4 + 4 * num_tex
    tex_offsets = []
    tex_payload = bytearray()
    for name in tex_names:
        blob = tex_blobs[name]
        tex_offsets.append(tex_header_size + len(tex_payload))
        tex_payload.extend(blob)
        while len(tex_payload) % 4 != 0:
            tex_payload.append(0)
    lump_textures = struct.pack(f"<i{num_tex}i", num_tex, *tex_offsets) + bytes(tex_payload)

    # Tablas globales de geometria BSP
    planes: List[Tuple[float, float, float, float, int]] = []
    plane_map: Dict[Tuple[int, float], int] = {}

    def get_plane_idx(axis: int, dist: float) -> int:
        key = (axis, round(float(dist), 2))
        if key not in plane_map:
            nx = 1.0 if axis == 0 else 0.0
            ny = 1.0 if axis == 1 else 0.0
            nz = 1.0 if axis == 2 else 0.0
            plane_map[key] = len(planes)
            planes.append((nx, ny, nz, float(key[1]), axis))
        return plane_map[key]

    texinfos: List[Tuple[float, float, float, float, float, float, float, float, int, int]] = []
    texinfo_map: Dict[Tuple[int, str], int] = {}

    def get_texinfo_idx(axis: int, tex_name: str) -> int:
        if tex_name not in tex_index:
            tex_name = "conc_road_D2"
        key = (axis, tex_name)
        if key not in texinfo_map:
            # Escala 0.25 (1 texel = 4 unidades) para minimizar lightmaps y extents <= 240
            sc = 0.25
            if axis == 0:
                vecs = (0.0, sc, 0.0, 0.0, 0.0, 0.0, -sc, 0.0)
            elif axis == 1:
                vecs = (sc, 0.0, 0.0, 0.0, 0.0, 0.0, -sc, 0.0)
            else:
                vecs = (sc, 0.0, 0.0, 0.0, 0.0, -sc, 0.0, 0.0)
            flags = TEX_SPECIAL if tex_name.startswith("sky") else 0
            texinfo_map[key] = len(texinfos)
            texinfos.append((*vecs, tex_index[tex_name], flags))
        return texinfo_map[key]

    vertices: List[Tuple[float, float, float]] = []
    vert_map: Dict[Tuple[int, int, int], int] = {}

    def get_vert_idx(pt: Tuple[float, float, float]) -> int:
        key = (int(round(pt[0] * 4)), int(round(pt[1] * 4)), int(round(pt[2] * 4)))
        if key not in vert_map:
            vert_map[key] = len(vertices)
            vertices.append((pt[0], pt[1], pt[2]))
        return vert_map[key]

    # Aristas: edge 0 es dummy
    edges: List[Tuple[int, int]] = [(0, 0)]
    edge_map: Dict[Tuple[int, int], int] = {}

    def get_surfedge(v0: int, v1: int) -> int:
        if (v0, v1) in edge_map:
            return edge_map[(v0, v1)]
        if (v1, v0) in edge_map:
            return -edge_map[(v1, v0)]
        idx = len(edges)
        edges.append((v0, v1))
        edge_map[(v0, v1)] = idx
        return idx

    surfedges: List[int] = []
    faces: List[bytes] = []
    face_meta: List[Tuple[int, Tuple[float, float, float, float, float, float]]] = []
    lighting_data = bytearray()

    # Rejilla espacial de luces: evita recorrer todos los focos por cada muestra
    # del lightmap (con ~200 luces el coste seria prohibitivo sin esta poda).
    LIGHT_CELL = 400.0
    light_grid: Dict[Tuple[int, int], List[int]] = {}

    def gidx(v: float) -> int:
        return int(math.floor(v / LIGHT_CELL))

    for li, pl in enumerate(lights):
        for gx in range(gidx(pl.x - pl.radius), gidx(pl.x + pl.radius) + 1):
            for gy in range(gidx(pl.y - pl.radius), gidx(pl.y + pl.radius) + 1):
                light_grid.setdefault((gx, gy), []).append(li)

    def lights_for_bbox(bminx: float, bminy: float, bmaxx: float, bmaxy: float) -> List[PointLight]:
        seen: set = set()
        out: List[PointLight] = []
        for gx in range(gidx(bminx), gidx(bmaxx) + 1):
            for gy in range(gidx(bminy), gidx(bmaxy) + 1):
                for li in light_grid.get((gx, gy), ()):
                    if li not in seen:
                        seen.add(li)
                        out.append(lights[li])
        return out

    def sample_lightmap_for_quad(q: QuadFace, ti_idx: int) -> int:
        if q.tex_name.startswith("sky"):
            return -1
        ti = texinfos[ti_idx]
        pts = quad_vertices(q)
        s_vals = [p[0] * ti[0] + p[1] * ti[1] + p[2] * ti[2] + ti[3] for p in pts]
        t_vals = [p[0] * ti[4] + p[1] * ti[5] + p[2] * ti[6] + ti[7] for p in pts]
        bmin_s = int(math.floor(min(s_vals) / 16.0))
        bmax_s = int(math.ceil(max(s_vals) / 16.0))
        bmin_t = int(math.floor(min(t_vals) / 16.0))
        bmax_t = int(math.ceil(max(t_vals) / 16.0))
        smax = (bmax_s - bmin_s) + 1
        tmax = (bmax_t - bmin_t) + 1

        if q.axis == 0:      # Plano X: la Y sale de (u0..u1)
            q_bx0, q_bx1 = q.dist, q.dist
            q_by0, q_by1 = min(q.u0, q.u1), max(q.u0, q.u1)
        elif q.axis == 1:    # Plano Y: la X sale de (u0..u1)
            q_bx0, q_bx1 = min(q.u0, q.u1), max(q.u0, q.u1)
            q_by0, q_by1 = q.dist, q.dist
        else:                # Plano Z: X de u y Y de v
            q_bx0, q_bx1 = min(q.u0, q.u1), max(q.u0, q.u1)
            q_by0, q_by1 = min(q.v0, q.v1), max(q.v0, q.v1)
        near_lights = lights_for_bbox(q_bx0, q_by0, q_bx1, q_by1)

        while len(lighting_data) % 3 != 0:
            lighting_data.append(0)
        lightofs = len(lighting_data)

        if q.fullbright is not None:
            r0, g0, b0 = q.fullbright
            for _ in range(smax * tmax):
                lighting_data.extend((r0, g0, b0))
            return lightofs

        # Luz ambiental base de Tranzit (noche cerrada con tinte azul verdoso)
        amb_r, amb_g, amb_b = 57.0, 63.0, 74.0
        for it in range(tmax):
            t_tex = (bmin_t + it) * 16.0
            for is_ in range(smax):
                s_tex = (bmin_s + is_) * 16.0
                # Reconstruir punto aproximado en el mundo a partir de (s_tex, t_tex)
                if q.axis == 0:
                    wx, wy, wz = q.dist, s_tex * 4.0, -t_tex * 4.0
                elif q.axis == 1:
                    wx, wy, wz = s_tex * 4.0, q.dist, -t_tex * 4.0
                else:
                    wx, wy, wz = s_tex * 4.0, -t_tex * 4.0, q.dist

                lr, lg, lb = amb_r, amb_g, amb_b
                for pl in near_lights:
                    dx = wx - pl.x
                    dy = wy - pl.y
                    dz = wz - pl.z
                    d2 = dx * dx + dy * dy + dz * dz
                    if d2 < pl.radius * pl.radius:
                        dist = math.sqrt(d2)
                        att = (1.0 - dist / pl.radius) ** 1.6
                        lr += pl.r * att * 0.45
                        lg += pl.g * att * 0.45
                        lb += pl.b * att * 0.45
                lighting_data.extend(
                    (
                        min(128, max(12, int(lr))),
                        min(128, max(12, int(lg))),
                        min(128, max(12, int(lb))),
                    )
                )
        return lightofs

    def emit_quad_face(q: QuadFace) -> int:
        pnum = get_plane_idx(q.axis, q.dist)
        ti_idx = get_texinfo_idx(q.axis, q.tex_name)
        pts = quad_vertices(q)
        v_idxs = [get_vert_idx(p) for p in pts]
        first_se = len(surfedges)
        for i in range(4):
            surfedges.append(get_surfedge(v_idxs[i], v_idxs[(i + 1) % 4]))
        lightofs = sample_lightmap_for_quad(q, ti_idx)
        styles = b"\x00\xff\xff\xff" if lightofs >= 0 else b"\xff\xff\xff\xff"
        f_idx = len(faces)
        faces.append(struct.pack("<hhihh4si", pnum, q.side, first_se, 4, ti_idx, styles, lightofs))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        face_meta.append((pnum, (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))))
        return f_idx

    # 2. Compilar caras del mundo (model 0) agrupadas por plano para asignarlas a los nodos BSP
    world_quads: List[QuadFace] = []
    for b in world_brushes:
        world_quads.extend(brush_to_quads(b))

    quads_by_plane: Dict[int, List[QuadFace]] = {}
    for q in world_quads:
        pnum = get_plane_idx(q.axis, q.dist)
        quads_by_plane.setdefault(pnum, []).append(q)

    # 3. Construir KD-Tree / BSP para Hulls de colision (LUMP_CLIPNODES)
    clipnodes: List[Tuple[int, int, int]] = []

    def build_clip_kdtree(
        boxes: List[Tuple[float, float, float, float, float, float]],
        bounds: Tuple[float, float, float, float, float, float],
        depth: int = 0,
    ):
        """Arbol de clipnodes con seleccion de plano por SAH (Surface Area Heuristic).

        Elegir el plano que menos cajas atraviesa (en lugar del mas cercano al
        centro) reduce los clipnodes de ~19.000 a ~2.300 por hull, lo que permite
        muchisimo mas detalle geometrico sin superar MAX_MAP_CLIPNODES (32767).

        NO escribe en `clipnodes`: devuelve un arbol en memoria
            CONTENTS_SOLID / CONTENTS_EMPTY (int)  -> hoja
            [planenum, hijo_front, hijo_back]      -> nodo
        para que emit_clip_tree() lo serialice en PRE-ORDEN (requisito del motor,
        ver el docstring de emit_clip_tree).
        """
        x0, y0, z0, x1, y1, z1 = bounds
        eps = 0.05
        active = []
        for b in boxes:
            if b[0] >= x1 - eps or b[3] <= x0 + eps:
                continue
            if b[1] >= y1 - eps or b[4] <= y0 + eps:
                continue
            if b[2] >= z1 - eps or b[5] <= z0 + eps:
                continue
            # Si una caja cubre por completo la celda actual, toda la celda es SOLID
            if (
                b[0] <= x0 + eps and b[3] >= x1 - eps and
                b[1] <= y0 + eps and b[4] >= y1 - eps and
                b[2] <= z0 + eps and b[5] >= z1 - eps
            ):
                return CONTENTS_SOLID
            active.append(b)

        if not active or depth > 64:
            return CONTENTS_EMPTY

        n = len(active)
        best: Optional[Tuple[float, int, float]] = None
        for axis in (0, 1, 2):
            lo, hi = bounds[axis], bounds[axis + 3]
            if hi - lo < 2.0:
                continue
            mins = sorted(b[axis] for b in active)
            maxs = sorted(b[axis + 3] for b in active)
            cands = sorted({v for b in active for v in (b[axis], b[axis + 3]) if lo + 0.5 < v < hi - 0.5})
            if not cands:
                continue
            if len(cands) > 48:
                step = len(cands) / 48.0
                cands = [cands[int(i * step)] for i in range(48)]
            mid = 0.5 * (lo + hi)
            for v in cands:
                n_left = bisect.bisect_right(maxs, v)
                n_right = n - bisect.bisect_left(mins, v)
                n_straddle = n - n_left - n_right
                cost = (n_left + n_right) + 4.0 * n_straddle + 0.001 * abs(v - mid)
                if best is None or cost < best[0]:
                    best = (cost, axis, v)

        if best is None:
            return CONTENTS_SOLID

        _cost, best_axis, best_coord = best
        b_front = list(bounds)
        b_back = list(bounds)
        b_front[best_axis] = best_coord
        b_back[best_axis + 3] = best_coord

        c_front = build_clip_kdtree(active, tuple(b_front), depth + 1)
        c_back = build_clip_kdtree(active, tuple(b_back), depth + 1)
        # Solo se colapsan hojas identicas: si ambos hijos son el mismo contenido
        # el nodo sobra. Nunca se colapsan subarboles (seria un grafo, y al
        # serializar se duplicarian nodos sin control).
        if isinstance(c_front, int) and c_front == c_back:
            return c_front

        pnum = get_plane_idx(best_axis, best_coord)
        return [pnum, c_front, c_back]

    def emit_clip_tree(tree) -> int:
        """Serializa el arbol en LUMP_CLIPNODES en PRE-ORDEN (padre antes que hijos).

        OBLIGATORIO: el motor comprueba en SV_HullPointContents

            if (num < hull->firstclipnode || num > hull->lastclipnode)
                Sys_Error ("bad node number");

        y hull->firstclipnode es el headnode del hull (gl_model.c, bucle de
        submodelos). Es decir: la raiz de cada arbol de clipnodes TIENE que ser
        el indice MAS BAJO de su bloque. Emitir en post-orden (como se hacia
        antes) dejaba la raiz con el indice mas alto y el mapa abortaba con
        "bad node number" nada mas conectarse el cliente.
        """
        # Hoja: el valor de contenido ya es un indice valido (< 0).
        if isinstance(tree, int):
            return tree

        root = len(clipnodes)
        clipnodes.append((tree[0], CONTENTS_EMPTY, CONTENTS_EMPTY))
        stack = [(tree, root)]
        while stack:
            node, idx = stack.pop()
            for slot in (1, 0):
                child = node[1 + slot]
                if isinstance(child, int):
                    cidx = child
                else:
                    cidx = len(clipnodes)
                    clipnodes.append((child[0], CONTENTS_EMPTY, CONTENTS_EMPTY))
                    stack.append((child, cidx))
                _pn, c0, c1 = clipnodes[idx]
                clipnodes[idx] = (_pn, cidx, c1) if slot == 0 else (_pn, c0, cidx)
        return root

    def build_hull_for_brushes(brushes: List[BoxBrush], hx: float, hy: float, hz: float) -> int:
        expanded = [
            (b.xmin - hx, b.ymin - hy, b.zmin - hz, b.xmax + hx, b.ymax + hy, b.zmax + hz)
            for b in brushes
        ]
        before = len(clipnodes)
        tree = build_clip_kdtree(expanded, (-3200.0, -3200.0, -1024.0, 3200.0, 3200.0, 1024.0))
        # Un hull vacio se fuerza a nodo real (comportamiento anterior): algunos
        # puntos del motor esperan un headnode >= 0.
        if isinstance(tree, int):
            tree = [get_plane_idx(2, -2048.0), tree, tree]
        idx = emit_clip_tree(tree)
        print(f"[INFO]   Hull({hx},{hy},{hz}): {len(clipnodes) - before} clipnodes")
        return idx

    # 4. Construir LUMP_NODES y LUMP_LEAFS para el mundo (model 0)
    # Leaf 0 es siempre CONTENTS_SOLID
    leafs: List[bytes] = [
        struct.pack("<iihhhhhhHH4B", CONTENTS_SOLID, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    ]
    marksurfaces: List[int] = []
    nodes: List[List] = []  # [planenum, c0, c1, mins(3), maxs(3), firstface, numfaces]
    emitted_planes: set = set()

    world_boxes_h0 = [(b.xmin, b.ymin, b.zmin, b.xmax, b.ymax, b.zmax) for b in world_brushes]

    def build_world_bsp(
        boxes: List[Tuple[float, float, float, float, float, float]],
        bounds: Tuple[float, float, float, float, float, float],
        remaining_planes: List[Tuple[int, float, int]],
        depth: int = 0,
    ) -> int:
        x0, y0, z0, x1, y1, z1 = bounds
        eps = 0.05
        active = []
        for b in boxes:
            if b[0] >= x1 - eps or b[3] <= x0 + eps:
                continue
            if b[1] >= y1 - eps or b[4] <= y0 + eps:
                continue
            if b[2] >= z1 - eps or b[5] <= z0 + eps:
                continue
            if (
                b[0] <= x0 + eps and b[3] >= x1 - eps and
                b[1] <= y0 + eps and b[4] >= y1 - eps and
                b[2] <= z0 + eps and b[5] >= z1 - eps and
                not remaining_planes
            ):
                return -1  # leaf 0 (CONTENTS_SOLID)
            active.append(b)

        # Si aun quedan planos con caras visuales, dividir primero por ellos para que
        # cada plano con caras tenga su propio nodo en LUMP_NODES
        if remaining_planes:
            mid_idx = len(remaining_planes) // 2
            axis, coord, pnum = remaining_planes[mid_idx]
            left_pl = remaining_planes[:mid_idx]
            right_pl = remaining_planes[mid_idx + 1 :]

            node_idx = len(nodes)
            nodes.append([pnum, -1, -1, [int(x0), int(y0), int(z0)], [int(x1), int(y1), int(z1)], 0, 0])

            if pnum in quads_by_plane and pnum not in emitted_planes:
                emitted_planes.add(pnum)
                qlist = quads_by_plane[pnum]
                ff = len(faces)
                for q in qlist:
                    emit_quad_face(q)
                nf = len(faces) - ff
                nodes[node_idx][5] = ff
                nodes[node_idx][6] = nf

            c_front = build_world_bsp(active, bounds, right_pl, depth + 1)
            c_back = build_world_bsp(active, bounds, left_pl, depth + 1)
            nodes[node_idx][1] = c_front
            nodes[node_idx][2] = c_back
            return node_idx

        if not active or depth > 26:
            leaf_idx = len(leafs)
            leafs.append(
                struct.pack(
                    "<iihhhhhhHH4B",
                    CONTENTS_EMPTY,
                    -1,
                    int(max(-32000, x0)), int(max(-32000, y0)), int(max(-32000, z0)),
                    int(min(32000, x1)), int(min(32000, y1)), int(min(32000, z1)),
                    0, 0,
                    0, 0, 0, 0,
                )
            )
            return -1 - leaf_idx

        best_axis = -1
        best_coord = 0.0
        best_score = 1e18
        rmins = (x0, y0, z0)
        rmaxs = (x1, y1, z1)
        spans = (x1 - x0, y1 - y0, z1 - z0)

        for axis in (0, 1, 2):
            lo, hi = rmins[axis], rmaxs[axis]
            mid = 0.5 * (lo + hi)
            for b in active:
                for val in (b[axis], b[axis + 3]):
                    if lo + 0.5 < val < hi - 0.5:
                        score = abs(val - mid) / max(1.0, spans[axis])
                        if score < best_score:
                            best_score = score
                            best_axis = axis
                            best_coord = val

        if best_axis < 0:
            return -1  # leaf 0 (CONTENTS_SOLID)

        pnum = get_plane_idx(best_axis, best_coord)
        node_idx = len(nodes)
        nodes.append([pnum, -1, -1, [int(x0), int(y0), int(z0)], [int(x1), int(y1), int(z1)], 0, 0])

        b_front = list(bounds)
        b_back = list(bounds)
        b_front[best_axis] = best_coord
        b_back[best_axis + 3] = best_coord

        c_front = build_world_bsp(active, tuple(b_front), [], depth + 1)
        c_back = build_world_bsp(active, tuple(b_back), [], depth + 1)
        nodes[node_idx][1] = c_front
        nodes[node_idx][2] = c_back
        return node_idx

    #Construimos primero el arbol geometrico exacto para Hull 0 (colision de rayos/balas)
    # y aseguramos que cada plano con caras visuales tenga un nodo que las dibuje.
    plane_list = []
    for pnum in sorted(quads_by_plane.keys()):
        nx, ny, nz, dist, axis = planes[pnum]
        plane_list.append((axis, dist, pnum))

    # Primero creamos el arbol espacial real de colision Hull 0:
    def build_spatial_world_bsp(
        boxes: List[Tuple[float, float, float, float, float, float]],
        bounds: Tuple[float, float, float, float, float, float],
        depth: int = 0,
    ) -> int:
        x0, y0, z0, x1, y1, z1 = bounds
        eps = 0.05
        active = []
        for b in boxes:
            if b[0] >= x1 - eps or b[3] <= x0 + eps:
                continue
            if b[1] >= y1 - eps or b[4] <= y0 + eps:
                continue
            if b[2] >= z1 - eps or b[5] <= z0 + eps:
                continue
            if (
                b[0] <= x0 + eps and b[3] >= x1 - eps and
                b[1] <= y0 + eps and b[4] >= y1 - eps and
                b[2] <= z0 + eps and b[5] >= z1 - eps
            ):
                return -1  # CONTENTS_SOLID leaf 0
            active.append(b)

        if not active or depth > 32:
            leaf_idx = len(leafs)
            leafs.append(
                struct.pack(
                    "<iihhhhhhHH4B",
                    CONTENTS_EMPTY,
                    -1,
                    int(max(-32000, x0)), int(max(-32000, y0)), int(max(-32000, z0)),
                    int(min(32000, x1)), int(min(32000, y1)), int(min(32000, z1)),
                    0, 0,
                    0, 0, 0, 0,
                )
            )
            return -1 - leaf_idx

        # Seleccion del plano de corte por SAH (Surface Area Heuristic): elegir el
        # plano que menos cajas atraviesa en lugar del mas cercano al centro.
        # Con el detalle actual del mapa esto baja LUMP_NODES de ~30.000 a ~10.000,
        # dejando margen holgado frente a MAX_MAP_NODES (32767).
        n = len(active)
        best: Optional[Tuple[float, int, float]] = None
        for axis in (0, 1, 2):
            lo, hi = bounds[axis], bounds[axis + 3]
            if hi - lo < 2.0:
                continue
            mins = sorted(b[axis] for b in active)
            maxs = sorted(b[axis + 3] for b in active)
            cands = sorted({v for b in active for v in (b[axis], b[axis + 3]) if lo + 0.5 < v < hi - 0.5})
            if not cands:
                continue
            if len(cands) > 48:
                step = len(cands) / 48.0
                cands = [cands[int(i * step)] for i in range(48)]
            mid = 0.5 * (lo + hi)
            for v in cands:
                n_left = bisect.bisect_right(maxs, v)
                n_right = n - bisect.bisect_left(mins, v)
                n_straddle = n - n_left - n_right
                cost = (n_left + n_right) + 4.0 * n_straddle + 0.001 * abs(v - mid)
                if best is None or cost < best[0]:
                    best = (cost, axis, v)

        if best is None:
            return -1
        _cost, best_axis, best_coord = best

        pnum = get_plane_idx(best_axis, best_coord)
        node_idx = len(nodes)
        # Ampliar minmaxs del nodo a todo el mapa para que ninguna cara asociada a este plano
        # sea descartada por R_CullBox cuando el plano aparece en otra rama
        nodes.append([pnum, -1, -1, [-2600, -2600, -256], [2600, 2600, 640], 0, 0])
        node_cells.append(bounds)

        b_front = list(bounds)
        b_back = list(bounds)
        b_front[best_axis] = best_coord
        b_back[best_axis + 3] = best_coord

        c_front = build_spatial_world_bsp(active, tuple(b_front), depth + 1)
        c_back = build_spatial_world_bsp(active, tuple(b_back), depth + 1)
        nodes[node_idx][1] = c_front
        nodes[node_idx][2] = c_back
        return node_idx

    node_cells: List[Tuple[float, float, float, float, float, float]] = []
    world_headnode0 = build_spatial_world_bsp(world_boxes_h0, (-2600.0, -2600.0, -256.0, 2600.0, 2600.0, 640.0))
    assert world_headnode0 == 0

    # Asignacion de caras a nodos.
    # Cada nodo solo puede referenciar un rango CONTIGUO de caras (firstsurface,
    # numsurfaces), asi que primero elegimos a que nodo va cada grupo de caras que
    # comparte plano y despues emitimos las caras ordenadas por nodo.
    #   1) Si existe un nodo cuyo plano de corte es exactamente el plano de las caras,
    #      se usa ese (es lo que hacen los compiladores reales).
    #   2) Si no, se busca el nodo mas profundo cuya celda contiene el centro del grupo.
    # Asi ninguna cara se queda huerfana y R_MarkLights sigue podando por el arbol.
    plane_first_node: Dict[int, int] = {}
    for nd_i, nd in enumerate(nodes):
        if nd[0] not in plane_first_node:
            plane_first_node[nd[0]] = nd_i

    def pick_node_for_group(qlist) -> int:
        """Nodo mas profundo cuya celda contiene el centro del grupo de caras."""
        acc = [0.0, 0.0, 0.0]
        for q in qlist:
            u = (0, 1, 2)
            others = [a for a in u if a != q.axis]
            pt = [0.0, 0.0, 0.0]
            pt[q.axis] = q.dist
            pt[others[0]] = 0.5 * (q.u0 + q.u1)
            pt[others[1]] = 0.5 * (q.v0 + q.v1)
            acc[0] += pt[0]
            acc[1] += pt[1]
            acc[2] += pt[2]
        n = max(1, len(qlist))
        px, py, pz = acc[0] / n, acc[1] / n, acc[2] / n
        ni = 0
        for _ in range(64):
            _nx, _ny, _nz, dist, axis = planes[nodes[ni][0]]
            coord = px if axis == 0 else (py if axis == 1 else pz)
            child = nodes[ni][1] if coord >= dist else nodes[ni][2]
            if child < 0:
                break
            ni = child
        return ni

    node_face_planes: Dict[int, List[int]] = {}
    for pnum, qlist in quads_by_plane.items():
        target = plane_first_node.get(pnum)
        if target is None:
            target = pick_node_for_group(qlist)
        node_face_planes.setdefault(target, []).append(pnum)

    for nd_i in sorted(node_face_planes.keys()):
        ff = len(faces)
        for pnum in node_face_planes[nd_i]:
            for q in quads_by_plane[pnum]:
                emit_quad_face(q)
        nf = len(faces) - ff
        if nf:
            nodes[nd_i][5] = ff
            nodes[nd_i][6] = nf

    world_numfaces = len(faces)
    world_visleafs = len(leafs) - 1

    # Marcar todas las superficies del mundo en las hojas vacias para que R_MarkLeaves
    # active visframe en las caras visibles
    for f_i in range(world_numfaces):
        marksurfaces.append(f_i)

    # Actualizar las hojas vacias (1..world_visleafs) para que apunten a marksurfaces
    for l_i in range(1, len(leafs)):
        old_data = struct.unpack("<iihhhhhhHH4B", leafs[l_i])
        leafs[l_i] = struct.pack(
            "<iihhhhhhHH4B",
            old_data[0],
            old_data[1],
            old_data[2], old_data[3], old_data[4],
            old_data[5], old_data[6], old_data[7],
            0, world_numfaces,
            0, 0, 0, 0,
        )

    # Calcular Hulls 1, 2 y 3 del mundo (model 0)
    # Importante: En gl_model.c Mod_LoadClipnodes carga TODOS los clipnodes en un unico array
    # donde hull 1 del mundo empieza en 0! Por tanto generamos Hull 1 del mundo en el indice 0.
    w_h1 = build_hull_for_brushes(world_brushes, 16.0, 16.0, 36.0)
    w_h2 = build_hull_for_brushes(world_brushes, 32.0, 32.0, 32.0)
    w_h3 = build_hull_for_brushes(world_brushes, 16.0, 16.0, 18.0)

    models_bin: List[bytes] = []
    models_bin.append(
        struct.pack(
            "<9f7i",
            -2500.0, -2500.0, -128.0,
            2500.0, 2500.0, 560.0,
            0.0, 0.0, 0.0,
            world_headnode0, w_h1, w_h2, w_h3,
            world_visleafs,
            0, world_numfaces,
        )
    )

    # 5. Compilar cada submodelo (*1, *2, ...)
    for idx, (cname, b_list, kv) in enumerate(submodels, start=1):
        ff = len(faces)
        for b in b_list:
            for q in brush_to_quads(b):
                emit_quad_face(q)
        nf = len(faces) - ff

        xmin = min(b.xmin for b in b_list)
        ymin = min(b.ymin for b in b_list)
        zmin = min(b.zmin for b in b_list)
        xmax = max(b.xmax for b in b_list)
        ymax = max(b.ymax for b in b_list)
        zmax = max(b.zmax for b in b_list)

        # Nodo Hull 0 para el submodelo en LUMP_NODES
        sub_boxes_h0 = [(b.xmin, b.ymin, b.zmin, b.xmax, b.ymax, b.zmax) for b in b_list]
        sub_h0 = build_spatial_world_bsp(
            sub_boxes_h0,
            (xmin - 64.0, ymin - 64.0, zmin - 64.0, xmax + 64.0, ymax + 64.0, zmax + 64.0),
        )
        if sub_h0 < 0:
            sub_h0 = 0
        sub_h1 = build_hull_for_brushes(b_list, 16.0, 16.0, 36.0)
        sub_h2 = build_hull_for_brushes(b_list, 32.0, 32.0, 32.0)
        sub_h3 = build_hull_for_brushes(b_list, 16.0, 16.0, 18.0)

        models_bin.append(
            struct.pack(
                "<9f7i",
                xmin, ymin, zmin,
                xmax, ymax, zmax,
                0.0, 0.0, 0.0,
                sub_h0, sub_h1, sub_h2, sub_h3,
                1,
                ff, nf,
            )
        )

        ent_dict = {"classname": cname, "model": f"*{idx}"}
        ent_dict.update(kv)
        point_entities.insert(idx - 1, ent_dict)

    # 6. Construir LUMP_ENTITIES
    ent_lines = [
        "{",
        '"compiler" "NZP-Tranzit-Builder 1.0"',
        '"mapversion" "220"',
        '"sky" "gfx/env/CloudyNightSky.png"',
        '"fog" "220 1450 42 46 48"',
        '"r_skycolor" "42 46 48"',
        '"chaptertitle" "GREEN RUN (TRANZIT)"',
        '"location" "Hanford Site, Washington"',
        '"date" "October 21, 2025"',
        '"person" "Victis Crew"',
        '"sounds" "1"',
        '"light" "25"',
        '"classname" "worldspawn"',
        "}",
    ]
    for ent in point_entities:
        ent_lines.append("{")
        for k, v in ent.items():
            ent_lines.append(f'"{k}" "{v}"')
        ent_lines.append("}")
    lump_entities = ("\n".join(ent_lines) + "\n\x00").encode("latin1")

    # 7. Empaquetar todos los 15 Lumps del archivo BSP v30
    lump_planes = b"".join(struct.pack("<ffffi", *p) for p in planes)
    lump_vertexes = b"".join(struct.pack("<fff", *v) for v in vertices)
    lump_visibility = b""
    lump_nodes = b"".join(
        struct.pack(
            "<ihhhhhhhhHH",
            nd[0], nd[1], nd[2],
            nd[3][0], nd[3][1], nd[3][2],
            nd[4][0], nd[4][1], nd[4][2],
            nd[5], nd[6],
        )
        for nd in nodes
    )
    lump_texinfo = b"".join(struct.pack("<8fii", *ti) for ti in texinfos)
    lump_faces = b"".join(faces)
    lump_lighting = bytes(lighting_data)
    if len(clipnodes) > MAX_MAP_CLIPNODES:
        raise SystemExit(
            f"[ERROR] {len(clipnodes)} clipnodes supera MAX_MAP_CLIPNODES "
            f"({MAX_MAP_CLIPNODES}) del motor; hay que reducir brushes."
        )

    # --- Validacion de la invariante de headnodes ---------------------------
    # SV_HullPointContents (world.c) aborta con "bad node number" en cuanto
    # encuentra un indice menor que hull->firstclipnode, y firstclipnode es el
    # headnode del hull (gl_model.c). Por tanto la raiz de CADA arbol (tanto de
    # clipnodes como de nodes para el hull 0) tiene que ser el indice MAS BAJO
    # de los nodos que cuelgan de ella. Esta comprobacion evita que una futura
    # optimizacion del arbol reintroduzca el fallo sin que se note al compilar.
    def reachable(first: int, table):
        seen = set()
        stack = [first]
        while stack:
            i = stack.pop()
            if i < 0 or i in seen or i >= len(table):
                continue
            seen.add(i)
            for c in (table[i][1], table[i][2]):
                if c >= 0:
                    stack.append(c)
        return seen

    for _mi, _m in enumerate(models_bin):
        _vals = struct.unpack("<9f7i", _m)
        _hn = _vals[9:13]
        for _h in range(4):
            _root = _hn[_h]
            if _root < 0:
                continue
            if _h == 0:
                _tbl = [(nd[0], nd[1], nd[2]) for nd in nodes]
            else:
                _tbl = clipnodes
            _set = reachable(_root, _tbl)
            _bad = sorted(i for i in _set if i < _root)
            if _bad:
                raise SystemExit(
                    f"[ERROR] modelo {_mi} hull {_h}: headnode={_root} pero "
                    f"{len(_bad)} nodos cuelgan por debajo (min={_bad[0]}). "
                    f"El motor abortaria con 'bad node number'."
                )
    lump_clipnodes = b"".join(struct.pack("<ihh", cn[0], cn[1], cn[2]) for cn in clipnodes)
    lump_leafs = b"".join(leafs)
    lump_marksurfaces = b"".join(struct.pack("<H", m) for m in marksurfaces)
    lump_edges = b"".join(struct.pack("<HH", e[0], e[1]) for e in edges)
    lump_surfedges = b"".join(struct.pack("<i", se) for se in surfedges)
    lump_models = b"".join(models_bin)

    all_lumps = [
        lump_entities,      # 0
        lump_planes,        # 1
        lump_textures,      # 2
        lump_vertexes,      # 3
        lump_visibility,    # 4
        lump_nodes,         # 5
        lump_texinfo,       # 6
        lump_faces,         # 7
        lump_lighting,      # 8
        lump_clipnodes,     # 9
        lump_leafs,         # 10
        lump_marksurfaces,  # 11
        lump_edges,         # 12
        lump_surfedges,     # 13
        lump_models,        # 14
    ]

    header_size = 4 + 15 * 8
    cur_ofs = header_size
    lump_dir = []
    body = bytearray()
    for ldata in all_lumps:
        lump_dir.append((cur_ofs, len(ldata)))
        body.extend(ldata)
        while len(body) % 4 != 0:
            body.append(0)
        cur_ofs = header_size + len(body)

    header = struct.pack("<i", 30) + b"".join(struct.pack("<ii", o, s) for o, s in lump_dir)
    os.makedirs(os.path.dirname(out_bsp_path), exist_ok=True)
    with open(out_bsp_path, "wb") as f:
        f.write(header + body)

    print(
        f"[OK] Compilado {out_bsp_path} ({len(header) + len(body):,} bytes): "
        f"{len(planes)} planos, {len(faces)} caras, {len(nodes)} nodos, "
        f"{len(clipnodes)} clipnodes, {len(models_bin)} modelos."
    )


def write_way_file(out_way_path: str, waypoints: List[dict]):
    """Escribe maps/tranzit.way en el formato exacto de NZ:P."""
    lines = []
    for wp in waypoints:
        ox, oy, oz = wp["origin"]
        lines.append("Waypoint")
        lines.append("{")
        lines.append(f"origin = '{ox} {oy} {oz}'")
        lines.append(f"id = {wp['id']}")
        lines.append(f"special = {wp['special']}")
        tlist = wp["targets"]
        for slot in range(8):
            tag = "target" if slot == 0 else f"target{slot + 1}"
            val = f" {tlist[slot]}" if slot < len(tlist) else ""
            lines.append(f"{tag} ={val}")
        lines.append("}")
        lines.append("")
    with open(out_way_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"[OK] Generado {out_way_path} ({len(waypoints)} waypoints)")


def write_txt_file(out_txt_path: str):
    """Escribe maps/tranzit.txt con las 12 lineas de metadatos para USER MAPS."""
    lines = [
        "Tranzit (BO2 Green Run)",
        "Full-scale Green Run loop:",
        "Bus Depot, Tunnel, Diner,",
        "Cornfield Nacht, Farm,",
        "Power Station & Town.",
        "Includes moving T.E.D.D. Bus,",
        "Green Lamp Teleporters, Lava,",
        "8 Perks & Pack-a-Punch.",
        "",
        "NZ:P Android (zurdo)",
        "1",
        "1",
    ]
    with open(out_txt_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[OK] Generado {out_txt_path}")


def write_rgb_png(path: str, width: int, height: int, rgb_bytes: bytes):
    """Codifica una imagen PNG RGB24 usando unicamente la libreria estandar (zlib/struct)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw_rows = bytearray()
    stride = width * 3
    for y in range(height):
        raw_rows.append(0)  # filter type 0 (None)
        row_start = y * stride
        raw_rows.extend(rgb_bytes[row_start : row_start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), 9)
    png_data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(png_data)


def generate_tranzit_thumbnails():
    """Genera las miniaturas de menu (480x272) y pantalla de carga (512x256) para Tranzit."""
    def render_scene(w: int, h: int) -> bytes:
        buf = bytearray(w * h * 3)
        for y in range(h):
            v = y / float(h - 1)
            for x in range(w):
                u = x / float(w - 1)
                # Cielo nocturno neblinoso de Green Run (arriba) y carretera con grietas de lava (abajo)
                if v < 0.54:
                    t = v / 0.54
                    r = int(20 + 38 * t)
                    g = int(28 + 54 * t)
                    b = int(32 + 52 * t)
                    # Silueta del Diner / Bus Depot y colinas en el horizonte
                    hill = 0.48 + 0.04 * math.sin(u * 9.0) + 0.02 * math.cos(u * 23.0)
                    if (0.12 < u < 0.34 and v > 0.36) or (0.62 < u < 0.84 and v > 0.39) or v > hill:
                        r, g, b = 18, 22, 24
                        # Ventanas iluminadas calidas del Bus Depot / Diner
                        if 0.16 < u < 0.30 and 0.41 < v < 0.49 and int(u * 60) % 3 != 0:
                            r, g, b = 215, 145, 65
                else:
                    # Carretera asfaltada en perspectiva con grietas de lava incandescente
                    t = (v - 0.54) / 0.46
                    road_half = 0.12 + 0.42 * t
                    dist_center = abs(u - 0.5)
                    if dist_center < road_half:
                        r = int(36 + 20 * (1.0 - t))
                        g = int(40 + 22 * (1.0 - t))
                        b = int(44 + 22 * (1.0 - t))
                        # Lineas discontinuas amarillas centrales
                        if dist_center < 0.012 * (0.4 + t) and int(t * 14) % 2 == 0:
                            r, g, b = 210, 175, 55
                    else:
                        r = int(32 + 18 * (1.0 - t))
                        g = int(34 + 16 * (1.0 - t))
                        b = int(28 + 14 * (1.0 - t))

                    # Grieta transversal de magma brillante de Tranzit
                    lava_band = 0.72 + 0.05 * math.sin(u * 14.0) + 0.02 * math.cos(u * 37.0)
                    lava_dist = abs(v - lava_band)
                    if lava_dist < 0.045:
                        glow = 1.0 - (lava_dist / 0.045)
                        r = min(255, int(r + 235 * glow))
                        g = min(255, int(g + 125 * (glow ** 1.4)))
                        b = min(255, int(b + 35 * (glow ** 2.2)))

                # Halo de la Farola Verde de Teletransporte de Tranzit (derecha)
                dx = (u - 0.82) * (w / float(h))
                dy = v - 0.24
                d_lamp = math.sqrt(dx * dx + dy * dy)
                if d_lamp < 0.14:
                    lg = (1.0 - d_lamp / 0.14) ** 1.8
                    r = min(255, int(r + 75 * lg))
                    g = min(255, int(g + 220 * lg))
                    b = min(255, int(b + 110 * lg))
                if abs(u - 0.82) < 0.005 and 0.24 < v < 0.56:
                    r, g, b = 30, 38, 36

                # Silueta frontal del Autobus de Tranzit con faros encendidos (centro-izquierda)
                if 0.38 < u < 0.58 and 0.34 < v < 0.58:
                    r, g, b = 32, 44, 52
                    if 0.40 < u < 0.56 and 0.37 < v < 0.44:
                        r, g, b = 95, 135, 145
                    # Faros delanteros del Bus
                    for hx in (0.41, 0.55):
                        dh = math.sqrt(((u - hx) * 1.6) ** 2 + (v - 0.52) ** 2)
                        if dh < 0.035:
                            hg = (1.0 - dh / 0.035) ** 1.3
                            r = min(255, int(r + 240 * hg))
                            g = min(255, int(g + 210 * hg))
                            b = min(255, int(b + 140 * hg))

                idx = (y * w + x) * 3
                buf[idx] = r
                buf[idx + 1] = g
                buf[idx + 2] = b
        return bytes(buf)

    menu_path = os.path.join(ASSET_NZP_DIR, "gfx", "menu", "custom", "tranzit.png")
    lscreen_path = os.path.join(ASSET_NZP_DIR, "gfx", "lscreen", "tranzit.png")
    write_rgb_png(menu_path, 480, 272, render_scene(480, 272))
    write_rgb_png(lscreen_path, 512, 256, render_scene(512, 256))
    print(f"[OK] Generadas miniaturas {menu_path} y {lscreen_path}")


def validate_textures(world_brushes, submodels):
    """Comprueba que todas las texturas referenciadas existen en el WAD3."""
    tex_blobs = load_wad3_textures_from_town()
    missing: Dict[str, int] = {}
    faces = ("+x", "-x", "+y", "-y", "+z", "-z")

    def scan(b: BoxBrush):
        for f in faces:
            if f in b.skip_faces:
                continue
            t = b.get_tex(f)
            if t not in tex_blobs:
                missing[t] = missing.get(t, 0) + 1

    for b in world_brushes:
        scan(b)
    for _cn, brushes, _kv in submodels:
        for b in brushes:
            scan(b)

    if missing:
        print("[AVISO] Texturas inexistentes (se sustituyen por conc_road_D2):")
        for k in sorted(missing):
            print(f"        - {k}  ({missing[k]} caras)")
    else:
        print(f"[OK] Texturas validadas: {len(tex_blobs)} disponibles, 0 inexistentes")
    return len(tex_blobs)


def main():
    world_brushes, submodels, point_entities, lights, waypoints = build_tranzit_world()

    n_brushes = len(world_brushes) + sum(len(b) for _c, b, _k in submodels)
    print(
        f"[INFO] Geometria: {len(world_brushes)} brushes de mundo, "
        f"{len(submodels)} submodelos, {n_brushes} brushes totales"
    )
    print(f"[INFO] Luces puntuales: {len(lights)}   Entidades: {len(point_entities)}")
    validate_textures(world_brushes, submodels)

    map_path = os.path.join(MAPS_SRC_DIR, "tranzit.map")
    export_map_file(map_path, world_brushes, submodels, point_entities)
    print(f"[OK] Exportado archivo fuente {map_path}")

    bsp_path = os.path.join(ASSET_NZP_DIR, "maps", "tranzit.bsp")
    compile_bsp30(bsp_path, world_brushes, submodels, point_entities, lights)

    way_path = os.path.join(ASSET_NZP_DIR, "maps", "tranzit.way")
    write_way_file(way_path, waypoints)

    txt_path = os.path.join(ASSET_NZP_DIR, "maps", "tranzit.txt")
    write_txt_file(txt_path)

    generate_tranzit_thumbnails()


if __name__ == "__main__":
    main()

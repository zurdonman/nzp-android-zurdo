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

import math
import os
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ASSET_NZP_DIR = os.path.join(ROOT_DIR, "android-app", "app", "src", "main", "assets", "base", "nzp")
MAPS_SRC_DIR = os.path.join(ROOT_DIR, "maps_src")

CONTENTS_EMPTY = -1
CONTENTS_SOLID = -2
TEX_SPECIAL = 1


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
    """Extrae las texturas WAD3 embebidas en town.bsp y anade texturas custom de Tranzit."""
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

    # Crear textura WAD3 custom para las grietas de lava incandescente de Tranzit
    raw_blobs["lava_cracks"] = make_custom_wad3_texture("lava_cracks", 64, 64, "lava")
    raw_blobs["corn_wall"] = make_custom_wad3_texture("corn_wall", 64, 64, "corn")
    raw_blobs["bus_metal"] = make_custom_wad3_texture("bus_metal", 64, 64, "bus")
    return raw_blobs


def make_custom_wad3_texture(name: str, w: int, h: int, style: str) -> bytes:
    """Construye un bloque miptex WAD3 (4 mipmaps + paleta 256 RGB)."""
    palette = []
    for i in range(256):
        if style == "lava":
            # Gradiente de roca volcanica oscura a magma naranja/amarillo brillante
            t = i / 255.0
            r = min(255, int(40 + 215 * (t ** 0.7)))
            g = min(255, int(10 + 165 * (t ** 1.4)))
            b = min(255, int(5 + 60 * (t ** 2.5)))
        elif style == "corn":
            t = i / 255.0
            r = int(22 + 55 * t)
            g = int(34 + 75 * t)
            b = int(16 + 30 * t)
        else:  # bus
            t = i / 255.0
            r = int(28 + 65 * t)
            g = int(42 + 85 * t)
            b = int(52 + 95 * t)
        palette.extend([r, g, b])

    def gen_mip(mw: int, mh: int) -> bytes:
        buf = bytearray(mw * mh)
        for y in range(mh):
            for x in range(mw):
                if style == "lava":
                    v = (
                        math.sin(x * 0.35 + y * 0.25)
                        + math.cos(x * 0.2 - y * 0.4)
                        + math.sin((x + y) * 0.5)
                    )
                    idx = max(16, min(254, int(135 + 85 * (v / 3.0))))
                elif style == "corn":
                    stripe = math.sin(x * 0.9) * 0.5 + math.sin(y * 0.35) * 0.5
                    idx = max(8, min(240, int(110 + 70 * stripe)))
                else:
                    border = 1 if (x < 2 or x >= mw - 2 or y < 2 or y >= mh - 2) else 0
                    idx = 45 if border else (130 if (y % 8 < 2) else 95)
                buf[y * mw + x] = idx
        return bytes(buf)

    mip0 = gen_mip(w, h)
    mip1 = gen_mip(w // 2, h // 2)
    mip2 = gen_mip(w // 4, h // 4)
    mip3 = gen_mip(w // 8, h // 8)

    o0 = 40
    o1 = o0 + len(mip0)
    o2 = o1 + len(mip1)
    o3 = o2 + len(mip2)
    name_bytes = name.encode("latin1")[:15].ljust(16, b"\x00")
    hdr = struct.pack("<16sIIIIII", name_bytes, w, h, o0, o1, o2, o3)
    tail = struct.pack("<H", 256) + bytes(palette) + b"\x00\x00"
    return hdr + mip0 + mip1 + mip2 + mip3 + tail


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

    # ------------------------------------------------------------------------
    # 0. CAJA DE CIELO SELLADA Y SUELO BASE DE GREEN RUN
    # ------------------------------------------------------------------------
    # Suelo general en Z=[-64..0]
    add_brush(-2400, -2400, -64, 2400, 2400, 0, tex_sides="ground_dirt", tex_top="asphalt", tex_bottom="ground_dirt")
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
    add_ent("info_player_start", (-1950, -1880, 44), angles="0 0 0")
    add_ent("info_player_1_spawn", (-1950, -1880, 44), angles="0 0 0")
    add_ent("info_player_2_spawn", (-1950, -1980, 44), angles="0 15 0")
    add_ent("info_player_3_spawn", (-2060, -1880, 44), angles="0 0 0")
    add_ent("info_player_4_spawn", (-2060, -1980, 44), angles="0 20 0")

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

    # Nucleo del Reactor en el centro del Laboratorio + Interruptor de Electricidad (power_switch)
    add_brush(1880, -620, 0, 2000, -500, 232, tex_sides="facility_contro")
    add_ent("power_switch", (1860, -560, 44), angles="0 180 0")

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

    add_buyable_door("door_bar", "z_town", 1000, -220, -1476, 0, -60, -1452, 160, tex="doors_dark")

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

    # Grietas centrales de lava en Town + Farola Verde #5 + luces + spawners
    add_lava_pit(-140, -1740, 120, -1560)
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
    # Construimos el modelo del autobus en coordenadas locales [0..160] x [0..112] x [0..44]
    # Suelo de 12u de grosor (para poder subir a pie sin siquiera saltar) + barandillas
    # delanteras/traseras que mantienen al jugador dentro durante el trayecto.
    bus_brushes = [
        # Plataforma base del autobus
        BoxBrush(0, 0, 0, 160, 112, 12, "metal_floor", "m_metal_darkBlu", "bus_metal"),
        # Cabina delantera (T.E.D.D.) y parachoques trasero
        BoxBrush(144, 0, 12, 160, 112, 46, "bus_metal", "bus_metal", "bus_metal"),
        BoxBrush(0, 0, 12, 16, 112, 44, "bus_metal", "bus_metal", "bus_metal"),
        # Barandillas laterales en las esquinas (dejando puertas anchas de 80u en el centro)
        BoxBrush(16, 0, 12, 40, 12, 42, "bus_metal", "bus_metal", "bus_metal"),
        BoxBrush(120, 0, 12, 144, 12, 42, "bus_metal", "bus_metal", "bus_metal"),
        BoxBrush(16, 100, 12, 40, 112, 42, "bus_metal", "bus_metal", "bus_metal"),
        BoxBrush(120, 100, 12, 144, 112, 42, "bus_metal", "bus_metal", "bus_metal"),
    ]
    add_submodel(
        "func_tranzit_bus",
        bus_brushes,
        {"target": "bus_stop_1", "speed": "210", "dmg": "500"},
    )

    # Nodos path_corner del circuito completo del Autobus de Tranzit
    bus_stops = [
        ("bus_stop_1", "bus_stop_2", (-1420, -1380, 2), 25, "BUS DEPOT"),
        ("bus_stop_2", "bus_stop_3", (-1420, 0, 2), 8, "HIGHWAY TUNNEL"),
        ("bus_stop_3", "bus_stop_4", (-1420, 1180, 2), 25, "DINER & GARAGE"),
        ("bus_stop_4", "bus_stop_5", (0, 1220, 2), 8, "CORNFIELD CROSSROADS"),
        ("bus_stop_5", "bus_stop_6", (1220, 1220, 2), 25, "FARM"),
        ("bus_stop_6", "bus_stop_7", (1220, -180, 2), 25, "POWER STATION"),
        ("bus_stop_7", "bus_stop_8", (1220, -1380, 2), 6, "SOUTH HIGHWAY"),
        ("bus_stop_8", "bus_stop_1", (160, -1660, 2), 25, "TOWN (BANK & BAR)"),
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
        fullbright = (255, 165, 65) if tex == "lava_cracks" else None
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

        while len(lighting_data) % 3 != 0:
            lighting_data.append(0)
        lightofs = len(lighting_data)

        if q.fullbright is not None:
            r0, g0, b0 = q.fullbright
            for _ in range(smax * tmax):
                lighting_data.extend((r0, g0, b0))
            return lightofs

        # Luz ambiental base de Tranzit (nocturna con tinte azulado/verdoso suave)
        amb_r, amb_g, amb_b = 34.0, 38.0, 44.0
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
                for pl in lights:
                    dx = wx - pl.x
                    dy = wy - pl.y
                    dz = wz - pl.z
                    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if dist < pl.radius:
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
            # Si una caja cubre por completo la celda actual, toda la celda es SOLID
            if (
                b[0] <= x0 + eps and b[3] >= x1 - eps and
                b[1] <= y0 + eps and b[4] >= y1 - eps and
                b[2] <= z0 + eps and b[5] >= z1 - eps
            ):
                return CONTENTS_SOLID
            active.append(b)

        if not active or depth > 28:
            return CONTENTS_EMPTY

        # Elegir el plano de corte mas cercano al centro de la celda
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
            return CONTENTS_SOLID

        b_front = list(bounds)
        b_back = list(bounds)
        b_front[best_axis] = best_coord
        b_back[best_axis + 3] = best_coord

        c_front = build_clip_kdtree(active, tuple(b_front), depth + 1)
        c_back = build_clip_kdtree(active, tuple(b_back), depth + 1)
        if c_front == c_back:
            return c_front

        pnum = get_plane_idx(best_axis, best_coord)
        node_idx = len(clipnodes)
        clipnodes.append((pnum, c_front, c_back))
        return node_idx

    def build_hull_for_brushes(brushes: List[BoxBrush], hx: float, hy: float, hz: float) -> int:
        expanded = [
            (b.xmin - hx, b.ymin - hy, b.zmin - hz, b.xmax + hx, b.ymax + hy, b.zmax + hz)
            for b in brushes
        ]
        idx = build_clip_kdtree(expanded, (-3200.0, -3200.0, -1024.0, 3200.0, 3200.0, 1024.0))
        if idx < 0:
            pnum = get_plane_idx(2, -2048.0)
            idx = len(clipnodes)
            clipnodes.append((pnum, idx_val := idx, CONTENTS_EMPTY))
            clipnodes[-1] = (pnum, CONTENTS_EMPTY, CONTENTS_EMPTY)
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
            return -1

        pnum = get_plane_idx(best_axis, best_coord)
        node_idx = len(nodes)
        # Ampliar minmaxs del nodo a todo el mapa para que ninguna cara asociada a este plano
        # sea descartada por R_CullBox cuando el plano aparece en otra rama
        nodes.append([pnum, -1, -1, [-2600, -2600, -256], [2600, 2600, 640], 0, 0])

        if pnum in quads_by_plane and pnum not in emitted_planes:
            emitted_planes.add(pnum)
            qlist = quads_by_plane[pnum]
            ff = len(faces)
            for q in qlist:
                emit_quad_face(q)
            nf = len(faces) - ff
            nodes[node_idx][5] = ff
            nodes[node_idx][6] = nf

        b_front = list(bounds)
        b_back = list(bounds)
        b_front[best_axis] = best_coord
        b_back[best_axis + 3] = best_coord

        c_front = build_spatial_world_bsp(active, tuple(b_front), depth + 1)
        c_back = build_spatial_world_bsp(active, tuple(b_back), depth + 1)
        nodes[node_idx][1] = c_front
        nodes[node_idx][2] = c_back
        return node_idx

    world_headnode0 = build_spatial_world_bsp(world_boxes_h0, (-2600.0, -2600.0, -256.0, 2600.0, 2600.0, 640.0))
    assert world_headnode0 == 0

    # Si algun plano con caras no fue usado como primer corte, emitir sus caras igualmente
    for pnum, qlist in quads_by_plane.items():
        if pnum not in emitted_planes:
            emitted_planes.add(pnum)
            ff = len(faces)
            for q in qlist:
                emit_quad_face(q)
            nf = len(faces) - ff
            # Buscar cualquier nodo con ese planenum
            for nd in nodes:
                if nd[0] == pnum and nd[6] == 0:
                    nd[5] = ff
                    nd[6] = nf
                    break

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


def main():
    world_brushes, submodels, point_entities, lights, waypoints = build_tranzit_world()

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

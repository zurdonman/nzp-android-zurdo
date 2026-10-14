#!/usr/bin/env python3
"""Genera mapas de zombies proceduralmente a partir del perfil real.

No se inventa nada: los numeros salen de medir los 60 mapas que ya
funcionan (scripts/compare_prison_reference.py), que es la regla de
universal-modder de "el original es la spec":

    caras ~2597   nodos ~1741   ratio 0.7   modelos ~48
    barricadas 12   zombies 16   path_corner 12
    armas de pared 8   perks 6   spawns 4
    distancia barricada->zombie ~213

Idea: una rejilla de salas unidas por puertas. El exterior se rodea de
una galeria donde viven los zombies, y cada barricada es un hueco a ras
de suelo tapado con tablas, con su propio spawn detras (porque los
zombies no trepan).

Uso: python3 scripts/gen_random_map.py            # genera los 10
     python3 scripts/gen_random_map.py nombre 42  # uno con semilla
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_prison_map import (World, X0, X1, WALL_H, T_WALL, T_WALL2, T_CONC,
                              T_FLOOR, T_FLOOR2, T_CEIL, T_DOOR, T_WOOD,
                              T_WOODTOP, T_DARK, T_METAL, T_GLASS)
from build_tranzit_map import compile_bsp30, export_map_file, \
    validate_textures, write_way_file

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(REPO, "android-app", "app", "src", "main", "assets",
                    "base", "nzp", "maps")
SRC = os.path.join(REPO, "maps_src")

# perfil objetivo (medianas de los mapas que funcionan)
TARGET = dict(bars=12, zomb=16, wallgun=8, perks=6, spawn=4, pcs=12,
              bar2zomb=213.0)

CELL = 768.0          # lado de una sala
WALL_T = 16.0
Z0, Z1 = 0.0, 160.0   # altura de planta
WIN_H = 112.0         # alto del hueco de madera (a ras de suelo)
PERKS = [("perk_juggernog", "models/machines/quake_scale/juggernog.mdl", 2500),
         ("perk_speed", "models/machines/quake_scale/speed_cola.mdl", 3000),
         ("perk_double", "models/machines/quake_scale/double_tap.mdl", 2000),
         ("perk_revive", "models/machines/quake_scale/quick_revive.mdl", 1500),
         ("perk_mule", "models/machines/quake_scale/mulekick.mdl", 4000),
         ("perk_deadshot", "models/machines/quake_scale/deadshot.mdl", 2000)]
WEAPONS = [(2, 500), (5, 600), (21, 1200), (8, 1200), (23, 1500),
           (3, 1000), (7, 2000), (11, 2000)]
PROPS = ["models/props/Barrel_m.mdl", "models/props/Kino_box.mdl",
         "models/props/Kino_boxes2.mdl", "models/props/Kino_boxes3.mdl",
         "models/props/Kino_table.mdl", "models/props/MopBucket.mdl",
         "models/props/metal_chair.mdl", "models/props/table_sq.mdl",
         "models/props/shelf.mdl", "models/props/trash_con.mdl",
         "models/props/sandbags.mdl", "models/props/container.mdl"]


class Room:
    def __init__(self, gx, gy, x0, y0, x1, y1):
        self.gx, self.gy = gx, gy
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.doors = {"N": None, "S": None, "E": None, "W": None}
        self.zone = None

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    def inside(self, x, y, m=140.0):
        return (self.x0 + m < x < self.x1 - m
                and self.y0 + m < y < self.y1 - m)



def decorate(w, r, rnd, name):
    """Detalle de una sala.

    Los mapas que funcionan usan func_wall (39 de 60) y func_illusionary
    (42 de 60) ademas de func_door_nzp, y rondan las 2300 caras. Una
    sala desnuda son ~60 caras, asi que aqui se anaden ~120 por sala:
    pilares, vigas, zocalos, marcos de puerta y cajas. Los pilares van
    como func_wall, que cuenta como modelo y como geometria a la vez.
    """
    from build_prison_map import BoxBrush
    H = Z1 - Z0
    # 4 pilares (func_wall): modelo + geometria
    for px, py in ((0.18, 0.18), (0.82, 0.18), (0.18, 0.82), (0.82, 0.82)):
        cx = r.x0 + (r.x1 - r.x0) * px
        cy = r.y0 + (r.y1 - r.y0) * py
        w.submodel("func_wall",
                   [BoxBrush(cx - 40, cy - 40, Z0, cx + 40, cy + 40, Z1 - 24,
                             T_CONC, T_CONC, T_CONC, {}, set())], )
    # vigas del techo: geometria de mundo
    for i in range(3):
        yy = r.y0 + (r.y1 - r.y0) * (i + 1) / 4.0
        w.brush(r.x0 + 60, yy - 18, Z1 - 40, r.x1 - 60, yy + 18, Z1 - 16,
                tex=T_METAL)
    # zocalo corrido en las cuatro paredes
    t = 12.0
    h = 40.0
    w.brush(r.x0 + t, r.y0 + t, Z0, r.x1 - t, r.y0 + t + 20, Z0 + h, tex=T_WALL2)
    w.brush(r.x0 + t, r.y1 - t - 20, Z0, r.x1 - t, r.y1 - t, Z0 + h, tex=T_WALL2)
    w.brush(r.x0 + t, r.y0 + t, Z0, r.x0 + t + 20, r.y1 - t, Z0 + h, tex=T_WALL2)
    w.brush(r.x1 - t - 20, r.y0 + t, Z0, r.x1 - t, r.y1 - t, Z0 + h, tex=T_WALL2)
    # marcos de las puertas abiertas
    for side in ("N", "S", "E", "W"):
        if not r.doors[side]:
            continue
        if side in ("E", "W"):
            x = r.x1 - 20 if side == "E" else r.x0
            w.brush(x, r.cy - 110, Z0, x + 40, r.cy - 90, Z1, tex=T_WALL2)
            w.brush(x, r.cy + 90, Z0, x + 40, r.cy + 110, Z1, tex=T_WALL2)
            w.brush(x, r.cy - 110, Z1 - 40, x + 40, r.cy + 110, Z1, tex=T_WALL2)
        else:
            y = r.y1 - 20 if side == "N" else r.y0
            w.brush(r.cx - 110, y, Z0, r.cx - 90, y + 40, Z1, tex=T_WALL2)
            w.brush(r.cx + 90, y, Z0, r.cx + 110, y + 40, Z1, tex=T_WALL2)
            w.brush(r.cx - 110, y, Z1 - 40, r.cx + 110, y + 40, Z1, tex=T_WALL2)
    # tarima con escalones en la mitad de las salas
    if rnd.random() < 0.5:
        w.brush(r.x0 + 120, r.y0 + 120, Z0, r.x0 + 380, r.y0 + 380, 32,
                tex=T_WOOD, top=T_WOODTOP)
        for i in range(3):
            w.brush(r.x0 + 380 + i * 40, r.y0 + 140, Z0,
                    r.x0 + 420 + i * 40, r.y0 + 360, 12 + i * 7, tex=T_WOOD,
                    top=T_WOODTOP)
    # cajas apiladas: modelo + geometria
    for _ in range(rnd.randint(1, 3)):
        bx = rnd.uniform(r.x0 + 120, r.x1 - 160)
        by = rnd.uniform(r.y0 + 120, r.y1 - 160)
        sz = rnd.choice((40, 56, 72))
        w.submodel("func_wall",
                   [BoxBrush(bx, by, Z0, bx + sz, by + sz, sz,
                             T_WOOD, T_WOODTOP, T_WOOD, {}, set())])
    # rejilla decorativa: no bloquea
    if rnd.random() < 0.4:
        gx = rnd.uniform(r.x0 + 150, r.x1 - 250)
        gy = rnd.uniform(r.y0 + 150, r.y1 - 150)
        w.submodel("func_illusionary",
                   [BoxBrush(gx, gy, Z1 - 70, gx + 120, gy + 12, Z1 - 58,
                             T_METAL, T_METAL, T_METAL, {}, set())])


def build(seed, name):
    rnd = random.Random(seed)
    w = World()
    gw = rnd.choice((3, 4))
    gh = rnd.choice((3, 4))
    # rejilla de salas
    rooms = {}
    for gy in range(gh):
        for gx in range(gw):
            x0 = gx * CELL
            y0 = gy * CELL
            rooms[(gx, gy)] = Room(gx, gy, x0, y0, x0 + CELL, y0 + CELL)
    # arbol de expansion: todas las salas conectadas
    visited = {(0, 0)}
    frontier = [(0, 0)]
    edges = []
    while frontier:
        gx, gy = frontier.pop(rnd.randrange(len(frontier)))
        for d, (dx, dy) in (("N", (0, 1)), ("S", (0, -1)),
                            ("E", (1, 0)), ("W", (-1, 0))):
            nx, ny = gx + dx, gy + dy
            if (nx, ny) in rooms and (nx, ny) not in visited:
                visited.add((nx, ny))
                frontier.append((nx, ny))
                edges.append(((gx, gy), (nx, ny), d))
                opp = {"N": "S", "S": "N", "E": "W", "W": "E"}[d]
                rooms[(gx, gy)].doors[d] = "abierto"
                rooms[(nx, ny)].doors[opp] = "abierto"
    # unas cuantas conexiones extra para que no sea un arbol puro
    for (gx, gy) in list(rooms):
        for d, (dx, dy) in (("N", (0, 1)), ("E", (1, 0))):
            nx, ny = gx + dx, gy + dy
            if (nx, ny) in rooms and rnd.random() < 0.35:
                opp = {"N": "S", "E": "W"}[d]
                rooms[(gx, gy)].doors[d] = "abierto"
                rooms[(nx, ny)].doors[opp] = "abierto"

    XMAX, YMAX = gw * CELL, gh * CELL

    # 1) elegir barricadas ANTES de levantar muros, para poder abrirles hueco
    border = [r for r in rooms.values()
              if r.gx in (0, gw - 1) or r.gy in (0, gh - 1)]
    rnd.shuffle(border)
    nbar = min(TARGET["bars"], len(border))
    chosen = []
    for r in border[:nbar]:
        opts = []
        if r.gx == 0:
            opts.append(("W", r.x0, r.cy))
        if r.gx == gw - 1:
            opts.append(("E", r.x1, r.cy))
        if r.gy == 0:
            opts.append(("S", r.cx, r.y0))
        if r.gy == gh - 1:
            opts.append(("N", r.cx, r.y1))
        if opts:
            side, bx, by = rnd.choice(opts)
            chosen.append((r, side, bx, by))

    # huecos agrupados por sala y lado
    gaps = {}
    for r, side, bx, by in chosen:
        if side in ("W", "E"):
            a, b = by - 48.0, by + 48.0
        else:
            a, b = bx - 48.0, bx + 48.0
        gaps.setdefault((r.gx, r.gy, side), []).append((a, b))

    def muro_con_huecos(mk, span_a, span_b, key):
        """Muro en tres franjas: dintel, franja del hueco, antepecho."""
        g = gaps.get(key, [])
        if g:
            mk(Z0, WIN_H, g)        # franja del hueco (a ras de suelo)
            mk(WIN_H, Z1, [])       # dintel
        else:
            mk(Z0, Z1, [])

    # 2) suelo, techo y muros
    for r in rooms.values():
        w.floor(r.x0, r.y0, r.x1, r.y1, Z0, tex=T_FLOOR)
        w.ceiling(r.x0, r.y0, r.x1, r.y1, Z1, tex=T_CEIL)
    for r in rooms.values():
        if r.gx == 0:
            muro_con_huecos(
                lambda a, b, g: w.wall_y(r.x0, r.y0, r.y1, a, b, T_CONC, gaps=g),
                r.y0, r.y1, (r.gx, r.gy, "W"))
        if r.gx == gw - 1:
            muro_con_huecos(
                lambda a, b, g: w.wall_y(r.x1, r.y0, r.y1, a, b, T_CONC, gaps=g),
                r.y0, r.y1, (r.gx, r.gy, "E"))
        if r.gy == 0:
            muro_con_huecos(
                lambda a, b, g: w.wall_x(r.y0, r.x0, r.x1, a, b, T_CONC, gaps=g),
                r.x0, r.x1, (r.gx, r.gy, "S"))
        if r.gy == gh - 1:
            muro_con_huecos(
                lambda a, b, g: w.wall_x(r.y1, r.x0, r.x1, a, b, T_CONC, gaps=g),
                r.x0, r.x1, (r.gx, r.gy, "N"))
        # muros interiores: hueco de puerta si las salas estan conectadas
        if r.gx < gw - 1:
            g = [(r.cy - 90, r.cy + 90)] if r.doors["E"] else []
            w.wall_y(r.x1, r.y0, r.y1, Z0, Z1, T_WALL, gaps=g)
        if r.gy < gh - 1:
            g = [(r.cx - 90, r.cx + 90)] if r.doors["N"] else []
            w.wall_x(r.y1, r.x0, r.x1, Z0, Z1, T_WALL, gaps=g)

    # ---- galeria exterior: anillo cerrado alrededor del bloque ----
    RING = 420.0
    rx0, ry0 = -RING, -RING
    rx1, ry1 = XMAX + RING, YMAX + RING
    for ax0, ay0, ax1, ay1 in ((rx0, ry0, rx1, 0.0),
                               (rx0, YMAX, rx1, ry1),
                               (rx0, 0.0, 0.0, YMAX),
                               (XMAX, 0.0, rx1, YMAX)):
        w.floor(ax0, ay0, ax1, ay1, Z0, tex=T_FLOOR2)
        w.ceiling(ax0, ay0, ax1, ay1, 320.0, tex=T_CEIL)
    w.wall_x(ry1, rx0, rx1, Z0, 320.0, T_CONC)
    w.wall_x(ry0, rx0, rx1, Z0, 320.0, T_CONC)
    w.wall_y(rx0, ry0, ry1, Z0, 320.0, T_CONC)
    w.wall_y(rx1, ry0, ry1, Z0, 320.0, T_CONC)

    # ---- entidades de barricada sobre los huecos ya abiertos ----
    RING = 420.0
    bars = []
    for i, (r, side, bx, by) in enumerate(chosen):
        if side == "W":
            zx, zy, ang = bx - TARGET["bar2zomb"], by, "90"
        elif side == "E":
            zx, zy, ang = bx + TARGET["bar2zomb"], by, "270"
        elif side == "S":
            zx, zy, ang = bx, by - TARGET["bar2zomb"], "0"
        else:
            zx, zy, ang = bx, by + TARGET["bar2zomb"], "180"
        bn = "bar_%d" % i
        w.ent("item_barricade", (bx, by, 8), angles="0 %s 0" % ang,
              targetname=bn, model="models/misc/window.mdl",
              oldmodel="sounds/misc/barricade.wav",
              aistatus="sounds/misc/barricade_destroy.wav")
        w.ent("path_corner", (zx, zy, 40), targetname="pc_%d" % i, target=bn)
        w.ent("spawn_zombie", (zx, zy, 40), angles="0 0 0",
              targetname="z_%s" % name, target="pc_%d" % i, spawnflags="0")
        bars.append((zx, zy))

    # ---- zona de spawn y jugadores ----
    start = rooms[(0, 0)]
    w.ent("spawn_zone", zone_name="z_%s" % name,
          zone_target="z_%s" % name, adjacent_zones="", door_way_targets="")
    for i, dx in enumerate((-140, 0, 140, -70)):
        w.ent("info_player_start" if i == 0 else "info_player_%d_spawn" % (i + 1),
              (start.cx + dx, start.y0 + 180, 40), angles="0 0 0")

    # ---- perks en salas distintas a la de salida ----
    spots = [r for r in rooms.values() if r is not start]
    rnd.shuffle(spots)
    for i, (pk, mdl, cost) in enumerate(PERKS):
        if i >= len(spots):
            break
        r = spots[i % len(spots)]
        w.ent(pk, (r.cx + (120 if i % 2 else -120), r.cy, 8),
              angles="0 0 0", spawnflags="8", cost=str(cost),
              cost2=str(cost), model=mdl)

    # ---- armas de pared contra los muros ----
    wg = min(TARGET["wallgun"], len(rooms) * 2)
    for i in range(wg):
        r = rooms[(i // 2) % gw, (i // 2) // gw % gh]
        wid, cost = WEAPONS[i % len(WEAPONS)]
        ang = "90" if i % 2 else "270"
        x = r.x0 + 60 if i % 2 else r.x1 - 60
        tn = "wg_%d" % i
        w.ent("weapon_wall", (x, r.cy, 40), targetname=tn, angle=ang,
              sequence=str(wid))
        w.ent("buy_weapon", (x, r.cy, 40), target=tn, weapon=str(wid),
              cost=str(cost), cost2=str(cost // 2))

    # ---- caja misteriosa e interruptor ----
    mb = spots[len(spots) // 2] if spots else start
    w.ent("mystery_box", (mb.cx, mb.cy, 8), angles="0 0 0")
    w.ent("power_switch", (start.x1 - 80, start.y0 + 200, 8),
          angles="0 270 0")
    # botones y final: los mapas buenos los llevan (14 y 16 de 60)
    from build_prison_map import BoxBrush
    w.submodel("func_button",
               [BoxBrush(start.x1 - 96, start.y0 + 176, 40,
                         start.x1 - 64, start.y0 + 224, 88,
                         T_METAL, T_METAL, T_METAL, {}, set())],
               target="z_%s" % name, wait="-1", sounds="1")
    end = rooms[(gw - 1, gh - 1)]
    w.submodel("func_ending",
               [BoxBrush(end.cx - 120, end.cy - 120, Z0,
                         end.cx + 120, end.cy + 120, 24,
                         T_DOOR, T_DOOR, T_DOOR, {}, set())])

    # ---- luces y props (los mapas buenos tienen ~48 modelos) ----
    for r in rooms.values():
        decorate(w, r, rnd, name)
        w.light(r.cx, r.cy, Z1 - 16, radius=420)
        for _ in range(4):
            w.prop(rnd.choice(PROPS),
                   rnd.uniform(r.x0 + 100, r.x1 - 100),
                   rnd.uniform(r.y0 + 100, r.y1 - 100), 16,
                   "0 %d 0" % rnd.choice((0, 90, 180, 270)))
    # puertas comprables: brush real colocado en el hueco de union
    from build_prison_map import BoxBrush
    ndoors = 6
    for i in range(ndoors):
        r = rooms[((i + 1) % gw, ((i + 1) // gw) % gh)]
        if r.doors["E"]:
            b = BoxBrush(r.x1 - 8, r.cy - 90, Z0, r.x1 + 8, r.cy + 90, Z1,
                         T_DOOR, T_DOOR, T_DOOR, {}, set())
        else:
            b = BoxBrush(r.cx - 90, r.y1 - 8, Z0, r.cx + 90, r.y1 + 8, Z1,
                         T_DOOR, T_DOOR, T_DOOR, {}, set())
        w.submodel("func_door_nzp", [b], wayTarget="", target="z_%s" % name,
                   cost=str(500 + i * 250), speed="120", wait="4",
                   lip="8", sounds="2", angles="0 90 0", spawnflags="32")

    # ---- waypoints: los path_corner y los spawns de zombie ----
    ways = []
    wid = 0
    for x, y in bars:
        ways.append({"id": wid, "origin": (float(x), float(y), 40.0),
                     "special": 0, "targets": [max(0, wid - 1)]})
        wid += 1
    for r in rooms.values():
        ways.append({"id": wid, "origin": (float(r.cx), float(r.cy), 40.0),
                     "special": 0, "targets": [max(0, wid - 1)]})
        wid += 1

    sub = sum(len(b) for _c, b, _k in w.submodels)
    print(f"  {name}: {len(w.brushes)} brushes, {len(w.submodels)} submodelos, "
          f"{len(w.lights)} luces, {len(w.ents)} entidades")
    validate_textures(w.brushes, w.submodels)
    os.makedirs(SRC, exist_ok=True)
    export_map_file(os.path.join(SRC, name + ".map"), w.brushes,
                    w.submodels, w.ents)
    compile_bsp30(os.path.join(MAPS, name + ".bsp"), w.brushes,
                  w.submodels, w.ents, w.lights)
    write_way_file(os.path.join(MAPS, name + ".way"), ways)
    title = name.replace("_", " ").title()
    with open(os.path.join(MAPS, name + ".txt"), "w", newline="\n") as f:
        f.write("\n".join([title, "Procedural map for NZ:P", "Rooms, "
                           "barricades and perks", "", "", "", "", "",
                           "NZ:P Android (zurdo)", "1", "1"]) + "\n")
    return name


def main():
    args = sys.argv[1:]
    if len(args) >= 2:
        build(int(args[1]), args[0])
        return
    seeds = [101, 202, 303, 404, 505, 606, 707, 808, 909, 1010]
    names = ["block_house", "foundry", "docks", "bunker_two", "greenhouse",
             "railyard", "clinic", "arcade", "vault", "outpost"]
    for s, n in zip(seeds, names):
        build(s, n)
    print(f"\n{len(names)} mapas generados")


if __name__ == "__main__":
    main()

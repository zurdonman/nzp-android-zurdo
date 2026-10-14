#!/usr/bin/env python3
"""Adapta un .bsp de CS 1.6 (GoldSrc BSP30) a una experiencia NZ:P.

La geometria es compatible tal cual. El trabajo es sustituir la capa de
entidades: un mapa de deathmatch no tiene zombies, armas de pared,
perks ni barricadas.

Se parchea UNICAMENTE el lump de entidades, rellenando con nulos, asi que
no cambia ningun offset del BSP. Para colocar cosas se reutilizan los
info_player_start / info_player_deathmatch del CS, que son posiciones
de suelo garantizadas.
"""
import os, re, struct, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "maps_src", "cs16", "fy_garden_c11.bsp")
OUT_DIR = os.path.join(REPO, "android-app", "app", "src", "main", "assets", "base", "nzp", "maps")
NAME = "fy_garden"

d = open(SRC, "rb").read()
L = struct.unpack("<30i", d[4:4 + 120])
e_off, e_len = L[0], L[1]                 # lump 0 = entities
m_off = L[28]                             # lump 14 = models
assert struct.unpack("<i", d[:4])[0] == 30, "no es BSP30"

ent = d[e_off:e_off + e_len].split(b"\x00")[0].decode("latin1")
mins = struct.unpack("<3f", d[m_off:m_off + 12])
maxs = struct.unpack("<3f", d[m_off + 12:m_off + 24])
print(f"  mundo: X[{mins[0]:.0f}..{maxs[0]:.0f}] Y[{mins[1]:.0f}..{maxs[1]:.0f}] Z[{mins[2]:.0f}..{maxs[2]:.0f}]")

# --- posiciones de suelo validas: los spawns del CS ---
spawns, lights = [], []
for blk in re.findall(r"\{([^}]*)\}", ent):
    cn = re.search(r'"classname"\s+"([^"]+)"', blk)
    if not cn:
        continue
    o = re.search(r'"origin"\s+"([^"]+)"', blk)
    if not o:
        continue
    xyz = tuple(float(v) for v in o.group(1).split())
    if cn.group(1) in ("info_player_start", "info_player_deathmatch"):
        spawns.append(xyz)
    elif cn.group(1) == "light":
        lights.append(xyz)
spawns.sort(key=lambda p: (p[0], p[1]))
print(f"  {len(spawns)} spawns de CS reutilizables, {len(lights)} luces originales")

def W(items):
    """items = lista de dicts -> texto de entidades Quake"""
    out = []
    for it in items:
        out.append("{\n")
        for k, v in it.items():
            out.append(f'"{k}" "{v}"\n')
        out.append("}\n")
    return "".join(out)

def f3(p, dz=0.0):
    return f"{p[0]:.0f} {p[1]:.0f} {p[2] + dz:.0f}"

cx = (mins[0] + maxs[0]) / 2
cy = (mins[1] + maxs[1]) / 2
# reparto en 4 zonas por cuadrante
def zone_of(p):
    return ("z_sw" if p[0] < cx else "z_se") if p[1] < cy else ("z_nw" if p[0] < cx else "z_ne")
ZONES = {"z_sw": "z_se z_nw", "z_se": "z_sw z_ne", "z_nw": "z_ne z_sw", "z_ne": "z_nw z_se"}

E = []
E.append({"classname": "worldspawn", "sounds": "1", "MaxRange": "4096",
          "mapversion": "220", "rendermode": "0", "lightmap": "1", "skyname": "desert"})

E.append({"classname": "info_player_start", "origin": f3(spawns[0], 16), "angle": "0"})

# --- spawn_zone: una por cuadrante ---
for i, (zn, adj) in enumerate(ZONES.items()):
    p = [s for s in spawns if zone_of(s) == zn]
    if not p:
        continue
    E.append({"classname": "spawn_zone", "zone_name": zn, "zone_target": str(i),
              "adjacent_zones": adj, "door_way_targets": "", "origin": f3(p[0])})

# --- zombies: cada uno nace en una zona y camina al centro ---
for i, s in enumerate(spawns[:12]):
    zn = zone_of(s)
    pc = f"pc_{i}_0"
    E.append({"classname": "spawn_zombie", "targetname": zn, "target": pc,
              "origin": f3(s, 16)})
    # cadena de 2 path_corner hacia el centro
    nxt = [s, ((s[0] + cx) / 2, (s[1] + cy) / 2, s[2]), (cx, cy, s[2])]
    for j in range(2):
        n = f"pc_{i}_{j + 1}"
        E.append({"classname": "path_corner", "targetname": pc, "target": n, "origin": f3(nxt[j + 1])})
        pc = n

# --- armas de pared + compra emparejadas ---
# Los mapas del juego identifican las armas por ID numerico, no por nombre.
WALLS = [(2, 500), (5, 600), (21, 1200), (8, 1200), (23, 1500)]
for i, (wid, cost) in enumerate(WALLS):
    p = spawns[(i * 3 + 1) % len(spawns)]
    tn = f"ww_{i}"
    E.append({"classname": "weapon_wall", "targetname": tn, "origin": f3(p, 40),
              "angle": "0", "sequence": str(wid)})
    E.append({"classname": "buy_weapon", "target": tn, "weapon": str(wid),
              "cost": str(cost), "cost2": str(cost // 2), "origin": f3(p, 40)})

# --- perks ---
PERKS = [("perk_juggernog", "2500"), ("perk_speed", "3000"), ("perk_double", "2000"),
         ("perk_revive", "1500"), ("perk_mule", "4000"), ("perk_deadshot", "2000")]
for i, (pk, cost) in enumerate(PERKS):
    p = spawns[(i * 4 + 2) % len(spawns)]
    # la maquina lleva su propio coste, no se compra con buy_weapon
    E.append({"classname": pk, "targetname": f"pk_{i}", "origin": f3(p, 8),
              "cost": cost, "cost2": cost, "angles": "0 0 0"})

E.append({"classname": "mystery_box", "origin": f3(spawns[len(spawns) // 2], 8)})
E.append({"classname": "power_switch", "origin": f3(spawns[3], 8)})

# --- luces: nos quedamos con una de cada 3 para no ahogar el motor ---
for l in lights[::3]:
    E.append({"classname": "light", "origin": f3(l), "light": "150", "_color": "255 255 255"})
print(f"  luces: {len(lights)} -> {len(lights[::3])}")

new = W(E).encode("latin1")
print(f"  lump entidades: {e_len} -> {len(new)} bytes")
assert len(new) <= e_len, f"NO CABE: {len(new)} > {e_len}"

out = bytearray(d)
out[e_off:e_off + e_len] = new + b"\x00" * (e_len - len(new))
os.makedirs(OUT_DIR, exist_ok=True)
bsp = os.path.join(OUT_DIR, NAME + ".bsp")
open(bsp, "wb").write(bytes(out))
print(f"[OK] {bsp}  {len(out):,} bytes, {len(E)} entidades")

# --- waypoints de IA: los spawns ---
with open(os.path.join(OUT_DIR, NAME + ".way"), "w", newline="\n") as f:
    f.write(f"{len(spawns)}\n")
    for s in spawns:
        f.write(f"{s[0]:.1f} {s[1]:.1f} {s[2] + 16:.1f}\n")
print(f"[OK] {NAME}.way  {len(spawns)} waypoints")

with open(os.path.join(OUT_DIR, NAME + ".txt"), "w", newline="\n") as f:
    f.write("\n".join(["Garden (CS 1.6)", "Recinto ajardinado con", "pasillos, terrazas y",
                       "naves. Mapa adaptado de", "Counter-Strike 1.6 a", "NZ:P: zombies por zonas,",
                       "armas de pared, perks", "y caja misteriosa.", "",
                       "NZ:P Android (zurdo)", "1", "1"]) + "\n")
print(f"[OK] {NAME}.txt")

#!/usr/bin/env python3
"""Oraculo de mapas NZ:P.

principio de universal-modder (port-a-mod): "el original es la spec".
En vez de recordar o inventar como tiene que ser un mapa, se aprende de
los mapas que ya funcionan en el juego y se comprueba el resto contra
ellos.

Comprueba cada .bsp de assets/base/nzp/maps y dice que le falta para ser
jugable. No publicar nada que no pase.

Uso: python3 scripts/verify_maps.py
"""
import os
import re
import struct
import sys
import collections

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(REPO, "android-app", "app", "src", "main", "assets",
                    "base", "nzp", "maps")

# entidades que valen como punto de aparicion del jugador
SPAWN_ENTS = ("info_player_start", "info_player_1_spawn", "info_player_2_spawn",
              "info_player_3_spawn", "info_player_4_spawn", "info_player_takeo",
              "info_player_doctor", "info_player_tank", "info_player_nikolai")
PERKS = ("perk_juggernog", "perk_speed", "perk_double", "perk_revive",
         "perk_mule", "perk_deadshot", "perk_flopper", "perk_staminup")

# lo minimo para que un mapa sea jugable de verdad
CRITICAL = ("spawn", "zombies")
# lo que hace falta para que se parezca a COD zombies
NICE = ("zones", "path", "wallgun", "box", "power", "perks", "way", "txt")


def analyse(path):
    d = open(path, "rb").read()
    r = {"size": len(d), "fail": []}
    if len(d) < 124 or struct.unpack("<i", d[:4])[0] != 30:
        r["fail"].append("no es BSP30")
        return r
    L = struct.unpack("<30i", d[4:4 + 120])
    # texturas: embebidas frente a solo referenciadas (necesitarian WAD)
    to, tl = L[4], L[5]
    ntex = struct.unpack("<i", d[to:to + 4])[0]
    emb = ext = 0
    for i in range(ntex):
        dofs = struct.unpack("<i", d[to + 4 + i * 4:to + 8 + i * 4])[0]
        if dofs <= 0 or dofs > tl:
            continue
        a = to + dofs
        w, h = struct.unpack("<II", d[a + 16:a + 24])
        if w and h:
            emb += 1
        else:
            ext += 1
    r["tex_emb"], r["tex_ext"] = emb, ext
    if ext:
        r["fail"].append("texturas de WAD externo")

    # geometria dentro del arbol de nodos
    mo = L[28]
    mn = struct.unpack("<3f", d[mo:mo + 12])
    mx = struct.unpack("<3f", d[mo + 12:mo + 24])
    no, nl = L[10], L[11]
    nn = nl // 24
    nmin = [32767] * 3
    nmax = [-32768] * 3
    for k in range(nn):
        b = no + k * 24
        a = struct.unpack("<3h", d[b + 8:b + 14])
        c = struct.unpack("<3h", d[b + 14:b + 20])
        for j in range(3):
            nmin[j] = min(nmin[j], a[j])
            nmax[j] = max(nmax[j], c[j])
    if not all(nmin[j] <= mn[j] and nmax[j] >= mx[j] for j in range(3)):
        r["fail"].append("geometria fuera del arbol de nodos")
    r["faces"] = L[15] // 20
    r["nodes"] = nn
    r["ratio"] = nn / max(1, r["faces"])

    # entidades
    txt = d[L[0]:L[0] + L[1]].decode("latin1", "ignore")
    cls = collections.Counter(re.findall(r'"classname"\s+"([^"]+)"', txt))
    r["cls"] = cls
    r["spawn"] = sum(cls.get(k, 0) for k in SPAWN_ENTS)
    r["zombies"] = cls.get("spawn_zombie", 0)
    r["zones"] = cls.get("spawn_zone", 0)
    r["path"] = cls.get("path_corner", 0)
    r["wallgun"] = cls.get("weapon_wall", 0)
    r["box"] = cls.get("mystery_box", 0)
    r["power"] = cls.get("power_switch", 0)
    r["perks"] = sum(cls.get(k, 0) for k in PERKS)
    r["barricade"] = cls.get("item_barricade", 0)
    r["way"] = os.path.exists(path[:-4] + ".way")
    r["txt"] = os.path.exists(path[:-4] + ".txt")
    if not r["spawn"]:
        r["fail"].append("sin spawn de jugador")
    if not r["zombies"]:
        r["fail"].append("sin zombies")
    return r


def main():
    rows = []
    for f in sorted(os.listdir(MAPS)):
        if f.endswith(".bsp"):
            r = analyse(os.path.join(MAPS, f))
            r["name"] = f[:-4]
            rows.append(r)

    crit = [r for r in rows if r["fail"]]
    print(f"{len(rows)} mapas en el APK")
    print(f"{len(crit)} con fallos criticos\n")

    print(f"{'mapa':32s} {'zomb':>4s} {'zon':>3s} {'cam':>4s} "
          f"{'arm':>3s} {'caj':>3s} {'luz':>3s} {'perk':>4s} {'barr':>4s} "
          f"{'way':>3s} {'txt':>3s}  estado")
    print("-" * 104)
    for r in sorted(rows, key=lambda x: (bool(x["fail"]), x["name"].lower())):
        c = r["cls"]
        if r["fail"]:
            est = "FALLO: " + ", ".join(r["fail"])
        elif not (r["zones"] or r["path"]):
            est = "jugable (spawn directo)"
        elif not r["wallgun"]:
            est = "ok, sin armas de pared"
        else:
            est = "completo"
        print(f"{r['name'][:32]:32s} {r.get('zombies',0):>4d} "
              f"{r.get('zones',0):>3d} {r.get('path',0):>4d} "
              f"{r.get('wallgun',0):>3d} {r.get('box',0):>3d} "
              f"{r.get('power',0):>3d} {r.get('perks',0):>4d} "
              f"{r.get('barricade',0):>4d} "
              f"{'si' if r.get('way') else 'NO':>3s} "
              f"{'si' if r.get('txt') else 'NO':>3s}  {est}")

    print(f"\nRESUMEN")
    print(f"  completos (zombies + zonas + armas): "
          f"{sum(1 for r in rows if not r['fail'] and r.get('zones') and r.get('wallgun'))}")
    print(f"  jugables pero simples:               "
          f"{sum(1 for r in rows if not r['fail'] and not (r.get('zones') and r.get('wallgun')))}")
    print(f"  con fallos criticos:                 {len(crit)}")
    return 1 if crit else 0


if __name__ == "__main__":
    sys.exit(main())

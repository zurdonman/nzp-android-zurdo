#!/usr/bin/env python3
"""Compara el mapa prison con los 60 mapas que funcionan.

Metodologia de universal-modder (port-a-mod): "el original es la spec".
No se inventa como tiene que ser una prision: se miden los mapas que ya
funcionan en el juego y se comparan. Lo que se salga de rango es
sospechoso, no lo que a uno le parezca.

La medida clave es bar2zomb: la distancia de cada barricada al
spawn_zombie mas cercano. Si en los mapas buenos el zombie nace a X
unidades de la madera y en prison nace a otra muy distinta, ahi esta
el fallo de por que no llegan a las ventanas.

Uso: python3 scripts/compare_prison_reference.py [nombre_del_mapa]
"""
import glob
import math
import os
import re
import statistics as st
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(REPO, "android-app", "app", "src", "main", "assets",
                    "base", "nzp", "maps")
SPAWN = ("info_player_start", "info_player_1_spawn", "info_player_2_spawn",
         "info_player_3_spawn", "info_player_4_spawn")


def load(path):
    d = open(path, "rb").read()
    if len(d) < 124 or struct.unpack("<i", d[:4])[0] != 30:
        return None
    L = struct.unpack("<30i", d[4:4 + 120])
    txt = d[L[0]:L[0] + L[1]].decode("latin1", "ignore")
    ents = []
    for blk in re.findall(r"\{([^{}]*)\}", txt):
        kv = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', blk))
        if kv.get("classname"):
            ents.append(kv)
    return dict(ents=ents, faces=L[15] // 20, nodes=L[11] // 24,
                models=L[29] // 64, size=len(d))


def org(e):
    v = e.get("origin", "").split()
    return tuple(float(x) for x in v) if len(v) == 3 else None


def count(e, *names):
    return sum(1 for x in e if x["classname"] in names)


def profile(rec):
    e = rec["ents"]
    bars = [x for x in e if x["classname"] == "item_barricade"]
    zs = [x for x in e if x["classname"] == "spawn_zombie"]
    ds = []
    for b in bars:
        bo = org(b)
        if not bo:
            continue
        cand = [math.dist(bo, org(z)) for z in zs if org(z)]
        if cand:
            ds.append(min(cand))
    return dict(faces=rec["faces"], nodes=rec["nodes"],
                ratio=rec["nodes"] / max(1, rec["faces"]),
                models=rec["models"], size=rec["size"] / 1e6,
                bars=len(bars), zomb=len(zs),
                pcs=count(e, "path_corner"),
                zones=count(e, "spawn_zone"),
                lights=count(e, "light"),
                wallgun=count(e, "weapon_wall"),
                perks=count(e, "perk_juggernog", "perk_speed", "perk_double",
                            "perk_revive", "perk_mule", "perk_deadshot",
                            "perk_flopper", "perk_staminup"),
                spawn=count(e, *SPAWN),
                bar2zomb=(st.median(ds) if ds else None))


KEYS = ("faces", "nodes", "ratio", "models", "size", "bars", "zomb", "pcs",
        "zones", "lights", "wallgun", "perks", "spawn", "bar2zomb")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "prison"
    prof = {}
    for p in sorted(glob.glob(os.path.join(MAPS, "*.bsp"))):
        n = os.path.basename(p)[:-4]
        rec = load(p)
        if rec:
            prof[n] = profile(rec)
    ref = {k: v for k, v in prof.items() if k != target}
    if target not in prof:
        print(f"no esta {target}")
        return 1

    print(f"LOS {len(ref)} MAPAS QUE FUNCIONAN (mediana y rango):")
    for k in KEYS:
        vals = [p[k] for p in ref.values() if p[k] is not None]
        if not vals:
            continue
        print(f"  {k:10s} mediana={st.median(vals):8.1f}   "
              f"rango {min(vals):.0f} .. {max(vals):.0f}")

    pr = prof[target]
    print(f"\n{target.upper()} frente a esa mediana "
          f"(<<< = fuera del rango 0.4x..2.5x):")
    for k in KEYS:
        vals = [p[k] for p in ref.values() if p[k] is not None]
        if not vals:
            continue
        m = st.median(vals)
        v = pr[k]
        if v is None:
            print(f"  {k:10s} {target}= --")
            continue
        ok = (0.4 * m <= v <= 2.5 * m) if m else True
        print(f"  {k:10s} {target}={v:8.1f}  mediana={m:8.1f}  "
              f"{'ok' if ok else '<<<'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

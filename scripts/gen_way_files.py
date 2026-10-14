#!/usr/bin/env python3
"""Genera el .way de los mapas que no lo traen.

El motor carga %s/maps/%s.way (sv_main.c, W_fopen) y expone el grafo al
QC como waypoints[]/n_waypoints, con busqueda A* (pr_cmds.c). Los zombies
navegan con path_corner (ai_core.qc), asi que ese grafo es la fuente mas
fiel para reconstruirlo: cada path_corner es un nodo y su "target" es la
arista al siguiente.

Formato que espera el motor:
    Waypoint
    {
    origin = 'x y z'
    id = 0
    special =
    target = 3
    target2 =
    ...
    }

Uso: python3 scripts/gen_way_files.py
"""
import os
import re
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(REPO, "android-app", "app", "src", "main", "assets",
                    "base", "nzp", "maps")
MAX_WAYPOINTS = 256   # nzportable_def.h
MAX_TARGETS = 8


def entities(bsp_path):
    """Devuelve la lista de entidades como dicts de claves/valores."""
    d = open(bsp_path, "rb").read()
    if struct.unpack("<i", d[:4])[0] != 30:
        return []
    L = struct.unpack("<30i", d[4:4 + 120])
    txt = d[L[0]:L[0] + L[1]].decode("latin1", "ignore")
    out = []
    for blk in re.findall(r"\{([^{}]*)\}", txt):
        kv = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', blk))
        if kv.get("classname"):
            out.append(kv)
    return out


def build_way(bsp_path):
    ents = entities(bsp_path)
    nodes = [e for e in ents if e.get("classname") == "path_corner"]
    if not nodes:
        return None, "sin path_corner"
    # indice: targetname -> posicion en la lista
    by_name = {}
    for i, n in enumerate(nodes):
        if n.get("targetname"):
            by_name.setdefault(n["targetname"], []).append(i)
    if len(nodes) > MAX_WAYPOINTS:
        nodes = nodes[:MAX_WAYPOINTS]

    lines = []
    for i, n in enumerate(nodes):
        org = n.get("origin", "0 0 0")
        parts = org.split()
        if len(parts) != 3:
            continue
        try:
            x, y, z = (float(v) for v in parts)
        except ValueError:
            continue
        # el nodo destino es el path_corner cuyo targetname es mi "target"
        tg = n.get("target", "")
        targets = []
        if tg and tg in by_name:
            for j in by_name[tg]:
                if j != i and j < len(nodes):
                    targets.append(j)
        lines.append("Waypoint\n{\norigin = '%.1f %.1f %.1f'\nid = %d\n"
                     "special =\n" % (x, y, z, i))
        for k in range(MAX_TARGETS):
            key = "target" if k == 0 else "target%d" % (k + 1)
            val = str(targets[k]) if k < len(targets) else ""
            lines.append("%s = %s\n" % (key, val))
        lines.append("}\n\n")
    return "".join(lines), "%d nodos" % len(nodes)


def main():
    made = skipped = 0
    for f in sorted(os.listdir(MAPS)):
        if not f.endswith(".bsp"):
            continue
        stem = f[:-4]
        way = os.path.join(MAPS, stem + ".way")
        if os.path.exists(way):
            skipped += 1
            continue
        body, note = build_way(os.path.join(MAPS, f))
        if body is None:
            print(f"  {stem[:30]:30s}  omitido ({note})")
            continue
        with open(way, "w", newline="\n") as fh:
            fh.write(body)
        made += 1
        print(f"  {stem[:30]:30s}  .way generado ({note})")
    print(f"\ngenerados: {made}   ya lo tenian: {skipped}")


if __name__ == "__main__":
    main()

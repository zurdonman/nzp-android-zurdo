#!/usr/bin/env python3
"""Importa mapas de la comunidad de NZ:P al APK.

Fuentes (repos publicos de GitHub con mapas de usuario):
  Blade04208/NZP-Server-Maps      Jeyphr/NZP-Maps
  BCDeshiG/NZP-Requiem-OLD        Cybrock9000/M.R.S.-Titanic-Nzp-Map
  Evilslayer506/Sunken-Bar

Comprobaciones antes de aceptar un mapa:
  1. BSP30 (version 30), el unico que entiende el motor.
  2. Texturas embebidas en el propio BSP. El motor las carga con
     WAD3_LoadTexture sobre el miptex lump, asi que si vinieran de un
     WAD externo saldrian en damero.
  3. Que tenga info_player_start (si no, no se puede jugar).
  4. Que no sea un mapa de menu, de test o de ejemplo.

Uso: python3 scripts/import_community_maps.py <dir_con_bsp>...
"""
import os
import re
import shutil
import struct
import sys
import collections

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(REPO, "android-app", "app", "src", "main", "assets",
                    "base", "nzp", "maps")

# nombres que no son mapas jugables
SKIP = {"menu", "test", "shield", "example_01", "tbm", "weapon_test"}
# entidades que valen como punto de aparicion del jugador
SPAWN_ENTS = ("info_player_start", "info_player_1_spawn", "info_player_2_spawn",
              "info_player_3_spawn", "info_player_4_spawn", "info_player_takeo",
              "info_player_doctor", "info_player_tank", "info_player_nikolai")
# ya presentes en el APK (comparacion sin sensibilidad a mayusculas)
ALREADY = {f.lower() for f in os.listdir(MAPS) if f.endswith(".bsp")}


def analyse(path):
    """Devuelve dict con lo que hay que saber de un .bsp, o None si no vale."""
    d = open(path, "rb").read()
    if len(d) < 124 or struct.unpack("<i", d[:4])[0] != 30:
        return None
    L = struct.unpack("<30i", d[4:4 + 120])
    # texturas: embebidas (con pixeles) frente a solo referenciadas
    to, tl = L[4], L[5]
    ntex = struct.unpack("<i", d[to:to + 4])[0]
    embedded = external = 0
    for i in range(ntex):
        dofs = struct.unpack("<i", d[to + 4 + i * 4:to + 8 + i * 4])[0]
        if dofs <= 0 or dofs > tl:
            continue
        a = to + dofs
        w, h = struct.unpack("<II", d[a + 16:a + 24])
        if w and h:
            embedded += 1
        else:
            external += 1
    # entidades
    eo, el = L[0], L[1]
    txt = d[eo:eo + el].decode("latin1", "ignore")
    cls = collections.Counter(re.findall(r'"classname"\s+"([^"]+)"', txt))
    return dict(size=len(d), tex_emb=embedded, tex_ext=external, cls=cls,
                nodes=L[11] // 24, faces=L[15] // 20)


def main():
    srcs = sys.argv[1:] or ["/tmp/nzpmaps"]
    seen = {}      # nombre en minusculas -> ruta (deduplica entre repos)
    for s in srcs:
        for root, _dirs, files in os.walk(s):
            for f in files:
                if f.lower().endswith(".bsp"):
                    seen.setdefault(f.lower(), os.path.join(root, f))

    print(f"{len(seen)} mapas unicos encontrados\n")
    print(f"{'mapa':34s} {'MB':>6s} {'tex':>4s} {'zomb':>5s} {'spawn':>5s}  estado")
    print("-" * 78)

    added, skipped = [], []
    for low, path in sorted(seen.items()):
        name = os.path.basename(path)
        stem = os.path.splitext(name)[0].lower()
        if stem in SKIP:
            print(f"{name[:34]:34s} {'':>6s} {'':>4s} {'':>5s} {'':>5s}  descartado (menu/test)")
            skipped.append((name, "menu/test"))
            continue
        if low in ALREADY:
            print(f"{name[:34]:34s} {'':>6s} {'':>4s} {'':>5s} {'':>5s}  ya estaba")
            skipped.append((name, "ya presente"))
            continue
        info = analyse(path)
        if not info:
            print(f"{name[:34]:34s} {'':>6s} {'':>4s} {'':>5s} {'':>5s}  descartado (no BSP30)")
            skipped.append((name, "no BSP30"))
            continue
        if info["tex_ext"]:
            print(f"{name[:34]:34s} {info['size']/1e6:6.2f} {info['tex_emb']:>4d}"
                  f" {'':>5s} {'':>5s}  descartado (necesita WAD)")
            skipped.append((name, "WAD externo"))
            continue
        c = info["cls"]
        nz = c.get("spawn_zombie", 0)
        # NZP acepta el spawn unico o los cuatro cooperativos (y los
        # especificos de personaje de los mapas tipo WaW)
        sp = sum(c.get(k, 0) for k in SPAWN_ENTS)
        if not sp:
            print(f"{name[:34]:34s} {info['size']/1e6:6.2f} {info['tex_emb']:>4d}"
                  f" {nz:>5d} {sp:>5d}  descartado (sin info_player_start)")
            skipped.append((name, "sin spawn"))
            continue

        shutil.copy2(path, os.path.join(MAPS, name))
        stem2 = os.path.splitext(name)[0]
        # descripcion para el menu de USER MAPS (12 lineas: 8 texto,
        # una en blanco, autor, y dos flags)
        with open(os.path.join(MAPS, stem2 + ".txt"), "w", newline="\n") as f:
            f.write("\n".join([
                stem2.replace("_", " ").replace("-", " ").title()[:40],
                "Community map for NZ:P", "", "", "", "", "", "",
                "NZ:P community", "1", "1",
            ]) + "\n")
        note = "sin zombies" if not nz else ""
        print(f"{name[:34]:34s} {info['size']/1e6:6.2f} {info['tex_emb']:>4d}"
              f" {nz:>5d} {sp:>5d}  ANADIDO {note}")
        added.append((name, info))

    print(f"\nanadidos: {len(added)}   descartados: {len(skipped)}")
    tot = sum(i["size"] for _n, i in added)
    print(f"tamano anadido: {tot/1e6:.1f} MB")
    if added:
        print("\npor numero de zombies (los que no tienen no son jugables como tal):")
        for n, i in sorted(added, key=lambda x: -x[1]["cls"].get("spawn_zombie", 0))[:10]:
            print(f"  {n[:32]:32s} zombies={i['cls'].get('spawn_zombie',0):3d}"
                  f"  caras={i['faces']:5d}  nodos={i['nodes']:5d}")


if __name__ == "__main__":
    main()

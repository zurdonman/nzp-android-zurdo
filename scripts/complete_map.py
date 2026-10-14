#!/usr/bin/env python3
"""Completa un mapa NZ:P anadiendo las maquinas de perks que le faltan.

El original es la spec: se compara el mapa contra los otros 70 y se
anaden las maquinas que tiene la mayoria y este no. Las posiciones no
se inventan: se busca suelo real recorriendo el arbol de colision del
propio BSP (hull 1, el mismo que usa el motor), se comprueba que la
maquina cabe y se la orienta mirando hacia donde mas sitio libre hay.

Uso:
    python3 scripts/complete_map.py prison            # solo informe
    python3 scripts/complete_map.py prison --apply    # escribe el .bsp

Escribe un NUEVO bloque de entidades al final del fichero y apunta el
lump 0 a el: no se toca ni un byte de la geometria.
"""
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(ROOT, "android-app", "app", "src", "main", "assets",
                    "base", "nzp", "maps")
MODELOS = os.path.join(ROOT, "android-app", "app", "src", "main",
                       "assets", "base", "nzp", "models", "machines")

VACIO = -1          # CONTENTS_EMPTY
SOLIDO = -2         # CONTENTS_SOLID

# maquinas que anadir, en orden de importancia (lo mas comun en los 70
# mapas de referencia). El modelo es el de la escala que usa el mapa.
PERKS = [
    # classname,        modelo (quake_scale),              coste
    ("perk_juggernog", "juggernog.mdl",   "2500"),
    ("perk_double",    "double_tap.mdl",  "2000"),
    ("perk_pap",       "pap.mdl",         "5000"),
    ("perk_mule",      "mulekick.mdl",    "4000"),
    ("perk_deadshot",  "deadshot.mdl",    "1500"),
]

# entidades que sabemos que estan de pie en el suelo: sus origenes son
# semillas fiables para buscar sitio. Las de pared (item_barricade,
# weapon_wall, place_model) NO valen: su origen esta dentro del muro.
DE_PIE = ("info_player_start", "info_player_1_spawn", "info_player_2_spawn",
          "info_player_3_spawn", "info_player_4_spawn", "spawn_zone",
          "path_corner", "spawn_zombie", "mystery_box")

SEPARACION = 128.0      # separacion minima a cualquier otra entidad


class Bsp:
    def __init__(self, path):
        self.path = path
        d = open(path, "rb").read()
        assert struct.unpack("<i", d[:4])[0] == 30, "no es BSP30"
        self.d = d
        self.L = struct.unpack("<30i", d[4:4 + 120])

    def lump(self, i):
        return self.L[i * 2], self.L[i * 2 + 1]

    def planos(self):
        o, l = self.lump(1)
        return [struct.unpack("<4f", self.d[o + k * 20:o + k * 20 + 16])
                for k in range(l // 20)]

    def clipnodes(self):
        o, l = self.lump(9)
        return [struct.unpack("<ihh", self.d[o + k * 8:o + k * 8 + 8])
                for k in range(l // 8)]

    def modelo0(self):
        o, _ = self.lump(14)
        b = o
        mn = struct.unpack("<3f", self.d[b:b + 12])
        mx = struct.unpack("<3f", self.d[b + 12:b + 24])
        hn = [struct.unpack("<i", self.d[b + 36 + 4 * i:b + 40 + 4 * i])[0]
              for i in range(4)]
        return dict(mins=mn, maxs=mx, headnode=hn)

    def entities(self):
        o, l = self.lump(0)
        return self.d[o:o + l].decode("latin1", "ignore")


class Mapa:
    def __init__(self, path):
        self.b = Bsp(path)
        self.planos = self.b.planos()
        self.cn = self.b.clipnodes()
        self.m0 = self.b.modelo0()
        self.hull = self.m0["headnode"][1]      # hull 1 = punto

    def contenido(self, p):
        """contents del punto recorriendo el arbol de colision."""
        n = self.hull
        x, y, z = p
        for _ in range(100000):
            if n < 0:
                return n
            pn, c0, c1 = self.cn[n]
            nx, ny, nz, dist = self.planos[pn]
            n = c0 if (nx * x + ny * y + nz * z - dist) >= 0 else c1
        return VACIO

    def vacio(self, p):
        return self.contenido(p) == VACIO

    def suelo(self, x, y, z, paso=4.0, max_bajada=400.0):
        """Primer suelo por debajo de z. Devuelve None si no hay."""
        bajado = 0.0
        while bajado < max_bajada:
            if self.contenido((x, y, z - bajado)) == SOLIDO:
                return z - bajado
            bajado += paso
        return None

    def cabe(self, x, y, z):
        """La maquina (caja de 48x48 y 80 de alto) cabe en ese sitio."""
        for dz in (8.0, 40.0, 72.0):
            for dx in (-20.0, 0.0, 20.0):
                for dy in (-20.0, 0.0, 20.0):
                    if not self.vacio((x + dx, y + dy, z + dz)):
                        return False
        return True

    def orientacion(self, x, y, z):
        """Yaw hacia donde mas sitio libre hay (la maquina mira al hueco)."""
        import math
        mejor, mejor_hueco = 0.0, -1.0
        for i in range(16):
            yaw = i * 22.5
            r = math.radians(yaw)
            dx, dy = math.sin(r), math.cos(r)
            hueco = 0.0
            while hueco < 240.0 and self.vacio(
                    (x + dx * (32 + hueco), y + dy * (32 + hueco), z + 40)):
                hueco += 8.0
            if hueco > mejor_hueco:
                mejor_hueco, mejor = hueco, yaw
        return mejor


def origenes(ent):
    """[(classname, (x,y,z))] de todas las entidades con origin."""
    out = []
    for blk in re.findall(r"\{([^{}]*)\}", ent):
        kv = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', blk))
        o = kv.get("origin", "").split()
        if len(o) == 3:
            try:
                out.append((kv.get("classname", ""),
                            tuple(float(v) for v in o)))
            except ValueError:
                pass
    return out


def bloque(cls, org, yaw, coste, modelo):
    return ('{\n'
            '"classname" "%s"\n'
            '"origin" "%g %g %g"\n'
            '"angles" "0 %g 0"\n'
            '"spawnflags" "8"\n'
            '"cost" "%s"\n'
            '"oldmodel" "sounds/machines/perk_drink.wav"\n'
            '"model" "models/machines/quake_scale/%s"\n'
            '}\n' % (cls, org[0], org[1], org[2], yaw, coste, modelo))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    aplicar = "--apply" in sys.argv
    if not args:
        print("Uso: python3 scripts/complete_map.py <mapa> [--apply]")
        return 2
    nombre = args[0]
    path = os.path.join(MAPS, nombre + ".bsp")
    if not os.path.exists(path):
        print("no existe " + path)
        return 2

    m = Mapa(path)
    ent = m.b.entities()
    ents = origenes(ent)
    ya = {c for c, _ in ents}

    # 1) que perks faltan
    falta = [(c, mo, co) for c, mo, co in PERKS if c not in ya]
    print("%s: tiene %d perks, faltan %d -> %s"
          % (nombre, len([c for c in ya if c.startswith("perk")]),
             len(falta), ", ".join(c for c, _, _ in falta)))
    if not falta:
        print("nada que anadir")
        return 0

    # 2) el modelo tiene que existir en el APK
    for c, mo, _ in falta:
        ruta = os.path.join(MODELOS, "quake_scale", mo)
        if not os.path.exists(ruta):
            print("  ERROR: falta el modelo " + ruta)
            return 1

    # 3) semillas: origenes de entidades que estan de pie, dentro del mapa
    mn, mx = m.m0["mins"], m.m0["maxs"]
    semillas = []
    for c, o in ents:
        if c not in DE_PIE and not c.startswith("perk"):
            continue
        if not (mn[0] <= o[0] <= mx[0] and mn[1] <= o[1] <= mx[1]
                and mn[2] <= o[2] <= mx[2]):
            continue
        semillas.append(o)
    print("  semillas en suelo: %d" % len(semillas))

    # 4) candidatos: suelo real + hueco suficiente
    cands = []
    for (x, y, z) in semillas:
        s = m.suelo(x, y, z)
        if s is None:
            continue
        if not m.cabe(x, y, s):
            continue
        cands.append((x, y, s))
    print("  sitios validos: %d" % len(cands))
    if not cands:
        print("  ERROR: no encuentro ningun sitio donde quepa una maquina")
        return 1

    # 5) elegir los mas separados entre si y del resto de entidades.
    #    Ojo: los candidatos salen de origenes de entidades, asi que cada
    #    uno coincide consigo mismo; esa distancia no cuenta.
    def elegir(sep):
        ocupados = [o for _, o in ents]
        elegs = []
        for _ in falta:
            mejor, mejor_d = None, -1.0
            for c in cands:
                if c in elegs:
                    continue
                ds = [((c[0] - o[0]) ** 2 + (c[1] - o[1]) ** 2) ** .5
                      for o in ocupados]
                ds = [x for x in ds if x > 1.0]        # ignora a si mismo
                if not ds:
                    continue
                d = min(ds)
                if d < sep:
                    continue
                if d > mejor_d:
                    mejor, mejor_d = c, d
            if mejor is None:
                break
            elegs.append(mejor)
            ocupados.append(mejor)
        return elegs

    elegidos = []
    for sep in (SEPARACION, 96.0, 64.0, 48.0, 32.0):
        elegidos = elegir(sep)
        if len(elegidos) >= len(falta):
            print("  separacion usada: %g u" % sep)
            break
    if len(elegidos) < len(falta):
        print("  aviso: solo hay sitio bien separado para %d de %d"
              % (len(elegidos), len(falta)))
        falta = falta[:len(elegidos)]

    print("  se anaden %d maquinas:" % len(falta))
    nuevo = ""
    for (c, mo, co), p in zip(falta, elegidos):
        yaw = m.orientacion(p[0], p[1], p[2])
        nuevo += bloque(c, p, yaw, co, mo)
        print("    %-16s en (%g, %g, %g) mirando a %g"
              % (c, p[0], p[1], p[2], yaw))

    if not aplicar:
        print("\n(escribe de verdad con --apply)")
        return 0

    # 6) escribir: nuevo lump de entidades al final del fichero
    viejo = ent.rstrip("\x00")
    texto = (viejo + "\n" + nuevo).encode("latin1") + b"\x00"
    d = bytearray(m.b.d)
    while len(d) % 4:
        d.append(0)                      # alinear
    off = len(d)
    d += texto
    struct.pack_into("<ii", d, 4, off, len(texto))
    open(path, "wb").write(bytes(d))
    print("\nescrito %s (%d -> %d bytes)" % (path, len(m.b.d), len(d)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

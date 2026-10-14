#!/usr/bin/env python3
"""Oraculo de los visores 3D de visor/.

Un HTML puede "generarse bien" y seguir estando roto: sintaxis invalida,
datos con la forma equivocada, grupos que no cubren los indices... Este
script abre cada HTML generado, le saca el JS y lo somete a dos pruebas
mecanicas con node:

  1. node --check          -> sintaxis
  2. viewer_headless.js    -> ejecucion real contra un Three.js simulado

Ademas comprueba en Python que los grupos de material cubren todos los
indices y que no hay NaN en posiciones/UVs.

Uso:
    python3 scripts/check_viewers.py            # todos los de visor/
    python3 scripts/check_viewers.py prison     # solo uno

Sale con codigo 1 si algo falla.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISOR = os.path.join(ROOT, "visor")
HEADLESS = os.path.join(ROOT, "scripts", "viewer_headless.js")


def inline_js(html):
    """Devuelve el ultimo <script> sin src (el del visor)."""
    bloques = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    return bloques[-1] if bloques else ""


def check_data(js, name):
    """Comprueba el JSON embebido: claves, grupos y ausencia de NaN."""
    m = re.search(r"const M = (.*?);\n", js, re.S)
    if not m:
        return ["no encuentro 'const M = ...'"]
    try:
        d = json.loads(m.group(1))
    except Exception as e:
        return ["JSON invalido: %s" % e]
    errs = []
    for k in ("name", "pos", "uv", "idx", "groups", "tex"):
        if k not in d:
            errs.append("falta la clave '%s'" % k)
    if errs:
        return errs
    if len(d["pos"]) % 3:
        errs.append("pos no multiplo de 3: %d" % len(d["pos"]))
    if len(d["uv"]) % 2:
        errs.append("uv no multiplo de 2: %d" % len(d["uv"]))
    if len(d["uv"]) // 2 != len(d["pos"]) // 3:
        errs.append("uv/pos desalineados: %d vs %d" % (len(d["uv"]) // 2, len(d["pos"]) // 3))
    if len(d["idx"]) % 3:
        errs.append("idx no multiplo de 3: %d" % len(d["idx"]))
    # los grupos deben cubrir todos los indices, en orden y sin huecos
    base = 0
    for g in d["groups"]:
        if g.get("start") != base:
            errs.append("grupo '%s' empieza en %s, se esperaba %d" % (g.get("tex"), g.get("start"), base))
            break
        base += g.get("count", 0)
    if base != len(d["idx"]):
        errs.append("los grupos cubren %d indices pero hay %d" % (base, len(d["idx"])))
    # tex es una LISTA: cada grupo guarda el INDICE de su textura
    for g in d["groups"]:
        t = g.get("tex")
        if not isinstance(t, int) or not (0 <= t < len(d["tex"])):
            errs.append("grupo con tex fuera de rango: %r" % t)
            break
    if any(v != v for v in d["pos"]):   # NaN != NaN
        errs.append("NaN en pos")
    if any(v != v for v in d["uv"]):
        errs.append("NaN en uv")
    if d["idx"] and max(d["idx"]) >= len(d["pos"]) // 3:
        errs.append("indice fuera de rango: %d >= %d" % (max(d["idx"]), len(d["pos"]) // 3))
    return errs


def main():
    args = sys.argv[1:]
    if args:
        ficheros = [os.path.join(VISOR, a + ".html") for a in args]
    else:
        ficheros = sorted(glob.glob(os.path.join(VISOR, "*.html")))
        ficheros = [f for f in ficheros if os.path.basename(f) != "index.html"]
    if not ficheros:
        print("nada que comprobar en visor/")
        return 1

    buenos, malos = [], []
    tmp = tempfile.mkdtemp(prefix="visorcheck")
    for f in ficheros:
        name = os.path.basename(f)[:-5]
        html = open(f, encoding="utf-8", errors="replace").read()
        js = inline_js(html)
        if not js:
            print("  %-28s FALLO: sin <script> inline" % name)
            malos.append(name)
            continue
        ruta = os.path.join(tmp, name + ".js")
        open(ruta, "w", encoding="utf-8").write(js)

        errs = check_data(js, name)
        if not errs:
            p = subprocess.run(["node", "--check", ruta], capture_output=True, text=True)
            if p.returncode:
                errs.append("sintaxis: " + (p.stderr.strip().splitlines() or ["?"])[0])
        if not errs:
            p = subprocess.run(["node", HEADLESS, ruta], capture_output=True, text=True)
            if p.returncode:
                errs.append("ejecucion: " + (p.stdout + p.stderr).strip().splitlines()[0][:150])

        if errs:
            print("  %-28s FALLO: %s" % (name, errs[0]))
            malos.append(name)
        else:
            nv = len(json.loads(re.search(r"const M = (.*?);\n", js, re.S).group(1))["pos"]) // 3
            print("  %-28s ok (%d vert)" % (name, nv))
            buenos.append(name)

    print("\n%d bien, %d mal" % (len(buenos), len(malos)))
    if malos:
        print("mapas mal: " + ", ".join(malos))
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main())

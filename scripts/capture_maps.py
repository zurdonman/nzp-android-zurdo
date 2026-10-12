#!/usr/bin/env python3
"""
capture_maps.py
===============
Captor generico de vistas para cualquier .bsp de NZ:P. Reutiliza el renderer por
software (Z-buffer + WAD3 + lightmaps) de render_bsp_views.py y, a partir del
bounding box del mapa, genera:

  <mapa>_top.png      vista cenital (ortografica aproximada por perspectiva)
  <mapa>_n.png        horizonte norte
  <mapa>_e.png        horizonte este
  <mapa>_s.png        horizonte sur
  <mapa>_w.png        horizonte oeste

Se usa para inspeccionar visualmente los mapas de la rama (prison, fy_garden,
town, tranzit) sin necesidad de VR ni screencap del casco.

Uso:
  python scripts/capture_maps.py prison fy_garden
  python scripts/capture_maps.py            # todos los mapas nuevos de la rama
"""

import math
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ASSET_MAPS_DIR = os.path.join(ROOT_DIR, "android-app", "app", "src", "main",
                              "assets", "base", "nzp", "maps")
OUT_DIR = os.path.join(ROOT_DIR, "maps_src", "screenshots")

# Reutilizar renderer ya implementado
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_bsp_views import BSPScene, write_png  # noqa: E402

# Mapas que la rama arena/b3f2fbc5 anadio o modifico respecto a main
DEFAULT_MAPS = ["prison", "fy_garden", "town", "tranzit"]

TAN_HALF_FOV = math.tan(math.radians(82.0 * 0.5))  # fov por defecto del renderer


def scene_bounds(scene: BSPScene):
    """Bounding box en coordenadas de mundo uniendo todos los dmodels."""
    w_min = [1e18, 1e18, 1e18]
    w_max = [-1e18, -1e18, -1e18]
    for mdl in scene.models:
        mins = mdl[0:3]
        size = mdl[3:6]
        origin = mdl[6:9]
        for k in range(3):
            lo = origin[k] + mins[k]
            hi = lo + size[k]
            w_min[k] = min(w_min[k], lo)
            w_max[k] = max(w_max[k], hi)
    if w_min[0] > w_max[0]:  # sin modelos
        return (0, 0, 0), (0, 0, 0)
    return tuple(w_min), tuple(w_max)


def capture_map(name: str):
    bsp = os.path.join(ASSET_MAPS_DIR, f"{name}.bsp")
    if not os.path.isfile(bsp):
        print(f"[SKIP] {name}: no existe {bsp}")
        return
    scene = BSPScene(bsp)
    wmin, wmax = scene_bounds(scene)
    cx = (wmin[0] + wmax[0]) / 2.0
    cy = (wmin[1] + wmax[1]) / 2.0
    cz_lo = wmin[2]
    cz_hi = wmax[2]
    extent_xy = max(wmax[0] - wmin[0], wmax[1] - wmin[1]) / 2.0
    height = extent_xy / TAN_HALF_FOV + (cz_hi - cz_lo) * 0.5 + 64.0
    eye_z = cz_lo + (cz_hi - cz_lo) * 0.62

    os.makedirs(OUT_DIR, exist_ok=True)
    jobs = [
        (f"{name}_top", (cx, cy, height), 0.0, -90.0, 720, 720),
        (f"{name}_n", (cx, cy, eye_z), 90.0, -3.0, 640, 360),
        (f"{name}_e", (cx, cy, eye_z), 0.0, -3.0, 640, 360),
        (f"{name}_s", (cx, cy, eye_z), -90.0, -3.0, 640, 360),
        (f"{name}_w", (cx, cy, eye_z), 180.0, -3.0, 640, 360),
    ]
    print(f"\n=== {name}  bbox=({wmin[0]:.0f},{wmin[1]:.0f},{wmin[2]:.0f})..({wmax[0]:.0f},{wmax[1]:.0f},{wmax[2]:.0f}) ===")
    for out_name, pos, yaw, pitch, w, h in jobs:
        st = {}
        rgb = scene.render_camera(pos, yaw, pitch, w, h, stats=st)
        out_p = os.path.join(OUT_DIR, f"{out_name}.png")
        write_png(out_p, w, h, rgb)
        cov = st.get("coverage_pct", 0.0)
        sky = st.get("sky_pct", 0.0)
        lum = st.get("lum_mean", 0.0)
        dark = st.get("dark_pct", 0.0)
        print(f"  [OK] {out_name}.png  cobertura={cov:.1f}% cielo={sky:.1f}% "
              f"luz={lum:.1f} oscuro={dark:.1f}%")


def main():
    maps = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_MAPS
    for m in maps:
        capture_map(m)
    print(f"\nCapturas en: {OUT_DIR}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tranzit_textures.py
===================
Generador de texturas WAD3 (Half-Life / NZ:P) 100% procedurales para el mapa
BO2 Tranzit (Green Run).

Incluye:
  * Una tipografia bitmap 5x7 para rotular los letreros de cada estacion
    (BUS DEPOT, DINER, FARM, POWER, BANK, BAR, ...).
  * Un cuantizador a paleta de 256 colores (rejilla 6x7x6 + negros/blancos).
  * Un constructor de bloques miptex WAD3 completos (4 mipmaps + paleta RGB).
  * ~24 texturas tematicas: asfalto con linea central, aceras, lona del bus,
    reactor azul, baldosas de diner, heno, tejas, rejillas, ventanas
    iluminadas, ladrillo decorativo, persiana de garaje, torre de
    refrigeracion, silo, valla de madera, franjas de peligro, etc.

No depende de Pillow: usa solo zlib/struct de la libreria estandar.
"""

import math
import struct
from typing import Callable, Dict, List, Tuple

# ============================================================================
# TIPOGRAFIA BITMAP 5x7 (A-Z, 0-9 y signos basicos)
# ============================================================================

FONT_5x7: Dict[str, List[str]] = {
    " ": ["     ", "     ", "     ", "     ", "     ", "     ", "     "],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "J": ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#...#", "#.#.#", "##.##", "#...#"],
    "X": ["#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": ["#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": ["..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
    ".": [".....", ".....", ".....", ".....", ".....", ".##..", ".##.."],
    "&": [".##..", "#..#.", "#.#..", ".#...", "#.#.#", "#..#.", ".##.#"],
    "!": ["..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."],
    "'": ["..#..", "..#..", ".....", ".....", ".....", ".....", "....."],
    "/": ["....#", "...#.", "...#.", "..#..", ".#...", ".#...", "#...."],
    ":": [".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."],
}

GLYPH_W = 5
GLYPH_H = 7


def text_mask(text: str) -> Tuple[int, int, List[Tuple[int, int]]]:
    """Devuelve (ancho, alto, lista de pixeles encendidos) del texto."""
    text = text.upper()
    cols: List[Tuple[int, int]] = []
    x = 0
    for ch in text:
        glyph = FONT_5x7.get(ch, FONT_5x7[" "])
        for gy in range(GLYPH_H):
            row = glyph[gy]
            for gx in range(GLYPH_W):
                if row[gx] == "#" or row[gx] == "*":
                    cols.append((x + gx, gy))
        x += GLYPH_W + 1
    if x > 0:
        x -= 1
    return x, GLYPH_H, cols


# ============================================================================
# PALETA UNIVERSAL DE 256 COLORES (rejilla 6 x 7 x 6 + negros y blancos puros)
# ============================================================================

_R_LEVELS = [0, 51, 102, 153, 204, 255]
_G_LEVELS = [0, 43, 85, 128, 170, 213, 255]
_B_LEVELS = [0, 51, 102, 153, 204, 255]


def _build_palette() -> List[Tuple[int, int, int]]:
    pal: List[Tuple[int, int, int]] = []
    for bi in range(6):
        for gi in range(7):
            for ri in range(6):
                pal.append((_R_LEVELS[ri], _G_LEVELS[gi], _B_LEVELS[bi]))
    # 252 = negro puro, 253 = blanco puro, 254/255 reservados
    while len(pal) < 254:
        pal.append((0, 0, 0))
    pal.append((0, 0, 0))
    pal.append((255, 255, 255))
    return pal


PALETTE: List[Tuple[int, int, int]] = _build_palette()

# Cache de cuantizacion RGB -> indice de paleta (65536 entradas como maximo)
_Q64 = [0, 51, 102, 153, 204, 255]
_QCACHE: Dict[int, int] = {}


def quant_idx(r: int, g: int, b: int) -> int:
    r = 0 if r < 0 else (255 if r > 255 else int(r))
    g = 0 if g < 0 else (255 if g > 255 else int(g))
    b = 0 if b < 0 else (255 if b > 255 else int(b))
    key = (r << 16) | (g << 8) | b
    cached = _QCACHE.get(key)
    if cached is not None:
        return cached
    ri = (r * 5 + 127) // 255
    bi = (b * 5 + 127) // 255
    # El verde tiene 7 niveles
    gi = int(round(g / 255.0 * 6.0))
    if gi > 6:
        gi = 6
    idx = bi * 42 + gi * 6 + ri
    _QCACHE[key] = idx
    return idx


# ============================================================================
# CONSTRUCTOR DE BLOQUES MIPTEX WAD3
# ============================================================================

Painter = Callable[[int, int, int, int], Tuple[int, int, int]]


def make_wad3_miptex(name: str, w: int, h: int, painter: Painter) -> bytes:
    """Crea un miptex WAD3 completo: cabecera + 4 mipmaps + paleta de 256 RGB."""
    if (w & (w - 1)) != 0 or (h & (h - 1)) != 0:
        raise ValueError(f"{name}: las dimensiones deben ser potencia de 2")

    # Mip 0
    mip0 = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            r, g, b = painter(x, y, w, h)
            mip0[y * w + x] = quant_idx(r, g, b)

    def downsample(src: bytes, sw: int, sh: int) -> bytes:
        dw, dh = max(1, sw // 2), max(1, sh // 2)
        out = bytearray(dw * dh)
        for y in range(dh):
            for x in range(dw):
                a = src[(2 * y) * sw + 2 * x]
                bb = src[(2 * y) * sw + min(2 * x + 1, sw - 1)]
                c = src[min(2 * y + 1, sh - 1) * sw + 2 * x]
                d = src[min(2 * y + 1, sh - 1) * sw + min(2 * x + 1, sw - 1)]
                out[y * dw + x] = (a + bb + c + d) // 4
        return bytes(out)

    m1 = downsample(bytes(mip0), w, h)
    m2 = downsample(m1, max(1, w // 2), max(1, h // 2))
    m3 = downsample(m2, max(1, w // 4), max(1, h // 4))

    o0 = 40
    o1 = o0 + len(mip0)
    o2 = o1 + len(m1)
    o3 = o2 + len(m2)
    name_bytes = name.encode("latin1")[:15].ljust(16, b"\x00")
    hdr = struct.pack("<16sIIIIII", name_bytes, w, h, o0, o1, o2, o3)
    tail = struct.pack("<H", 256) + bytes(v for c in PALETTE for v in c) + b"\x00\x00"
    return hdr + bytes(mip0) + m1 + m2 + m3 + tail


# ============================================================================
# UTILIDADES DE DIBUJO
# ============================================================================

def _hash2(x: int, y: int) -> float:
    """Ruido determinista barato en [0, 1)."""
    n = (x * 374761393 + y * 668265263) & 0xFFFFFFFF
    n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 65535.0


def _value_noise(x: float, y: float, seed: int = 0) -> float:
    xi, yi = int(math.floor(x)), int(math.floor(y))
    xf, yf = x - xi, y - yi
    u = xf * xf * (3 - 2 * xf)
    v = yf * yf * (3 - 2 * yf)
    a = _hash2(xi + seed, yi - seed)
    b = _hash2(xi + 1 + seed, yi - seed)
    c = _hash2(xi + seed, yi + 1 - seed)
    d = _hash2(xi + 1 + seed, yi + 1 - seed)
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v


def _fbm(x: float, y: float, octaves: int = 4, seed: int = 0) -> float:
    amp = 0.5
    freq = 1.0
    total = 0.0
    norm = 0.0
    for _ in range(octaves):
        total += amp * _value_noise(x * freq, y * freq, seed)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm if norm else 0.0


def _mix(c0: Tuple[int, int, int], c1: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return (
        int(c0[0] + (c1[0] - c0[0]) * t),
        int(c0[1] + (c1[1] - c0[1]) * t),
        int(c0[2] + (c1[2] - c0[2]) * t),
    )


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# ============================================================================
# PINTORES DE TEXTURAS
# ============================================================================

def p_asphalt(x, y, w, h):
    """Asfalto oscuro con linea central amarilla discontinua (carretera)."""
    n = _fbm(x * 0.12, y * 0.12, 4, 11)
    base = _mix((38, 38, 42), (62, 62, 66), n)
    grain = _hash2(x, y)
    base = (base[0] + int(grain * 14) - 7, base[1] + int(grain * 14) - 7, base[2] + int(grain * 14) - 7)
    # Banda central amarilla (discontinua a lo largo de X)
    cy0, cy1 = int(h * 0.42), int(h * 0.58)
    if cy0 <= y < cy1 and (x % 32) < 20:
        edge = 1.0 if (y < cy0 + 2 or y > cy1 - 3) else 0.0
        base = _mix(base, (214, 176, 58), 0.92 - 0.18 * edge)
        base = (base[0] + int(grain * 12) - 6, base[1] + int(grain * 12) - 6, base[2])
    # Lineas de borde blancas
    if y < 3 or y > h - 4:
        if (x % 24) < 14:
            base = _mix(base, (196, 196, 190), 0.7)
    return base


def p_sidewalk(x, y, w, h):
    """Aceras de hormigon con juntas de dilatacion."""
    n = _fbm(x * 0.09, y * 0.09, 3, 23)
    base = _mix((118, 118, 114), (150, 150, 146), n)
    grain = _hash2(x, y)
    base = (base[0] + int(grain * 16) - 8, base[1] + int(grain * 16) - 8, base[2] + int(grain * 16) - 8)
    sx, sy = w // 2, h // 2
    if x % sx < 2 or y % sy < 2:
        base = _mix(base, (84, 84, 82), 0.65)
    # Manchas de humedad
    if _fbm(x * 0.05, y * 0.05, 3, 71) > 0.68:
        base = _mix(base, (92, 92, 88), 0.35)
    return base


def p_road_stripe(x, y, w, h):
    """Asfalto con doble linea continua amarilla central (autopista)."""
    base = p_asphalt(x, y, w, h)
    c = h // 2
    if abs(y - c) <= 1 or abs(y - (c + 5)) <= 1:
        grain = _hash2(x, y)
        return (
            min(255, 224 + int(grain * 24) - 12),
            min(255, 186 + int(grain * 24) - 12),
            62,
        )
    return base


def p_bus_side(x, y, w, h):
    """Lona lateral del autobus de Tranzit: franja crema, verde y ventanillas."""
    grain = _hash2(x, y)
    if y < int(h * 0.16):
        col = (226, 220, 198)          # Techo crema
    elif y < int(h * 0.42):
        # Faja de ventanillas
        in_frame = (x % 34) < 3
        if in_frame:
            col = (48, 60, 66)
        else:
            col = (36, 52, 62)
            if _fbm(x * 0.2, y * 0.5, 2, 5) > 0.6:
                col = (58, 76, 84)
    elif y < int(h * 0.48):
        col = (48, 60, 66)             # Marco inferior de las ventanas
    elif y < int(h * 0.74):
        col = (58, 104, 74)            # Verde clasico del bus de Tranzit
        if (x % 128) < 4:
            col = (226, 220, 198)
    else:
        col = (40, 44, 46)             # Faldon / bajos
        if (y % 8) < 2:
            col = (56, 60, 62)
    d = int(grain * 12) - 6
    return (col[0] + d, col[1] + d, col[2] + d)


def p_glass(x, y, w, h):
    """Cristal de mampara / vitrina: verde azulado con reflejos diagonales."""
    band = ((x + y) % 24)
    if band < 3:
        c = (150, 190, 190)
    elif band < 6:
        c = (86, 124, 128)
    else:
        c = (48, 78, 86)
    n = _fbm(x * 0.09, y * 0.09, 3, 131)
    c = _mix(c, (120, 170, 180), n * 0.35)
    grain = _hash2(x, y)
    d = int(grain * 10) - 5
    return (c[0] + d, c[1] + d, c[2] + d)


def p_bus_headlight(x, y, w, h):
    """Faro delantero: lente blanca muy luminosa con cerco cromado."""
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    d = math.hypot(x - cx, y - cy) / max(1.0, min(cx, cy))
    if d < 0.62:
        c = (255, 252, 232)
    elif d < 0.78:
        c = (222, 214, 176)
    else:
        c = (86, 90, 96)
    grain = _hash2(x, y)
    dd = int(grain * 10) - 5
    return (c[0] + dd, c[1] + dd, c[2] + dd)


def p_bus_tail(x, y, w, h):
    """Piloto trasero rojo del autobus."""
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    d = math.hypot(x - cx, y - cy) / max(1.0, min(cx, cy))
    if d < 0.62:
        c = (255, 72, 52)
    elif d < 0.8:
        c = (196, 46, 32)
    else:
        c = (66, 30, 28)
    grain = _hash2(x, y)
    dd = int(grain * 10) - 5
    return (c[0] + dd, c[1] + dd, c[2] + dd)


def p_bus_metal(x, y, w, h):
    """Chapa metalica ondulada del autobus."""
    rib = math.sin(x * 0.9) * 0.5 + 0.5
    base = _mix((70, 78, 84), (110, 118, 124), rib)
    grain = _hash2(x, y)
    d = int(grain * 18) - 9
    if (y % 8) < 1:
        base = _mix(base, (46, 52, 58), 0.5)
    return (base[0] + d, base[1] + d, base[2] + d)


def p_reactor_blue(x, y, w, h):
    """Panel del reactor con rejilla y nucleo azul incandescente."""
    grain = _hash2(x, y)
    gx = 1 if (x % 8 == 0 or y % 8 == 0) else 0
    glow = _fbm(x * 0.07, y * 0.07, 3, 91)
    base = (26, 34, 46)
    if gx:
        base = (58, 74, 92)
    # Venas de energia
    vein = _fbm(x * 0.16 + 3.0, y * 0.16, 4, 17)
    if vein > 0.62:
        t = (vein - 0.62) / 0.38
        base = _mix(base, (60, 190, 255), t * 0.95)
    if glow > 0.72:
        base = _mix(base, (120, 230, 255), (glow - 0.72) / 0.28 * 0.6)
    d = int(grain * 10) - 5
    return (base[0] + d, base[1] + d, base[2] + d)


def p_diner_tile(x, y, w, h):
    """Baldosas blancas y negras clasica de un Diner americano."""
    tx = (x // 16) & 1
    ty = (y // 16) & 1
    if tx ^ ty:
        col = (226, 226, 224)
    else:
        col = (32, 32, 36)
    grain = _hash2(x, y)
    d = int(grain * 12) - 6
    if x % 16 < 1 or y % 16 < 1:
        col = _mix(col, (96, 96, 96), 0.55)
    return (col[0] + d, col[1] + d, col[2] + d)


def p_hay(x, y, w, h):
    """Paja / heno prensado (pajar del granero)."""
    n = _fbm(x * 0.35, y * 0.05, 3, 33)
    stripe = math.sin(y * 1.5 + _hash2(0, y) * 3.0) * 0.5 + 0.5
    base = _mix((158, 128, 52), (206, 178, 88), 0.55 * n + 0.45 * stripe)
    grain = _hash2(x * 3, y * 7)
    d = int(grain * 26) - 13
    if (y % 6) < 1:
        base = _mix(base, (110, 86, 32), 0.45)
    return (base[0] + d, base[1] + d, base[2] + d)


def p_corn(x, y, w, h):
    """Muro denso de maizal (follaje verde con mazorcas amarillas)."""
    n = _fbm(x * 0.3, y * 0.22, 3, 7)
    base = _mix((26, 46, 20), (74, 104, 40), n)
    stalk = math.sin(x * 1.2 + y * 0.15) * 0.5 + 0.5
    if stalk > 0.82:
        base = _mix(base, (122, 148, 58), 0.6)
    if _hash2(x * 5, y * 5) > 0.965:
        base = (206, 190, 72)          # Mazorca
    grain = _hash2(x, y)
    d = int(grain * 18) - 9
    return (base[0] + d, base[1] + d, base[2] + d)


def p_shingle(x, y, w, h):
    """Tejas de asfalto para los tejados de las estaciones."""
    row = y // 8
    off = (row & 1) * (w // 8)
    col = ((x + off) // 10) & 1
    if col:
        c = (72, 62, 58)
    else:
        c = (54, 46, 44)
    grain = _hash2(x, y)
    d = int(grain * 22) - 11
    if y % 8 == 0:
        c = _mix(c, (28, 24, 24), 0.7)
    return (c[0] + d, c[1] + d, c[2] + d)


def p_metal_grate(x, y, w, h):
    """Rejilla metalica industrial (suelos tecnicos y pasarelas)."""
    bar = 1 if (x % 8) < 4 else 0
    cross = 1 if (y % 8) < 3 else 0
    if bar and cross:
        col = (128, 132, 136)
    elif bar:
        col = (96, 100, 104)
    elif cross:
        col = (78, 82, 86)
    else:
        col = (18, 20, 22)
    grain = _hash2(x, y)
    d = int(grain * 14) - 7
    return (col[0] + d, col[1] + d, col[2] + d)


def p_window_lit(x, y, w, h):
    """Ventana iluminada en calido (para fachadas de Town y la granja)."""
    frame = 3
    if x < frame or x >= w - frame or y < frame or y >= h - frame:
        return (58, 44, 32)
    if abs(x - w // 2) < 2 or abs(y - h // 2) < 2:
        return (58, 44, 32)
    glow = _fbm(x * 0.08, y * 0.08, 3, 55)
    c = _mix((196, 150, 76), (255, 226, 158), glow)
    grain = _hash2(x, y)
    d = int(grain * 12) - 6
    return (c[0] + d, c[1] + d, c[2] + d)


def p_brick_pillar(x, y, w, h):
    """Ladrillo decorativo (pilares del banco y chimeneas)."""
    row = y // 8
    off = (row & 1) * 8
    bx = (x + off) % 16
    by = y % 8
    mortar = bx < 2 or by < 2
    if mortar:
        c = (140, 134, 124)
    else:
        idx = _hash2((x + off) // 16, row)
        c = _mix((128, 62, 48), (170, 92, 68), idx)
    grain = _hash2(x, y)
    d = int(grain * 16) - 8
    return (c[0] + d, c[1] + d, c[2] + d)


def p_garage_door(x, y, w, h):
    """Persiana enrollable metalica del garaje."""
    slat = y % 6
    if slat < 1:
        c = (44, 48, 52)
    elif slat < 4:
        c = (104, 110, 116)
    else:
        c = (74, 80, 86)
    if _fbm(x * 0.15, y * 0.5, 2, 13) > 0.72:
        c = _mix(c, (128, 96, 52), 0.35)   # Oxido
    grain = _hash2(x, y)
    d = int(grain * 14) - 7
    if x < 2 or x >= w - 2:
        c = (58, 62, 66)
    return (c[0] + d, c[1] + d, c[2] + d)


def p_cooling_tower(x, y, w, h):
    """Hormigon nervado de las torres de refrigeracion."""
    rib = math.sin(x * 0.55) * 0.5 + 0.5
    c = _mix((96, 98, 96), (146, 148, 142), rib)
    n = _fbm(x * 0.1, y * 0.1, 3, 41)
    c = _mix(c, (70, 72, 70), n * 0.45)
    if (y % 16) < 2:
        c = _mix(c, (66, 68, 66), 0.6)
    grain = _hash2(x, y)
    d = int(grain * 14) - 7
    return (c[0] + d, c[1] + d, c[2] + d)


def p_silo(x, y, w, h):
    """Chapa ondulada galvanizada del silo de la granja."""
    rib = math.sin(x * 0.7) * 0.5 + 0.5
    c = _mix((122, 126, 130), (182, 186, 190), rib)
    band = 1 if (y % 16) < 2 else 0
    if band:
        c = _mix(c, (86, 90, 94), 0.6)
    if _fbm(x * 0.12, y * 0.12, 3, 61) > 0.74:
        c = _mix(c, (140, 104, 60), 0.3)
    grain = _hash2(x, y)
    d = int(grain * 12) - 6
    return (c[0] + d, c[1] + d, c[2] + d)


def p_fence_wood(x, y, w, h):
    """Valla de madera de la granja (tablas verticales + travesanos)."""
    plank = (x // 10) & 1
    c = (128, 94, 56) if plank else (108, 78, 46)
    grain = _hash2(x * 3, y)
    c = _mix(c, (150, 112, 68), grain * 0.5)
    if x % 10 < 1:
        c = (58, 42, 26)
    if y < int(h * 0.18) or y > int(h * 0.82):
        c = _mix(c, (92, 66, 40), 0.55)   # Travesanos horizontales
    return c


def p_danger(x, y, w, h):
    """Franjas diagonales amarillas y negras de peligro."""
    d = (x + y) % 16
    if d < 8:
        c = (216, 176, 40)
    else:
        c = (36, 36, 34)
    n = _fbm(x * 0.2, y * 0.2, 2, 3)
    c = _mix(c, (150, 122, 30) if d < 8 else (24, 24, 22), n * 0.3)
    grain = _hash2(x, y)
    dd = int(grain * 12) - 6
    return (c[0] + dd, c[1] + dd, c[2] + dd)


def p_lava(x, y, w, h):
    """Roca volcanica agrietada con magma incandescente."""
    n = _fbm(x * 0.09, y * 0.09, 4, 101)
    crack = abs(_fbm(x * 0.05, y * 0.05, 3, 202) - 0.5)
    if crack < 0.045:
        t = 1.0 - crack / 0.045
        c = _mix((120, 34, 8), (255, 226, 130), t ** 1.6)
    else:
        c = _mix((28, 26, 26), (62, 56, 54), n)
        if crack < 0.075:
            c = _mix(c, (150, 62, 16), (0.075 - crack) / 0.03 * 0.8)
    grain = _hash2(x, y)
    d = int(grain * 16) - 8
    return (c[0] + d, c[1] + d, c[2] + d)


def p_tunnel_tile(x, y, w, h):
    """Azulejo blanco de tunel (Highway Tunnel) con juntas de suciedad."""
    tx, ty = (x // 12), (y // 12)
    jx, jy = x % 12, y % 12
    if jx < 1 or jy < 1:
        c = (62, 62, 60)
    else:
        idx = _hash2(tx, ty)
        c = _mix((196, 196, 188), (232, 230, 220), idx)
        grime = _fbm(x * 0.06, y * 0.06, 3, 77)
        if grime > 0.6:
            c = _mix(c, (104, 96, 84), (grime - 0.6) / 0.4 * 0.55)
    grain = _hash2(x, y)
    d = int(grain * 10) - 5
    return (c[0] + d, c[1] + d, c[2] + d)


def make_sign_painter(text: str, bg=(24, 26, 30), fg=(238, 232, 210), accent=(198, 46, 40), border=True):
    """Crea un pintor de letrero rotulado con la tipografia 5x7."""
    tw, th, pixels = text_mask(text)

    def painter(x, y, w, h):
        # Marco exterior
        if border and (x < 3 or x >= w - 3 or y < 3 or y >= h - 3):
            return accent
        # Banda superior de acento
        if border and (5 <= y < 8):
            return accent
        # Fondo con ligero degradado y suciedad
        t = y / max(1, h - 1)
        c = _mix(bg, (bg[0] + 16, bg[1] + 16, bg[2] + 18), t)
        n = _fbm(x * 0.15, y * 0.15, 3, 19)
        c = _mix(c, (bg[0] + 28, bg[1] + 28, bg[2] + 26), n * 0.35)

        # Rotulacion centrada con escala entera que quepa
        scale = 1
        while scale < 4 and (tw + 1) * scale <= w - 12 and (th + 1) * scale <= h - 12:
            scale += 1
        gw = tw * scale
        gh = th * scale
        ox = (w - gw) // 2
        oy = (h - gh) // 2 + 2
        rx = x - ox
        ry = y - oy
        if 0 <= rx < gw and 0 <= ry < gh:
            gx = rx // scale
            gy = ry // scale
            if (gx, gy) in pixels:
                # Ligero realce superior para dar volumen
                if (gx, gy - 1) not in pixels:
                    return (min(255, fg[0] + 22), min(255, fg[1] + 22), min(255, fg[2] + 18))
                return fg
        return c

    return painter


# ============================================================================
# CATALOGO DE TEXTURAS CUSTOM DE TRANZIT
# ============================================================================

def build_custom_textures() -> Dict[str, bytes]:
    """Devuelve un dict {nombre: bloque miptex WAD3} con todas las custom textures."""
    tex: Dict[str, bytes] = {}

    def reg(name, w, h, painter):
        tex[name] = make_wad3_miptex(name, w, h, painter)

    # Superficies de suelo y carretera
    reg("asphalt_line", 64, 64, p_asphalt)
    reg("road_stripe", 64, 64, p_road_stripe)
    reg("sidewalk", 64, 64, p_sidewalk)
    reg("metal_grate", 64, 64, p_metal_grate)
    reg("diner_tile", 64, 64, p_diner_tile)

    # Vegetacion y entorno
    reg("corn_wall", 64, 64, p_corn)
    reg("hay_wall", 64, 64, p_hay)
    reg("lava_cracks", 64, 64, p_lava)

    # Autobus y vehiculos
    reg("bus_side", 128, 64, p_bus_side)
    reg("bus_metal", 64, 64, p_bus_metal)
    reg("bus_headlight", 32, 32, p_bus_headlight)
    reg("bus_tail", 32, 32, p_bus_tail)

    # Infraestructuras
    reg("reactor_blue", 64, 64, p_reactor_blue)
    reg("cooling_tower", 64, 64, p_cooling_tower)
    reg("silo_wall", 64, 64, p_silo)
    reg("tunnel_tile", 64, 64, p_tunnel_tile)
    reg("shingle_roof", 64, 64, p_shingle)
    reg("garage_door", 64, 128, p_garage_door)
    reg("danger_stripe", 64, 64, p_danger)

    # Edificacion
    reg("brick_pillar", 64, 64, p_brick_pillar)
    reg("window_lit", 64, 64, p_window_lit)
    reg("fence_wood", 64, 64, p_fence_wood)
    reg("g_glass_", 64, 64, p_glass)

    # Rotulacion de las estaciones
    reg("sign_depot", 128, 32, make_sign_painter("BUS DEPOT", bg=(26, 30, 38), fg=(240, 236, 220), accent=(210, 158, 40)))
    reg("sign_diner", 128, 32, make_sign_painter("DINER", bg=(30, 22, 20), fg=(248, 240, 214), accent=(206, 52, 44)))
    reg("sign_farm", 128, 32, make_sign_painter("FARM", bg=(22, 30, 22), fg=(240, 238, 208), accent=(150, 92, 40)))
    reg("sign_power", 128, 32, make_sign_painter("POWER", bg=(18, 24, 34), fg=(226, 240, 255), accent=(58, 150, 220)))
    reg("sign_bank", 128, 32, make_sign_painter("BANK", bg=(28, 28, 32), fg=(238, 236, 228), accent=(176, 148, 60)))
    reg("sign_bar", 128, 32, make_sign_painter("BAR", bg=(32, 24, 18), fg=(246, 226, 190), accent=(178, 116, 44)))
    reg("sign_tunnel", 128, 32, make_sign_painter("TUNNEL", bg=(26, 26, 28), fg=(232, 232, 226), accent=(196, 176, 52)))
    reg("sign_town", 128, 32, make_sign_painter("TOWN", bg=(28, 26, 30), fg=(240, 234, 220), accent=(150, 90, 150)))
    reg("sign_garage", 128, 32, make_sign_painter("GARAGE", bg=(24, 26, 30), fg=(232, 234, 236), accent=(70, 130, 190)))
    reg("sign_nacht", 128, 32, make_sign_painter("NACHT", bg=(26, 26, 26), fg=(226, 226, 220), accent=(120, 120, 120)))

    return tex


# Texturas que emiten luz propia (color RGB del lightmap) en el motor NZ:P
FULLBRIGHT_TEXTURES: Dict[str, Tuple[int, int, int]] = {
    "lava_cracks": (255, 150, 52),
    "br_lightGL": (255, 240, 200),
    "reactor_blue": (70, 180, 255),
    "window_lit": (255, 206, 128),
    "bus_headlight": (255, 250, 226),
    "bus_tail": (255, 70, 50),
}

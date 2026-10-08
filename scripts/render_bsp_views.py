#!/usr/bin/env python3
"""
render_bsp_views.py
===================
Renderizador 3D por software (Z-buffer + texturas WAD3 + lightmaps RGB + modelos .MDL
+ niebla exponencial de Tranzit) y visor cenital para inspeccionar visualmente
android-app/app/src/main/assets/base/nzp/maps/tranzit.bsp y generar capturas PNG.
"""

import math
import os
import re
import struct
import zlib
from typing import Dict, List, Optional, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ASSET_NZP_DIR = os.path.join(ROOT_DIR, "android-app", "app", "src", "main", "assets", "base", "nzp")
BSP_PATH = os.path.join(ASSET_NZP_DIR, "maps", "tranzit.bsp")
OUT_DIR = os.path.join(ROOT_DIR, "maps_src", "screenshots")

WEAPON_ID_TO_MDL = {
    1: "models/weapons/m1911/g_colt.mdl",
    2: "models/weapons/kar/g_kar.mdl",
    3: "models/weapons/thomp/g_thomp.mdl",
    4: "models/weapons/357/g_357.mdl",
    5: "models/weapons/bar/g_bar.mdl",
    6: "models/weapons/stg/g_stg.mdl",
    8: "models/weapons/db/g_db.mdl",
    10: "models/weapons/gewehr/g_gewehr.mdl",
    11: "models/weapons/kar/g_kars.mdl",
    13: "models/weapons/m1carbine/g_m1a1.mdl",
    17: "models/weapons/fg42/g_fg.mdl",
    21: "models/weapons/sawnoff/g_sawnoff.mdl",
    23: "models/weapons/trench/g_trench.mdl",
    29: "models/weapons/mp5k/g_mp5k.mdl",
}


def write_png(path: str, w: int, h: int, rgb: bytes):
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw.extend(rgb[y * stride : (y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)


class BSPScene:
    def __init__(self, bsp_path: str):
        with open(bsp_path, "rb") as f:
            self.data = f.read()

        lumps = [struct.unpack_from("<ii", self.data, 4 + 8 * i) for i in range(15)]
        self.planes = [
            struct.unpack_from("<ffffi", self.data, lumps[1][0] + 20 * i)
            for i in range(lumps[1][1] // 20)
        ]
        self.verts = [
            struct.unpack_from("<fff", self.data, lumps[3][0] + 12 * i)
            for i in range(lumps[3][1] // 12)
        ]
        self.edges = [
            struct.unpack_from("<HH", self.data, lumps[12][0] + 4 * i)
            for i in range(lumps[12][1] // 4)
        ]
        self.surfedges = [
            struct.unpack_from("<i", self.data, lumps[13][0] + 4 * i)[0]
            for i in range(lumps[13][1] // 4)
        ]
        self.texinfos = [
            struct.unpack_from("<8fii", self.data, lumps[6][0] + 40 * i)
            for i in range(lumps[6][1] // 40)
        ]
        self.faces = [
            struct.unpack_from("<hhihh4si", self.data, lumps[7][0] + 20 * i)
            for i in range(lumps[7][1] // 20)
        ]
        self.lighting = self.data[lumps[8][0] : lumps[8][0] + lumps[8][1]]
        self.models = [
            struct.unpack_from("<9f7i", self.data, lumps[14][0] + 64 * i)
            for i in range(lumps[14][1] // 64)
        ]

        # Parse textures (WAD3)
        self.textures: List[Tuple[str, int, int, bytes, bytes]] = []
        tofs, _ = lumps[2]
        numtex = struct.unpack_from("<i", self.data, tofs)[0]
        offs = [struct.unpack_from("<i", self.data, tofs + 4 + 4 * i)[0] for i in range(numtex)]
        for o in offs:
            abs_p = tofs + o
            name = self.data[abs_p : abs_p + 16].split(b"\x00", 1)[0].decode("latin1", errors="ignore")
            tw, th = struct.unpack_from("<II", self.data, abs_p + 16)
            m0, m1, m2, m3 = struct.unpack_from("<IIII", self.data, abs_p + 24)
            pix0 = self.data[abs_p + m0 : abs_p + m0 + tw * th]
            pal_pos = abs_p + m3 + (tw // 8) * (th // 8)
            c_used = struct.unpack_from("<H", self.data, pal_pos)[0]
            pal = self.data[pal_pos + 2 : pal_pos + 2 + c_used * 3]
            self.textures.append((name, tw, th, pix0, pal))

        # Parse entities
        ent_str = self.data[lumps[0][0] : lumps[0][0] + lumps[0][1]].decode("latin1", errors="ignore")
        self.entities = [
            dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', b))
            for b in re.findall(r"\{([^}]*)\}", ent_str)
        ]

        # Map submodels (*1, *2, ...) to their entity offsets
        self.submodel_offsets: Dict[int, Tuple[float, float, float]] = {}
        self.submodel_skip: set = set()
        targetnames = {
            e["targetname"]: tuple(float(v) for v in e["origin"].split())
            for e in self.entities
            if "targetname" in e and "origin" in e
        }
        for e in self.entities:
            m_str = e.get("model", "")
            if m_str.startswith("*"):
                midx = int(m_str[1:])
                cn = e.get("classname", "")
                if cn in ("buy_weapon", "trigger_hurt"):
                    self.submodel_skip.add(midx)
                elif cn == "func_tranzit_bus":
                    t_org = targetnames.get(e.get("target", ""), (0.0, 0.0, 0.0))
                    m_mins = self.models[midx][0:3]
                    self.submodel_offsets[midx] = (
                        t_org[0] - m_mins[0],
                        t_org[1] - m_mins[1],
                        t_org[2] - m_mins[2],
                    )
                else:
                    self.submodel_offsets[midx] = (0.0, 0.0, 0.0)

        self.mdl_cache: Dict[str, Optional[Tuple[List[Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]], Tuple[int, int, int]]]] = {}

    def load_mdl_mesh(self, rel_path: str):
        if rel_path in self.mdl_cache:
            return self.mdl_cache[rel_path]
        full_path = os.path.join(ASSET_NZP_DIR, rel_path)
        if not os.path.exists(full_path):
            self.mdl_cache[rel_path] = None
            return None
        with open(full_path, "rb") as f:
            mdata = f.read()
        ident = mdata[:4]
        if ident != b"IDPO":
            self.mdl_cache[rel_path] = None
            return None
        # Quake 1 MDL (IDPO v6) header
        sx, sy, sz, tx, ty, tz = struct.unpack_from("<6f", mdata, 8)
        numskins, skinw, skinh, numverts, numtris, numframes = struct.unpack_from("<6i", mdata, 48)
        pos = 84
        # Skip skins and grab average color
        r_avg, g_avg, b_avg = 155, 145, 130
        for _ in range(numskins):
            group = struct.unpack_from("<i", mdata, pos)[0]
            pos += 4
            if group == 0:
                pos += skinw * skinh
            else:
                nb = struct.unpack_from("<i", mdata, pos)[0]
                pos += 4 + 4 * nb + nb * skinw * skinh
        # Skip stverts (numverts * 12)
        pos += numverts * 12
        tris = []
        for _ in range(numtris):
            _, i0, i1, i2 = struct.unpack_from("<4i", mdata, pos)
            tris.append((i0, i1, i2))
            pos += 16
        # Read first frame vertices
        ftype = struct.unpack_from("<i", mdata, pos)[0]
        pos += 4
        if ftype != 0:
            pos += 12  # group header
        pos += 24  # bboxmin(4) + bboxmax(4) + name(16)
        vlist = []
        for _ in range(numverts):
            vx, vy, vz, _ = struct.unpack_from("<4B", mdata, pos)
            pos += 4
            vlist.append((vx * sx + tx, vy * sy + ty, vz * sz + tz))
        tri_pts = [(vlist[i0], vlist[i1], vlist[i2]) for (i0, i1, i2) in tris if i0 < len(vlist) and i1 < len(vlist) and i2 < len(vlist)]
        if "quick_revive" in rel_path:
            r_avg, g_avg, b_avg = 75, 185, 235
        elif "juggernog" in rel_path:
            r_avg, g_avg, b_avg = 220, 65, 65
        elif "speed_cola" in rel_path:
            r_avg, g_avg, b_avg = 70, 210, 95
        elif "double_tap" in rel_path:
            r_avg, g_avg, b_avg = 230, 165, 55
        elif "staminup" in rel_path:
            r_avg, g_avg, b_avg = 235, 195, 60
        elif "mulekick" in rel_path:
            r_avg, g_avg, b_avg = 85, 175, 85
        elif "flopper" in rel_path:
            r_avg, g_avg, b_avg = 175, 95, 220
        elif "deadshot" in rel_path:
            r_avg, g_avg, b_avg = 120, 130, 140
        elif "lamp" in rel_path:
            r_avg, g_avg, b_avg = 80, 245, 120
        elif "tractor" in rel_path:
            r_avg, g_avg, b_avg = 195, 70, 55
        elif "jeep" in rel_path:
            r_avg, g_avg, b_avg = 95, 115, 85
        elif "tree" in rel_path:
            r_avg, g_avg, b_avg = 85, 72, 56
        elif "flame" in rel_path:
            r_avg, g_avg, b_avg = 255, 150, 40

        self.mdl_cache[rel_path] = (tri_pts, (r_avg, g_avg, b_avg))
        return self.mdl_cache[rel_path]

    def render_camera(
        self,
        cam_pos: Tuple[float, float, float],
        yaw_deg: float,
        pitch_deg: float = 0.0,
        w: int = 480,
        h: int = 272,
        fov_deg: float = 82.0,
        stats: Optional[dict] = None,
    ) -> bytes:
        cx, cy, cz = cam_pos
        yaw = math.radians(yaw_deg)
        pitch = math.radians(pitch_deg)
        # Forward, Right, Up vectors in Quake coordinates (X forward at yaw=0, Y left, Z up)
        fx = math.cos(yaw) * math.cos(pitch)
        fy = math.sin(yaw) * math.cos(pitch)
        fz = math.sin(pitch)
        rx = math.sin(yaw)
        ry = -math.cos(yaw)
        rz = 0.0
        ux = -math.cos(yaw) * math.sin(pitch)
        uy = -math.sin(yaw) * math.sin(pitch)
        uz = math.cos(pitch)

        focal = (w * 0.5) / math.tan(math.radians(fov_deg * 0.5))
        fb = bytearray(w * h * 3)
        zbuf = [1e18] * (w * h)
        # Buffers para el analisis cuantitativo de la escena
        texid_buf = [0] * (w * h) if stats is not None else None

        # Fill sky background gradient (CloudyNightSky + fog horizon)
        for py in range(h):
            t = min(1.0, py / (h * 0.6))
            sr = int(20 + 22 * t)
            sg = int(24 + 22 * t)
            sb = int(28 + 20 * t)
            row_ofs = py * w * 3
            for px in range(w):
                fb[row_ofs + px * 3] = sr
                fb[row_ofs + px * 3 + 1] = sg
                fb[row_ofs + px * 3 + 2] = sb

        def world_to_cam(pt: Tuple[float, float, float]) -> Tuple[float, float, float]:
            dx = pt[0] - cx
            dy = pt[1] - cy
            dz = pt[2] - cz
            # x_cam = right, y_cam = up, z_cam = forward depth
            return (
                dx * rx + dy * ry + dz * rz,
                dx * ux + dy * uy + dz * uz,
                dx * fx + dy * fy + dz * fz,
            )

        def clip_near(poly: List[Tuple[float, float, float, float, float]], znear: float = 8.0):
            out = []
            n = len(poly)
            for i in range(n):
                a = poly[i]
                b = poly[(i + 1) % n]
                ina = a[2] >= znear
                inb = b[2] >= znear
                if ina and inb:
                    out.append(b)
                elif ina and not inb:
                    t = (znear - a[2]) / (b[2] - a[2])
                    out.append(tuple(a[k] + t * (b[k] - a[k]) for k in range(5)))
                elif not ina and inb:
                    t = (znear - a[2]) / (b[2] - a[2])
                    out.append(tuple(a[k] + t * (b[k] - a[k]) for k in range(5)))
                    out.append(b)
            return out

        # Render all BSP models (world + visible submodels)
        for midx, mdl in enumerate(self.models):
            if midx in self.submodel_skip:
                continue
            ox, oy, oz = self.submodel_offsets.get(midx, (0.0, 0.0, 0.0))
            ff, nf = mdl[14], mdl[15]
            for f_i in range(ff, ff + nf):
                pnum, side, fedge, nedges, ti_idx, styles, lightofs = self.faces[f_i]
                ti = self.texinfos[ti_idx]
                tex_name, tw, th, pix0, pal = self.textures[ti[8]]
                if tex_name.startswith("sky"):
                    continue

                # Check backface
                nx, ny, nz, pdist, _ = self.planes[pnum]
                if side != 0:
                    nx, ny, nz = -nx, -ny, -nz
                v0_idx = self.edges[self.surfedges[fedge]][0] if self.surfedges[fedge] >= 0 else self.edges[abs(self.surfedges[fedge])][1]
                v0w = (self.verts[v0_idx][0] + ox, self.verts[v0_idx][1] + oy, self.verts[v0_idx][2] + oz)
                if (cx - v0w[0]) * nx + (cy - v0w[1]) * ny + (cz - v0w[2]) * nz <= 0:
                    continue

                # Gather vertices + (s, t) coordinates
                cpoly = []
                s_list, t_list = [], []
                for k in range(nedges):
                    se = self.surfedges[fedge + k]
                    v_i = self.edges[se][0] if se >= 0 else self.edges[abs(se)][1]
                    vw = (self.verts[v_i][0] + ox, self.verts[v_i][1] + oy, self.verts[v_i][2] + oz)
                    s = vw[0] * ti[0] + vw[1] * ti[1] + vw[2] * ti[2] + ti[3]
                    t = vw[0] * ti[4] + vw[1] * ti[5] + vw[2] * ti[6] + ti[7]
                    s_list.append(s)
                    t_list.append(t)
                    xc, yc, zc = world_to_cam(vw)
                    cpoly.append((xc, yc, zc, s, t))

                clipped = clip_near(cpoly)
                if len(clipped) < 3:
                    continue

                # Compute average face lightmap color
                lm_r, lm_g, lm_b = 95, 98, 102
                if lightofs >= 0 and lightofs + 3 <= len(self.lighting):
                    bmin_s = int(math.floor(min(s_list) / 16.0))
                    bmax_s = int(math.ceil(max(s_list) / 16.0))
                    bmin_t = int(math.floor(min(t_list) / 16.0))
                    bmax_t = int(math.ceil(max(t_list) / 16.0))
                    ns = max(1, (bmax_s - bmin_s) + 1)
                    nt = max(1, (bmax_t - bmin_t) + 1)
                    mid_sample = (nt // 2) * ns + (ns // 2)
                    p_lm = lightofs + mid_sample * 3
                    if p_lm + 3 <= len(self.lighting):
                        lm_r = min(255, (self.lighting[p_lm] * 264) >> 7)
                        lm_g = min(255, (self.lighting[p_lm + 1] * 264) >> 7)
                        lm_b = min(255, (self.lighting[p_lm + 2] * 264) >> 7)

                # Project and rasterize fan triangles
                proj = []
                for xc, yc, zc, s, t in clipped:
                    inv_z = 1.0 / zc
                    sx_p = (w * 0.5) + (xc * focal) * inv_z
                    sy_p = (h * 0.5) - (yc * focal) * inv_z
                    proj.append((sx_p, sy_p, zc, inv_z, s * inv_z, t * inv_z))

                p0 = proj[0]
                for ti_k in range(1, len(proj) - 1):
                    p1 = proj[ti_k]
                    p2 = proj[ti_k + 1]
                    min_x = max(0, int(math.floor(min(p0[0], p1[0], p2[0]))))
                    max_x = min(w - 1, int(math.ceil(max(p0[0], p1[0], p2[0]))))
                    min_y = max(0, int(math.floor(min(p0[1], p1[1], p2[1]))))
                    max_y = min(h - 1, int(math.ceil(max(p0[1], p1[1], p2[1]))))
                    if min_x > max_x or min_y > max_y:
                        continue
                    denom = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
                    if abs(denom) < 1e-5:
                        continue
                    inv_den = 1.0 / denom
                    for py in range(min_y, max_y + 1):
                        vy = py + 0.5
                        for px in range(min_x, max_x + 1):
                            vx = px + 0.5
                            w0 = ((p1[1] - p2[1]) * (vx - p2[0]) + (p2[0] - p1[0]) * (vy - p2[1])) * inv_den
                            if w0 < 0:
                                continue
                            w1 = ((p2[1] - p0[1]) * (vx - p2[0]) + (p0[0] - p2[0]) * (vy - p2[1])) * inv_den
                            if w1 < 0:
                                continue
                            w2 = 1.0 - w0 - w1
                            if w2 < 0:
                                continue
                            inv_z = w0 * p0[3] + w1 * p1[3] + w2 * p2[3]
                            z_depth = 1.0 / inv_z
                            pidx = py * w + px
                            if z_depth >= zbuf[pidx]:
                                continue
                            zbuf[pidx] = z_depth
                            u_tex = int((w0 * p0[4] + w1 * p1[4] + w2 * p2[4]) * z_depth) % tw
                            v_tex = int((w0 * p0[5] + w1 * p1[5] + w2 * p2[5]) * z_depth) % th
                            c_idx = pix0[v_tex * tw + u_tex] * 3
                            tr = (pal[c_idx] * lm_r) >> 8
                            tg = (pal[c_idx + 1] * lm_g) >> 8
                            tb = (pal[c_idx + 2] * lm_b) >> 8

                            # Apply Green Run distance fog ("fog" "220 1450 42 46 48")
                            if z_depth > 220.0:
                                fog_f = max(0.0, min(0.95, (z_depth - 700.0) / 3200.0))
                                tr = int(tr * (1.0 - fog_f) + 42 * fog_f)
                                tg = int(tg * (1.0 - fog_f) + 46 * fog_f)
                                tb = int(tb * (1.0 - fog_f) + 48 * fog_f)

                            fb[pidx * 3] = max(0, min(255, tr))
                            fb[pidx * 3 + 1] = max(0, min(255, tg))
                            fb[pidx * 3 + 2] = max(0, min(255, tb))
                            if texid_buf is not None:
                                texid_buf[pidx] = ti[8] + 1

        # Render 3D .MDL entities (Perks, Mystery Box, Power Switch, Lamps, Props, Wall Weapons)
        for e in self.entities:
            if "origin" not in e:
                continue
            cn = e.get("classname", "")
            mdl_path = e.get("model", "") or e.get("mdl", "")
            if cn == "mystery_box":
                mdl_path = "models/machines/mystery.mdl"
            elif cn == "perk_pap":
                mdl_path = "models/machines/quake_scale/pap.mdl"
            elif cn == "power_switch":
                mdl_path = "models/machines/quake_scale/power_switch.mdl"
            elif cn == "tranzit_lamp_tp":
                mdl_path = "models/props/lamp_kino.mdl"
            elif cn == "weapon_wall":
                wid = int(e.get("frame", "1"))
                mdl_path = WEAPON_ID_TO_MDL.get(wid, "")

            if not mdl_path or mdl_path.startswith("*"):
                continue
            loaded = self.load_mdl_mesh(mdl_path)
            if not loaded:
                continue
            tri_pts, base_rgb = loaded
            ex, ey, ez = [float(v) for v in e["origin"].split()]
            ang_parts = [float(v) for v in e.get("angles", "0 0 0").split()]
            eyaw = math.radians(ang_parts[1] if len(ang_parts) > 1 else 0.0)
            ca, sa = math.cos(eyaw), math.sin(eyaw)

            # Quick distance cull
            dist_c = math.sqrt((ex - cx) ** 2 + (ey - cy) ** 2 + (ez - cz) ** 2)
            if dist_c > 1600.0:
                continue

            for v0m, v1m, v2m in tri_pts:
                pts_cam = []
                for vm in (v0m, v1m, v2m):
                    wx = ex + vm[0] * ca - vm[1] * sa
                    wy = ey + vm[0] * sa + vm[1] * ca
                    wz = ez + vm[2]
                    pts_cam.append(world_to_cam((wx, wy, wz)))
                if pts_cam[0][2] < 8.0 or pts_cam[1][2] < 8.0 or pts_cam[2][2] < 8.0:
                    continue
                # Simple directional shading on triangle normal
                ux_t = pts_cam[1][0] - pts_cam[0][0]
                uy_t = pts_cam[1][1] - pts_cam[0][1]
                uz_t = pts_cam[1][2] - pts_cam[0][2]
                vx_t = pts_cam[2][0] - pts_cam[0][0]
                vy_t = pts_cam[2][1] - pts_cam[0][1]
                vz_t = pts_cam[2][2] - pts_cam[0][2]
                ny_n = uz_t * vx_t - ux_t * vz_t
                n_len = math.sqrt(
                    (uy_t * vz_t - uz_t * vy_t) ** 2
                    + ny_n ** 2
                    + (ux_t * vy_t - uy_t * vx_t) ** 2
                ) + 1e-6
                shade = 0.65 + 0.35 * abs(ny_n / n_len)

                proj_m = []
                for xc, yc, zc in pts_cam:
                    inv_z = 1.0 / zc
                    proj_m.append(((w * 0.5) + (xc * focal) * inv_z, (h * 0.5) - (yc * focal) * inv_z, zc))
                p0, p1, p2 = proj_m
                min_x = max(0, int(math.floor(min(p0[0], p1[0], p2[0]))))
                max_x = min(w - 1, int(math.ceil(max(p0[0], p1[0], p2[0]))))
                min_y = max(0, int(math.floor(min(p0[1], p1[1], p2[1]))))
                max_y = min(h - 1, int(math.ceil(max(p0[1], p1[1], p2[1]))))
                denom = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
                if abs(denom) < 1e-5:
                    continue
                inv_den = 1.0 / denom
                for py in range(min_y, max_y + 1):
                    vy = py + 0.5
                    for px in range(min_x, max_x + 1):
                        vx = px + 0.5
                        w0 = ((p1[1] - p2[1]) * (vx - p2[0]) + (p2[0] - p1[0]) * (vy - p2[1])) * inv_den
                        w1 = ((p2[1] - p0[1]) * (vx - p2[0]) + (p0[0] - p2[0]) * (vy - p2[1])) * inv_den
                        w2 = 1.0 - w0 - w1
                        if w0 < 0 or w1 < 0 or w2 < 0:
                            continue
                        z_depth = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
                        pidx = py * w + px
                        if z_depth < zbuf[pidx]:
                            zbuf[pidx] = z_depth
                            fb[pidx * 3] = min(255, int(base_rgb[0] * shade))
                            fb[pidx * 3 + 1] = min(255, int(base_rgb[1] * shade))
                            fb[pidx * 3 + 2] = min(255, int(base_rgb[2] * shade))
                            if texid_buf is not None:
                                texid_buf[pidx] = -1

        if stats is not None and texid_buf is not None:
            counts: Dict[str, int] = {}
            for v in texid_buf:
                if v == 0:
                    name = "<sky/fondo>"
                elif v < 0:
                    name = "<mdl 3D>"
                else:
                    name = self.textures[v - 1][0]
                counts[name] = counts.get(name, 0) + 1
            total = w * h
            stats["histogram"] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
            stats["sky_pct"] = 100.0 * counts.get("<sky/fondo>", 0) / total
            stats["mdl_pct"] = 100.0 * counts.get("<mdl 3D>", 0) / total
            stats["distinct_textures"] = len([k for k in counts if not k.startswith("<")])
            # Profundidad media y distribucion
            finite_z = [z for z in zbuf if z < 1e17]
            if finite_z:
                finite_z.sort()
                stats["depth_mean"] = sum(finite_z) / len(finite_z)
                stats["depth_p50"] = finite_z[len(finite_z) // 2]
                stats["depth_p95"] = finite_z[min(len(finite_z) - 1, int(len(finite_z) * 0.95))]
                stats["coverage_pct"] = 100.0 * len(finite_z) / total
            # Luminancia
            lum = [
                (fb[i * 3] * 299 + fb[i * 3 + 1] * 587 + fb[i * 3 + 2] * 114) // 1000
                for i in range(total)
            ]
            stats["lum_mean"] = sum(lum) / total
            stats["lum_min"] = min(lum)
            stats["lum_max"] = max(lum)
            stats["lum_std"] = (sum((v - stats["lum_mean"]) ** 2 for v in lum) / total) ** 0.5
            stats["dark_pct"] = 100.0 * sum(1 for v in lum if v < 24) / total
            stats["blown_pct"] = 100.0 * sum(1 for v in lum if v > 235) / total

        return bytes(fb)


# El mapa se genera con WORLD_SCALE_XY sobre las coordenadas de diseno:
# las camaras estan expresadas en ese mismo espacio de diseno.
WORLD_SCALE_XY = 2.35


# ---------------------------------------------------------------------------
# CAMARAS DE INSPECCION (primera persona, altura de ojos ~56u sobre el suelo)
# ---------------------------------------------------------------------------
CAMERAS = [
    ("01_bus_depot_interior",   (-2050.0, -1950.0, 56.0),  15.0, -4.0),
    ("02_bus_depot_ext_bus",    (-1300.0, -1900.0, 62.0), 190.0, -4.0),
    ("03_depot_platform",       (-1420.0, -1860.0, 58.0), 180.0, -3.0),
    ("04_highway_tunnel",       (-1880.0, -480.0, 58.0),   85.0, -3.0),
    ("05_tunnel_portal_south",  (-1760.0, -900.0, 58.0),   10.0, -3.0),
    ("06_diner_exterior_canopy", (-1520.0, 1320.0, 62.0),  42.0, -3.0),
    ("07_diner_interior",       (-1380.0, 1820.0, 58.0),   35.0, -4.0),
    ("08_garage_interior",      (-1900.0, 1900.0, 60.0),   20.0, -4.0),
    ("09_cornfield_nacht",      (0.0, 700.0, 58.0),        90.0, -3.0),
    ("10_nacht_bunker",         (0.0, -250.0, 58.0),      180.0, -4.0),
    ("11_farm_yard",            (1560.0, 1460.0, 62.0),   -18.0, -4.0),
    ("12_farm_barn",            (1800.0, 1160.0, 60.0),    30.0, -4.0),
    ("13_farmhouse_porch",      (1600.0, 1900.0, 58.0),     0.0, -3.0),
    ("14_power_station",        (1660.0, -240.0, 62.0),   -48.0, -5.0),
    ("15_power_reactor",        (1780.0, -400.0, 60.0),     0.0, -3.0),
    ("16_town_street",          (-380.0, -1640.0, 62.0),    8.0, -4.0),
    ("17_town_bar",             (-160.0, -1320.0, 58.0),  180.0, -4.0),
    ("18_town_bank_vault",      (430.0, -2080.0, 58.0),   180.0, -4.0),
    ("19_bus_stop_depot",       (-1420.0, -1300.0, 62.0),  95.0, -6.0),
    ("20_bus_interior",         (-1470.0, -1660.0, 34.0),   5.0, -2.0),
    ("21_cooling_towers",       (1780.0, 800.0, 62.0),    -90.0, -4.0),
    ("22_town_bank_lobby",      (-100.0, -1950.0, 58.0),    0.0, -4.0),
    ("23_east_road_power",      (900.0, 60.0, 62.0),        0.0, -3.0),
    ("24_highway_west",         (-1230.0, -600.0, 62.0),   70.0, -3.0),
    ("25_cornfield_fence",      (-520.0, -250.0, 62.0),    15.0, -3.0),
    ("26_north_wreck_yard",     (250.0, 1720.0, 62.0),   180.0, -4.0),
    ("27_highway_north",        (-1000.0, 1220.0, 62.0),    0.0, -3.0),
    ("28_barn_hay_loft",        (2157.0, 1265.0, 176.0),  200.0, -12.0),
    ("29_diner_rooftop",        (-850.0, 2000.0, 268.0),  190.0, -8.0),
    ("30_diner_fire_escape",    (-660.0, 1900.0, 150.0),  330.0, -14.0),
]


def main():
    scene = BSPScene(BSP_PATH)
    report_lines: List[str] = []
    for name, pos, yaw, pitch in CAMERAS:
        pos = (pos[0] * WORLD_SCALE_XY, pos[1] * WORLD_SCALE_XY, pos[2])
        out_p = os.path.join(OUT_DIR, f"{name}.png")
        st: dict = {}
        rgb = scene.render_camera(pos, yaw, pitch, 480, 272, stats=st)
        write_png(out_p, 480, 272, rgb)

        report_lines.append(f"### {name}  cam=({pos[0]:.0f},{pos[1]:.0f},{pos[2]:.0f}) yaw={yaw}")
        report_lines.append(
            f"    cobertura={st['coverage_pct']:.1f}%  cielo={st['sky_pct']:.1f}%  mdl3D={st['mdl_pct']:.2f}%"
        )
        report_lines.append(
            f"    texturas_visibles={st['distinct_textures']}  profundidad p50={st['depth_p50']:.0f} p95={st['depth_p95']:.0f}"
        )
        report_lines.append(
            f"    luminancia media={st['lum_mean']:.1f} (min={st['lum_min']} max={st['lum_max']} std={st['lum_std']:.1f})"
            f"  oscuro={st['dark_pct']:.1f}%  quemado={st['blown_pct']:.1f}%"
        )
        top = list(st["histogram"].items())[:8]
        report_lines.append("    top: " + ", ".join(f"{k}={100.0*v/(480*272):.1f}%" for k, v in top))
        print(f"[OK] {out_p}")

    rep_path = os.path.join(OUT_DIR, "analisis_vistas.txt")
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")
    print(f"\n[OK] Informe de analisis: {rep_path}")
    print("\n".join(report_lines))


if __name__ == "__main__":
    main()

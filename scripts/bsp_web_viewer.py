#!/usr/bin/env python3
"""Genera un visor 3D autocontenido en HTML de un mapa BSP30.

Lee el .bsp real -no una reconstruccion-: caras, vertices, aristas,
texinfo y las miptex embebidas (WAD3, paleta de 8 bits). Escribe un
unico .html con Three.js y las texturas en base64, para que abra desde
file:// sin servidor (fetch() de ficheros locales lo bloquea CORS).

Controles: WASD moverse, raton mirar, R/F subir/bajar, Shift correr.

Uso: python3 scripts/bsp_web_viewer.py <mapa> [mas mapas...]
     python3 scripts/bsp_web_viewer.py --all
"""
import base64
import glob
import io
import os
import struct
import sys
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(REPO, "android-app", "app", "src", "main", "assets",
                    "base", "nzp", "maps")
OUT = os.path.join(REPO, "visor")

# ---------------------------------------------------------------------------
# PNG minimo (RGB24) con solo la libreria estandar
# ---------------------------------------------------------------------------


def _chunk(t, d):
    c = t + d
    return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)


def png_rgb(w, h, rows):
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# BSP30
# ---------------------------------------------------------------------------


class Bsp:
    def __init__(self, path):
        d = open(path, "rb").read()
        assert struct.unpack("<i", d[:4])[0] == 30, "no es BSP30"
        self.d = d
        L = struct.unpack("<30i", d[4:4 + 120])
        self.L = L

    def lump(self, i):
        return self.L[i * 2], self.L[i * 2 + 1]

    def planes(self):
        o, l = self.lump(1)
        d = self.d
        return [struct.unpack("<3ff", d[o + k * 20:o + k * 20 + 16])
                for k in range(l // 20)]

    def texinfo(self):
        o, l = self.lump(6)
        d = self.d
        out = []
        for k in range(l // 40):
            b = o + k * 40
            v = struct.unpack("<8f", d[b:b + 32])
            mip, flags = struct.unpack("<II", d[b + 32:b + 40])
            out.append(dict(s=(v[0], v[1], v[2], v[3]),
                            t=(v[4], v[5], v[6], v[7]),
                            mip=mip, flags=flags))
        return out

    def textures(self):
        """Devuelve [(nombre, ancho, alto, bytes_indices, paleta)]"""
        o, l = self.lump(2)
        d = self.d
        n = struct.unpack("<i", d[o:o + 4])[0]
        out = []
        for i in range(n):
            off = struct.unpack("<i", d[o + 4 + i * 4:o + 8 + i * 4])[0]
            if off <= 0 or off > l:
                out.append(None)
                continue
            a = o + off
            name = d[a:d.index(b"\x00", a)].decode("latin1")
            w, h = struct.unpack("<II", d[a + 16:a + 24])
            o0, = struct.unpack("<I", d[a + 24:a + 28])
            if not (w and h):
                out.append(None)
                continue
            px = d[a + o0:a + o0 + w * h]
            # la paleta va justo detras del ultimo mip
            po = a + struct.unpack("<I", d[a + 36:a + 40])[0] + w * h // 64 + 2
            pal = d[po:po + 768]
            out.append((name, w, h, px, pal))
        return out

    def faces(self):
        o, l = self.lump(7)
        d = self.d
        out = []
        for k in range(l // 20):
            b = o + k * 20
            p, side, fe, ne, ti = struct.unpack("<HHIHH", d[b:b + 12])
            out.append(dict(plane=p, side=side, firstedge=fe, numedges=ne,
                            texinfo=ti))
        return out

    def model(self, i=0):
        """model_t: mins[3] maxs[3] origin[3] (36) headnode[4] (16)
        visleafs (4) -> firstface en 52, numfaces en 56."""
        o, _ = self.lump(14)
        b = o + i * 64
        ff, nf = struct.unpack("<ii", self.d[b + 56:b + 64])
        return dict(firstface=ff, nfaces=nf)

    def surfedges(self):
        o, l = self.lump(13)
        return struct.unpack("<%di" % (l // 4), self.d[o:o + l])

    def edges(self):
        o, l = self.lump(12)
        return struct.unpack("<%dH" % (l // 2), self.d[o:o + l])

    def vertices(self):
        o, l = self.lump(3)
        return struct.unpack("<%df" % (l // 4), self.d[o:o + l])

    def entities(self):
        o, l = self.lump(0)
        return self.d[o:o + l].decode("latin1", "ignore")


def tex_png(w, h, px, pal):
    """miptex WAD3: indices de 8 bits + paleta RGB 256*3 al final.

    En algunos mapas el offset calculado de la paleta no cae justo, asi
    que se rellena y se acota el indice: mejor una textura con colores
    raros que reventar el visor entero.
    """
    if len(pal) < 768:
        pal = pal + bytes(768 - len(pal))
    if len(px) < w * h:
        px = px + bytes(w * h - len(px))
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            c = min(px[y * w + x], 255) * 3
            row += bytes((pal[c], pal[c + 1], pal[c + 2]))
        rows.append(bytes(row))
    return png_rgb(w, h, rows)


def build(name):
    b = Bsp(os.path.join(MAPS, name + ".bsp"))
    planes = b.planes()
    tis = b.texinfo()
    texs = b.textures()
    faces = b.faces()
    verts = b.vertices()
    edges = b.edges()
    se = b.surfedges()
    m0 = b.model(0)

    # texturas a PNG en base64, solo las que usa alguna cara del modelo 0
    used = {}
    tris = []           # (tex_idx, [(x,y,z,u,v)...])
    for fi in range(m0["firstface"], m0["firstface"] + m0["nfaces"]):
        f = faces[fi]
        ti = tis[f["texinfo"]]
        tx = texs[ti["mip"]] if ti["mip"] < len(texs) else None
        if tx is None:
            continue
        tw, th = tx[1], tx[2]
        if not tw or not th:
            continue
        idx = used.setdefault(ti["mip"], len(used))
        poly = []
        ok = True
        for k in range(f["numedges"]):
            si = f["firstedge"] + k
            if si >= len(se):
                ok = False
                break
            e = se[si]
            if abs(e) * 2 + 1 >= len(edges):
                ok = False
                break
            v = edges[abs(e) * 2 + (1 if e < 0 else 0)]
            if v * 3 + 2 >= len(verts):
                ok = False
                break
            vi = edges[e >> 31 if False else (abs(e) * 2 + (1 if e < 0 else 0))] \
                if False else None
            x, y, z = verts[v * 3], verts[v * 3 + 1], verts[v * 3 + 2]
            u = (ti["s"][0] * x + ti["s"][1] * y + ti["s"][2] * z + ti["s"][3]) / tw
            vv = (ti["t"][0] * x + ti["t"][1] * y + ti["t"][2] * z + ti["t"][3]) / th
            poly.append((x, y, z, u, vv))
        if not ok or len(poly) < 3:
            continue
        for k in range(1, len(poly) - 1):
            tris.append((idx, poly[0], poly[k], poly[k + 1]))

    # empaquetar por textura
    pos, uv, idxs, groups = [], [], [], []
    start = 0
    for ti_mip, gidx in sorted(used.items(), key=lambda kv: kv[1]):
        cnt = 0
        for t in tris:
            if t[0] != gidx:
                continue
            for vtx in t[1:]:
                pos += [vtx[0], vtx[2], -vtx[1]]   # Y arriba en Three
                uv += [vtx[3], vtx[4]]
                idxs.append(start)
                start += 1
                cnt += 1
        if cnt:
            groups.append(dict(start=start - cnt, count=cnt, tex=gidx))

    tex_b64 = []
    for ti_mip, gidx in sorted(used.items(), key=lambda kv: kv[1]):
        tx = texs[ti_mip]
        data = tex_png(tx[1], tx[2], tx[3], tx[4])
        tex_b64.append(base64.b64encode(data).decode())

    # punto de aparicion del jugador, para arrancar la camara DENTRO
    import re as _re
    ent = b.entities()
    spawns = []
    for blk in _re.findall(r"\{([^{}]*)\}", ent):
        kv = dict(_re.findall(r'"([^"]+)"\s+"([^"]*)"', blk))
        if kv.get("classname") in ("info_player_start",
                                   "info_player_1_spawn",
                                   "info_player_2_spawn"):
            o = kv.get("origin", "").split()
            if len(o) == 3:
                spawns.append([float(o[0]), float(o[2]), -float(o[1])])
    return dict(name=name, pos=pos, uv=uv, idx=idxs, groups=groups,
                tex=tex_b64, spawns=spawns)


HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Visor NZ:P - __NAME__</title>
<style>
 body{margin:0;overflow:hidden;background:#111;font:12px monospace;color:#eee}
 #hud{position:fixed;left:10px;top:10px;background:rgba(0,0,0,.7);
      padding:10px;border-radius:6px;line-height:1.6;pointer-events:none;
      white-space:pre}
 #err{position:fixed;inset:0;display:none;place-items:center;text-align:center;
      padding:40px;font:14px monospace;color:#f88;background:#111}
 canvas{display:block}
</style></head><body>
<div id="hud">cargando...</div>
<div id="err">No se pudo cargar Three.js desde unpkg.com.<br>
Este visor necesita internet la primera vez.</div>
<script src="https://unpkg.com/three@0.160.0/build/three.min.js"></script>
<script>
if (typeof THREE === 'undefined') { document.getElementById('err').style.display='grid'; }
else { main(); }

function main(){
const M = __MAPS__;
const scene=new THREE.Scene();
scene.background=new THREE.Color(0x15151a);
scene.add(new THREE.AmbientLight(0xffffff,0.85));
const dl=new THREE.DirectionalLight(0xffffff,0.45);
dl.position.set(0.5,1,0.3); scene.add(dl);

const cam=new THREE.PerspectiveCamera(75,innerWidth/innerHeight,1,30000);
const renderer=new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth,innerHeight);document.body.appendChild(renderer.domElement);

// un unico buffer; cada grupo de material reutiliza los mismos vertices
const geo=new THREE.BufferGeometry();
geo.setAttribute('position',new THREE.Float32BufferAttribute(M.pos,3));
geo.setAttribute('uv',new THREE.Float32BufferAttribute(M.uv,2));
geo.setIndex(M.idx);
geo.computeVertexNormals();
const mats=[];
let base=0;
M.groups.forEach((gr,k)=>{
  geo.addGroup(base,gr.count,k); base+=gr.count;
  const img=new Image(); img.src='data:image/png;base64,'+M.tex[gr.tex];
  const t=new THREE.Texture(img); t.needsUpdate=true;
  t.magFilter=THREE.NearestFilter;
  t.minFilter=THREE.LinearMipmapLinearFilter;
  t.wrapS=t.wrapT=THREE.RepeatWrapping;
  mats.push(new THREE.MeshLambertMaterial({map:t,side:THREE.DoubleSide}));
});
const mesh=new THREE.Mesh(geo,mats);
scene.add(mesh);

// arrancar donde nace el jugador, mirando al centro del mapa
const box=new THREE.Box3().setFromObject(mesh);
const ctr=box.getCenter(new THREE.Vector3());
let yaw=0,pitch=0;
if(M.spawns&&M.spawns.length){
  const p=M.spawns[0];
  cam.position.set(p[0],p[1]+40,p[2]);
  const d=new THREE.Vector3(ctr.x-cam.position.x,0,ctr.z-cam.position.z).normalize();
  yaw=Math.atan2(-d.x,-d.z);
}else{
  cam.position.set(ctr.x,box.max.y+200,ctr.z+400);
}

const keys={};
let wire=false;
addEventListener('keydown',e=>{keys[e.code]=true;
  if(e.code==='KeyG'){wire=!wire;mats.forEach(m=>m.wireframe=wire);}});
addEventListener('keyup',e=>keys[e.code]=false);
addEventListener('click',()=>document.body.requestPointerLock());
addEventListener('mousemove',e=>{if(document.pointerLockElement){
  yaw-=e.movementX*0.002;pitch-=e.movementY*0.002;
  pitch=Math.max(-1.5,Math.min(1.5,pitch));}});
addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;
  cam.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight);});

const hud=document.getElementById('hud');
function loop(){
  requestAnimationFrame(loop);
  const sp=(keys['ShiftLeft']||keys['ShiftRight'])?80:22;
  const f=new THREE.Vector3(Math.sin(yaw)*Math.cos(pitch),Math.sin(pitch),
                            Math.cos(yaw)*Math.cos(pitch));
  const r=new THREE.Vector3().crossVectors(new THREE.Vector3(0,1,0),f).normalize();
  if(keys['KeyW'])cam.position.addScaledVector(f,sp);
  if(keys['KeyS'])cam.position.addScaledVector(f,-sp);
  if(keys['KeyA'])cam.position.addScaledVector(r,sp);
  if(keys['KeyD'])cam.position.addScaledVector(r,-sp);
  if(keys['KeyR'])cam.position.y+=sp;
  if(keys['KeyF'])cam.position.y-=sp;
  if(keys['KeyQ'])cam.position.addScaledVector(new THREE.Vector3(0,-1,0),sp);
  if(keys['KeyE'])cam.position.addScaledVector(new THREE.Vector3(0,1,0),sp);
  cam.rotation.set(pitch,yaw,0,'YXZ');
  const p=cam.position;
  const NL=String.fromCharCode(10);
  hud.textContent=[M.name+' - '+(M.pos.length/3|0)+' vert / '+(M.idx.length/3|0)+' tris / '+M.groups.length+' materiales',
    'pos '+p.x.toFixed(0)+', '+p.y.toFixed(0)+', '+p.z.toFixed(0),
    'WASD mover | raton mirar | R/F subir/bajar | Q/E vertical',
    'Shift rapido | G wireframe'].join(NL);
  renderer.render(scene,cam);
}
loop();
}
</script></body></html>
"""

def main():
    args = sys.argv[1:]
    if args and args[0] == "--all":
        names = [os.path.basename(p)[:-4]
                 for p in sorted(glob.glob(os.path.join(MAPS, "*.bsp")))]
    else:
        names = args or ["prison"]
    os.makedirs(OUT, exist_ok=True)
    ok = []
    for n in names:
        try:
            d = build(n)
        except Exception as e:
            print(f"  {n}: ERROR {e}")
            continue
        if not d["pos"]:
            print(f"  {n}: sin geometria, omitido")
            continue
        # un HTML por mapa: se abre con doble clic desde file://
        html = (HTML.replace("__MAPS__", _js(d))
                      .replace("__NAME__", n))
        f = os.path.join(OUT, n + ".html")
        open(f, "w", encoding="utf-8").write(html)
        ok.append((n, len(d["pos"]) // 3, len(d["groups"]),
                   os.path.getsize(f)))
        print(f"  {n}: {len(d['pos'])//3} vertices, "
              f"{len(d['groups'])} materiales, "
              f"{os.path.getsize(f)/1e6:.1f} MB")
    # indice con enlaces
    items = "".join(
        f'<li><a href="{n}.html">{n}</a> '
        f'<span>{v} vert / {t} mat / {sz/1e6:.1f} MB</span></li>'
        for n, v, t, sz in sorted(ok))
    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(
        "<!DOCTYPE html><meta charset='utf-8'><title>Visor NZ:P</title>"
        "<style>body{background:#151518;color:#ddd;font:14px monospace;"
        "padding:30px}a{color:#7fd1ff;text-decoration:none}"
        "li{margin:4px 0}span{color:#777;margin-left:10px}</style>"
        f"<h1>Visor de mapas NZ:P</h1><p>{len(ok)} mapas. "
        "Clic para abrir: WASD moverse, raton mirar, R/F subir/bajar, "
        "Shift correr.</p><ul>" + items + "</ul>")
    print(f"\n{len(ok)} visores en visor/ (abre visor/index.html)")


def _js(data):
    import json
    return json.dumps(data)


if __name__ == "__main__":
    main()

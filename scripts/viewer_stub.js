/*
 * Simulacro de navegador + Three.js para EJECUTAR los visores sin GPU.
 *
 * Oraculo del visor 3D: un HTML que "se genera bien" puede seguir estando
 * roto (sintaxis invalida, M con la forma equivocada, grupos con NaN...).
 * Aqui se detecta en segundos y sin navegador, igual que en el cliente.
 *
 * No es un Three.js de verdad: solo implementa lo que usa la plantilla de
 * scripts/bsp_web_viewer.py, y lanza un error si le llega algo raro
 * (array no multiplo del itemSize, addGroup con NaN, etc.).
 */
'use strict';
const fs = require('fs');

class V3 {
  constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
  set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; }
  copy(v) { return this.set(v.x, v.y, v.z); }
  clone() { return new V3(this.x, this.y, this.z); }
  add(v) { this.x += v.x; this.y += v.y; this.z += v.z; return this; }
  sub(v) { this.x -= v.x; this.y -= v.y; this.z -= v.z; return this; }
  addScaledVector(v, s) { this.x += v.x * s; this.y += v.y * s; this.z += v.z * s; return this; }
  crossVectors(a, b) { const x = a.y * b.z - a.z * b.y, y = a.z * b.x - a.x * b.z, z = a.x * b.y - a.y * b.x; return this.set(x, y, z); }
  cross(v) { return this.crossVectors(this, v); }
  length() { return Math.hypot(this.x, this.y, this.z); }
  normalize() { const l = this.length() || 1; this.x /= l; this.y /= l; this.z /= l; return this; }
}
class Col { constructor(h) { this.h = h; } }
class Obj {
  constructor() { this.position = new V3(); this.rotation = { set() {}, x: 0, y: 0, z: 0 }; this.children = []; }
  add(o) { this.children.push(o); return this; }
  lookAt() {}
}
class Box3 {
  constructor() { this.min = new V3(); this.max = new V3(); }
  setFromObject(o) {
    const g = o && o.geometry;
    if (g && g.attributes && g.attributes.position) {
      const a = g.attributes.position.array;
      this.min.set(Infinity, Infinity, Infinity); this.max.set(-Infinity, -Infinity, -Infinity);
      for (let i = 0; i < a.length; i += 3) {
        if (a[i] < this.min.x) this.min.x = a[i]; if (a[i] > this.max.x) this.max.x = a[i];
        if (a[i + 1] < this.min.y) this.min.y = a[i + 1]; if (a[i + 1] > this.max.y) this.max.y = a[i + 1];
        if (a[i + 2] < this.min.z) this.min.z = a[i + 2]; if (a[i + 2] > this.max.z) this.max.z = a[i + 2];
      }
    }
    return this;
  }
  getCenter(t) { t.set((this.min.x + this.max.x) / 2, (this.min.y + this.max.y) / 2, (this.min.z + this.max.z) / 2); return t; }
  getSize(t) { t.set(this.max.x - this.min.x, this.max.y - this.min.y, this.max.z - this.min.z); return t; }
}
const NOOP = function () {};

// registro de eventos: permite a la prueba disparar sinteticamente
// touchstart/touchmove/touchend como si fueran dedos de verdad
global.__L = {};
function reg(t, fn) { (global.__L[t] = global.__L[t] || []).push(fn); }

global.THREE = {
  Scene: class extends Obj {},
  Color: Col,
  AmbientLight: class extends Obj {},
  DirectionalLight: class extends Obj {},
  PerspectiveCamera: class extends Obj { constructor() { super(); this.aspect = 1; } updateProjectionMatrix() {} },
  WebGLRenderer: class {
    constructor() { global.__canvas = this.domElement = { style: {}, addEventListener: reg }; }
    setPixelRatio() {}
    setSize() {}
    render() { global.__rendered = (global.__rendered || 0) + 1; }
  },
  BufferGeometry: class {
    constructor() { this.attributes = {}; this.groups = []; }
    setAttribute(n, a) { if (!a || !a.array) throw new Error('atributo invalido: ' + n); this.attributes[n] = a; }
    setIndex(i) { this.index = i; }
    computeVertexNormals() { this.__normals = true; }
    addGroup(s, c, m) {
      if (!Number.isFinite(s) || !Number.isFinite(c) || !Number.isFinite(m)) throw new Error('addGroup con NaN: ' + s + ',' + c + ',' + m);
      this.groups.push({ start: s, count: c, material: m });
    }
  },
  Float32BufferAttribute: class {
    constructor(arr, item) {
      if (!Array.isArray(arr)) throw new Error('Float32BufferAttribute sin array');
      if (arr.length % item) throw new Error('array no multiplo de ' + item + ': ' + arr.length);
      this.array = arr; this.itemSize = item;
    }
  },
  Texture: class { constructor(i) { this.image = i; } },
  MeshLambertMaterial: class { constructor(o) { Object.assign(this, o); } },
  Mesh: class extends Obj { constructor(g, m) { super(); this.geometry = g; this.material = m; } },
  Box3, Vector3: V3,
  NearestFilter: 1, LinearMipmapLinearFilter: 2, RepeatWrapping: 3, DoubleSide: 2,
};

// los elementos se cachean por id: asi la prueba puede leer el texto del HUD
const cacheEls = {};
const el = (id) => (cacheEls[id] = cacheEls[id] ||
  { style: {}, textContent: '', setAttribute: NOOP, appendChild: NOOP, addEventListener: reg });
global.document = {
  getElementById: (id) => el(id),
  createElement: () => el('_tmp'),
  body: { appendChild: NOOP, addEventListener: reg },
  addEventListener: reg,
};
global.window = global;
global.innerWidth = 1280; global.innerHeight = 720;
global.devicePixelRatio = 2;   // como un movil, que es donde mas se usa
global.addEventListener = reg;
global.keys = {};
global.Image = class { constructor() { this.src = ''; } };

// frames ilimitados por defecto: la prueba del tactil necesita que el
// bucle siga vivo entre gesto y gesto. Quien quiera limitarlos, baja esto.
global.__maxFrames = 1e9;
let frames = 0;
global.requestAnimationFrame = (fn) => {
  if (frames++ < global.__maxFrames) setTimeout(fn, 4);
};

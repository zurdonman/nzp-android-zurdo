/*
 * Ejecuta el JS de un visor generado contra el simulacro (viewer_stub.js)
 * y comprueba los CONTROLES con dedos sinteticos.
 *
 * Cubre lo que el usuario reporto: "segun donde mire, en vez de avanzar
 * subo y bajo". Por eso la prueba inclina la camara ANTES de avanzar y
 * exige que avanzar no cambie la altura.
 *
 *   1. inclinar la camara arriba
 *   2. pinza: avanza  -> avance horizontal, sin volar
 *   3. joystick arriba / derecha / izquierda -> horizontal y perpendicular
 *   4. soltar el joystick -> se para
 *   5. botones SUBIR / BAJAR -> cambian la altura
 *   6. el joystick NO debe girar la camara (si girase, el avance con
 *      pinza saldria en otra direccion)
 *
 * Uso: node scripts/viewer_headless.js <fichero.js>
 * Sale con 1 si algo falla.
 */
'use strict';
require('./viewer_stub.js');
const fs = require('fs');

const fichero = process.argv[2];
if (!fichero) { console.error('falta la ruta del .js'); process.exit(2); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// target: objeto con _L (canvas, joystick, boton) o null para window
function fire(target, type, touches) {
  const L = target ? (target._L || {}) : (global.__L || {});
  (L[type] || []).forEach((fn) => fn({ touches, preventDefault() {} }));
}
const dedo = (x, y) => ({ clientX: x, clientY: y });
const cv = () => global.__canvas;
const porId = (id) => global.document.getElementById(id);

const JC = () => [18 + 65, global.innerHeight - 18 - 65];   // centro del joystick
function joy(x, y) {
  const j = porId('joy');
  fire(j, 'touchstart', [dedo(x, y)]);
  fire(j, 'touchmove', [dedo(x, y)]);
}
function joySoltar() { fire(porId('joy'), 'touchend', []); }

function pinza(de, a) {
  const cy = 300, h = de / 2, h2 = a / 2;
  fire(cv(), 'touchstart', [dedo(640 - h, cy), dedo(640 + h, cy)]);
  fire(cv(), 'touchmove', [dedo(640 - h2, cy), dedo(640 + h2, cy)]);
  fire(cv(), 'touchend', []);
}
function arrastrar(x0, y0, x1, y1) {
  fire(cv(), 'touchstart', [dedo(x0, y0)]);
  fire(cv(), 'touchmove', [dedo(x1, y1)]);
  fire(cv(), 'touchend', []);
}
function pos() {
  const t = (global.document.getElementById('hud').textContent || '');
  const m = t.match(/pos (-?[\d.]+), (-?[\d.]+), (-?[\d.]+)/);
  return m ? [parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])] : null;
}
const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
const dHor = (a, b) => Math.hypot(b[0] - a[0], b[2] - a[2]);
function angulo(a, b, c) {   // grados entre a->b y b->c
  const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const v = [c[0] - b[0], c[1] - b[1], c[2] - b[2]];
  const nu = Math.hypot(...u), nv = Math.hypot(...v);
  if (!nu || !nv) return 0;
  const d = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (nu * nv);
  return Math.acos(Math.max(-1, Math.min(1, d))) * 180 / Math.PI;
}

const fallos = [];
function exige(cond, msg) { if (!cond) fallos.push(msg); }

(async function () {
  try {
    eval(fs.readFileSync(fichero, 'utf8'));
  } catch (e) {
    console.log('  FALLO: ' + e.message);
    process.exit(1);
  }

  await sleep(150);
  if (!global.__rendered) { console.log('  FALLO: 0 frames renderizados'); process.exit(1); }

  const p0 = pos();
  if (!p0) { console.log('  FALLO: el HUD no muestra la posicion'); process.exit(1); }

  // 1) inclinar la camara arriba: a partir de aqui, avanzar NO debe volar
  arrastrar(600, 300, 600, 200);

  // 2) pinza: avanza en horizontal, sin subir ni bajar
  pinza(100, 400);
  await sleep(150);
  const p1 = pos();
  exige(dHor(p0, p1) > 20, 'la pinza no mueve la camara (' + dHor(p0, p1).toFixed(1) + ' u)');
  exige(Math.abs(p1[1] - p0[1]) < 1,
    'avanzar vuela: con la camara inclinada subio ' + (p1[1] - p0[1]).toFixed(1) + ' u');

  // 3) joystick: arriba, derecha, izquierda
  const c = JC();
  joy(c[0], c[1] - 48);
  await sleep(150);
  const p2 = pos();
  exige(dHor(p1, p2) > 20, 'el joystick hacia delante no mueve (' + dHor(p1, p2).toFixed(1) + ' u)');
  exige(Math.abs(p2[1] - p1[1]) < 1, 'el joystick vuela en vez de avanzar');

  joy(c[0] + 48, c[1]);
  await sleep(150);
  const p3 = pos();
  const g90 = angulo(p1, p2, p3);
  exige(dHor(p2, p3) > 20, 'el joystick hacia la derecha no mueve');
  exige(g90 > 60 && g90 < 120, 'el joystick no se mueve en cruz (angulo ' + g90.toFixed(0) + ')');
  exige(Math.abs(p3[1] - p2[1]) < 1, 'el joystick lateral vuela');

  joy(c[0] - 48, c[1]);
  await sleep(150);
  const p4 = pos();
  const g180 = angulo(p2, p3, p4);
  exige(g180 > 150, 'izquierda no es lo contrario de derecha (angulo ' + g180.toFixed(0) + ')');

  // 4) soltar el joystick: se para
  joySoltar();
  await sleep(120);
  const p5 = pos();
  exige(dist(p4, p5) < 5, 'el joystick sigue moviendo despues de soltarlo');

  // 5) botones SUBIR y BAJAR
  fire(porId('bup'), 'touchstart', [dedo(100, 100)]);
  await sleep(150);
  const p6 = pos();
  fire(porId('bup'), 'touchend', []);
  exige(p6[1] - p5[1] > 20, 'SUBIR no sube (' + (p6[1] - p5[1]).toFixed(1) + ' u)');

  await sleep(60);
  const p6b = pos();
  fire(porId('bdn'), 'touchstart', [dedo(100, 100)]);
  await sleep(150);
  const p7 = pos();
  fire(porId('bdn'), 'touchend', []);
  exige(p7[1] - p6b[1] < -20, 'BAJAR no baja (' + (p7[1] - p6b[1]).toFixed(1) + ' u)');

  // 6) el joystick no debe girar la camara: avanzar con el y con la pinza
  //    tiene que salir en la misma direccion
  await sleep(60);
  const p7b = pos();
  joy(c[0], c[1] - 48);
  await sleep(150);
  const p8 = pos();
  joySoltar();
  await sleep(60);
  const p8b = pos();
  pinza(100, 400);
  await sleep(150);
  const p9 = pos();
  const mismo = angulo(p7b, p8, p9);
  exige(mismo < 25, 'el joystick gira la camara: el avance sale torcido (' +
    mismo.toFixed(0) + ' grados)');

  if (fallos.length) {
    console.log('  FALLO: ' + fallos[0]);
    process.exit(1);
  }
  console.log('  ok, horiz ' + dHor(p0, p1).toFixed(0) + ' u, cruz ' + g90.toFixed(0) +
    ' grados, subida ' + (p6[1] - p5[1]).toFixed(0) + ' u');
  process.exit(0);
})();

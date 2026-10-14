/*
 * Ejecuta el JS de un visor generado contra el simulacro (viewer_stub.js)
 * y comprueba los CONTROLES TACTILES con dedos sinteticos:
 *
 *   1. separar dos dedos            -> la camara AVANZA
 *   2. arrastrar un dedo en horizontal -> la camara GIRA
 *   (se gira y se vuelve a avanzar: el segundo tramo debe salir en
 *    angulo respecto al primero; si no gira, sale paralelo)
 *
 * Uso: node scripts/viewer_headless.js <fichero.js>
 * Sale con 1 si el visor revienta, no renderiza, o el tactil no responde.
 */
'use strict';
require('./viewer_stub.js');
const fs = require('fs');

const fichero = process.argv[2];
if (!fichero) { console.error('falta la ruta del .js'); process.exit(2); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// dispara un evento tactil como lo haria un dedo de verdad
function fire(type, touches) {
  const e = { touches, preventDefault() {} };
  (global.__L[type] || []).forEach((fn) => fn(e));
}
const dedo = (x, y) => ({ clientX: x, clientY: y });

// separar/juntar dos dedos: 'de' y 'a' son distancias en px
function pinza(de, a) {
  const cy = 300, h = de / 2, h2 = a / 2;
  fire('touchstart', [dedo(640 - h, cy), dedo(640 + h, cy)]);
  fire('touchmove', [dedo(640 - h2, cy), dedo(640 + h2, cy)]);
  fire('touchend', []);
}
function pos() {
  const t = (global.document.getElementById('hud').textContent || '');
  const m = t.match(/pos (-?[\d.]+), (-?[\d.]+), (-?[\d.]+)/);
  return m ? [parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])] : null;
}
const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
function angulo(a, b, c) {   // angulo en grados entre a->b y b->c
  const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const v = [c[0] - b[0], c[1] - b[1], c[2] - b[2]];
  const nu = Math.hypot(...u), nv = Math.hypot(...v);
  if (!nu || !nv) return 0;
  const d = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (nu * nv);
  return Math.acos(Math.max(-1, Math.min(1, d))) * 180 / Math.PI;
}

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

  // 1) separar los dedos debe avanzar
  pinza(100, 400);
  await sleep(150);
  const p1 = pos();
  const avance = dist(p0, p1);
  if (avance < 20) {
    console.log('  FALLO: la pinza no mueve la camara (' + avance.toFixed(1) + ' u)');
    process.exit(1);
  }

  // 2) arrastrar un dedo debe girar (y solo girar: no debe mover)
  fire('touchstart', [dedo(500, 300)]);
  fire('touchmove', [dedo(238, 300)]);   // -262 px -> ~90 grados
  fire('touchend', []);
  await sleep(80);
  const p2 = pos();
  if (dist(p1, p2) > 5) {
    console.log('  FALLO: girar con un dedo tambien desplaza la camara');
    process.exit(1);
  }

  // 3) tras girar, avanzar de nuevo: el tramo nuevo sale en otro angulo
  pinza(100, 400);
  await sleep(150);
  const p3 = pos();
  const giro = angulo(p0, p1, p3);
  if (giro < 60) {
    console.log('  FALLO: arrastrar un dedo no gira (angulo ' + giro.toFixed(1) + ')');
    process.exit(1);
  }

  // 4) juntar los dedos debe RETROCEDER (sentido contrario al avance)
  pinza(400, 100);
  await sleep(150);
  const p4 = pos();
  const atras = angulo(p1, p3, p4);
  if (atras < 150) {
    console.log('  FALLO: juntar los dedos no retrocede (angulo ' + atras.toFixed(1) + ')');
    process.exit(1);
  }

  console.log('  ok, avance ' + avance.toFixed(0) + ' u, giro ' + giro.toFixed(0) +
    ' grados, retroceso ' + atras.toFixed(0) + ' grados');
  process.exit(0);
})();

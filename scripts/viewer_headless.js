/*
 * Ejecuta el JS de un visor generado contra el simulacro (viewer_stub.js).
 * Uso: node scripts/viewer_headless.js <fichero.js>
 * Sale con 1 si el visor revienta o si no renderiza ni un frame.
 */
'use strict';
require('./viewer_stub.js');
const fs = require('fs');
const path = process.argv[2];
if (!path) { console.error('falta la ruta del .js'); process.exit(2); }

try {
  eval(fs.readFileSync(path, 'utf8'));
} catch (e) {
  console.log('  FALLO: ' + e.message);
  process.exit(1);
}
setTimeout(() => {
  const f = global.__rendered || 0;
  if (!f) { console.log('  FALLO: 0 frames renderizados'); process.exit(1); }
  console.log('  ok, ' + f + ' frames');
}, 300);

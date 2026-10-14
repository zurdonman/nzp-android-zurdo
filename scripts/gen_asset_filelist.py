#!/usr/bin/env python3
# ============================================================================
#  gen_asset_filelist.py
#  Genera el indice de assets que consume el motor en Android:
#     android-app/app/src/main/assets/base/nzp/filelist.txt
#
#  Android descarta SILENCIOSAMENTE los ficheros de assets que empiezan por
#  punto (".filelist"), por eso el indice DEBE tener un nombre normal.
#
#  Ademas, AAssetDir_getNextFileName() en Android solo devuelve los ficheros
#  del nivel pedido: NO devuelve los subdirectorios, asi que la recursion
#  nativa no puede descubrir el arbol. Este indice es la unica via fiable.
#
#  Equivalente multiplataforma de scripts/gen_asset_filelist.ps1 (PowerShell,
#  pensado para el entorno Windows del usuario).
#
#  Uso: python3 scripts/gen_asset_filelist.py
# ============================================================================

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(REPO_ROOT, "android-app", "app", "src", "main", "assets", "base", "nzp")
FILELIST = os.path.join(ASSETS, "filelist.txt")
LEGACY = os.path.join(ASSETS, ".filelist")
SKIP = {"filelist.txt", ".filelist"}


def main() -> int:
    if not os.path.isdir(ASSETS):
        print(f"No existe {ASSETS}", file=sys.stderr)
        return 1

    entries = []
    for dirpath, _dirnames, filenames in os.walk(ASSETS):
        for name in filenames:
            if name in SKIP:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ASSETS).replace(os.sep, "/")
            entries.append(rel)
    # Orden alfabetico sin distinguir mayusculas, igual que Sort-Object de
    # PowerShell, para que este script y gen_asset_filelist.ps1 generen
    # exactamente el mismo fichero y no haya "ruido" en los diffs.
    entries.sort(key=lambda s: (s.lower(), s))

    # UTF-8 sin BOM y saltos LF (nada de \r: el motor lo tomaria como parte
    # del nombre del fichero).
    text = "\n".join(entries) + "\n"
    with open(FILELIST, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    print("=== Indice de assets generado ===")
    print(f"   {FILELIST}")
    print(f"   {len(entries)} entradas   {os.path.getsize(FILELIST):,} bytes")

    # Elimina el indice antiguo (inutil: Android no lo empaqueta).
    if os.path.exists(LEGACY):
        os.remove(LEGACY)
        print("   eliminado .filelist (Android no lo empaqueta)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

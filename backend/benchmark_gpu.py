"""
Compara la velocidad del OCR en CPU contra GPU (DirectML) en este equipo,
y verifica que el texto reconocido sea el mismo.

Uso:
    python benchmark_gpu.py [archivo.pdf] [--paginas N] [--dpi D] [--gpu-id I]

Por defecto usa las primeras 6 páginas del PDF de ejemplo del proyecto, al
mismo DPI que el sistema (300). Al final indica qué configuración conviene.
"""

from __future__ import annotations

import argparse
import difflib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np

from app.ocr_engine import crear_motor, directml_disponible, pdf_to_images

PDF_EJEMPLO = Path(__file__).parent.parent / "DOCUMENTACION PARTICIPANTES 3574135 (1).pdf"

CONFIGURACIONES = [
    ("CPU", []),
    ("GPU: rec", ["rec"]),
    ("GPU: det", ["det"]),
    ("GPU: det,rec", ["det", "rec"]),
    ("GPU: det,cls,rec", ["det", "cls", "rec"]),
]


def ocr_paginas(motor, imagenes):
    textos = []
    for img in imagenes:
        result, _ = motor(img)
        textos.append("\n".join(t for _, t, _ in result) if result else "")
    return textos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?", default=str(PDF_EJEMPLO))
    ap.add_argument("--paginas", type=int, default=6)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--gpu-id", type=int, default=None, help="adaptador DirectML (por defecto: el de alto rendimiento)")
    args = ap.parse_args()

    print(f"PDF: {Path(args.pdf).name}  |  páginas: {args.paginas}  |  DPI: {args.dpi}")
    imagenes = [np.array(img) for img in pdf_to_images(Path(args.pdf).read_bytes(), dpi=args.dpi)[: args.paginas]]

    configs = CONFIGURACIONES if directml_disponible() else CONFIGURACIONES[:1]
    if len(configs) == 1:
        print("DirectML no está instalado: solo se mide CPU. Ejecuta activar_gpu.bat primero.")

    resultados = []  # (nombre, modulos, s/página, % texto igual a CPU)
    textos_cpu = None
    for nombre, modulos in configs:
        print(f"\n[{nombre}] cargando...", flush=True)
        try:
            motor = crear_motor(modulos, args.gpu_id)
            motor(imagenes[0])  # calentamiento: la GPU compila los modelos en la 1a pasada
            t0 = time.perf_counter()
            textos = ocr_paginas(motor, imagenes)
            seg = (time.perf_counter() - t0) / len(imagenes)
        except Exception as e:
            print(f"[{nombre}] FALLÓ: {e}")
            continue

        if textos_cpu is None:
            textos_cpu = textos
        similitud = 100 * sum(difflib.SequenceMatcher(None, a, b).ratio() for a, b in zip(textos_cpu, textos)) / len(textos)
        print(f"[{nombre}] {seg:.2f} s/página  |  texto igual a CPU: {similitud:.1f}%")
        resultados.append((nombre, modulos, seg, similitud))

    if not resultados:
        return 1

    base = resultados[0][2]
    print("\n" + "=" * 62)
    print(f"{'Configuración':<20}{'s/página':>10}{'vs CPU':>10}{'texto = CPU':>15}")
    for nombre, _, seg, sim in resultados:
        print(f"{nombre:<20}{seg:>10.2f}{base / seg:>9.2f}x{sim:>14.1f}%")
    print("=" * 62)
    if len(configs) == 1:
        return 0

    # Solo cuenta como opción una GPU que no cambie el texto reconocido.
    validas = [r for r in resultados if r[3] >= 99.0]
    mejor = min(validas, key=lambda r: r[2])
    if not mejor[1] or mejor[2] > base * 0.9:
        print("\nRecomendación: la GPU no mejora lo suficiente en este equipo. Usa CPU:")
        print("    set OCR_DISPOSITIVO=cpu        (en run.bat)")
    else:
        print(f"\nRecomendación: {mejor[0]} ({base / mejor[2]:.1f}x más rápido). En run.bat:")
        print(f"    set OCR_GPU_MODULOS={','.join(mejor[1])}")
        if args.gpu_id is not None:
            print(f"    set OCR_GPU_ID={args.gpu_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

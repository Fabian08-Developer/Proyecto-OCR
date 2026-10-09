"""
Compara la velocidad del OCR en CPU contra GPU (DirectML) en este equipo,
verifica que el texto reconocido sea el mismo y guarda la mejor
configuración en backend/gpu_config.json (el servidor la usa al arrancar).

Uso:
    python benchmark_gpu.py [archivo.pdf] [--paginas N] [--dpi D] [--gpu-id I]

Cada configuración se mide en un proceso aparte: un fallo interno de
DirectML cierra el proceso entero, y así solo se pierde esa configuración.
Por defecto usa las primeras 6 páginas del PDF de ejemplo del proyecto, al
mismo DPI que el sistema (300).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import queue
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PDF_EJEMPLO = Path(__file__).parent.parent / "DOCUMENTACION PARTICIPANTES 3574135 (1).pdf"
MARCA = "RESULTADO_JSON "

# (nombre, modelos en GPU, páginas a la vez)
CONFIG_CPU = ("CPU", [], 2)
CONFIGS_GPU = [
    (f"GPU {'+'.join(m)} x{t}", m, t)
    for m in (["rec"], ["det"], ["det", "rec"], ["det", "cls", "rec"])
    for t in (1, 2)
]


# --------------------------------------------------------------------------- #
# Proceso hijo: mide UNA configuración
# --------------------------------------------------------------------------- #

def medir(args) -> None:
    import numpy as np

    from app.ocr_engine import crear_motor, pdf_to_images

    modulos = [m for m in args.modulos.split(",") if m]
    imagenes = [np.array(img) for img in pdf_to_images(Path(args.pdf).read_bytes(), dpi=args.dpi)[: args.paginas]]

    print("  creando motores...", flush=True)
    motores = [crear_motor(modulos, args.gpu_id) for _ in range(args.trabajadores)]
    print("  primera lectura (la GPU prepara los modelos)...", flush=True)
    for motor in motores:
        motor(imagenes[0])

    libres: queue.Queue = queue.Queue()
    for motor in motores:
        libres.put(motor)

    def leer(img):
        motor = libres.get()
        try:
            result, _ = motor(img)
        finally:
            libres.put(motor)
        return "\n".join(t for _, t, _ in result) if result else ""

    print(f"  midiendo {len(imagenes)} páginas...", flush=True)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.trabajadores) as ejecutor:
        textos = list(ejecutor.map(leer, imagenes))
    seg = (time.perf_counter() - t0) / len(imagenes)
    print(MARCA + json.dumps({"seg": seg, "textos": textos}), flush=True)


# --------------------------------------------------------------------------- #
# Proceso principal: lanza cada configuración y compara
# --------------------------------------------------------------------------- #

def lanzar(nombre: str, modulos: list, trabajadores: int, args) -> dict | None:
    cmd = [
        sys.executable, "-X", "faulthandler", str(Path(__file__).resolve()), args.pdf,
        "--paginas", str(args.paginas), "--dpi", str(args.dpi),
        "--medir", "--modulos", ",".join(modulos), "--trabajadores", str(trabajadores),
    ]
    if args.gpu_id is not None:
        cmd += ["--gpu-id", str(args.gpu_id)]

    print(f"\n[{nombre}]", flush=True)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        print("  FALLÓ: tardó más de 15 minutos.")
        return None

    resultado = None
    for linea in proc.stdout.splitlines():
        if linea.startswith(MARCA):
            resultado = json.loads(linea[len(MARCA):])
        elif linea.strip():
            print(linea)

    if resultado is None:
        print(f"  FALLÓ: el proceso se cerró (código {proc.returncode}). Últimas líneas del error:")
        errores = [l for l in proc.stderr.splitlines() if l.strip() and not l.startswith("[")]
        for linea in errores[-15:]:
            print("    " + linea)
    return resultado


def guardar_config(config: dict) -> None:
    from app.ocr_engine import CONFIG_GPU

    CONFIG_GPU.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"\nGuardado en {CONFIG_GPU.name}: el servidor lo usará la próxima vez que inicies run.bat.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?", default=str(PDF_EJEMPLO))
    ap.add_argument("--paginas", type=int, default=6)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--gpu-id", type=int, default=None, help="adaptador DirectML (por defecto: el de alto rendimiento)")
    ap.add_argument("--medir", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--modulos", default="", help=argparse.SUPPRESS)
    ap.add_argument("--trabajadores", type=int, default=2, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.medir:
        medir(args)
        return 0

    from app.ocr_engine import directml_disponible

    print(f"PDF: {Path(args.pdf).name}  |  páginas: {args.paginas}  |  DPI: {args.dpi}")
    configs = [CONFIG_CPU] + (CONFIGS_GPU if directml_disponible() else [])
    if len(configs) == 1:
        print("DirectML no está instalado: solo se mide CPU. Ejecuta activar_gpu.bat primero.")

    resultados = []  # (nombre, modulos, trabajadores, s/página, % texto igual a CPU)
    textos_cpu = None
    for nombre, modulos, trabajadores in configs:
        r = lanzar(nombre, modulos, trabajadores, args)
        if r is None:
            if textos_cpu is None:
                print("\nNi siquiera la CPU funcionó; revisa el error de arriba.")
                return 1
            continue
        if textos_cpu is None:
            textos_cpu = r["textos"]
        similitud = 100 * sum(
            difflib.SequenceMatcher(None, a, b).ratio() for a, b in zip(textos_cpu, r["textos"])
        ) / len(textos_cpu)
        print(f"  {r['seg']:.2f} s/página  |  texto igual a CPU: {similitud:.1f}%")
        resultados.append((nombre, modulos, trabajadores, r["seg"], similitud))

    base = resultados[0][3]
    print("\n" + "=" * 62)
    print(f"{'Configuración':<24}{'s/página':>10}{'vs CPU':>10}{'texto = CPU':>15}")
    for nombre, _, _, seg, sim in resultados:
        print(f"{nombre:<24}{seg:>10.2f}{base / seg:>9.2f}x{sim:>14.1f}%")
    fallidas = len(configs) - len(resultados)
    if fallidas:
        print(f"({fallidas} configuraciones fallaron; ver arriba)")
    print("=" * 62)
    if len(configs) == 1:
        return 0

    # Solo cuenta como opción una GPU que no cambie el texto reconocido y gane al menos un 10 %.
    validas = [r for r in resultados[1:] if r[4] >= 99.0 and r[3] < base * 0.9]
    if not validas:
        print("\nRecomendación: la GPU no mejora lo suficiente en este equipo; se queda en CPU.")
        guardar_config({"modulos": [], "trabajadores": CONFIG_CPU[2]})
        return 0

    nombre, modulos, trabajadores, seg, _ = min(validas, key=lambda r: r[3])
    print(f"\nRecomendación: {nombre} ({base / seg:.1f}x más rápido que CPU).")
    config = {"modulos": modulos, "trabajadores": trabajadores}
    if args.gpu_id is not None:
        config["gpu_id"] = args.gpu_id
    guardar_config(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())

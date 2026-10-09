"""
Instala (o quita) la aceleración por GPU del OCR usando DirectML (Windows).

Uso (con el entorno virtual activado, o desde activar_gpu.bat):
    python activar_gpu.py           # instala onnxruntime-directml
    python activar_gpu.py --quitar  # vuelve al onnxruntime normal (solo CPU)

Por qué no basta con `pip install onnxruntime-directml`:
  rapidocr-onnxruntime depende del paquete `onnxruntime`, y ambos paquetes
  instalan la misma carpeta `onnxruntime/`. Si se desinstala `onnxruntime`,
  run.bat lo vuelve a instalar en cada arranque y pisa la versión DirectML.
  Por eso se dejan los dos instalados en la MISMA versión y se reinstala
  DirectML encima: pip queda satisfecho y los archivos son los de DirectML.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from importlib import metadata


def pip(*args: str) -> None:
    cmd = [sys.executable, "-m", "pip", *args]
    print(">", " ".join(cmd[2:]), flush=True)
    subprocess.check_call(cmd)


def version(paquete: str) -> str | None:
    try:
        return metadata.version(paquete)
    except metadata.PackageNotFoundError:
        return None


def proveedores() -> str:
    # En un proceso nuevo: este ya pudo haber importado el onnxruntime viejo.
    return subprocess.check_output(
        [sys.executable, "-c", "import onnxruntime as o; print(o.get_available_providers())"],
        text=True,
    ).strip()


def instalar() -> int:
    if platform.system() != "Windows":
        print("DirectML solo funciona en Windows 10/11. No se instaló nada.")
        return 1

    pip("install", "--upgrade", "--no-deps", "onnxruntime-directml")
    v = version("onnxruntime-directml")
    if version("onnxruntime") != v:
        pip("install", "--no-deps", f"onnxruntime=={v}")
    pip("install", "--force-reinstall", "--no-deps", f"onnxruntime-directml=={v}")

    prov = proveedores()
    print(f"\nProveedores disponibles: {prov}")
    if "DmlExecutionProvider" not in prov:
        print("ERROR: DirectML no quedó disponible. Revisa los mensajes de pip de arriba.")
        return 1

    print(
        "\nListo. La próxima vez que inicies run.bat el OCR usará la GPU.\n"
        "Para medir si realmente es más rápido en este equipo:\n"
        "    python benchmark_gpu.py"
    )
    return 0


def quitar() -> int:
    v = version("onnxruntime")
    if version("onnxruntime-directml"):
        pip("uninstall", "-y", "onnxruntime-directml")
    # Desinstalar DirectML borra archivos compartidos: se reinstala el normal.
    pip("install", "--force-reinstall", "--no-deps", f"onnxruntime=={v}" if v else "onnxruntime")
    print(f"\nProveedores disponibles: {proveedores()}")
    print("Listo. El OCR vuelve a correr solo en CPU.")
    return 0


if __name__ == "__main__":
    sys.exit(quitar() if "--quitar" in sys.argv[1:] else instalar())

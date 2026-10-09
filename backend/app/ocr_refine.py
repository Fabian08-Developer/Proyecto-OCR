"""
Segunda lectura de palabras dudosas.

La cédula clásica lleva "REPUBLICA DE COLOMBIA" como marca de agua clara sobre los campos. Cuando
esa marca pasa sobre una letra del nombre, el OCR la lee mal y con mucha confianza (ALEXANDER ->
ALEXANDEA, 95 %). El texto de la cédula es oscuro y la marca es clara, así que al volver a leer la
línea recortada y con otra preparación de la imagen (canal mínimo, gris con contraste, blanco y
negro) la letra suele salir bien.

Solo se relee cuando una palabra NO es un nombre del léxico pero se parece mucho a uno, y solo se
corrige si al menos MIN_LECTURAS lecturas independientes de la imagen traen ese mismo nombre. No
interviene el Excel: lo corregido es lo que dice la imagen, nunca lo que se espera que diga.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Iterable, List, Optional

import numpy as np
from PIL import Image, ImageOps

from .field_parser import palabras_conocidas_cercanas

logger = logging.getLogger(__name__)

# Lecturas independientes que deben traer el mismo nombre conocido para aceptar la corrección
MIN_LECTURAS = 2
# Tope de líneas que se releen por página (protege el tiempo de procesamiento)
MAX_LINEAS_POR_PAGINA = 6

_PALABRA_RE = re.compile(r"[A-ZÑ]{6,}")

# Lee una imagen y devuelve las palabras reconocidas
Lector = Callable[[Image.Image], List[str]]


def lector_rapidocr(motor=None) -> Lector:
    """
    Lector que lee el recorte de UNA línea usando solo el reconocedor de RapidOCR (sin volver a
    detectar): es de 10 a 100 veces más rápido que una pasada completa y no parte la línea en trozos.
    `motor` es el RapidOCR de la página (al leer páginas en paralelo cada una tiene el suyo).
    """
    def leer(imagen: Image.Image) -> List[str]:
        from .ocr_engine import get_ocr_engine

        resultado, _ = (motor or get_ocr_engine())(
            np.array(imagen.convert("RGB")), use_det=False, use_cls=False, use_rec=True
        )
        return [palabra for texto, _score in (resultado or []) for palabra in str(texto).upper().split()]

    return leer


def _variantes(recorte: Image.Image) -> List[Image.Image]:
    """Preparaciones distintas del mismo recorte; cada una puede leer bien lo que otra lee mal."""
    gris = ImageOps.grayscale(recorte)
    minimo = Image.fromarray(np.array(recorte.convert("RGB")).min(axis=2))
    return [
        ImageOps.autocontrast(minimo, cutoff=2),    # texto oscuro en todos los canales, marca clara
        ImageOps.autocontrast(gris, cutoff=2),
        gris.point(lambda v: 255 if v > 150 else 0),
        recorte,
    ]


def palabras_dudosas(texto: str) -> dict:
    """{palabra: [nombres conocidos parecidos]} de las palabras que parecen un nombre conocido mal leído."""
    dudosas = {}
    for palabra in set(_PALABRA_RE.findall(texto.upper())):
        cercanas = palabras_conocidas_cercanas(palabra)
        if cercanas:
            dudosas[palabra] = cercanas
    return dudosas


def corregir_texto(texto: str, lecturas: Iterable[List[str]]) -> str:
    """
    Reemplaza cada palabra dudosa por el nombre conocido que traigan al menos MIN_LECTURAS lecturas.
    Si dos nombres conocidos empatan, no se corrige: no hay forma de saber cuál es.
    """
    lecturas = [set(palabras) for palabras in lecturas]
    for palabra, cercanas in palabras_dudosas(texto).items():
        votos = {c: sum(1 for palabras in lecturas if c in palabras) for c in cercanas}
        mejor = max(votos.values(), default=0)
        ganadores = [c for c, v in votos.items() if v == mejor]
        if mejor >= MIN_LECTURAS and len(ganadores) == 1:
            logger.info("Segunda lectura: %s -> %s (%s lecturas)", palabra, ganadores[0], mejor)
            texto = re.sub(rf"\b{palabra}\b", ganadores[0], texto, flags=re.IGNORECASE)
    return texto


def _recortar(imagen: Image.Image, caja: list) -> Optional[Image.Image]:
    xs, ys = [p[0] for p in caja], [p[1] for p in caja]
    x0, x1, y0, y1 = int(min(xs)), int(max(xs)), int(min(ys)), int(max(ys))
    alto = y1 - y0
    if alto < 8 or x1 - x0 < 8:
        return None
    margen_y, margen_x = max(6, alto // 4), max(8, alto // 3)
    return imagen.crop((max(0, x0 - margen_x), max(0, y0 - margen_y), x1 + margen_x, y1 + margen_y))


def refinar_lineas(imagen: Image.Image, lineas: list, lector: Optional[Lector] = None) -> int:
    """
    Corrige en sitio el texto de las líneas con palabras dudosas (ver módulo). `lineas` son OcrLine.
    Devuelve cuántas líneas cambiaron. Nunca lanza: si la segunda lectura falla se conserva la primera.
    """
    lector = lector or lector_rapidocr()
    corregidas = 0
    pendientes = [l for l in lineas if palabras_dudosas(l.text)][:MAX_LINEAS_POR_PAGINA]
    for linea in pendientes:
        try:
            recorte = _recortar(imagen, linea.box)
            if recorte is None:
                continue
            nuevo = corregir_texto(linea.text, [lector(v) for v in _variantes(recorte)])
        except Exception:  # noqa: BLE001 - la relectura es opcional; el OCR original sigue valiendo
            logger.warning("No se pudo releer una línea dudosa", exc_info=True)
            continue
        if nuevo != linea.text:
            linea.text = nuevo
            corregidas += 1
    return corregidas

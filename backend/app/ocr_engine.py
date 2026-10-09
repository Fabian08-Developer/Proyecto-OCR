"""
Motor de OCR.

Responsable de:
  1. Convertir cada página de un PDF en una imagen (usando PyMuPDF, sin
     depender de Poppler/pdf2image).
  2. Ejecutar RapidOCR sobre cada imagen y devolver el texto reconocido,
     tanto como bloque de texto plano como lista de líneas con su posición
     (por si en el futuro se necesita ubicación espacial de los campos).

RapidOCR se carga una sola vez (patrón singleton) porque cargar los modelos
ONNX en cada llamada es costoso.
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from typing import List

import pymupdf as fitz  # PyMuPDF
import numpy as np
from PIL import Image

from .ocr_refine import lector_rapidocr, refinar_lineas

logger = logging.getLogger(__name__)

# Páginas que se leen a la vez (cada una con su propio motor). Medido en un i5 de 12 hilos: 2 es
# ~12 % más rápido que 1 y con 3 o 4 ya no mejora. OCR_TRABAJADORES=1 vuelve a leer de una en una.
try:
    OCR_TRABAJADORES = max(1, int(os.environ.get("OCR_TRABAJADORES", "2")))
except ValueError:
    OCR_TRABAJADORES = 2

_ocr_engine = None


def get_ocr_engine():
    """Devuelve una instancia única (singleton) de RapidOCR."""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        logger.info("Cargando modelos de RapidOCR (solo ocurre una vez)...")
        _ocr_engine = RapidOCR()
    return _ocr_engine


@dataclass
class OcrLine:
    text: str
    confidence: float
    box: list  # 4 puntos [[x,y], [x,y], [x,y], [x,y]]


@dataclass
class PageResult:
    page_number: int  # 1-indexado
    lines: List[OcrLine] = field(default_factory=list)
    raw_text: str = ""
    image: "Image.Image | None" = None  # se conserva para depuración/preview

    def as_text_lines(self) -> List[str]:
        return [l.text for l in self.lines]


def _open_pdf(pdf_bytes: bytes):
    """Abre el PDF; ValueError con mensaje claro si está dañado o protegido con contraseña."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - PyMuPDF lanza varios tipos según el daño
        raise ValueError("El archivo no es un PDF válido o está dañado.") from exc
    if doc.needs_pass:
        doc.close()
        raise ValueError("El PDF está protegido con contraseña; quítale la protección y vuelve a subirlo.")
    return doc


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Número de páginas del PDF (instantáneo). ValueError si no es un PDF utilizable."""
    with _open_pdf(pdf_bytes) as doc:
        return len(doc)


def iter_pdf_images(pdf_bytes: bytes, dpi: int = 300) -> Iterator[Tuple[int, int, Image.Image]]:
    """Genera (n_página, total, imagen) renderizando cada página solo cuando se pide."""
    zoom = dpi / 72  # PyMuPDF trabaja en puntos (72 dpi por defecto)
    matrix = fitz.Matrix(zoom, zoom)

    with _open_pdf(pdf_bytes) as doc:
        total = len(doc)
        for number, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            yield number, total, Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def pdf_to_images(pdf_bytes: bytes, dpi: int = 300) -> List[Image.Image]:
    """Convierte cada página de un PDF (bytes) en una imagen PIL (todas a la vez; ver iter_pdf_images)."""
    return [image for _, _, image in iter_pdf_images(pdf_bytes, dpi=dpi)]


def run_ocr_on_image(image: Image.Image, engine=None) -> PageResult:
    """Ejecuta RapidOCR sobre una imagen PIL y arma el PageResult (con `engine` o el motor común)."""
    engine = engine or get_ocr_engine()
    img_array = np.array(image)

    try:
        result, _elapse = engine(img_array)
    except Exception:
        if _ocr_dispositivo == "cpu":
            raise
        logger.exception("Falló el OCR en la GPU; se cambia a CPU y se reintenta.")
        _volver_a_cpu()
        result, _elapse = _ocr_engine(img_array)

    lines: List[OcrLine] = []
    if result:
        for box, text, score in result:
            lines.append(OcrLine(text=text, confidence=float(score), box=box))

    # Palabras que parecen un nombre conocido mal leído (marca de agua sobre una letra): segunda lectura
    refinar_lineas(image, lines, lector_rapidocr(engine))

    raw_text = "\n".join(l.text for l in lines)
    return PageResult(page_number=0, lines=lines, raw_text=raw_text, image=image)


def _leer_pagina(number: int, total: int, img: Image.Image, keep_images: bool) -> PageResult:
    logger.info("OCR página %s/%s", number, total)
    with _motor_prestado() as motor:
        page_result = run_ocr_on_image(img, engine=motor)
    page_result.page_number = number
    if not keep_images:
        page_result.image = None
    return page_result


def extract_pdf(pdf_bytes: bytes, dpi: int = 300, keep_images: bool = False) -> List[PageResult]:
    """
    Punto de entrada principal: recibe los bytes de un PDF y devuelve
    el resultado de OCR de cada página, en orden.

    Lee OCR_TRABAJADORES páginas a la vez y renderiza la siguiente mientras tanto; nunca hay más de
    OCR_TRABAJADORES + 1 páginas en memoria.
    """
    pages: List[PageResult] = []
    max_en_memoria = OCR_TRABAJADORES + 1
    pendientes: deque = deque()

    with ThreadPoolExecutor(max_workers=OCR_TRABAJADORES, thread_name_prefix="ocr") as ejecutor:
        try:
            for number, total, img in iter_pdf_images(pdf_bytes, dpi=dpi):
                pendientes.append(ejecutor.submit(_leer_pagina, number, total, img, keep_images))
                while len(pendientes) >= max_en_memoria:
                    pages.append(pendientes.popleft().result())
            while pendientes:
                pages.append(pendientes.popleft().result())
        except BaseException:
            for tarea in pendientes:
                tarea.cancel()
            raise

    return pages

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
from dataclasses import dataclass, field
from typing import List

import pymupdf as fitz  # PyMuPDF
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

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


def pdf_to_images(pdf_bytes: bytes, dpi: int = 300) -> List[Image.Image]:
    """Convierte cada página de un PDF (bytes) en una imagen PIL."""
    images: List[Image.Image] = []
    zoom = dpi / 72  # PyMuPDF trabaja en puntos (72 dpi por defecto)
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            images.append(img)

    return images


def run_ocr_on_image(image: Image.Image) -> PageResult:
    """Ejecuta RapidOCR sobre una imagen PIL y arma el PageResult."""
    engine = get_ocr_engine()
    img_array = np.array(image)

    result, _elapse = engine(img_array)

    lines: List[OcrLine] = []
    if result:
        for box, text, score in result:
            lines.append(OcrLine(text=text, confidence=float(score), box=box))

    raw_text = "\n".join(l.text for l in lines)
    return PageResult(page_number=0, lines=lines, raw_text=raw_text, image=image)


def extract_pdf(pdf_bytes: bytes, dpi: int = 300, keep_images: bool = False) -> List[PageResult]:
    """
    Punto de entrada principal: recibe los bytes de un PDF y devuelve
    el resultado de OCR de cada página, en orden.
    """
    images = pdf_to_images(pdf_bytes, dpi=dpi)
    pages: List[PageResult] = []

    for i, img in enumerate(images, start=1):
        logger.info("OCR página %s/%s", i, len(images))
        page_result = run_ocr_on_image(img)
        page_result.page_number = i
        if not keep_images:
            page_result.image = None
        pages.append(page_result)

    return pages

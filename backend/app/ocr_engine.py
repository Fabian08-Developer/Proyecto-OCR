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

Aceleración por GPU (opcional, solo Windows):
  Si está instalado `onnxruntime-directml` (ver `activar_gpu.bat`), los
  modelos de RapidOCR se ejecutan en la tarjeta gráfica vía DirectML. Si la
  GPU falla al cargar o al inferir, se vuelve automáticamente a CPU.
  Variables de entorno:
    OCR_DISPOSITIVO   auto (defecto) | gpu | cpu
    OCR_GPU_MODULOS   modelos que van a la GPU: det,cls,rec (defecto: todos)
    OCR_GPU_ID        índice del adaptador DirectML; sin definir se elige
                      la GPU de "alto rendimiento" (la dedicada en portátiles
                      con dos tarjetas).
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

import pymupdf as fitz  # PyMuPDF
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_ocr_engine = None
_ocr_dispositivo = "cpu"  # descripción legible de dónde corre el motor actual

DML_EP = "DmlExecutionProvider"

# Dónde guarda RapidOCR la sesión ONNX de cada modelo.
#   det = detección de cajas de texto, cls = orientación, rec = reconocimiento.
_SESIONES_RAPIDOCR = {"det": ("text_det", "infer"), "cls": ("text_cls", "infer"), "rec": ("text_rec", "session")}


def directml_disponible() -> bool:
    """True si el onnxruntime instalado trae el proveedor DirectML (GPU)."""
    try:
        import onnxruntime as ort
    except ImportError:
        return False
    return DML_EP in ort.get_available_providers()


def _sesion_directml(model_path: str, gpu_id: Optional[int]):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # DirectML no admite memory pattern ni ejecución en paralelo.
    opts.enable_mem_pattern = False
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

    if gpu_id is None:
        dml_opts = {"performance_preference": "high_performance", "device_filter": "gpu"}
    else:
        dml_opts = {"device_id": str(gpu_id)}

    try:
        sesion = ort.InferenceSession(model_path, sess_options=opts, providers=[(DML_EP, dml_opts), "CPUExecutionProvider"])
    except Exception:
        if gpu_id is not None:
            raise
        # Versiones de onnxruntime-directml sin esas opciones: adaptador por defecto.
        sesion = ort.InferenceSession(model_path, sess_options=opts, providers=[DML_EP, "CPUExecutionProvider"])
    if sesion.get_providers()[0] != DML_EP:
        raise RuntimeError(f"DirectML no quedó activo para {model_path}")
    return sesion


def crear_motor(modulos_gpu: Iterable[str] = (), gpu_id: Optional[int] = None):
    """
    Crea una instancia de RapidOCR. Los modelos listados en `modulos_gpu`
    (det/cls/rec) se pasan a DirectML; el resto queda en CPU.
    """
    from rapidocr_onnxruntime import RapidOCR

    motor = RapidOCR()
    for modulo in modulos_gpu:
        componente, atributo = _SESIONES_RAPIDOCR[modulo]
        envoltorio = getattr(getattr(motor, componente), atributo)
        envoltorio.session = _sesion_directml(envoltorio.session._model_path, gpu_id)
    return motor


def _config_gpu_desde_entorno() -> tuple[list[str], Optional[int]]:
    dispositivo = os.environ.get("OCR_DISPOSITIVO", "auto").strip().lower()
    if dispositivo == "cpu":
        return [], None
    if not directml_disponible():
        if dispositivo == "gpu":
            logger.warning("OCR_DISPOSITIVO=gpu pero onnxruntime-directml no está instalado; se usa CPU.")
        return [], None

    modulos = [m.strip() for m in os.environ.get("OCR_GPU_MODULOS", "det,cls,rec").lower().split(",") if m.strip()]
    desconocidos = [m for m in modulos if m not in _SESIONES_RAPIDOCR]
    if desconocidos:
        logger.warning("OCR_GPU_MODULOS ignora valores desconocidos: %s", desconocidos)
    modulos = [m for m in modulos if m in _SESIONES_RAPIDOCR]

    gpu_id = os.environ.get("OCR_GPU_ID", "").strip()
    return modulos, int(gpu_id) if gpu_id else None


def get_ocr_engine():
    """Devuelve una instancia única (singleton) de RapidOCR."""
    global _ocr_engine, _ocr_dispositivo
    if _ocr_engine is None:
        logger.info("Cargando modelos de RapidOCR (solo ocurre una vez)...")
        modulos, gpu_id = _config_gpu_desde_entorno()
        if modulos:
            try:
                _ocr_engine = crear_motor(modulos, gpu_id)
                _ocr_dispositivo = f"gpu (DirectML: {', '.join(modulos)})"
            except Exception:
                logger.exception("No se pudo cargar RapidOCR en la GPU; se usa CPU.")
        if _ocr_engine is None:
            _ocr_engine = crear_motor()
            _ocr_dispositivo = "cpu"
        logger.info("RapidOCR listo en: %s", _ocr_dispositivo)
    return _ocr_engine


def dispositivo_ocr() -> str:
    """Dónde corre el OCR ("cpu" o "gpu (DirectML: ...)"). Carga el motor si hace falta."""
    get_ocr_engine()
    return _ocr_dispositivo


def _volver_a_cpu():
    global _ocr_engine, _ocr_dispositivo
    _ocr_engine = crear_motor()
    _ocr_dispositivo = "cpu"


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

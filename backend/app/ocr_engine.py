"""
Motor de OCR.

Responsable de:
  1. Convertir cada página de un PDF en una imagen (usando PyMuPDF, sin
     depender de Poppler/pdf2image).
  2. Ejecutar RapidOCR sobre cada imagen y devolver el texto reconocido,
     tanto como bloque de texto plano como lista de líneas con su posición
     (por si en el futuro se necesita ubicación espacial de los campos).

RapidOCR se carga una sola vez (patrón singleton) porque cargar los modelos
ONNX en cada llamada es costoso; para leer varias páginas a la vez hay un
pequeño grupo de motores (OCR_TRABAJADORES) que se prestan por página.

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
import queue
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional, Tuple

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
_ocr_engine_lock = threading.Lock()
_creacion_lock = threading.Lock()  # el ajuste temporal de RapidOCR no admite creaciones simultáneas

_ocr_dispositivo = "cpu"  # descripción legible de dónde corre el OCR
_gpu_descartada = False   # True tras un fallo de la GPU: desde ahí todo va a CPU

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
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")

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


def _crear_motor(modulos_gpu: Iterable[str] = (), gpu_id: Optional[int] = None):
    """
    Crea una instancia de RapidOCR. Sus hilos no hacen "spinning" (espera activa): con varias
    páginas a la vez, cada motor quemaba CPU esperando y estorbaba a los demás. Los modelos
    listados en `modulos_gpu` (det/cls/rec) se pasan a DirectML; el resto queda en CPU.
    """
    from rapidocr_onnxruntime import RapidOCR

    try:
        from rapidocr_onnxruntime.utils import infer_engine
    except ImportError:
        infer_engine = None

    with _creacion_lock:
        if infer_engine is None:
            motor = RapidOCR()
        else:
            clase = infer_engine.OrtInferSession
            original = clase.__dict__["_init_sess_opts"]  # el staticmethod tal cual, para restaurarlo

            def sin_spinning(config):
                opts = original.__func__(config)
                opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
                return opts

            clase._init_sess_opts = staticmethod(sin_spinning)
            try:
                motor = RapidOCR()
            finally:
                clase._init_sess_opts = original

    modulos_gpu = list(modulos_gpu)
    for modulo in modulos_gpu:
        componente, atributo = _SESIONES_RAPIDOCR[modulo]
        envoltorio = getattr(getattr(motor, componente), atributo)
        envoltorio.session = _sesion_directml(envoltorio.session._model_path, gpu_id)
    motor._en_gpu = bool(modulos_gpu)
    return motor


crear_motor = _crear_motor  # nombre público (benchmark_gpu.py)


def _config_gpu_desde_entorno() -> tuple[list[str], Optional[int]]:
    dispositivo = os.environ.get("OCR_DISPOSITIVO", "auto").strip().lower()
    if dispositivo == "cpu" or _gpu_descartada:
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


def _descartar_gpu():
    global _gpu_descartada, _ocr_dispositivo
    _gpu_descartada = True
    _ocr_dispositivo = "cpu"


def _motor_configurado():
    """Motor según la configuración: en GPU si corresponde y funciona, si no en CPU."""
    global _ocr_dispositivo
    modulos, gpu_id = _config_gpu_desde_entorno()
    if modulos:
        try:
            motor = _crear_motor(modulos, gpu_id)
            _ocr_dispositivo = f"gpu (DirectML: {', '.join(modulos)})"
            return motor
        except Exception:
            logger.exception("No se pudo cargar RapidOCR en la GPU; se usa CPU.")
            _descartar_gpu()
    return _crear_motor()


def _vigente(motor):
    """El mismo motor, o uno de CPU si era de GPU y la GPU quedó descartada."""
    if _gpu_descartada and getattr(motor, "_en_gpu", False):
        return _crear_motor()
    return motor


def get_ocr_engine():
    """Devuelve una instancia única (singleton) de RapidOCR; segura con varios hilos."""
    global _ocr_engine
    with _ocr_engine_lock:
        if _ocr_engine is None:
            logger.info("Cargando modelos de RapidOCR (solo ocurre una vez)...")
            _ocr_engine = _motor_configurado()
            logger.info("RapidOCR listo en: %s", _ocr_dispositivo)
        else:
            _ocr_engine = _vigente(_ocr_engine)
        return _ocr_engine


def dispositivo_ocr() -> str:
    """Dónde corre el OCR ("cpu" o "gpu (DirectML: ...)"). Carga el motor si hace falta."""
    get_ocr_engine()
    return _ocr_dispositivo


# Motores para leer páginas en paralelo: el primero es el singleton, el resto se crea al necesitarlo.
_motores_libres: queue.LifoQueue = queue.LifoQueue()
_motores_creados = 0
_motores_lock = threading.Lock()


@contextmanager
def _motor_prestado():
    """Presta un motor libre durante la lectura de una página (como mucho OCR_TRABAJADORES)."""
    global _motores_creados
    try:
        motor = _motores_libres.get_nowait()
    except queue.Empty:
        with _motores_lock:
            crear = _motores_creados < OCR_TRABAJADORES
            if crear:
                _motores_creados += 1
                primero = _motores_creados == 1
        if crear:
            motor = get_ocr_engine() if primero else _motor_configurado()
        else:
            motor = _motores_libres.get()
    motor = _vigente(motor)
    try:
        yield motor
    finally:
        _motores_libres.put(_vigente(motor))


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
        if not getattr(engine, "_en_gpu", False):
            raise
        logger.exception("Falló el OCR en la GPU; se cambia a CPU y se reintenta.")
        _descartar_gpu()
        engine = _crear_motor()
        result, _elapse = engine(img_array)

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

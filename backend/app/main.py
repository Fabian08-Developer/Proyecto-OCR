from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import matcher
from .auth import get_current_user
from .database import Comparacion, Usuario, get_db, get_storage_dir, init_db
from .document_grouper import group_pages_into_documents
from .excel_reader import parse_excel_report
from .ocr_engine import extract_pdf
from .routers import auth_router, historial_router
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ocr_system")

app = FastAPI(title="Sistema OCR de Verificación de Documentos", version="0.2.0")

# CORS: el frontend se sirve desde este mismo servidor, asi que NO hace falta ningun origen cruzado
# y por defecto no se habilita ninguno (antes se aceptaba cualquier origen con "*").
# Solo si se publica el frontend en otro dominio: OCR_CORS_ORIGINS="https://app.ejemplo.com,https://..."
_cors_origins = [o.strip() for o in os.environ.get("OCR_CORS_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def cabeceras_de_seguridad(request, call_next):
    """Cabeceras defensivas; las respuestas de la API (datos de cedulas) nunca se guardan en cache."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response

# ---------------------------------------------------------------------------
# Startup: inicializar la base de datos
# ---------------------------------------------------------------------------
@app.on_event("startup")
def startup_event():
    init_db()
    logger.info("Base de datos inicializada correctamente.")


# ---------------------------------------------------------------------------
# Registrar routers
# ---------------------------------------------------------------------------
app.include_router(auth_router.router)
app.include_router(historial_router.router)

MAX_PDF_MB = 60
MAX_EXCEL_MB = 10

# ---------------------------------------------------------------------------
# Control de OCR simultáneos
# ---------------------------------------------------------------------------
# El OCR usa todos los núcleos y cientos de MB: dos a la vez no terminan antes, solo se estorban.
# Se procesa uno por vez (por proceso); hasta MAX_OCR_EN_COLA esperan su turno y el resto recibe
# un 503 inmediato en lugar de quedarse colgado hasta que el navegador o el proxy se rindan.
MAX_OCR_SIMULTANEOS = 1
MAX_OCR_EN_COLA = 2

_ocr_slots = threading.BoundedSemaphore(MAX_OCR_SIMULTANEOS)
_ocr_count_lock = threading.Lock()
_ocr_en_proceso = 0          # ejecutando + esperando turno


@contextmanager
def _ocr_turn():
    """Reserva un turno de OCR; HTTP 503 si ya hay demasiados documentos en proceso."""
    global _ocr_en_proceso
    with _ocr_count_lock:
        if _ocr_en_proceso >= MAX_OCR_SIMULTANEOS + MAX_OCR_EN_COLA:
            raise HTTPException(
                503,
                "El servidor está procesando otros documentos. Intenta de nuevo en unos minutos.",
                headers={"Retry-After": "60"},
            )
        _ocr_en_proceso += 1
    try:
        with _ocr_slots:
            yield
    finally:
        with _ocr_count_lock:
            _ocr_en_proceso -= 1


def _validate_pdf(pdf_bytes: bytes) -> int:
    """Comprueba el PDF antes de ocupar un turno de OCR; HTTP 422 si no es utilizable."""
    try:
        return count_pdf_pages(pdf_bytes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _read_upload(file: UploadFile, max_mb: int) -> bytes:
    content = file.file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > max_mb:
        raise HTTPException(413, f"El archivo '{file.filename}' supera el límite de {max_mb} MB.")
    return content


# ---------------------------------------------------------------------------
# Endpoints de utilidad
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "ocr_dispositivo": dispositivo_ocr()}


# Los endpoints que leen archivos u ocupan CPU son "def" (no "async def"): FastAPI los ejecuta en un
# hilo aparte. Con "async def" el OCR bloqueaba el bucle de eventos y todo el servidor (login,
# interfaz, /api/health) dejaba de responder mientras duraba.
@app.post("/api/extract-pdf")
def api_extract_pdf(
    pdf: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
):
    """Extrae y estructura los documentos de identidad presentes en el PDF."""
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "El archivo debe ser un PDF.")

    pdf_bytes = _read_upload(pdf, MAX_PDF_MB)
    _validate_pdf(pdf_bytes)

    with _ocr_turn():
        t0 = time.time()
        try:
            pages = extract_pdf(pdf_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fallo al ejecutar OCR sobre el PDF")
            raise HTTPException(500, f"Error ejecutando OCR sobre el PDF: {exc}") from exc

    documents = group_pages_into_documents(pages)
    logger.info("OCR de %s páginas completado en %.1fs -> %s documentos",
                len(pages), time.time() - t0, len(documents))

    return {
        "total_paginas": len(pages),
        "total_documentos": len(documents),
        "documentos": [d.to_dict() for d in documents],
    }


@app.post("/api/parse-excel")
def api_parse_excel(
    excel: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
):
    """Lee el reporte de inscripción en Excel y devuelve la lista de participantes."""
    if not excel.filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(400, "El archivo debe ser .xls o .xlsx.")

    excel_bytes = _read_upload(excel, MAX_EXCEL_MB)

    try:
        result = parse_excel_report_detailed(excel_bytes, excel.filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    participants = result.participantes
    return {
        "total_participantes": len(participants),
        "participantes": [p.to_dict() for p in participants],
        "advertencias": result.advertencias,
    }


# ---------------------------------------------------------------------------
# Endpoint principal de comparacion (con autenticacion + guardado en BD)
# ---------------------------------------------------------------------------
@app.post("/api/comparar")
def api_comparar(
    pdf: UploadFile = File(...),
    excel: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Recibe el PDF de documentos de identidad y el Excel de inscripcion,
    ejecuta OCR + parsing + comparacion, guarda en historial y devuelve
    el resultado consolidado.
    """
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "El primer archivo debe ser un PDF.")
    if not excel.filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(400, "El segundo archivo debe ser .xls o .xlsx.")

    pdf_bytes = _read_upload(pdf, MAX_PDF_MB)
    excel_bytes = _read_upload(excel, MAX_EXCEL_MB)

    # El Excel se valida PRIMERO: leerlo es instantáneo y el OCR puede tardar minutos,
    # así que un Excel inválido se rechaza antes de gastar ese tiempo.
    try:
        excel_result = parse_excel_report_detailed(excel_bytes, excel.filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    participants = excel_result.participantes
    for aviso in excel_result.advertencias:
        logger.warning("Excel '%s': %s", excel.filename, aviso)

    n_paginas = _validate_pdf(pdf_bytes)
    logger.info("PDF '%s' de %s página(s); esperando turno de OCR", pdf.filename, n_paginas)

    with _ocr_turn():
        t0 = time.time()          # el tiempo en cola no cuenta como tiempo de procesamiento
        try:
            pages = extract_pdf(pdf_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fallo al ejecutar OCR sobre el PDF")
            raise HTTPException(500, f"Error ejecutando OCR sobre el PDF: {exc}") from exc

    documents = group_pages_into_documents(pages)
    logger.info("OCR de %s páginas completado en %.1fs -> %s documentos",
                len(pages), time.time() - t0, len(documents))

    comparison = matcher.compare(documents, participants)
    # Se guarda dentro del resultado para que el historial y el informe también las muestren
    comparison["advertencias_excel"] = excel_result.advertencias
    elapsed = round(time.time() - t0, 1)

    # -------------------------------------------------------------------
    # Guardar archivos y resultado en la base de datos
    # -------------------------------------------------------------------
    comp_id = str(uuid.uuid4())
    storage = get_storage_dir()

    # Guardar archivos con nombre unico
    pdf_filename = f"{comp_id}_doc.pdf"
    excel_ext = Path(excel.filename).suffix
    excel_filename = f"{comp_id}_inscripcion{excel_ext}"

    pdf_path = storage / pdf_filename
    excel_path = storage / excel_filename

    pdf_path.write_bytes(pdf_bytes)
    excel_path.write_bytes(excel_bytes)

    resumen = comparison.get("resumen", {})

    comp = Comparacion(
        id=comp_id,
        usuario_id=current_user.id,
        nombre_pdf=pdf.filename,
        nombre_excel=excel.filename,
        ruta_pdf=str(pdf_path),
        ruta_excel=str(excel_path),
        total_excel=resumen.get("total_excel", 0),
        total_pdf_documentos=resumen.get("total_pdf_documentos", 0),
        coinciden=resumen.get("coinciden", 0),
        nombre_no_coincide=resumen.get("nombre_no_coincide", 0),
        documento_incompleto=resumen.get("documento_incompleto", 0),
        no_encontrado_en_pdf=resumen.get("no_encontrado_en_pdf", 0),
        sobrantes_en_pdf=resumen.get("sobrantes_en_pdf", 0),
        documentos_sin_numero=resumen.get("documentos_sin_numero_detectado", 0),
        tiempo_segundos=elapsed,
    )
    comp.set_resultado(comparison)

    db.add(comp)
    db.commit()
    db.refresh(comp)

    logger.info("Comparacion guardada en historial: %s", comp_id)

    return {
        "comparacion_id": comp_id,
        "meta": {
            "total_paginas_pdf": len(pages),
            "total_documentos_ocr": len(documents),
            "total_participantes_excel": len(participants),
            "tiempo_segundos": elapsed,
        },
        **comparison,
    }


# ---------------------------------------------------------------------------
# Servir el frontend estatico (debe ir AL FINAL)
# ---------------------------------------------------------------------------
_frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")

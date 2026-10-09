from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import matcher
from .auth import get_current_user
from .database import Comparacion, Usuario, get_db, get_storage_dir, init_db
from .document_grouper import group_pages_into_documents
from .excel_reader import parse_excel_report
from .ocr_engine import dispositivo_ocr, extract_pdf
from .routers import auth_router, historial_router
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ocr_system")

app = FastAPI(title="Sistema OCR de Verificación de Documentos", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.post("/api/extract-pdf")
async def api_extract_pdf(
    pdf: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
):
    """Extrae y estructura los documentos de identidad presentes en el PDF."""
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "El archivo debe ser un PDF.")

    pdf_bytes = _read_upload(pdf, MAX_PDF_MB)

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
async def api_parse_excel(
    excel: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
):
    """Lee el reporte de inscripción en Excel y devuelve la lista de participantes."""
    if not excel.filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(400, "El archivo debe ser .xls o .xlsx.")

    excel_bytes = _read_upload(excel, MAX_EXCEL_MB)

    try:
        participants = parse_excel_report(excel_bytes, excel.filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fallo al leer el Excel")
        raise HTTPException(500, f"Error leyendo el Excel: {exc}") from exc

    return {"total_participantes": len(participants), "participantes": [p.to_dict() for p in participants]}


# ---------------------------------------------------------------------------
# Endpoint principal de comparacion (con autenticacion + guardado en BD)
# ---------------------------------------------------------------------------
@app.post("/api/comparar")
async def api_comparar(
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

    t0 = time.time()
    try:
        pages = extract_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fallo al ejecutar OCR sobre el PDF")
        raise HTTPException(500, f"Error ejecutando OCR sobre el PDF: {exc}") from exc

    documents = group_pages_into_documents(pages)
    logger.info("OCR de %s páginas completado en %.1fs -> %s documentos",
                len(pages), time.time() - t0, len(documents))

    try:
        participants = parse_excel_report(excel_bytes, excel.filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    comparison = matcher.compare(documents, participants)
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

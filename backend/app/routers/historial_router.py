"""
routers/historial_router.py - Endpoints del historial de comparaciones.

Endpoints:
  GET    /api/historial              - Lista de comparaciones del usuario autenticado.
  GET    /api/historial/{id}         - Detalle completo de una comparacion.
  GET    /api/historial/{id}/reporte - Genera y descarga el informe PDF.
  GET    /api/historial/{id}/pdf     - Descarga el PDF original.
  GET    /api/historial/{id}/excel   - Descarga el Excel original.
  DELETE /api/historial/{id}         - Elimina un registro.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import Comparacion, Usuario, get_db, ruta_en_storage
from ..report_generator import generar_reporte_pdf

logger = logging.getLogger("ocr_system")

router = APIRouter(prefix="/api/historial", tags=["historial"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ResumenComparacion(BaseModel):
    id: str
    fecha_comparacion: str
    nombre_pdf: str
    nombre_excel: str
    total_excel: int
    total_pdf_documentos: int
    coinciden: int
    nombre_no_coincide: int
    documento_incompleto: int
    no_encontrado_en_pdf: int
    sobrantes_en_pdf: int
    documentos_sin_numero: int
    tiempo_segundos: float | None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_comp_or_404(comp_id: str, user: Usuario, db: Session) -> Comparacion:
    comp = (
        db.query(Comparacion)
        .filter(Comparacion.id == comp_id, Comparacion.usuario_id == user.id)
        .first()
    )
    if not comp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Comparacion no encontrada.")
    return comp


def _borrar_archivo(ruta: str | None) -> bool:
    """Borra un archivo guardado en storage/. Devuelve True si existia y se borro."""
    path = ruta_en_storage(ruta)
    if path is None:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.exception("No se pudo borrar el archivo %s", path)
        return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ResumenComparacion])
def listar_historial(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
):
    """Lista las comparaciones del usuario en orden cronologico inverso."""
    rows = (
        db.query(Comparacion)
        .filter(Comparacion.usuario_id == current_user.id)
        .order_by(Comparacion.fecha_comparacion.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        ResumenComparacion(
            id=r.id,
            fecha_comparacion=r.fecha_comparacion.strftime("%Y-%m-%d %H:%M:%S"),
            nombre_pdf=r.nombre_pdf,
            nombre_excel=r.nombre_excel,
            total_excel=r.total_excel,
            total_pdf_documentos=r.total_pdf_documentos,
            coinciden=r.coinciden,
            nombre_no_coincide=r.nombre_no_coincide,
            documento_incompleto=r.documento_incompleto,
            no_encontrado_en_pdf=r.no_encontrado_en_pdf,
            sobrantes_en_pdf=r.sobrantes_en_pdf,
            documentos_sin_numero=r.documentos_sin_numero,
            tiempo_segundos=r.tiempo_segundos,
        )
        for r in rows
    ]


@router.get("/{comp_id}/detalle")
def obtener_detalle(
    comp_id: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retorna el resultado JSON completo de una comparacion."""
    comp = _get_comp_or_404(comp_id, current_user, db)
    return {
        "id": comp.id,
        "fecha_comparacion": comp.fecha_comparacion.strftime("%Y-%m-%d %H:%M:%S"),
        "nombre_pdf": comp.nombre_pdf,
        "nombre_excel": comp.nombre_excel,
        "tiempo_segundos": comp.tiempo_segundos,
        **comp.get_resultado(),
    }


@router.get("/{comp_id}/reporte")
def descargar_reporte(
    comp_id: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genera un informe PDF y lo devuelve como descarga."""
    comp = _get_comp_or_404(comp_id, current_user, db)
    resultado = comp.get_resultado()

    try:
        pdf_bytes = generar_reporte_pdf(
            nombre_pdf=comp.nombre_pdf,
            nombre_excel=comp.nombre_excel,
            resultado=resultado,
            usuario=current_user.nombre or current_user.username,
            fecha_comparacion=comp.fecha_comparacion,
            tiempo_segundos=comp.tiempo_segundos,
        )
    except Exception as exc:
        logger.exception("Error generando el reporte PDF")
        raise HTTPException(500, f"Error generando el informe: {exc}") from exc

    safe_name = comp.nombre_pdf.replace(" ", "_").replace("/", "-")[:60]
    filename = f"informe_{safe_name}_{comp.fecha_comparacion.strftime('%Y%m%d_%H%M')}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{comp_id}/pdf")
def descargar_pdf_original(
    comp_id: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Descarga el PDF original que se uso en la comparacion."""
    comp = _get_comp_or_404(comp_id, current_user, db)
    if not comp.ruta_pdf:
        raise HTTPException(404, "El archivo PDF original no está almacenado.")
    path = ruta_en_storage(comp.ruta_pdf)
    if path is None or not path.exists():
        raise HTTPException(404, "El archivo PDF original no se encuentra en el servidor.")
    return FileResponse(
        path=str(path),
        filename=comp.nombre_pdf,
        media_type="application/pdf",
    )


@router.get("/{comp_id}/excel")
def descargar_excel_original(
    comp_id: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Descarga el Excel original que se uso en la comparacion."""
    comp = _get_comp_or_404(comp_id, current_user, db)
    if not comp.ruta_excel:
        raise HTTPException(404, "El archivo Excel original no está almacenado.")
    path = ruta_en_storage(comp.ruta_excel)
    if path is None or not path.exists():
        raise HTTPException(404, "El archivo Excel original no se encuentra en el servidor.")
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
             if comp.nombre_excel.endswith(".xlsx") else "application/vnd.ms-excel")
    return FileResponse(
        path=str(path),
        filename=comp.nombre_excel,
        media_type=media,
    )


@router.delete("/{comp_id}", status_code=204)
def eliminar_comparacion(
    comp_id: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Elimina una comparacion del historial y los archivos originales (PDF y Excel) guardados en disco."""
    comp = _get_comp_or_404(comp_id, current_user, db)
    ruta_pdf, ruta_excel = comp.ruta_pdf, comp.ruta_excel
    db.delete(comp)
    db.commit()
    # Los archivos se borran despues de confirmar la baja en la base de datos: si algo fallara
    # antes, el registro seguiria intacto con sus archivos. Contienen cedulas escaneadas.
    _borrar_archivo(ruta_pdf)
    _borrar_archivo(ruta_excel)

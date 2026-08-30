"""
Lee el "Reporte de Inscripción" en Excel.

El archivo típico (exportado de plataformas tipo SENA) trae unas filas de
encabezado libre (Código de Ficha, Programa de Formación) y luego una
tabla con columnas: Identificación | Nombre | Estado.

Este módulo NO asume una posición fija de la tabla: busca automáticamente
la fila que contiene los encabezados "Identificación" y "Nombre" y lee
desde ahí hacia abajo, hasta la primera fila totalmente vacía.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from .field_parser import normalize_for_match


@dataclass
class ExcelParticipant:
    row_index: int
    tipo_documento: Optional[str]
    numero_documento: str
    nombre_reportado: str
    estado: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "fila_excel": self.row_index,
            "tipo_documento": self.tipo_documento,
            "numero_documento": self.numero_documento,
            "nombre_reportado": self.nombre_reportado,
            "estado": self.estado,
        }


ID_CELL_RE = re.compile(r"([A-Z]{1,3})?\s*-?\s*(\d{5,12})")


def _split_identificacion(raw: str) -> tuple[Optional[str], Optional[str]]:
    """'CC - 1110487315' -> ('CC', '1110487315'). También soporta '1110487315' solo."""
    if raw is None:
        return None, None
    raw = str(raw).strip()
    m = ID_CELL_RE.match(raw)
    if not m:
        return None, None
    tipo, numero = m.groups()
    return (tipo or None, numero)


def _read_any_excel(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Lee .xls o .xlsx sin encabezados (header=None) para poder buscar
    la tabla manualmente en cualquier posición."""
    buffer = io.BytesIO(file_bytes)
    lower = filename.lower()

    if lower.endswith(".xls"):
        engine = "xlrd"
    else:
        engine = "openpyxl"

    return pd.read_excel(buffer, header=None, engine=engine, dtype=str)


def _find_header_row(df: pd.DataFrame) -> Optional[int]:
    for i in range(len(df)):
        row_values = [normalize_for_match(str(v)) for v in df.iloc[i].tolist() if pd.notna(v)]
        has_id = any("identificacion" in v for v in row_values)
        has_name = any(v == "nombre" or v.startswith("nombre") for v in row_values)
        if has_id and has_name:
            return i
    return None


def parse_excel_report(file_bytes: bytes, filename: str) -> List[ExcelParticipant]:
    df = _read_any_excel(file_bytes, filename)

    header_row = _find_header_row(df)
    if header_row is None:
        raise ValueError(
            "No se encontró una fila de encabezado con columnas "
            "'Identificación' y 'Nombre' en el Excel."
        )

    header_cells = [normalize_for_match(str(v)) if pd.notna(v) else "" for v in df.iloc[header_row].tolist()]

    col_id = next((i for i, v in enumerate(header_cells) if "identificacion" in v), None)
    col_nombre = next((i for i, v in enumerate(header_cells) if v.startswith("nombre")), None)
    col_estado = next((i for i, v in enumerate(header_cells) if "estado" in v), None)

    if col_id is None or col_nombre is None:
        raise ValueError("No se pudieron ubicar las columnas de Identificación y Nombre.")

    participants: List[ExcelParticipant] = []
    for i in range(header_row + 1, len(df)):
        row = df.iloc[i]
        raw_id = row[col_id] if col_id < len(row) else None
        raw_nombre = row[col_nombre] if col_nombre < len(row) else None

        if pd.isna(raw_id) and pd.isna(raw_nombre):
            # fila vacía -> se asume fin de la tabla
            if participants:
                break
            continue

        if pd.isna(raw_id) or pd.isna(raw_nombre):
            continue

        tipo_doc, numero_doc = _split_identificacion(str(raw_id))
        if not numero_doc:
            continue

        estado = None
        if col_estado is not None and col_estado < len(row) and pd.notna(row[col_estado]):
            estado = str(row[col_estado]).strip()

        participants.append(
            ExcelParticipant(
                row_index=i + 1,  # 1-indexado, más amigable para el usuario
                tipo_documento=tipo_doc,
                numero_documento=numero_doc,
                nombre_reportado=str(raw_nombre).strip(),
                estado=estado,
            )
        )

    return participants

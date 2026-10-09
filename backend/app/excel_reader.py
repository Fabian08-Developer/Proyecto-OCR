"""
Lee el "Reporte de Inscripción" en Excel.

El archivo típico (exportado de plataformas tipo SENA) trae unas filas de
encabezado libre (Código de Ficha, Programa de Formación) y luego una
tabla con columnas: Identificación | Nombre | Estado. Suele venir en dos hojas:
"Inscritos Primera Opción" e "Inscritos Segunda Opción".

Este módulo NO asume una posición fija de la tabla: busca en CADA hoja la fila
que contiene los encabezados "Identificación" y "Nombre" y lee desde ahí hacia abajo.

Principio: nada se descarta en silencio. Toda fila que no se pueda leer, y todo dato
dudoso, genera una advertencia que se devuelve junto a los participantes y se muestra
al usuario; así un participante omitido nunca pasa por "verificado".
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .field_parser import normalize_for_match

MIN_DIGITOS_ID = 5
MAX_DIGITOS_LEIBLES_OCR = 12      # el OCR solo lee números de 6 a 12 dígitos
MAX_ADVERTENCIAS = 25

_XLSX_MAGIC = b"PK\x03\x04"
_XLS_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


@dataclass
class ExcelParticipant:
    row_index: int
    tipo_documento: Optional[str]
    numero_documento: str
    nombre_reportado: str
    estado: Optional[str] = None
    hoja: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "fila_excel": self.row_index,
            "tipo_documento": self.tipo_documento,
            "numero_documento": self.numero_documento,
            "nombre_reportado": self.nombre_reportado,
            "estado": self.estado,
            "hoja": self.hoja,
        }


@dataclass
class ExcelParseResult:
    participantes: List[ExcelParticipant] = field(default_factory=list)
    advertencias: List[str] = field(default_factory=list)


# "CC - 1110487315", "CC-1.110.487.315", "cc 1110487315", "1110487315", "PEP - 123456789012345"
_ID_RE = re.compile(r"^\s*(?:([A-Za-z]{1,5})\s*[-:.]?\s*)?(\d[\d.,\s]*)\s*$")


def _normalize_numeric_cell(raw: str) -> str:
    """Celdas numéricas: '1110487315.0' o '1.110487315E+09' -> '1110487315'. El resto no se toca."""
    s = raw.strip()
    if re.fullmatch(r"\d+\.0+", s) or re.fullmatch(r"\d+(?:\.\d+)?[eE][+-]?\d+", s):
        try:
            d = Decimal(s)
            if d == d.to_integral_value():
                return str(int(d))
        except InvalidOperation:
            pass
    return s


def _split_identificacion(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    'CC - 1110487315' -> ('CC', '1110487315'). Soporta también '1110487315' solo,
    separadores de miles ('1.110.487.315'), tipo en minúsculas y celdas numéricas.
    Devuelve (None, None) si no es una identificación reconocible.
    """
    if raw is None:
        return None, None
    m = _ID_RE.match(_normalize_numeric_cell(str(raw)))
    if not m:
        return None, None
    tipo, numero = m.groups()
    digitos = re.sub(r"\D", "", numero)
    if len(digitos) < MIN_DIGITOS_ID:
        return None, None
    return (tipo.upper() if tipo else None), digitos


def _read_all_sheets(file_bytes: bytes, filename: str) -> Dict[str, pd.DataFrame]:
    """
    Lee TODAS las hojas sin encabezados (header=None). El motor se elige por el contenido
    del archivo y no por su extensión: un .xlsx renombrado a .xls (o al revés) se lee igual.
    """
    if file_bytes[:4] == _XLSX_MAGIC:
        engine = "openpyxl"
    elif file_bytes[:8] == _XLS_MAGIC:
        engine = "xlrd"
    else:
        engine = "xlrd" if filename.lower().endswith(".xls") else "openpyxl"

    try:
        return pd.read_excel(io.BytesIO(file_bytes), header=None, engine=engine, dtype=str, sheet_name=None)
    except Exception as exc:  # noqa: BLE001 - xlrd/openpyxl/zipfile lanzan tipos distintos
        raise ValueError(
            f"No se pudo leer el archivo Excel '{filename}': está dañado o no es un .xls/.xlsx válido "
            f"({type(exc).__name__})."
        ) from exc


def _find_header_row(df: pd.DataFrame) -> Optional[int]:
    for i in range(len(df)):
        row_values = [normalize_for_match(str(v)) for v in df.iloc[i].tolist() if pd.notna(v)]
        has_id = any("identificacion" in v for v in row_values)
        has_name = any(v == "nombre" or v.startswith("nombre") for v in row_values)
        if has_id and has_name:
            return i
    return None


def _cell(row: pd.Series, idx: Optional[int]) -> str:
    if idx is None or idx >= len(row) or pd.isna(row.iloc[idx]):
        return ""
    return str(row.iloc[idx]).strip()


def _looks_like_footer(raw_id: str, raw_nombre: str) -> bool:
    """Filas como 'Total | 31' al pie de la tabla: no son participantes ni merecen advertencia."""
    return not re.search(r"\d", raw_id) and (not raw_nombre or re.fullmatch(r"[\d.,\s]+", raw_nombre) is not None)


def _parse_sheet(hoja: str, df: pd.DataFrame, header_row: int, advertencias: List[str]) -> List[ExcelParticipant]:
    header_cells = [normalize_for_match(str(v)) if pd.notna(v) else "" for v in df.iloc[header_row].tolist()]

    col_id = next((i for i, v in enumerate(header_cells) if "identificacion" in v), None)
    col_nombre = next((i for i, v in enumerate(header_cells) if v.startswith("nombre")), None)
    col_apellidos = next((i for i, v in enumerate(header_cells) if "apellido" in v), None)
    col_estado = next((i for i, v in enumerate(header_cells) if "estado" in v), None)
    if col_apellidos == col_nombre:
        col_apellidos = None

    participantes: List[ExcelParticipant] = []
    vistos: Dict[str, int] = {}

    for i in range(header_row + 1, len(df)):
        row = df.iloc[i]
        fila = i + 1                                   # 1-indexado, igual que en Excel
        raw_id = _cell(row, col_id)
        nombre = _cell(row, col_nombre)
        if col_apellidos is not None:
            nombre = f"{nombre} {_cell(row, col_apellidos)}".strip()

        if not raw_id and not nombre:
            continue                                   # fila vacía: se sigue leyendo, no termina la tabla
        if _looks_like_footer(raw_id, nombre):
            continue
        if not raw_id:
            advertencias.append(
                f"Hoja '{hoja}', fila {fila}: '{nombre}' no tiene identificación; "
                f"no se pudo verificar y NO aparece en los resultados."
            )
            continue

        tipo, numero = _split_identificacion(raw_id)
        if not numero:
            advertencias.append(
                f"Hoja '{hoja}', fila {fila}: la identificación '{raw_id}' no es válida "
                f"(se esperan al menos {MIN_DIGITOS_ID} dígitos); '{nombre}' NO aparece en los resultados."
            )
            continue

        if len(numero) > MAX_DIGITOS_LEIBLES_OCR:
            advertencias.append(
                f"Hoja '{hoja}', fila {fila}: el documento {numero} tiene {len(numero)} dígitos "
                f"(tipo {tipo or 'sin tipo'}); el OCR solo lee de 6 a {MAX_DIGITOS_LEIBLES_OCR}, "
                f"así que probablemente aparecerá como no encontrado."
            )
        if not nombre:
            advertencias.append(
                f"Hoja '{hoja}', fila {fila}: el documento {numero} no tiene nombre en el Excel; "
                f"solo se podrá validar el número."
            )
        if numero in vistos:
            advertencias.append(
                f"Hoja '{hoja}', fila {fila}: el documento {numero} está repetido (también en la fila {vistos[numero]})."
            )
        vistos.setdefault(numero, fila)

        participantes.append(
            ExcelParticipant(
                row_index=fila,
                tipo_documento=tipo,
                numero_documento=numero,
                nombre_reportado=nombre,
                estado=_cell(row, col_estado) or None,
                hoja=hoja,
            )
        )
    return participantes


def _limit_warnings(advertencias: List[str]) -> List[str]:
    if len(advertencias) <= MAX_ADVERTENCIAS:
        return advertencias
    resto = len(advertencias) - MAX_ADVERTENCIAS
    return advertencias[:MAX_ADVERTENCIAS] + [f"... y {resto} advertencia(s) más."]


def parse_excel_report_detailed(file_bytes: bytes, filename: str) -> ExcelParseResult:
    """
    Lee todas las hojas del libro y devuelve los participantes junto con las advertencias
    de todo lo que no se pudo leer. Lanza ValueError con un mensaje claro si el archivo
    no se puede leer, no tiene la tabla esperada o no trae ningún participante.
    """
    hojas = _read_all_sheets(file_bytes, filename)

    advertencias: List[str] = []
    participantes: List[ExcelParticipant] = []
    hojas_sin_tabla: List[str] = []
    alguna_tabla = False

    for nombre_hoja, df in hojas.items():
        header_row = _find_header_row(df)
        if header_row is None:
            if len(df.dropna(how="all")) > 0:
                hojas_sin_tabla.append(str(nombre_hoja))
            continue
        alguna_tabla = True
        participantes.extend(_parse_sheet(str(nombre_hoja), df, header_row, advertencias))

    if not alguna_tabla:
        raise ValueError(
            "No se encontró una fila de encabezado con columnas "
            "'Identificación' y 'Nombre' en el Excel."
        )
    for h in hojas_sin_tabla:
        advertencias.append(f"Hoja '{h}' ignorada: no tiene las columnas 'Identificación' y 'Nombre'.")

    if not participantes:
        detalle = f" Advertencias: {' | '.join(advertencias[:3])}" if advertencias else ""
        raise ValueError(
            "El Excel tiene la tabla de inscritos pero ninguna fila con una identificación válida; "
            "no hay participantes que verificar." + detalle
        )

    return ExcelParseResult(participantes=participantes, advertencias=_limit_warnings(advertencias))


def parse_excel_report(file_bytes: bytes, filename: str) -> List[ExcelParticipant]:
    """Compatibilidad: devuelve solo los participantes (usa parse_excel_report_detailed para las advertencias)."""
    return parse_excel_report_detailed(file_bytes, filename).participantes

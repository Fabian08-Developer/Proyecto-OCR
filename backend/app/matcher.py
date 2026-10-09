"""
Compara los documentos extraídos por OCR contra los registros del Excel.

Reglas de emparejamiento:
  1. Clave primaria: número de documento (limpio de puntos/espacios).
  2. Tolerancia de ceros: se busca también con/sin ceros iniciales para
     manejar casos donde el reverso agrega ceros (0017660162 = 17660162).
  3. Si los números coinciden, se valida el nombre con una similitud de
     tokens (orden-independiente, por si el Excel trae "Nombre Apellido"
     y la cédula "Apellido / Nombre" por separado).

Estados posibles por participante del Excel:
  - "coincide"              -> ID encontrado en el PDF y nombre coincide
  - "nombre_no_coincide"    -> ID encontrado en el PDF pero el nombre difiere
  - "no_encontrado_en_pdf"  -> el ID del Excel no aparece en ningún documento OCR
  - "documento_incompleto"  -> se encontró el ID pero al documento OCR le
                                faltan campos clave (fecha nacimiento, etc.)

Además se listan los documentos del PDF que no corresponden a ningún
registro del Excel ("sobrantes_en_pdf"), típicamente por errores de OCR
en el número de documento o personas que no estaban en la lista.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Dict, List, Optional

from .document_grouper import DocumentRecord
from .excel_reader import ExcelParticipant
from .field_parser import digit_differences, normalize_for_match

NAME_MATCH_THRESHOLD = 0.72

# Tope de la similitud mostrada cuando los nombres no son idénticos (ver _name_similarity)
MAX_SIMILARITY_IF_DIFFERENT = 0.99

# Un número del PDF que difiere en pocos dígitos de uno del Excel y trae un nombre similar
# suele ser un dígito mal leído por el OCR: se avisa en las observaciones (no se empareja solo).
MAX_DIGIT_DIFF_HINT = 2


def _token_is_contained(token: str, candidates: set) -> bool:
    """True si el token está en candidates o algún candidate contiene al token (OCR parcial)."""
    if token in candidates:
        return True
    # Coincidencia parcial: el token de Excel está dentro del token OCR o viceversa
    for c in candidates:
        if len(token) >= 4 and len(c) >= 4:
            if token in c or c in token:
                return True
            # Similitud de caracteres para errores OCR letra a letra (ej: ESCALANTE vs ESCALA)
            ratio = difflib.SequenceMatcher(None, token, c).ratio()
            if ratio >= 0.80:
                return True
    return False


def _name_similarity(name_a: Optional[str], name_b: Optional[str]) -> float:
    """
    Calcula similitud entre nombre del Excel (name_a) y nombre del OCR (name_b).

    Estrategia:
    1. Similitud Jaccard clásica de tokens.
    2. Similitud de secuencia global.
    3. BOOST: si los apellidos (últimos tokens del Excel) coinciden en el OCR,
       se incrementa la puntuación porque el OCR frecuentemente corrompe los
       nombres de pila pero preserva los apellidos.
    4. Coincidencia parcial de tokens para ruido OCR letra a letra.
    """
    if not name_a or not name_b:
        return 0.0
    tokens_a = normalize_for_match(name_a).split()
    tokens_b = normalize_for_match(name_b).split()
    if not tokens_a or not tokens_b:
        return 0.0

    set_a = set(tokens_a)
    set_b = set(tokens_b)

    # 1. Jaccard clásico sobre tokens exactos
    intersection = set_a & set_b
    union = set_a | set_b
    jaccard = len(intersection) / len(union) if union else 0.0

    # 2. SequenceMatcher global
    seq_ratio = difflib.SequenceMatcher(
        None, normalize_for_match(name_a), normalize_for_match(name_b)
    ).ratio()

    # 3. Coincidencia parcial token a token (tolerante a OCR letra a letra)
    partial_matches = sum(1 for t in set_a if _token_is_contained(t, set_b))
    partial_ratio = partial_matches / len(set_a) if set_a else 0.0

    # 4. BOOST por apellidos: los últimos 2 tokens del Excel suelen ser los apellidos.
    #    Si ambos aparecen (exacto o parcialmente) en el nombre OCR → boost significativo.
    apellidos_excel = tokens_a[-2:] if len(tokens_a) >= 2 else tokens_a[-1:]
    apellidos_found = sum(1 for t in apellidos_excel if _token_is_contained(t, set_b))
    apellidos_ratio = apellidos_found / len(apellidos_excel) if apellidos_excel else 0.0

    # Score base = mejor de jaccard, seq y partial
    base_score = max(jaccard, seq_ratio, partial_ratio)

    # Boost si ambos apellidos coinciden: el score sube proporcionalmente
    # (máx boost de +0.20 cuando ambos apellidos están presentes)
    if apellidos_ratio >= 1.0:
        boosted = min(1.0, base_score + 0.20)
    elif apellidos_ratio >= 0.5:
        boosted = min(1.0, base_score + 0.10)
    else:
        boosted = base_score

    # El 100 % es solo para nombres idénticos: con una letra distinta (ALEXANDEA vs ALEXANDER) el
    # boost y la coincidencia parcial no pueden aparentar una lectura perfecta.
    if not names_are_identical(name_a, name_b):
        boosted = min(boosted, MAX_SIMILARITY_IF_DIFFERENT)

    return boosted


def names_are_identical(name_a: Optional[str], name_b: Optional[str]) -> bool:
    """
    True si ambos nombres tienen exactamente las mismas letras, sin importar tildes, mayúsculas,
    espacios (el OCR pega o separa palabras) ni el orden de las palabras.
    """
    tokens_a = normalize_for_match(name_a or "").split()
    tokens_b = normalize_for_match(name_b or "").split()
    if not tokens_a or not tokens_b:
        return False
    return "".join(tokens_a) == "".join(tokens_b) or sorted(tokens_a) == sorted(tokens_b)


def _normalize_id(num: Optional[str]) -> Optional[str]:
    """Normaliza un número de documento quitando ceros iniciales para comparación."""
    if not num:
        return None
    return num.lstrip("0") or num


@dataclass
class MatchResult:
    numero_documento: str
    estado: str
    excel: Optional[dict]
    ocr: Optional[dict]
    similitud_nombre: Optional[float] = None
    observaciones: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "numero_documento": self.numero_documento,
            "estado": self.estado,
            "excel": self.excel,
            "ocr": self.ocr,
            "similitud_nombre": round(self.similitud_nombre, 3) if self.similitud_nombre is not None else None,
            "observaciones": self.observaciones,
        }


REQUIRED_FIELDS_FOR_COMPLETE = ["fecha_nacimiento", "apellidos", "nombres"]


def _add_misread_hints(
    results: List[MatchResult], participants: List[ExcelParticipant], sobrante_docs: List[DocumentRecord]
) -> None:
    """
    A cada participante "no encontrado" le agrega una observación si algún documento sobrante del
    PDF tiene un número casi idéntico (1-2 dígitos de diferencia) y un nombre similar.
    No cambia el estado: la decisión final es de quien revisa.
    """
    by_number = {p.numero_documento: p for p in participants}
    for r in results:
        if r.estado != "no_encontrado_en_pdf":
            continue
        participante = by_number.get(r.numero_documento)
        if participante is None:
            continue
        best = None  # (similitud, diferencias, doc)
        for doc in sobrante_docs:
            diff = digit_differences(participante.numero_documento, doc.numero_documento)
            if diff is None or not 1 <= diff <= MAX_DIGIT_DIFF_HINT:
                continue
            sim = _name_similarity(participante.nombre_reportado, doc.fields.nombre_completo)
            if sim >= NAME_MATCH_THRESHOLD and (best is None or (sim, -diff) > (best[0], -best[1])):
                best = (sim, diff, doc)
        if best:
            sim, diff, doc = best
            paginas = ", ".join(str(n) for n in doc.source_pages)
            r.observaciones = (
                f"{r.observaciones} POSIBLE COINCIDENCIA: el PDF trae un documento (página {paginas}) con el "
                f"número {doc.numero_documento}, que difiere en {diff} dígito(s) del número del Excel, y un "
                f"nombre similar ('{doc.fields.nombre_completo}'). Probable dígito mal leído por el OCR; "
                f"verificar manualmente."
            )


def compare(
    documents: List[DocumentRecord], participants: List[ExcelParticipant]
) -> Dict[str, list]:
    # Indice primario: numero exacto tal como viene del OCR
    ocr_by_id: Dict[str, DocumentRecord] = {}
    # Indice secundario: numero normalizado (sin ceros iniciales)
    ocr_by_normalized_id: Dict[str, DocumentRecord] = {}

    for doc in documents:
        if doc.numero_documento:
            ocr_by_id[doc.numero_documento] = doc
            norm = _normalize_id(doc.numero_documento)
            if norm:
                ocr_by_normalized_id[norm] = doc

    matched_ocr_ids = set()
    results: List[MatchResult] = []

    for p in participants:
        # Busqueda exacta primero
        doc = ocr_by_id.get(p.numero_documento)

        # Si no hay coincidencia exacta, intentar con normalizacion de ceros
        if doc is None:
            norm_excel = _normalize_id(p.numero_documento)
            if norm_excel:
                doc = ocr_by_normalized_id.get(norm_excel)

        if doc is None:
            results.append(
                MatchResult(
                    numero_documento=p.numero_documento,
                    estado="no_encontrado_en_pdf",
                    excel=p.to_dict(),
                    ocr=None,
                    observaciones="El número de documento del Excel no se encontró en ninguna "
                                   "cédula reconocida del PDF (revisar calidad de imagen o "
                                   "posibles errores de OCR en el número).",
                )
            )
            continue

        matched_ocr_ids.add(doc.numero_documento)
        similarity = _name_similarity(p.nombre_reportado, doc.fields.nombre_completo)

        missing = [f for f in REQUIRED_FIELDS_FOR_COMPLETE if not getattr(doc.fields, f)]

        if not p.nombre_reportado:
            estado = "nombre_no_coincide"
            obs = (
                "El Excel no trae nombre para este participante: se encontró el documento "
                "pero no se pudo validar que el nombre corresponda."
            )
        elif similarity < NAME_MATCH_THRESHOLD:
            estado = "nombre_no_coincide"
            obs = (
                f"El nombre del Excel ('{p.nombre_reportado}') no coincide "
                f"suficientemente con el extraído del PDF "
                f"('{doc.fields.nombre_completo}')."
            )
        elif missing:
            estado = "documento_incompleto"
            obs = f"Coincide el nombre e ID, pero al documento OCR le faltan campos: {', '.join(missing)}."
        else:
            estado = "coincide"
            obs = None
            if not names_are_identical(p.nombre_reportado, doc.fields.nombre_completo):
                obs = (
                    f"Nombre no idéntico (Excel '{p.nombre_reportado}', OCR '{doc.fields.nombre_completo}'); "
                    f"el número sí coincide. Confirmar en la cédula: puede ser una letra mal leída por el "
                    f"OCR o una diferencia de escritura en el Excel."
                )

        results.append(
            MatchResult(
                numero_documento=p.numero_documento,
                estado=estado,
                excel=p.to_dict(),
                ocr=doc.to_dict(),
                similitud_nombre=similarity,
                observaciones=obs,
            )
        )

    sobrante_docs = [
        doc for doc in documents
        if doc.numero_documento and doc.numero_documento not in matched_ocr_ids
    ]
    _add_misread_hints(results, participants, sobrante_docs)
    sobrantes = [doc.to_dict() for doc in sobrante_docs]
    sin_numero = [doc.to_dict() for doc in documents if not doc.numero_documento]

    resumen = {
        "total_excel": len(participants),
        "total_pdf_documentos": len(documents),
        "coinciden": sum(1 for r in results if r.estado == "coincide"),
        "nombre_no_coincide": sum(1 for r in results if r.estado == "nombre_no_coincide"),
        "documento_incompleto": sum(1 for r in results if r.estado == "documento_incompleto"),
        "no_encontrado_en_pdf": sum(1 for r in results if r.estado == "no_encontrado_en_pdf"),
        "sobrantes_en_pdf": len(sobrantes),
        "documentos_sin_numero_detectado": len(sin_numero),
    }

    return {
        "resumen": resumen,
        "resultados": [r.to_dict() for r in results],
        "sobrantes_en_pdf": sobrantes,
        "documentos_sin_numero_detectado": sin_numero,
    }

"""
Agrupa los resultados de OCR por pagina en "documentos" (una cedula completa).

Logica de agrupamiento:
  - Si una pagina trae numero de documento -> abre un nuevo "documento".
  - Las paginas siguientes que NO traen numero de documento (reverso)
    se van fusionando al documento abierto.
  - Si una pagina tiene numero de documento que coincide con el documento
    previo (con o sin ceros iniciales del codigo de barras), se fusiona.
  - Si el reverso trae un numero validado por el digito de control del MRZ y
    difiere en 1-2 digitos del numero del frente anterior (digito mal leido por
    el OCR) con nombres compatibles, es la misma persona: se fusiona y prevalece
    el numero validado.
  - Si una pagina no aporta datos reconocibles, se ignora.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import List, Optional

from .field_parser import ParsedFields, digit_differences, normalize_for_match, parse_page
from .ocr_engine import PageResult

# Máximo de dígitos distintos entre el número del frente y el del MRZ validado
# para considerar que el OCR solo se equivocó al leer el frente.
MAX_DIGIT_DIFF_MISREAD = 2


@dataclass
class DocumentRecord:
    numero_documento: Optional[str]
    fields: ParsedFields
    source_pages: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.fields.to_dict()
        d["nombre_completo"] = self.fields.nombre_completo
        d["paginas_pdf"] = self.source_pages
        return d


def _numbers_match(num1: Optional[str], num2: Optional[str]) -> bool:
    """Compara dos numeros de documento ignorando ceros iniciales."""
    if not num1 or not num2:
        return False
    return num1.lstrip("0") == num2.lstrip("0")


def _is_back_only(fields: ParsedFields) -> bool:
    """La página aportó solo datos de reverso (sin parser de frente)."""
    fuente = fields.fuente or ""
    return "frente" not in fuente and ("reverso" in fuente or "mrz" in fuente)


def _has_back(fields: ParsedFields) -> bool:
    """
    El documento ya incorpora un reverso (clásico o MRZ).

    En una cédula digital la etiqueta "Fecha y lugar de expedición" del FRENTE activa el parser
    de reverso clásico, así que 'reverso_clasico' junto a 'frente_nuevo' no prueba que haya reverso.
    """
    fuente = fields.fuente or ""
    if "mrz" in fuente:
        return True
    return "reverso" in fuente and "frente_nuevo" not in fuente


def _names_compatible(a: ParsedFields, b: ParsedFields) -> bool:
    """Los nombres comparten algún apellido/nombre (tolerando nombres pegados por el OCR) o falta alguno."""
    na, nb = a.nombre_completo, b.nombre_completo
    if not na or not nb:
        return True
    ta, tb = normalize_for_match(na).split(), normalize_for_match(nb).split()
    if {t for t in ta if len(t) >= 4} & {t for t in tb if len(t) >= 4}:
        return True
    return difflib.SequenceMatcher(None, "".join(sorted(ta)), "".join(sorted(tb))).ratio() >= 0.75


def _is_misread_front_number(current: DocumentRecord, parsed: ParsedFields) -> bool:
    """
    El reverso (solo MRZ, con número validado por checksum) corrige un número de frente mal leído:
    difiere en 1-2 dígitos, el frente aún no tiene reverso y los nombres son compatibles.
    """
    if not (_is_back_only(parsed) and parsed.numero_verificado) or current.fields.numero_verificado:
        return False
    if _has_back(current.fields):
        return False
    diff = digit_differences(parsed.numero_documento, current.numero_documento)
    return diff is not None and 1 <= diff <= MAX_DIGIT_DIFF_MISREAD and _names_compatible(current.fields, parsed)


def _adopt_verified_number(doc: DocumentRecord) -> None:
    """Si el número del documento quedó validado por el MRZ, ese es el número del registro."""
    if doc.fields.numero_verificado:
        doc.numero_documento = doc.fields.numero_documento


def group_pages_into_documents(pages: List[PageResult]) -> List[DocumentRecord]:
    documents: List[DocumentRecord] = []
    current: Optional[DocumentRecord] = None

    for page in pages:
        parsed = parse_page(page.raw_text)

        has_new_number = bool(parsed.numero_documento)
        has_any_data = any(v for k, v in parsed.to_dict().items() if k not in ("fuente",))

        if not has_any_data:
            # pagina sin datos utiles -> se ignora
            continue

        # Determinar si es el mismo documento o uno nuevo
        if has_new_number and current is not None:
            if _numbers_match(parsed.numero_documento, current.numero_documento):
                # Mismo numero (posiblemente con ceros extra) -> es reverso, fusionar
                current.fields = current.fields.merge(parsed)
                _adopt_verified_number(current)
                current.source_pages.append(page.page_number)
                continue
            elif _is_misread_front_number(current, parsed):
                # Mismo titular: el OCR leyo mal un digito del frente; el MRZ validado prevalece
                current.fields = current.fields.merge(parsed)
                _adopt_verified_number(current)
                current.source_pages.append(page.page_number)
                continue
            else:
                # Numero distinto -> nuevo documento
                current = DocumentRecord(
                    numero_documento=parsed.numero_documento,
                    fields=parsed,
                    source_pages=[page.page_number],
                )
                documents.append(current)
        elif has_new_number and current is None:
            # Primer documento
            current = DocumentRecord(
                numero_documento=parsed.numero_documento,
                fields=parsed,
                source_pages=[page.page_number],
            )
            documents.append(current)
        elif not has_new_number and current is not None:
            # Si current ya tiene nombres/apellidos y esta página también trae nombres/apellidos
            # distintos, es un documento nuevo (frente con número no detectado), NO un reverso.
            is_new_person = (
                bool(current.fields.nombre_completo)
                and bool(parsed.nombre_completo)
                and current.fields.nombre_completo != parsed.nombre_completo
            )
            # Reverso sin número legible: solo se une al documento anterior si este aún no
            # tiene reverso. Si ya lo tiene, pertenece a otra persona cuyo frente no se leyó;
            # unirlo mezclaría datos de dos personas.
            if _is_back_only(parsed) and _has_back(current.fields):
                is_new_person = True
            if is_new_person:
                current = DocumentRecord(
                    numero_documento=None,
                    fields=parsed,
                    source_pages=[page.page_number],
                )
                documents.append(current)
            else:
                # Pagina sin numero (reverso u otra pagina del mismo documento) -> fusionar
                current.fields = current.fields.merge(parsed)
                current.numero_documento = current.numero_documento or current.fields.numero_documento
                current.source_pages.append(page.page_number)
        else:
            # No hay documento abierto y esta pagina no trae numero:
            # crear documento huerfano para no perder datos
            current = DocumentRecord(
                numero_documento=None,
                fields=parsed,
                source_pages=[page.page_number],
            )
            documents.append(current)

    return documents

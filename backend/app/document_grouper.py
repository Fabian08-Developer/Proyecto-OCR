"""
Agrupa los resultados de OCR por pagina en "documentos" (una cedula completa).

Logica de agrupamiento:
  - Si una pagina trae numero de documento -> abre un nuevo "documento".
  - Las paginas siguientes que NO traen numero de documento (reverso)
    se van fusionando al documento abierto.
  - Si una pagina tiene numero de documento que coincide con el documento
    previo (con o sin ceros iniciales del codigo de barras), se fusiona.
  - Si una pagina no aporta datos reconocibles, se ignora.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .field_parser import ParsedFields, parse_page
from .ocr_engine import PageResult


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

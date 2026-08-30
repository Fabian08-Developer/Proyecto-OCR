"""
Script de diagnóstico y prueba para el sistema OCR.

Uso:
    python test_ocr.py <ruta_al_pdf> [ruta_al_excel]

Ejemplo:
    python test_ocr.py ./uploads/cedulas_ejemplo.pdf
    python test_ocr.py ./uploads/cedulas.pdf ./uploads/reporte_sena.xlsx
"""

import sys
import os
from pathlib import Path

# Añadir directorio backend al path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ocr_engine import extract_pdf
from app.document_grouper import group_pages_into_documents
from app.excel_reader import parse_excel_report
from app.matcher import compare


def main():
    if len(sys.argv) < 2:
        print("\n=======================================================")
        print(" SISTEMA OCR - HERRAMIENTA DE PRUEBA Y DIAGNÓSTICO")
        print("=======================================================")
        print("Uso:")
        print("   python test_ocr.py <archivo.pdf> [reporte.xlsx]")
        print("\nEjemplo:")
        print("   python test_ocr.py mis_cedulas.pdf")
        print("   python test_ocr.py mis_cedulas.pdf lista_inscritos.xlsx")
        print("=======================================================\n")
        return

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"❌ Error: El archivo PDF '{pdf_path}' no existe.")
        return

    print(f"\n📄 Procesando PDF: {pdf_path.name} ...")
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    pages = extract_pdf(pdf_bytes)
    print(f"✅ OCR completado. Total de páginas extraídas: {len(pages)}\n")

    print("-" * 60)
    print(" DETALLE POR PÁGINA (TEXTO CRUDO DETECTADO POR OCR)")
    print("-" * 60)
    for p in pages:
        print(f"\n--- [ PÁGINA {p.page_number} ] ---")
        if not p.raw_text.strip():
            print("  (Página vacía o sin texto legible)")
        else:
            for idx, line in enumerate(p.raw_text.splitlines(), start=1):
                print(f"  [{idx:02d}] {line}")

    documents = group_pages_into_documents(pages)
    print("\n" + "=" * 60)
    print(f" DOCUMENTOS CONSOLIDADOS ({len(documents)} encontrados)")
    print("=" * 60)

    for i, doc in enumerate(documents, start=1):
        fields = doc.fields
        print(f"\n📋 [ Documento #{i} ] - Páginas: {doc.source_pages}")
        print(f"  • Cédula / NUIP      : {fields.numero_documento or '⚠️ NO DETECTADO'}")
        print(f"  • Nombres            : {fields.nombres or '⚠️ NO DETECTADO'}")
        print(f"  • Apellidos          : {fields.apellidos or '⚠️ NO DETECTADO'}")
        print(f"  • Nombre Completo    : {fields.nombre_completo or '⚠️ NO DETECTADO'}")
        print(f"  • Fecha Nacimiento   : {fields.fecha_nacimiento or '⚠️ NO DETECTADO'}")
        print(f"  • Lugar Nacimiento   : {fields.lugar_nacimiento or '⚠️ NO DETECTADO'}")
        print(f"  • Sexo               : {fields.sexo or '-'}")
        print(f"  • RH                 : {fields.rh or '-'}")
        print(f"  • Estatura           : {fields.estatura or '-'}")
        print(f"  • Fecha Expedición   : {fields.fecha_expedicion or '-'}")
        print(f"  • Lugar Expedición   : {fields.lugar_expedicion or '-'}")
        print(f"  • Fuente Reconocida  : {fields.fuente or '-'}")

    # Si se pasó un archivo Excel, realizar cruce
    if len(sys.argv) >= 3:
        excel_path = Path(sys.argv[2])
        if not excel_path.exists():
            print(f"\n⚠️ Advertencia: El archivo Excel '{excel_path}' no existe.")
            return

        print("\n" + "=" * 60)
        print(f"📊 COMPARANDO CON EXCEL: {excel_path.name}")
        print("=" * 60)

        with open(excel_path, "rb") as f:
            excel_bytes = f.read()

        participants = parse_excel_report(excel_bytes, excel_path.name)
        comparison = compare(documents, participants)

        print(f"  Total Participantes en Excel : {len(participants)}")
        print(f"  Coincidencias Exactas (OK)   : {len(comparison.get('coincidencias_ok', []))}")
        print(f"  Diferencias de Nombre        : {len(comparison.get('diferencias_nombre', []))}")
        print(f"  En Excel pero sin PDF        : {len(comparison.get('en_excel_sin_pdf', []))}")
        print(f"  En PDF pero no en Excel      : {len(comparison.get('en_pdf_sin_excel', []))}")
        print(f"  PDFs sin número legible      : {len(comparison.get('pdf_sin_numero', []))}")


if __name__ == "__main__":
    main()

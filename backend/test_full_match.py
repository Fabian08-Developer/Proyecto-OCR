import sys
from pathlib import Path
sys.path.insert(0, '.')

from app.excel_reader import parse_excel_report
from app.ocr_engine import extract_pdf
from app.document_grouper import group_pages_into_documents
from app.matcher import compare

excel_path = Path('../reporte_inscripcion 3574135 (1).xls')
with open(excel_path, 'rb') as f:
    participants = parse_excel_report(f.read(), excel_path.name)
print(f'Excel participants loaded: {len(participants)}')

pdf_path = Path('../DOCUMENTACION PARTICIPANTES 3574135 (1).pdf')
with open(pdf_path, 'rb') as f:
    pages = extract_pdf(f.read())
print(f'PDF pages extracted: {len(pages)}')

docs = group_pages_into_documents(pages)
print(f'Documents grouped: {len(docs)}')

result = compare(docs, participants)
resumen = result['resumen']
print('\n================== RESUMEN DE COMPARACION ==================')
for k, v in resumen.items():
    print(f'  {k:35s}: {v}')
print('============================================================\n')

print('--- DETALLE DE CADA PARTICIPANTE DEL EXCEL ---')
for r in result['resultados']:
    st = r['estado']
    num = r['numero_documento']
    exc = r['excel']['nombre_reportado'] if r.get('excel') else '-'
    ocr_name = r['ocr']['fields']['nombre_completo'] if r.get('ocr') and r['ocr'].get('fields') else '-'
    sim = r.get('similitud_nombre')
    sim_pct = f'{sim:.0%}' if sim is not None else '-'
    print(f'[{st:22s}] {num:12s} | Excel: {exc:32s} | OCR: {ocr_name:32s} | Sim: {sim_pct}')

if result['sobrantes_en_pdf']:
    print('\n--- SOBRANTES EN PDF (NO ENCONTRADOS EN EL EXCEL) ---')
    for s in result['sobrantes_en_pdf']:
        doc_n = s.get("numero_documento")
        nom_c = s.get("fields", {}).get("nombre_completo")
        print(f'  Doc: {doc_n} | Nombre: {nom_c}')

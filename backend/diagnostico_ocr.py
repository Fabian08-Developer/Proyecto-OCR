"""
Script de diagnóstico avanzado: guarda texto crudo OCR de cada página en JSON.
Uso:
    python diagnostico_ocr.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.ocr_engine import pdf_to_images, run_ocr_on_image
from app.field_parser import parse_page

PDF_PATH = Path(__file__).parent.parent / "DOCUMENTACION PARTICIPANTES 3574135 (1).pdf"
OUT_JSON = Path(__file__).parent / "diagnostico_resultado.json"

def main():
    print(f"Leyendo PDF: {PDF_PATH.name}")
    with open(PDF_PATH, "rb") as f:
        pdf_bytes = f.read()

    print("Convirtiendo paginas a imagenes (DPI=200)...")
    images = pdf_to_images(pdf_bytes, dpi=200)
    print(f"Total de paginas: {len(images)}")

    resultados = []
    for i, img in enumerate(images, start=1):
        print(f"  OCR pagina {i}/{len(images)}...", flush=True)
        page_result = run_ocr_on_image(img)
        page_result.page_number = i

        parsed = parse_page(page_result.raw_text)

        lineas_detalle = [
            {"texto": l.text, "confianza": round(l.confidence, 3)}
            for l in page_result.lines
        ]

        resultados.append({
            "pagina": i,
            "texto_crudo": page_result.raw_text,
            "lineas": lineas_detalle,
            "campos_extraidos": parsed.to_dict(),
        })

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"\nResultados guardados en: {OUT_JSON}")

    print("\n=== RESUMEN POR PAGINA ===")
    for r in resultados:
        c = r["campos_extraidos"]
        print(f"  Pag {r['pagina']:02d} | "
              f"Doc: {(c.get('numero_documento') or 'NO'):<12} | "
              f"Apellidos: {(c.get('apellidos') or 'NO'):<25} | "
              f"Nombres: {(c.get('nombres') or 'NO'):<25} | "
              f"Nacimiento: {(c.get('fecha_nacimiento') or 'NO'):<12} | "
              f"RH: {c.get('rh') or 'NO'}")

if __name__ == "__main__":
    main()

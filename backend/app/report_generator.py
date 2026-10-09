"""
report_generator.py - Generacion de informes PDF con ReportLab.

El informe incluye:
  1. Portada con logo textual, fecha y nombres de los archivos.
  2. Resumen estadistico con barras de progreso visuales.
  3. Tabla de resultados de la comparacion (por estado).
  4. Seccion de sobrantes en PDF.
  5. Seccion de documentos sin numero detectado.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Paleta de colores
# ---------------------------------------------------------------------------
COLOR_PRIMARY   = colors.HexColor("#1f5fa8")
COLOR_PRIMARY_DK= colors.HexColor("#16447d")
COLOR_GREEN     = colors.HexColor("#1f9d55")
COLOR_AMBER     = colors.HexColor("#c98a1f")
COLOR_RED       = colors.HexColor("#d64545")
COLOR_MUTED     = colors.HexColor("#6b7684")
COLOR_BG        = colors.HexColor("#f4f6f9")
COLOR_BORDER    = colors.HexColor("#e2e6ea")
COLOR_WHITE     = colors.white
COLOR_DARK      = colors.HexColor("#1f2933")

ESTADO_COLOR = {
    "coincide":            COLOR_GREEN,
    "nombre_no_coincide":  COLOR_AMBER,
    "documento_incompleto":COLOR_AMBER,
    "no_encontrado_en_pdf":COLOR_RED,
}
ESTADO_LABEL = {
    "coincide":            "Coincide",
    "nombre_no_coincide":  "Nombre no coincide",
    "documento_incompleto":"Datos incompletos",
    "no_encontrado_en_pdf":"No encontrado en PDF",
}


# ---------------------------------------------------------------------------
# Estilos de parrafo
# ---------------------------------------------------------------------------
def _build_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"],
            textColor=COLOR_WHITE, fontSize=22, spaceAfter=6, alignment=TA_CENTER),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"],
            textColor=COLOR_WHITE, fontSize=12, spaceAfter=4, alignment=TA_CENTER),
        "meta": ParagraphStyle("meta", parent=base["Normal"],
            textColor=colors.HexColor("#dbeafe"), fontSize=9, alignment=TA_CENTER),
        "section": ParagraphStyle("section", parent=base["Heading2"],
            textColor=COLOR_PRIMARY, fontSize=13, spaceBefore=14, spaceAfter=6,
            borderPad=(0, 0, 0, 4)),
        "cell": ParagraphStyle("cell", parent=base["Normal"],
            fontSize=8, textColor=COLOR_DARK, leading=10),
        "cell_sm": ParagraphStyle("cell_sm", parent=base["Normal"],
            fontSize=7, textColor=COLOR_MUTED, leading=9),
        "obs": ParagraphStyle("obs", parent=base["Normal"],
            fontSize=7, textColor=COLOR_MUTED, leading=9, wordWrap="CJK"),
        "normal": ParagraphStyle("normal", parent=base["Normal"],
            fontSize=9, textColor=COLOR_DARK),
        "stat_label": ParagraphStyle("stat_label", parent=base["Normal"],
            fontSize=9, textColor=COLOR_MUTED),
        "stat_value": ParagraphStyle("stat_value", parent=base["Normal"],
            fontSize=18, textColor=COLOR_DARK, alignment=TA_CENTER),
        "footer": ParagraphStyle("footer", parent=base["Normal"],
            fontSize=7, textColor=COLOR_MUTED, alignment=TA_CENTER),
    }


# ---------------------------------------------------------------------------
# Helpers de tabla
# ---------------------------------------------------------------------------
def _header_row(cells: List[str], st) -> List[Paragraph]:
    hdr = ParagraphStyle("hdr", parent=st["cell"],
        textColor=COLOR_WHITE, fontName="Helvetica-Bold", fontSize=8)
    return [Paragraph(c, hdr) for c in cells]


def _badge(text: str, estado: str, st) -> Paragraph:
    col = ESTADO_COLOR.get(estado, COLOR_MUTED)
    # Simular badge con color de texto
    ps = ParagraphStyle("badge", parent=st["cell"],
        textColor=col, fontName="Helvetica-Bold", fontSize=7.5)
    return Paragraph(text, ps)


def _bar(value: int, total: int, color) -> str:
    """Retorna una representacion visual simple de barra (bloques Unicode)."""
    if total == 0:
        return "0%"
    pct = value / total
    filled = int(pct * 20)
    bar = "█" * filled + "░" * (20 - filled)
    return f"{bar}  {value} ({pct:.0%})"


# ---------------------------------------------------------------------------
# Pagina de portada
# ---------------------------------------------------------------------------
def _cover_page(story: list, st, nombre_pdf: str, nombre_excel: str,
                resumen: dict, fecha: str, usuario: str, tiempo: Optional[float]):
    # Encabezado azul simulado con tabla de fondo
    header_data = [[Paragraph("Sistema OCR de Verificación de Documentos", st["title"])],
                   [Paragraph("Informe de Comparación de Identidades", st["subtitle"])],
                   [Paragraph(f"Generado el {fecha}  ·  Usuario: {usuario}", st["meta"])]]
    header_table = Table(header_data, colWidths=[19 * cm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_PRIMARY),
        ("TOPPADDING",    (0, 0), (-1, -1), 18),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
        ("LEFTPADDING",   (0, 0), (-1, -1), 20),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 20),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [COLOR_PRIMARY]),
        ("ROUNDEDCORNERS", [8]),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.5 * cm))

    # Archivos procesados
    files_data = [
        [Paragraph("<b>PDF de cédulas:</b>", st["normal"]),
         Paragraph(nombre_pdf, st["normal"])],
        [Paragraph("<b>Excel de inscripción:</b>", st["normal"]),
         Paragraph(nombre_excel, st["normal"])],
    ]
    if tiempo is not None:
        files_data.append([
            Paragraph("<b>Tiempo de procesamiento:</b>", st["normal"]),
            Paragraph(f"{tiempo:.1f} segundos", st["normal"]),
        ])
    ft = Table(files_data, colWidths=[5 * cm, 14 * cm])
    ft.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_BG),
        ("GRID", (0, 0), (-1, -1), 0.3, COLOR_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    story.append(ft)
    story.append(Spacer(1, 0.5 * cm))

    # Tarjetas estadisticas (2 x 4)
    total = resumen.get("total_excel", 0) or 1
    cards = [
        ("Registros en Excel",   resumen.get("total_excel", 0),    None),
        ("Documentos en PDF",    resumen.get("total_pdf_documentos", 0), None),
        ("Coinciden",            resumen.get("coinciden", 0),         COLOR_GREEN),
        ("Nombre no coincide",   resumen.get("nombre_no_coincide", 0),COLOR_AMBER),
        ("Datos incompletos",    resumen.get("documento_incompleto", 0),COLOR_AMBER),
        ("No encontrados",       resumen.get("no_encontrado_en_pdf", 0),COLOR_RED),
        ("Sobrantes en PDF",     resumen.get("sobrantes_en_pdf", 0), None),
        ("Sin número detectado", resumen.get("documentos_sin_numero_detectado", 0), None),
    ]
    st_val = ParagraphStyle("sv", parent=st["normal"],
        fontSize=22, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=COLOR_DARK)
    st_lbl = ParagraphStyle("sl", parent=st["normal"],
        fontSize=8, alignment=TA_CENTER, textColor=COLOR_MUTED)

    cards_data = []
    row = []
    for i, (lbl, val, col) in enumerate(cards):
        vc = ParagraphStyle("vc", parent=st_val,
            textColor=(col or COLOR_PRIMARY))
        cell = Table([
            [Paragraph(str(val), vc)],
            [Paragraph(lbl, st_lbl)],
        ], colWidths=[4.5 * cm])
        cell.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), COLOR_WHITE),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        row.append(cell)
        if len(row) == 4:
            cards_data.append(row)
            row = []
    if row:
        while len(row) < 4:
            row.append(Paragraph("", st["normal"]))
        cards_data.append(row)

    ct = Table(cards_data, colWidths=[4.75 * cm] * 4, hAlign="CENTER")
    ct.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]))
    story.append(ct)
    story.append(Spacer(1, 0.4 * cm))

    # Barras de progreso textuales
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_BORDER))
    story.append(Spacer(1, 0.2 * cm))
    for estado, key, col in [
        ("Coinciden",            "coinciden",            COLOR_GREEN),
        ("Nombre no coincide",   "nombre_no_coincide",   COLOR_AMBER),
        ("Datos incompletos",    "documento_incompleto", COLOR_AMBER),
        ("No encontrados en PDF","no_encontrado_en_pdf", COLOR_RED),
    ]:
        val = resumen.get(key, 0)
        bar = _bar(val, resumen.get("total_excel", 1) or 1, col)
        ps = ParagraphStyle("bar", parent=st["normal"], fontSize=8, textColor=col)
        row_data = [[Paragraph(f"<b>{estado}</b>", st["stat_label"]),
                     Paragraph(bar, ps)]]
        bt = Table(row_data, colWidths=[6 * cm, 13 * cm])
        bt.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(bt)

    story.append(PageBreak())


# ---------------------------------------------------------------------------
# Advertencias de lectura del Excel
# ---------------------------------------------------------------------------
def _excel_warnings(story: list, st, advertencias: list):
    """Recuadro con lo que no se pudo leer del Excel (participantes omitidos o dudosos)."""
    if not advertencias:
        return
    story.append(Paragraph("Advertencias de lectura del Excel", st["section"]))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_AMBER, spaceAfter=6))
    story.append(Paragraph(
        "Los participantes afectados pueden no figurar en los resultados de este informe.",
        st["normal"],
    ))
    story.append(Spacer(1, 0.2 * cm))
    rows = [[Paragraph(f"• {escape(str(a))}", st["cell"])] for a in advertencias]
    t = Table(rows, colWidths=[18 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff8e6")),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_AMBER),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.5 * cm))


# ---------------------------------------------------------------------------
# Tabla de resultados
# ---------------------------------------------------------------------------
def _results_table(story: list, st, resultados: list):
    story.append(Paragraph("Detalle de la Comparación", st["section"]))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_PRIMARY, spaceAfter=6))

    if not resultados:
        story.append(Paragraph("Sin resultados.", st["normal"]))
        return

    col_w = [3.5 * cm, 2.8 * cm, 5.0 * cm, 5.0 * cm, 2.2 * cm, 1.5 * cm]
    headers = _header_row(
        ["Estado", "N° Documento", "Nombre Excel", "Nombre OCR", "Fecha Nac.", "Sim."], st
    )
    rows = [headers]

    for r in resultados:
        estado = r.get("estado", "")
        badge = _badge(ESTADO_LABEL.get(estado, estado), estado, st)
        sim = r.get("similitud_nombre")
        sim_str = f"{sim*100:.0f}%" if sim is not None else "—"
        ocr = r.get("ocr") or {}
        exc = r.get("excel") or {}
        rows.append([
            badge,
            Paragraph(str(r.get("numero_documento", "—")), st["cell"]),
            Paragraph(str(exc.get("nombre_reportado", "—")), st["cell"]),
            Paragraph(str(ocr.get("nombre_completo", "—")), st["cell"]),
            Paragraph(str(ocr.get("fecha_nacimiento", "—")), st["cell_sm"]),
            Paragraph(sim_str, st["cell_sm"]),
        ])

    t = Table(rows, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_PRIMARY),
        ("TEXTCOLOR",  (0, 0), (-1, 0), COLOR_WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_WHITE, COLOR_BG]),
        ("GRID", (0, 0), (-1, -1), 0.3, COLOR_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("WORDWRAP", (0, 0), (-1, -1), True),
    ]))
    story.append(t)


# ---------------------------------------------------------------------------
# Tabla de sobrantes
# ---------------------------------------------------------------------------
def _sobrantes_table(story: list, st, sobrantes: list):
    story.append(PageBreak())
    story.append(Paragraph("Sobrantes en PDF (no están en el Excel)", st["section"]))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_PRIMARY, spaceAfter=6))

    if not sobrantes:
        story.append(Paragraph("No hay documentos sobrantes.", st["normal"]))
        return

    col_w = [3.5 * cm, 7.5 * cm, 3.5 * cm, 5.5 * cm]
    headers = _header_row(["N° Documento (OCR)", "Nombre (OCR)", "Fecha Nac.", "Páginas PDF"], st)
    rows = [headers]
    for d in sobrantes:
        pages = ", ".join(str(p) for p in (d.get("paginas_pdf") or []))
        rows.append([
            Paragraph(str(d.get("numero_documento", "—")), st["cell"]),
            Paragraph(str(d.get("nombre_completo", "—")), st["cell"]),
            Paragraph(str(d.get("fecha_nacimiento", "—")), st["cell_sm"]),
            Paragraph(pages or "—", st["cell_sm"]),
        ])

    t = Table(rows, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_PRIMARY_DK),
        ("TEXTCOLOR",  (0, 0), (-1, 0), COLOR_WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_WHITE, COLOR_BG]),
        ("GRID", (0, 0), (-1, -1), 0.3, COLOR_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)


# ---------------------------------------------------------------------------
# Tabla de sin número
# ---------------------------------------------------------------------------
def _sin_numero_table(story: list, st, docs: list):
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph("Documentos sin Número Detectado por OCR", st["section"]))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_PRIMARY, spaceAfter=6))

    if not docs:
        story.append(Paragraph("No hay documentos sin número.", st["normal"]))
        return

    col_w = [7.5 * cm, 4.0 * cm, 8.5 * cm]
    headers = _header_row(["Nombre (OCR)", "Fecha Nac.", "Páginas PDF"], st)
    rows = [headers]
    for d in docs:
        pages = ", ".join(str(p) for p in (d.get("paginas_pdf") or []))
        rows.append([
            Paragraph(str(d.get("nombre_completo", "—")), st["cell"]),
            Paragraph(str(d.get("fecha_nacimiento", "—")), st["cell_sm"]),
            Paragraph(pages or "—", st["cell_sm"]),
        ])

    t = Table(rows, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_MUTED),
        ("TEXTCOLOR",  (0, 0), (-1, 0), COLOR_WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_WHITE, COLOR_BG]),
        ("GRID", (0, 0), (-1, -1), 0.3, COLOR_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)


# ---------------------------------------------------------------------------
# Funcion principal
# ---------------------------------------------------------------------------
def generar_reporte_pdf(
    nombre_pdf: str,
    nombre_excel: str,
    resultado: Dict[str, Any],
    usuario: str,
    fecha_comparacion: Optional[datetime] = None,
    tiempo_segundos: Optional[float] = None,
) -> bytes:
    """
    Genera un informe PDF completo y retorna los bytes del archivo.

    Args:
        nombre_pdf: Nombre del archivo PDF original.
        nombre_excel: Nombre del archivo Excel original.
        resultado: Diccionario con keys 'resumen', 'resultados',
                   'sobrantes_en_pdf', 'documentos_sin_numero_detectado'.
        usuario: Nombre/username del usuario que genera el informe.
        fecha_comparacion: Fecha de la comparacion (usa ahora si None).
        tiempo_segundos: Tiempo de procesamiento en segundos.

    Returns:
        bytes del PDF generado.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    st = _build_styles()
    story = []

    fecha_dt = fecha_comparacion or datetime.now()
    fecha_str = fecha_dt.strftime("%d/%m/%Y %H:%M")

    resumen = resultado.get("resumen", {})
    resultados = resultado.get("resultados", [])
    sobrantes = resultado.get("sobrantes_en_pdf", [])
    sin_numero = resultado.get("documentos_sin_numero_detectado", [])

    # 1. Portada
    _cover_page(story, st, nombre_pdf, nombre_excel, resumen, fecha_str, usuario, tiempo_segundos)

    # 2. Advertencias de lectura del Excel (si las hubo) y tabla de resultados
    _excel_warnings(story, st, resultado.get("advertencias_excel") or [])
    _results_table(story, st, resultados)

    # 3. Sobrantes
    _sobrantes_table(story, st, sobrantes)

    # 4. Sin número
    _sin_numero_table(story, st, sin_numero)

    # Pie de pagina
    story.append(Spacer(1, 1 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_BORDER))
    story.append(Paragraph(
        f"Sistema OCR de Verificación de Documentos · Informe generado el {fecha_str} · Usuario: {usuario}",
        st["footer"]
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()

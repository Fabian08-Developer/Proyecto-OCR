"""
Pruebas de regresión - Paso 2: un dígito mal leído por el OCR partía a una persona en dos
documentos (un "sobrante" falso y un participante emparejado con datos incompletos).

El MRZ de las cédulas digitales trae un dígito de control compuesto (ICAO 9303) que permite
saber qué lectura del número es la correcta. Todos los datos son inventados; el MRZ de prueba
se genera con una implementación propia del checksum (independiente del código bajo prueba).

Uso (desde la carpeta backend):
    python test_regresion_paso2.py
Termina con código 1 si alguna prueba falla. También es compatible con pytest.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.document_grouper import _has_back, group_pages_into_documents
from app.excel_reader import ExcelParticipant
from app.field_parser import ParsedFields, digit_differences, parse_page, verify_mrz_number
from app.matcher import compare
from app.ocr_engine import PageResult


# --------------------------------------------------------------------------- #
# Generador de MRZ TD1 válido (checksum ICAO 9303 implementado aparte)
# --------------------------------------------------------------------------- #

def _chk(data: str) -> int:
    total = 0
    for i, c in enumerate(data):
        v = int(c) if c.isdigit() else 0 if c == "<" else ord(c) - 55
        total += v * (7, 3, 1)[i % 3]
    return total % 10


def build_mrz(numero: str, apellidos: str = "GARCIA<LOPEZ", nombres: str = "MARIA<ELENA") -> list:
    serial = "000123456"
    l1 = "ICCOL" + serial + str(_chk(serial)) + "99001" + "<" * 10
    birth, expiry = "900101", "350101"
    campo = numero + "<" * (11 - len(numero))
    l2 = birth + str(_chk(birth)) + "F" + expiry + str(_chk(expiry)) + "COL" + campo
    l2 += str(_chk(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]))
    l3 = (apellidos + "<<" + nombres).ljust(30, "<")
    assert len(l1) == len(l2) == len(l3) == 30
    return [l1, l2, l3]


def reverso_digital(numero: str, mrz_lines=None) -> str:
    lineas = mrz_lines or build_mrz(numero)
    return "\n".join(["CO", "000123456", "REGISTRADORNACIONAL", "Fulano de Tal Perez"] + lineas)


def frente_digital(numero_impreso: str, apellidos="GARCIA LOPEZ", nombres="MARIA ELENA") -> str:
    # Incluye la etiqueta "Fecha y lugar de expedicion" (activa el parser de reverso clásico)
    return f"""CEDULA DE
REPUBLICA DE COLOMBIA
CIUDADANIA
NUIP {numero_impreso}
Apellidos
{apellidos}
Nombres
{nombres}
Nacionalidad
COL
Fecha de nacimiento
01 ENE 1990
Fecha y lugar de expedicion
01 ENE 2025, BOGOTA"""


def _paginas(*textos):
    return [PageResult(page_number=i + 1, raw_text=t) for i, t in enumerate(textos)]


NUM_REAL = "1000000007"
NUM_FRENTE_MAL = "1000000017"      # 1 dígito distinto (el OCR leyó 0 -> 1)
NUM_MUY_DISTINTO = "1005500017"    # 3 dígitos distintos de NUM_REAL


# --------------------------------------------------------------------------- #
# Validación del checksum del MRZ
# --------------------------------------------------------------------------- #

def test_mrz_valido_se_marca_verificado():
    lineas = build_mrz(NUM_REAL)
    assert verify_mrz_number(lineas, NUM_REAL) is True
    pf = parse_page(reverso_digital(NUM_REAL))
    assert pf.numero_documento == NUM_REAL and pf.numero_verificado is True


def test_un_digito_mal_leido_en_el_mrz_invalida_el_checksum():
    for malo in ("1000000017", "1000500007", "2000000007"):
        lineas = build_mrz(NUM_REAL)
        lineas[1] = lineas[1].replace(NUM_REAL, malo)
        assert verify_mrz_number(lineas, malo) is False, f"{malo} deberia invalidar"
        pf = parse_page(reverso_digital(malo, mrz_lines=lineas))
        assert pf.numero_documento == malo and pf.numero_verificado is False


def test_mrz_incompleto_no_se_puede_evaluar():
    lineas = build_mrz(NUM_REAL)
    lineas[0] = lineas[0][:-3]                       # el OCR perdió 3 caracteres de la línea 1
    assert verify_mrz_number(lineas, NUM_REAL) is None
    assert parse_page(reverso_digital(NUM_REAL, mrz_lines=lineas)).numero_verificado is False


def test_digit_differences():
    assert digit_differences("1000000007", "1000000017") == 1
    assert digit_differences("0001000007", "1000007") == 0          # ceros iniciales no cuentan
    assert digit_differences("1000000007", "100000007") is None     # distinta longitud
    assert digit_differences(None, "1000000007") is None


# --------------------------------------------------------------------------- #
# merge: el número verificado prevalece
# --------------------------------------------------------------------------- #

def test_merge_prefiere_numero_verificado_aunque_el_otro_sea_mas_largo():
    frente = ParsedFields(numero_documento="10000000070", fuente="frente_nuevo")           # 11 dígitos, sin validar
    mrz = ParsedFields(numero_documento=NUM_REAL, fuente="mrz", numero_verificado=True)
    merged = frente.merge(mrz)
    assert merged.numero_documento == NUM_REAL and merged.numero_verificado is True
    # y en el otro orden
    merged2 = mrz.merge(frente)
    assert merged2.numero_documento == NUM_REAL and merged2.numero_verificado is True


def test_merge_sin_verificacion_conserva_la_regla_del_mas_largo():
    a = ParsedFields(numero_documento="117660162", fuente="frente_clasico")
    b = ParsedFields(numero_documento="0117660162", fuente="reverso_clasico")
    assert a.merge(b).numero_documento == "0117660162"
    assert a.merge(b).numero_verificado is False


# --------------------------------------------------------------------------- #
# document_grouper
# --------------------------------------------------------------------------- #

def test_digito_mal_leido_en_el_frente_no_parte_a_la_persona():
    docs = group_pages_into_documents(_paginas(frente_digital(NUM_FRENTE_MAL), reverso_digital(NUM_REAL)))
    assert len(docs) == 1, f"se esperaba 1 documento, hay {len(docs)}: {[d.numero_documento for d in docs]}"
    d = docs[0]
    assert d.numero_documento == NUM_REAL, d.numero_documento
    assert d.fields.numero_documento == NUM_REAL and d.fields.numero_verificado is True
    assert d.source_pages == [1, 2]
    assert d.fields.nombre_completo == "MARIA ELENA GARCIA LOPEZ"


def test_frente_digital_no_cuenta_como_documento_con_reverso():
    pf = parse_page(frente_digital(NUM_REAL))
    assert "frente_nuevo" in pf.fuente
    assert _has_back(pf) is False, pf.fuente


def test_no_se_une_si_los_nombres_no_son_compatibles():
    otro = frente_digital(NUM_FRENTE_MAL, apellidos="PEREZ RUIZ", nombres="JUAN CARLOS")
    docs = group_pages_into_documents(_paginas(otro, reverso_digital(NUM_REAL)))
    assert len(docs) == 2


def test_no_se_une_si_difiere_en_mas_de_dos_digitos():
    docs = group_pages_into_documents(_paginas(frente_digital(NUM_MUY_DISTINTO), reverso_digital(NUM_REAL)))
    assert len(docs) == 2


def test_no_se_une_si_el_mrz_no_esta_validado():
    lineas = build_mrz(NUM_REAL)
    lineas[1] = lineas[1].replace(NUM_REAL, "1000000017")     # checksum ya no cuadra
    docs = group_pages_into_documents(
        _paginas(frente_digital(NUM_REAL), reverso_digital("1000000017", mrz_lines=lineas))
    )
    assert len(docs) == 2


def test_no_se_une_a_un_documento_que_ya_tiene_reverso():
    # Página apilada frente+reverso (ya trae MRZ) seguida de otro reverso verificado casi igual
    apilada = frente_digital(NUM_FRENTE_MAL) + "\n" + "\n".join(build_mrz(NUM_FRENTE_MAL))
    docs = group_pages_into_documents(_paginas(apilada, reverso_digital(NUM_REAL)))
    assert len(docs) == 2


def test_numero_igual_sigue_fusionando_como_antes():
    docs = group_pages_into_documents(_paginas(frente_digital(NUM_REAL), reverso_digital(NUM_REAL)))
    assert len(docs) == 1 and docs[0].numero_documento == NUM_REAL and docs[0].source_pages == [1, 2]


# --------------------------------------------------------------------------- #
# matcher: aviso de posible coincidencia
# --------------------------------------------------------------------------- #

def _participante(numero=NUM_REAL, nombre="MARIA ELENA GARCIA LOPEZ"):
    return ExcelParticipant(row_index=7, tipo_documento="CC", numero_documento=numero, nombre_reportado=nombre, estado="Preinscrito")


def test_end_to_end_el_participante_coincide_tras_corregir_el_digito():
    docs = group_pages_into_documents(_paginas(frente_digital(NUM_FRENTE_MAL), reverso_digital(NUM_REAL)))
    res = compare(docs, [_participante()])
    assert res["resumen"]["coinciden"] == 1, res["resumen"]
    assert res["resumen"]["sobrantes_en_pdf"] == 0 and res["resumen"]["no_encontrado_en_pdf"] == 0


def test_aviso_de_posible_coincidencia_cuando_no_se_pudo_fusionar():
    # Frente con el dígito mal leído y SIN reverso en el PDF: no hay forma de corregirlo,
    # pero la fila "no encontrado" debe avisar que existe un documento casi idéntico.
    docs = group_pages_into_documents(_paginas(frente_digital(NUM_FRENTE_MAL)))
    res = compare(docs, [_participante()])
    fila = res["resultados"][0]
    assert fila["estado"] == "no_encontrado_en_pdf"
    assert "POSIBLE COINCIDENCIA" in fila["observaciones"]
    assert NUM_FRENTE_MAL in fila["observaciones"] and "1 dígito" in fila["observaciones"]


def test_sin_aviso_si_el_nombre_no_se_parece():
    docs = group_pages_into_documents(_paginas(frente_digital(NUM_FRENTE_MAL, apellidos="PEREZ RUIZ", nombres="JUAN CARLOS")))
    res = compare(docs, [_participante()])
    assert "POSIBLE COINCIDENCIA" not in res["resultados"][0]["observaciones"]


def test_sin_aviso_si_el_numero_difiere_mucho():
    docs = group_pages_into_documents(_paginas(frente_digital(NUM_MUY_DISTINTO)))
    res = compare(docs, [_participante()])
    assert "POSIBLE COINCIDENCIA" not in res["resultados"][0]["observaciones"]


# --------------------------------------------------------------------------- #
# Ejecución como script
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    pruebas = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    fallos = 0
    for nombre, fn in pruebas:
        try:
            fn()
            print(f"[PASS] {nombre}")
        except Exception as exc:  # noqa: BLE001 - cualquier error cuenta como fallo de la prueba
            fallos += 1
            print(f"[FAIL] {nombre}\n       {type(exc).__name__}: {exc}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas correctas")
    sys.exit(1 if fallos else 0)

"""
Pruebas de regresión - Paso 1: fechas / pies de página tomados como número de cédula
y reversos sin número que fabricaban documentos falsos.

Los textos son sintéticos (nombres y números inventados) pero reproducen la estructura
de las lecturas OCR reales que fallaban.

Uso (desde la carpeta backend):
    python test_regresion_paso1.py
Termina con código 1 si alguna prueba falla. También es compatible con pytest.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.document_grouper import group_pages_into_documents
from app.field_parser import clean_document_number, parse_page
from app.ocr_engine import PageResult

# --------------------------------------------------------------------------- #
# Fixtures sintéticos
# --------------------------------------------------------------------------- #

FRENTE_CLASICO = """REPUBLICA DE COLOMBIA
IDENTIFICACION PERSONAL
CEDULA DE CIUDADANIA
NUMERO
1.000.000.007
GARCIA LOPEZ
APELLIDOS
MARIA ELENA
NOMBRES
FIRMA"""

# Reverso clásico con OCR muy degradado: etiquetas ilegibles, sexo del código de barras
# leído como '1' y un pie de página alfanumérico. Es la estructura de la página que
# generaba un "documento" con la fecha de nacimiento como número.
REVERSO_CLASICO_BORROSO = """ABCDEFGHIJKLMNOPQ
12-MAR-1995
CIUDADX
(DEPTOX)
LTGARENAONENTG
1.60
0+
M
ESTACWRA
0S.BH
SEXD
03-MAY-2016CIUDADY
FEOHAYLMGNRHLEXPOMCKON
SADILECEREOKO
P-1400100-01002866-1-1000000007-20160503
G06105322041
1884248571
Scannedwith
CamScanner"""

REVERSO_CLASICO_BORROSO_OTRA_PERSONA = REVERSO_CLASICO_BORROSO.replace("12-MAR-1995", "20-JUL-1990")

# Reverso de cédula digital: solo MRZ y el nombre impreso del Registrador Nacional.
REVERSO_DIGITAL = """CO
012345678
REGISTRADORNACIONAL
Fulano de Tal Perez
ICCOL012345678901001<<<<<<<<<<
9001015F3501012COL1000000007<3
GARCIA<LOPEZ<<MARIA<ELENA<<<<"""


def _paginas(*textos):
    return [PageResult(page_number=i + 1, raw_text=t) for i, t in enumerate(textos)]


# --------------------------------------------------------------------------- #
# clean_document_number
# --------------------------------------------------------------------------- #

def test_fechas_codigos_y_pies_no_son_numero_de_documento():
    no_son_numero = [
        "28-JUL-1999", "10ABR2024,SANVICENTE", "19-DIC-2000", "02-AGO-2017FLORENC1A",
        "05/04/1987", "1987-04-05", "24 DIC 1995", "08JUL1989",
        "0043842199A1",                                      # pie alfanumérico
        "P-1400100-01002866-1-1137563913-20180507",          # código de barras con sexo mal leído
        "A-4400100-00686192-M-0096333300-20150411",          # código de barras
        "1.57", "0134",
    ]
    for texto in no_son_numero:
        assert clean_document_number(texto) is None, f"{texto!r} no debe ser número (modo normal)"
        assert clean_document_number(texto, strict=True) is None, f"{texto!r} no debe ser número (modo estricto)"


def test_numeros_validos_se_siguen_leyendo():
    validos = {
        "1.110.487.315": "1110487315",
        "NUIP1.117.811.433": "1117811433",
        "NUMERO96.333.300": "96333300",
        "1:117.513.499": "1117513499",
        "12.234.223": "12234223",
        "1 110 487 315": "1110487315",
        "1110487315": "1110487315",
    }
    for texto, esperado in validos.items():
        assert clean_document_number(texto) == esperado, f"{texto!r} -> {clean_document_number(texto)!r}"


def test_modo_estricto_solo_acepta_formato_impreso():
    assert clean_document_number("1.110.487.315", strict=True) == "1110487315"
    assert clean_document_number("1110487315", strict=True) is None


# --------------------------------------------------------------------------- #
# parse_page sobre reversos
# --------------------------------------------------------------------------- #

def test_reverso_clasico_borroso_no_inventa_numero_ni_nombres():
    pf = parse_page(REVERSO_CLASICO_BORROSO)
    assert pf.numero_documento is None, f"número inventado: {pf.numero_documento}"
    assert pf.nombres is None and pf.apellidos is None, f"nombres inventados: {pf.nombres} / {pf.apellidos}"
    assert pf.fecha_nacimiento == "1995-03-12", pf.fecha_nacimiento
    assert pf.fuente == "reverso_clasico", pf.fuente


def test_reverso_digital_no_toma_el_nombre_del_registrador():
    pf = parse_page(REVERSO_DIGITAL)
    assert pf.numero_documento == "1000000007", pf.numero_documento
    assert pf.apellidos == "GARCIA LOPEZ", pf.apellidos
    assert pf.nombres == "MARIA ELENA", pf.nombres
    assert "FULANO" not in (pf.apellidos or "") + (pf.nombres or "")
    assert pf.fuente == "mrz", pf.fuente


def test_numero_antes_de_su_etiqueta_se_sigue_leyendo():
    # El OCR a veces entrega el valor y después la etiqueta; también sin puntos de miles.
    for numero_impreso in ("1.000.000.007", "1000000007"):
        texto = f"""REPUBLICA DE COLOMBIA
IDENTIFICACIONPERSONAL
CEDULADECIUDADANIA
{numero_impreso}
NUMERO
GARCIA LOPEZ
APELLIDOS
MARIA ELENA
NOMBRES"""
        pf = parse_page(texto)
        assert pf.numero_documento == "1000000007", f"{numero_impreso!r} -> {pf.numero_documento!r}"
        assert pf.apellidos == "GARCIA LOPEZ" and pf.nombres == "MARIA ELENA"


def test_frente_clasico_no_cambia():
    pf = parse_page(FRENTE_CLASICO)
    assert pf.numero_documento == "1000000007"
    assert pf.apellidos == "GARCIA LOPEZ" and pf.nombres == "MARIA ELENA"
    assert pf.fuente == "frente_clasico"


# --------------------------------------------------------------------------- #
# document_grouper
# --------------------------------------------------------------------------- #

def test_reverso_sin_numero_se_une_al_frente_anterior():
    docs = group_pages_into_documents(_paginas(FRENTE_CLASICO, REVERSO_CLASICO_BORROSO))
    assert len(docs) == 1, f"se esperaba 1 documento, hay {len(docs)}: {[d.numero_documento for d in docs]}"
    d = docs[0]
    assert d.numero_documento == "1000000007"
    assert d.fields.fecha_nacimiento == "1995-03-12"
    assert d.source_pages == [1, 2]


def test_segundo_reverso_sin_numero_no_contamina_al_documento_anterior():
    docs = group_pages_into_documents(
        _paginas(FRENTE_CLASICO, REVERSO_CLASICO_BORROSO, REVERSO_CLASICO_BORROSO_OTRA_PERSONA)
    )
    assert len(docs) == 2, f"se esperaban 2 documentos, hay {len(docs)}"
    assert docs[0].fields.fecha_nacimiento == "1995-03-12"
    assert docs[0].source_pages == [1, 2]
    assert docs[1].numero_documento is None          # queda visible en "sin número detectado"
    assert docs[1].fields.fecha_nacimiento == "1990-07-20"
    assert docs[1].source_pages == [3]


def test_reverso_digital_con_mismo_numero_se_une_al_frente():
    frente_digital = """CEDULA DE
REPUBLICA DE COLOMBIA
CIUDADANIA
NUIP 1.000.000.007
Apellidos
GARCIA LOPEZ
Nombres
MARIA ELENA
Nacionalidad
COL"""
    docs = group_pages_into_documents(_paginas(frente_digital, REVERSO_DIGITAL))
    assert len(docs) == 1, f"hay {len(docs)} documentos"
    assert docs[0].numero_documento == "1000000007"
    assert docs[0].fields.nombre_completo == "MARIA ELENA GARCIA LOPEZ"


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

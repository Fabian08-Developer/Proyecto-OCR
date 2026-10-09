"""
Pruebas de regresión - Paso 6: nombres mal leídos que el sistema daba por "Coincide" (97 % y 100 %).

Causas (vistas en un PDF real, reproducidas aquí con datos inventados):
  1. La marca de agua "COLOMBIA" impresa sobre el campo NOMBRES se leyó como "COLONBI" y el
     analizador la tomó como el nombre en lugar del nombre real.
  2. Una letra tapada por la marca de agua ("ALEXANDEA" en vez de "ALEXANDER") se partía en
     "ALEX ANDEA" porque ALEX está en el léxico, y el comparador lo daba por idéntico (100 %).

Uso (desde la carpeta backend):
    python test_regresion_paso6.py
Termina con código 1 si alguna prueba falla. También es compatible con pytest.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from app.document_grouper import DocumentRecord
from app.excel_reader import ExcelParticipant
from app.field_parser import (
    ParsedFields,
    _k_de_relleno_a_signo,
    clean_name_line,
    is_header_or_stopword,
    palabras_conocidas_cercanas,
    parse_mrz,
    parse_page,
    split_concatenated_name,
)
from app.matcher import MAX_SIMILARITY_IF_DIFFERENT, _name_similarity, compare, names_are_identical
from app.ocr_engine import OcrLine
from app.ocr_refine import corregir_texto, refinar_lineas
from test_regresion_paso2 import build_mrz  # generador de MRZ válido (checksum ICAO)


# --------------------------------------------------------------------------- #
# Marca de agua COLOMBIA leída como nombre
# --------------------------------------------------------------------------- #

FRENTE_CON_MARCA = """REPUBLICA DE COLOMBIA
IDENTIFICACIONPERSONAL
CEDULADECIUDADANIA
NUMERO12.345.678
GARCIA LOPEZ
APELLIDOS
RETUELCAE
RODRIGO
COLONBI
NOMORES
04-ABR-1982
FECHA DE NACIMIENTO
FIRMA"""


def test_marca_de_agua_deformada_no_es_un_nombre():
    for palabra in ("COLONBI", "COLOMBI", "COLOMBLA", "COLOMBIA", "REPUBLIC", "REPUBLICA"):
        assert is_header_or_stopword(palabra), f"{palabra} deberia tratarse como encabezado"
        assert clean_name_line(palabra) is None, f"{palabra} no puede ser un nombre"


def test_nombres_y_apellidos_parecidos_no_se_confunden_con_la_marca():
    for nombre in ("COLOMBO", "COLON", "RODRIGO", "REINALDO", "MARIA", "ALEXANDER", "CAROLINA"):
        assert not is_header_or_stopword(nombre), f"{nombre} es un nombre valido"
    assert clean_name_line("COLOMBO") == "COLOMBO"


def test_el_nombre_real_gana_a_la_marca_de_agua():
    pf = parse_page(FRENTE_CON_MARCA)
    assert pf.nombres == "RODRIGO", pf.nombres
    assert pf.apellidos == "GARCIA LOPEZ", pf.apellidos
    assert pf.fecha_nacimiento == "1982-04-04"


def test_la_marca_junto_al_nombre_en_la_misma_linea_se_descarta():
    assert clean_name_line("RODRIGO COLONBI") == "RODRIGO"


def test_una_linea_con_fecha_nunca_es_un_nombre():
    for linea in ("04-ABR-1982", "12-JUN-2000 ELPAUJIL", "17-JUL-2012 GARZON"):
        assert clean_name_line(linea) is None, linea
    # y un nombre con algún dígito de ruido sigue leyéndose
    assert clean_name_line("MARIA1") == "MARIA"


# --------------------------------------------------------------------------- #
# Nombres pegados: solo se parten si todas las piezas son conocidas
# --------------------------------------------------------------------------- #

def test_no_inventa_nombres_al_partir_una_palabra_mal_leida():
    assert split_concatenated_name("ALEXANDEA") == "ALEXANDEA"      # ALEXANDER con la R tapada
    assert split_concatenated_name("ALEXANDRE") == "ALEXANDRE"      # casi ALEXANDER/ALEXANDRA
    assert split_concatenated_name("ALEJANDRA") == "ALEJANDRA"      # casi ALEJANDRO


def test_no_parte_cuando_el_resto_es_solo_el_final_de_otro_nombre():
    assert split_concatenated_name("JUANITA") == "JUANITA"          # JUAN + ITA
    assert split_concatenated_name("ANGELICA") == "ANGELICA"        # ANGEL + ICA


def test_sigue_partiendo_palabras_pegadas_aunque_una_pieza_no_este_en_el_lexicon():
    assert split_concatenated_name("MONTOYAMANCERA") == "MONTOYA MANCERA"
    assert split_concatenated_name("CHAROMOSQUERA") == "CHARO MOSQUERA"
    assert split_concatenated_name("ESCALANTELOPEZ") == "ESCALANTE LOPEZ"
    assert split_concatenated_name("CARLOSANDRESMONTOYA") == "CARLOS ANDRES MONTOYA"
    assert split_concatenated_name("DIAZPAREJA") == "DIAZ PAREJA"                      # PAREJA no esta
    assert split_concatenated_name("RODRIGUEZVALDERRAMA") == "RODRIGUEZ VALDERRAMA"
    assert split_concatenated_name("MARIAEUGENIA") == "MARIA EUGENIA"
    assert split_concatenated_name("VANESSAALEXANDRA") == "VANESSA ALEXANDRA"          # prefijo desconocido
    assert split_concatenated_name("MAURICIO") == "MAURICIO"
    assert split_concatenated_name("DELAHOZ") == "DE LA HOZ"


FRENTE_LETRA_TAPADA = """REPUBLICA DE COLOMBIA
IDENTIFICACION PERSONAL
CEDULA DE CIUDADANIA
1.234.567.890
NUMERO
GARCIA LOPEZ
APELLIDOS
ANDRES ALEXANDEA
NOMBRES
FIRMA"""


def test_una_letra_mal_leida_no_se_convierte_en_dos_nombres():
    pf = parse_page(FRENTE_LETRA_TAPADA)
    assert pf.nombres == "ANDRES ALEXANDEA", pf.nombres
    assert pf.apellidos == "GARCIA LOPEZ", pf.apellidos


# --------------------------------------------------------------------------- #
# Segunda lectura: una letra tapada por la marca de agua (ALEXANDER leído como ALEXANDEA)
# --------------------------------------------------------------------------- #

def test_reconoce_palabras_que_parecen_un_nombre_conocido_mal_leido():
    assert palabras_conocidas_cercanas("ALEXANDEA") == ["ALEXANDER", "ALEXANDRA"]
    assert palabras_conocidas_cercanas("ALEXANDER") == []        # ya es un nombre conocido
    assert palabras_conocidas_cercanas("GARAVITO") == []         # apellido que no se parece a ninguno
    assert palabras_conocidas_cercanas("PEREZ") == []            # muy corta


def test_corrige_solo_con_lecturas_que_coinciden():
    lecturas = [["ANDRES", "ALEXANDER"], ["ALEXANDER"], ["ALEXANDEH"], ["ALEXANDEA"]]
    assert corregir_texto("ANDRES ALEXANDEA", lecturas) == "ANDRES ALEXANDER"
    # una sola lectura con el nombre conocido no basta
    assert corregir_texto("ANDRES ALEXANDEA", [["ALEXANDER"], ["ALEXANDEH"], ["ALEXANDEY"]]) == "ANDRES ALEXANDEA"
    # dos nombres conocidos empatados: no hay forma de saber cuál es
    empate = [["ALEXANDER"], ["ALEXANDER"], ["ALEXANDRA"], ["ALEXANDRA"]]
    assert corregir_texto("ANDRES ALEXANDEA", empate) == "ANDRES ALEXANDEA"
    # una palabra que ya es un nombre conocido o sin parecido no se toca
    assert corregir_texto("ANDRES ALEXANDER", [["ALEXANDRA"], ["ALEXANDRA"]]) == "ANDRES ALEXANDER"
    assert corregir_texto("GARCIA GARAVITO", [["ALEXANDER"], ["ALEXANDER"]]) == "GARCIA GARAVITO"


def _linea(texto):
    return OcrLine(text=texto, confidence=0.95, box=[[10, 10], [310, 10], [310, 60], [10, 60]])


def test_refinar_lineas_corrige_la_linea_dudosa_y_no_relee_las_demas():
    llamadas = []
    respuestas = [["ALEXANDER"], ["ALEXANDER"], ["ALEXANDEH"], ["ALEXANDEA"]]

    def lector(imagen):
        llamadas.append(imagen.size)
        return respuestas[len(llamadas) - 1]

    dudosa, normal = _linea("ANDRES ALEXANDEA"), _linea("GARCIA LOPEZ")
    cambiadas = refinar_lineas(Image.new("RGB", (400, 100), "white"), [normal, dudosa], lector)
    assert cambiadas == 1
    assert dudosa.text == "ANDRES ALEXANDER" and normal.text == "GARCIA LOPEZ"
    assert len(llamadas) == 4                                # solo la línea dudosa se relee, 4 preparaciones


def test_refinar_lineas_sobrevive_a_un_fallo_de_la_segunda_lectura():
    def lector(imagen):
        raise RuntimeError("fallo del OCR")

    linea = _linea("ANDRES ALEXANDEA")
    assert refinar_lineas(Image.new("RGB", (400, 100), "white"), [linea], lector) == 0
    assert linea.text == "ANDRES ALEXANDEA"               # se conserva la primera lectura


# --------------------------------------------------------------------------- #
# MRZ: la K de un nombre real no es un "<" mal leído
# --------------------------------------------------------------------------- #

def test_k_de_relleno_se_convierte_y_la_k_real_se_conserva():
    assert _k_de_relleno_a_signo("PEREZ<GOMEZK<WILKIN<ANDRESKKKK") == "PEREZ<GOMEZ<<WILKIN<ANDRES<<<<"
    assert _k_de_relleno_a_signo("PEREZ<GOMEZ<<WILKIN<ANDRES<<<<") == "PEREZ<GOMEZ<<WILKIN<ANDRES<<<<"
    assert _k_de_relleno_a_signo("RIOS<<ERIKA<YAKELIN<<<<<<") == "RIOS<<ERIKA<YAKELIN<<<<<<"
    assert _k_de_relleno_a_signo("K") == "<"


def test_k_entre_dos_palabras_largas_es_un_separador_mal_leido():
    assert _k_de_relleno_a_signo("RIOS<<LIDAKYASMIN<<<") == "RIOS<<LIDA<YASMIN<<<"


def test_k_impresa_en_el_frente_se_conserva():
    # sin el frente, estas K parecen relleno; con el nombre impreso en el frente son reales
    assert _k_de_relleno_a_signo("RIOS<<NICKOLAS<<<<") == "RIOS<<NIC<OLAS<<<<"
    assert _k_de_relleno_a_signo("RIOS<<NICKOLAS<<<<", frente="RIOSNICKOLAS") == "RIOS<<NICKOLAS<<<<"
    assert _k_de_relleno_a_signo("RIOS<<ERICK<<<<", frente="RIOSERICK") == "RIOS<<ERICK<<<<"
    assert _k_de_relleno_a_signo("RIOS<<KEVIN<KAREN<<<<", frente="RIOSKEVINKAREN") == "RIOS<<KEVIN<KAREN<<<<"


def _nombres_del_mrz(l3: str, frente=()):
    lineas = list(frente) + build_mrz("1000000007")
    lineas[len(frente) + 2] = l3
    pf = parse_mrz(lineas)
    assert pf is not None
    return pf.apellidos, pf.nombres


def test_mrz_conserva_la_k_de_wilkin():
    assert _nombres_del_mrz("PEREZ<GOMEZ<<WILKIN<ANDRES<<<<<<<<") == ("PEREZ GOMEZ", "WILKIN ANDRES")


def test_mrz_con_relleno_leido_como_k_sigue_funcionando():
    # el OCR leyó "<<" como "K<" y el relleno final como "KKKK"
    assert _nombres_del_mrz("PEREZ<GOMEZK<WILKIN<ANDRESKKKK") == ("PEREZ GOMEZ", "WILKIN ANDRES")
    assert _nombres_del_mrz("RIOS<LOPEZK<ERIKA<YAKELINKKKKKK") == ("RIOS LOPEZ", "ERIKA YAKELIN")
    # separador entre dos nombres leído como K
    assert _nombres_del_mrz("RIOS<<LIDAKYASMIN<<<<<<<<<<<<") == ("RIOS", "LIDA YASMIN")


def test_mrz_usa_el_frente_de_la_pagina_para_conservar_una_k_real():
    frente = ("RIOSLOPEZ", "NICKOLASANDRES")
    assert _nombres_del_mrz("RIOS<LOPEZ<<NICKOLAS<ANDRES<<<<", frente) == ("RIOS LOPEZ", "NICKOLAS ANDRES")


# --------------------------------------------------------------------------- #
# Comparador: el 100 % es solo para nombres idénticos
# --------------------------------------------------------------------------- #

def _doc(numero, nombres, apellidos, nac="1990-01-01", paginas=(1,)):
    campos = ParsedFields(
        numero_documento=numero, nombres=nombres, apellidos=apellidos, fecha_nacimiento=nac, fuente="frente_clasico"
    )
    return DocumentRecord(numero_documento=numero, fields=campos, source_pages=list(paginas))


def _participante(numero, nombre):
    return ExcelParticipant(row_index=1, tipo_documento="CC", numero_documento=numero, nombre_reportado=nombre)


def test_nombres_identicos_ignoran_tildes_espacios_y_orden():
    assert names_are_identical("RUBEN VANEGAS MUÑOZ", "RUBEN VANEGAS MUNOZ")
    assert names_are_identical("MARIA FERNANDA GOMEZ RUIZ", "MARIAFERNANDA GOMEZ RUIZ")
    assert names_are_identical("GOMEZ RUIZ MARIA", "MARIA GOMEZ RUIZ")
    assert not names_are_identical("ANDRES ALEXANDER GARCIA LOPEZ", "ANDRES ALEX ANDEA GARCIA LOPEZ")
    assert not names_are_identical("ANDRES ALEXANDER GARCIA LOPEZ", "ANDRES ALEXANDEA GARCIA LOPEZ")
    assert not names_are_identical(None, "MARIA")


def test_similitud_no_llega_a_100_si_hay_una_letra_distinta():
    excel = "ANDRES ALEXANDER GARCIA LOPEZ"
    assert _name_similarity(excel, "ANDRES ALEXANDER GARCIA LOPEZ") == 1.0
    assert _name_similarity(excel, "ANDRES ALEXANDEA GARCIA LOPEZ") <= MAX_SIMILARITY_IF_DIFFERENT
    # el caso que daba 100 %: ALEX contenido en ALEXANDER
    assert _name_similarity(excel, "ANDRES ALEX ANDEA GARCIA LOPEZ") <= MAX_SIMILARITY_IF_DIFFERENT
    # el caso que daba 97 % con un nombre de pila equivocado sigue por debajo del tope
    assert _name_similarity("RODRIGO GARCIA LOPEZ", "COLONBI GARCIA LOPEZ") < 1.0


def test_coincide_con_nombre_distinto_deja_una_observacion():
    r = compare(
        [_doc("1234567890", "ANDRES ALEXANDEA", "GARCIA LOPEZ")],
        [_participante("1234567890", "ANDRES ALEXANDER GARCIA LOPEZ")],
    )["resultados"][0]
    assert r["estado"] == "coincide"                       # el número manda: el criterio no cambia
    assert r["similitud_nombre"] <= MAX_SIMILARITY_IF_DIFFERENT
    assert r["observaciones"] and "ALEXANDEA" in r["observaciones"] and "ALEXANDER" in r["observaciones"]


def test_coincide_con_nombre_identico_no_lleva_observacion():
    for nombre_ocr in ("ANDRES ALEXANDER", "ANDRES  ALEXANDER"):
        r = compare(
            [_doc("1234567890", nombre_ocr, "GARCIA LOPEZ")],
            [_participante("1234567890", "ANDRES ALEXANDER GARCIA LOPEZ")],
        )["resultados"][0]
        assert r["estado"] == "coincide"
        assert r["similitud_nombre"] == 1.0
        assert r["observaciones"] is None


def test_un_nombre_muy_distinto_sigue_siendo_nombre_no_coincide():
    r = compare(
        [_doc("1234567890", "PEDRO", "RAMIREZ SOTO")],
        [_participante("1234567890", "ANDRES ALEXANDER GARCIA LOPEZ")],
    )["resultados"][0]
    assert r["estado"] == "nombre_no_coincide"


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

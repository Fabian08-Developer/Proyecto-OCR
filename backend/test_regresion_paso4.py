"""
Pruebas de regresión - Paso 4: el servidor se congelaba durante cada OCR.

Causa: /api/comparar y /api/extract-pdf eran "async def" pero ejecutaban el OCR de forma
síncrona, así que bloqueaban el bucle de eventos: mientras duraba el OCR (minutos) nadie
podía iniciar sesión ni abrir la interfaz, y /api/health no respondía. Además el PDF se
renderizaba completo (todas las páginas a 300 DPI) antes de empezar.

La prueba con servidor real (latencia de /api/health durante un OCR) no cabe en una prueba
unitaria; aquí se verifica cada pieza del arreglo. Datos inventados.

Uso (desde la carpeta backend):
    python test_regresion_paso4.py
Termina con código 1 si alguna prueba falla. También es compatible con pytest.
"""
import contextlib
import inspect
import itertools
import os
import sys
import threading
import time
import types
from pathlib import Path

# Importar la aplicación carga la clave de firma; con una definida no se crea backend/.ocr_secret_key
os.environ.setdefault("OCR_SECRET_KEY", "clave-de-prueba-solo-para-los-tests-0123456789")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf
from fastapi import HTTPException

from app import main, ocr_engine


def pdf_de(n_paginas: int, texto: str = "") -> bytes:
    d = pymupdf.open()
    for i in range(n_paginas):
        p = d.new_page()
        if texto:
            p.insert_text((50, 80), f"{texto} {i + 1}", fontsize=18)
    return d.tobytes()


# --------------------------------------------------------------------------- #
# Los endpoints que hacen OCR no bloquean el bucle de eventos
# --------------------------------------------------------------------------- #

def test_ningun_endpoint_que_hace_trabajo_pesado_es_async():
    for nombre in ("api_comparar", "api_extract_pdf", "api_parse_excel"):
        fn = getattr(main, nombre)
        assert not inspect.iscoroutinefunction(fn), (
            f"{nombre} es 'async def': ejecutaría el OCR/lectura dentro del bucle de eventos y congelaría el servidor"
        )


# --------------------------------------------------------------------------- #
# Control de turnos de OCR
# --------------------------------------------------------------------------- #

def _ocupar_turno(entradas, salir, adentro, max_adentro):
    with main._ocr_turn():
        with entradas["lock"]:
            adentro[0] += 1
            max_adentro[0] = max(max_adentro[0], adentro[0])
            entradas["n"] += 1
        salir.wait(timeout=10)
        with entradas["lock"]:
            adentro[0] -= 1


def test_los_ocr_se_procesan_de_a_uno_y_el_excedente_recibe_503():
    assert main._ocr_en_proceso == 0
    salir = threading.Event()
    entradas = {"lock": threading.Lock(), "n": 0}
    adentro, max_adentro = [0], [0]
    hilos = [threading.Thread(target=_ocupar_turno, args=(entradas, salir, adentro, max_adentro)) for _ in range(3)]
    for h in hilos:
        h.start()
    try:
        # 1 ejecutando + 2 esperando turno = 3 en proceso
        for _ in range(100):
            if main._ocr_en_proceso == 3:
                break
            time.sleep(0.02)
        assert main._ocr_en_proceso == 3, main._ocr_en_proceso
        assert adentro[0] == 1, f"solo uno debe estar ejecutando; hay {adentro[0]}"

        try:
            with main._ocr_turn():
                raise AssertionError("el 4.º documento no debia obtener turno")
        except HTTPException as exc:
            assert exc.status_code == 503
            assert exc.headers.get("Retry-After") == "60"
            assert "otros documentos" in exc.detail
    finally:
        salir.set()
        for h in hilos:
            h.join(timeout=15)

    assert entradas["n"] == 3, "los 3 primeros debian terminar su turno"
    assert max_adentro[0] == 1, f"hubo {max_adentro[0]} OCR a la vez"
    assert main._ocr_en_proceso == 0, "el contador debe volver a 0"


def test_el_turno_se_libera_aunque_el_ocr_falle():
    for _ in range(5):                       # si fugara turnos, a la tercera vuelta daria 503
        try:
            with main._ocr_turn():
                raise RuntimeError("fallo simulado del OCR")
        except RuntimeError:
            pass
    assert main._ocr_en_proceso == 0
    with main._ocr_turn():                   # y todavia hay turno disponible
        pass


# --------------------------------------------------------------------------- #
# Validación del PDF antes de ocupar turno
# --------------------------------------------------------------------------- #

def test_pdf_valido_cuenta_sus_paginas():
    assert ocr_engine.count_pdf_pages(pdf_de(3)) == 3
    assert main._validate_pdf(pdf_de(2)) == 2


def test_pdf_danado_se_rechaza_con_422_sin_gastar_ocr():
    try:
        ocr_engine.count_pdf_pages(b"esto no es un pdf")
    except ValueError as exc:
        assert "no es un PDF válido" in str(exc)
    else:
        raise AssertionError("debia lanzar ValueError")
    try:
        main._validate_pdf(b"esto no es un pdf")
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("debia lanzar HTTPException 422")


def test_pdf_con_contrasena_se_rechaza_con_mensaje_claro():
    d = pymupdf.open()
    d.new_page()
    protegido = d.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secreto", owner_pw="secreto")
    try:
        ocr_engine.count_pdf_pages(protegido)
    except ValueError as exc:
        assert "contraseña" in str(exc)
    else:
        raise AssertionError("debia rechazar el PDF protegido")


# --------------------------------------------------------------------------- #
# Lectura página por página
# --------------------------------------------------------------------------- #

def test_iter_pdf_images_es_perezoso():
    gen = ocr_engine.iter_pdf_images(pdf_de(3), dpi=72)
    assert inspect.isgenerator(gen)
    numero, total, img = next(gen)
    assert (numero, total) == (1, 3) and img.size[0] > 0
    assert [n for n, _, _ in gen] == [2, 3]


@contextlib.contextmanager
def _ocr_simulado(ocr_falso):
    """Sustituye el OCR y el préstamo de motores (no se carga ningún modelo real)."""
    original_ocr, original_prestamo = ocr_engine.run_ocr_on_image, ocr_engine._motor_prestado
    ocr_engine.run_ocr_on_image = ocr_falso
    ocr_engine._motor_prestado = lambda: contextlib.nullcontext(None)
    try:
        yield
    finally:
        ocr_engine.run_ocr_on_image, ocr_engine._motor_prestado = original_ocr, original_prestamo


def test_extract_pdf_no_renderiza_todas_las_paginas_antes_de_leer():
    """Con la lectura anterior se creaban las N imágenes antes del primer OCR (N x ~26 MB a 300 DPI)."""
    creadas = []
    original_frombytes = ocr_engine.Image.frombytes

    def frombytes_contando(*a, **k):
        creadas.append(1)
        return original_frombytes(*a, **k)

    vistas_al_leer = {}

    candado = threading.Lock()

    def ocr_falso(image, engine=None):
        with candado:                                  # k-ésima lectura pedida -> imágenes ya creadas
            vistas_al_leer[len(vistas_al_leer) + 1] = len(creadas)
        time.sleep(0.05)
        return ocr_engine.PageResult(page_number=0, raw_text="x", image=image)

    ocr_engine.Image.frombytes = frombytes_contando
    try:
        with _ocr_simulado(ocr_falso):
            pages = ocr_engine.extract_pdf(pdf_de(8), dpi=72)
    finally:
        ocr_engine.Image.frombytes = original_frombytes

    # Mientras se lee la k-ésima página, como mucho hay OCR_TRABAJADORES páginas más ya renderizadas
    limite = ocr_engine.OCR_TRABAJADORES
    assert len(creadas) == 8
    assert all(v <= k + limite for k, v in vistas_al_leer.items()), f"demasiadas imágenes vivas: {vistas_al_leer}"
    assert [p.page_number for p in pages] == list(range(1, 9))
    assert all(p.image is None for p in pages)


def test_extract_pdf_lee_varias_paginas_a_la_vez_y_conserva_el_orden():
    activas, maximo, candado, contador = [0], [0], threading.Lock(), itertools.count()

    def ocr_falso(image, engine=None):
        with candado:
            activas[0] += 1
            maximo[0] = max(maximo[0], activas[0])
            n = next(contador)
        time.sleep(0.15 if n % 2 == 0 else 0.05)      # las impares terminan antes que la anterior
        with candado:
            activas[0] -= 1
        return ocr_engine.PageResult(page_number=0, raw_text="x", image=image)

    with _ocr_simulado(ocr_falso):
        pages = ocr_engine.extract_pdf(pdf_de(6), dpi=72)

    assert [p.page_number for p in pages] == [1, 2, 3, 4, 5, 6]
    assert maximo[0] == min(ocr_engine.OCR_TRABAJADORES, 6), f"páginas a la vez: {maximo[0]}"


def test_extract_pdf_propaga_el_error_de_una_pagina():
    def ocr_falso(image, engine=None):
        raise RuntimeError("OCR roto")

    with _ocr_simulado(ocr_falso):
        try:
            ocr_engine.extract_pdf(pdf_de(5), dpi=72)
        except RuntimeError as exc:
            assert "OCR roto" in str(exc)
        else:
            raise AssertionError("debia propagar el error del OCR")


def test_el_motor_se_crea_sin_spinning_y_sin_dejar_cambios_en_rapidocr():
    from rapidocr_onnxruntime.utils import infer_engine

    antes = infer_engine.OrtInferSession._init_sess_opts
    motor = ocr_engine._crear_motor()
    assert infer_engine.OrtInferSession._init_sess_opts is antes, "el ajuste debe ser solo durante la creación"
    for sesion in (motor.text_det.infer, motor.text_cls.infer, motor.text_rec.session):  # nombres de RapidOCR
        opciones = sesion.session.get_session_options()
        assert opciones.get_session_config_entry("session.intra_op.allow_spinning") == "0"


def test_el_resultado_es_identico_al_de_renderizar_todo_primero():
    contenido = pdf_de(2, "PRUEBA DE LECTURA")
    nuevo = ocr_engine.extract_pdf(contenido, dpi=150)
    viejo = [ocr_engine.run_ocr_on_image(img) for img in ocr_engine.pdf_to_images(contenido, dpi=150)]
    assert [p.raw_text for p in nuevo] == [p.raw_text for p in viejo]
    assert any("PRUEBA" in p.raw_text.upper() for p in nuevo), "el OCR deberia haber leido el texto de prueba"


# --------------------------------------------------------------------------- #
# Motor OCR: inicialización segura con varios hilos
# --------------------------------------------------------------------------- #

def test_el_motor_ocr_se_crea_una_sola_vez_aunque_lo_pidan_varios_hilos():
    instancias = []

    class RapidOCRFalso:
        def __init__(self):
            time.sleep(0.2)                  # carga lenta: da tiempo a que otros hilos entren
            instancias.append(self)

    modulo_falso = types.ModuleType("rapidocr_onnxruntime")
    modulo_falso.RapidOCR = RapidOCRFalso
    original_modulo = sys.modules.get("rapidocr_onnxruntime")
    original_motor = ocr_engine._ocr_engine
    sys.modules["rapidocr_onnxruntime"] = modulo_falso
    ocr_engine._ocr_engine = None
    try:
        resultados = []
        hilos = [threading.Thread(target=lambda: resultados.append(ocr_engine.get_ocr_engine())) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=10)
        assert len(instancias) == 1, f"se cargaron {len(instancias)} motores (cada uno ~500 MB)"
        assert len({id(r) for r in resultados}) == 1
    finally:
        ocr_engine._ocr_engine = original_motor
        if original_modulo is not None:
            sys.modules["rapidocr_onnxruntime"] = original_modulo
        else:
            sys.modules.pop("rapidocr_onnxruntime", None)


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

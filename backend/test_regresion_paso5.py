"""
Pruebas de regresión - Paso 5: seguridad de acceso.

Fallas cubiertas (todas verificadas en el sistema original):
  - La clave de firma de sesiones por defecto estaba en el repositorio: cualquiera podia firmar
    un token de administrador (se comprobó: un token forjado fue aceptado).
  - Usuario inicial admin/admin123 fijo y sin forma de cambiarlo.
  - Sin limite de intentos de login (40 intentos fallidos en 11 s, todos aceptados para probar).
  - CORS abierto a cualquier origen ("*").
  - Eliminar un registro del historial dejaba el PDF y el Excel (cedulas) en disco.
  - Comparar contra la lista de usuarios revelaba si un usuario existia (tiempos de respuesta).

Datos inventados. Usa bases de datos y carpetas temporales: no toca ocr_system.db ni storage/.
Uso (desde la carpeta backend):
    python test_regresion_paso5.py
Termina con código 1 si alguna prueba falla. También es compatible con pytest.
"""
import asyncio
import contextlib
import logging
import os
import shutil
import sys
import tempfile
import threading
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Antes de importar la aplicación: una clave de prueba propia para no crear backend/.ocr_secret_key
os.environ["OCR_SECRET_KEY"] = "clave-de-prueba-solo-para-los-tests-0123456789"
os.environ.pop("OCR_ADMIN_PASSWORD", None)
os.environ.pop("OCR_CORS_ORIGINS", None)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import HTTPException
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import gestionar_usuarios as cli
from app import auth, database, main, password_policy
from app.database import Base, Comparacion, Usuario
from app.routers import auth_router, historial_router
from app.throttle import MAX_FALLOS_IP, MAX_FALLOS_USUARIO, BLOQUEO_S, LoginThrottle

PASSWORD_BUENA = "Clave-Segura-2026"


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #

@contextlib.contextmanager
def entorno_temporal():
    """BD SQLite y storage/ temporales; parchea el módulo database y lo restaura al salir."""
    tmp = Path(tempfile.mkdtemp(prefix="ocr_test_"))
    storage = tmp / "storage"
    storage.mkdir()
    eng = create_engine(f"sqlite:///{tmp / 't.db'}", connect_args={"check_same_thread": False})
    sesion = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    originales = (database.engine, database.SessionLocal, database._STORAGE_DIR)
    database.engine, database.SessionLocal, database._STORAGE_DIR = eng, sesion, storage
    Base.metadata.create_all(bind=eng)
    try:
        yield tmp, storage, sesion
    finally:
        database.engine, database.SessionLocal, database._STORAGE_DIR = originales
        eng.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


class CapturaLog(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.registros = []

    def emit(self, record):
        self.registros.append((record.levelno, record.getMessage()))


@contextlib.contextmanager
def capturar_logs():
    h = CapturaLog()
    log = logging.getLogger("ocr_system")
    nivel = log.level
    log.addHandler(h)
    log.setLevel(logging.DEBUG)
    try:
        yield h
    finally:
        log.removeHandler(h)
        log.setLevel(nivel)


def crear_usuario(db, username="ana", password=PASSWORD_BUENA, activo=True):
    u = Usuario(username=username, nombre=username.title(), password_hash=Usuario.hash_password(password), is_active=activo)
    db.add(u)
    db.commit()
    return u


def falso_request(ip="10.0.0.1"):
    return types.SimpleNamespace(client=types.SimpleNamespace(host=ip))


def formulario(usuario, password):
    return types.SimpleNamespace(username=usuario, password=password)


@contextlib.contextmanager
def throttle_limpio():
    original = auth_router.login_throttle
    auth_router.login_throttle = LoginThrottle()
    try:
        yield auth_router.login_throttle
    finally:
        auth_router.login_throttle = original


def esperar_http(coro):
    return asyncio.run(coro)


async def llamar_asgi(method, path, headers=None):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "server": ("test", 80), "client": ("1.2.3.4", 1234),
    }
    mensajes = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        mensajes.append(m)

    await main.app(scope, receive, send)
    inicio = next(m for m in mensajes if m["type"] == "http.response.start")
    return inicio["status"], {k.decode().lower(): v.decode() for k, v in inicio["headers"]}


# --------------------------------------------------------------------------- #
# Clave de firma
# --------------------------------------------------------------------------- #

def test_clave_de_entorno_valida_se_usa():
    clave = "x" * 40
    assert auth.cargar_clave_firma(clave, Path("no-se-usa")) == clave


def test_la_clave_publica_antigua_se_rechaza_aunque_la_configuren():
    try:
        auth.cargar_clave_firma(auth.CLAVE_PUBLICA_ANTIGUA, Path("no-se-usa"))
    except RuntimeError as exc:
        assert "no es segura" in str(exc)
    else:
        raise AssertionError("debia rechazar la clave publica antigua")


def test_una_clave_corta_se_rechaza():
    try:
        auth.cargar_clave_firma("corta", Path("no-se-usa"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("debia rechazar una clave corta")


def test_sin_variable_se_genera_una_clave_aleatoria_y_se_reutiliza():
    tmp = Path(tempfile.mkdtemp())
    try:
        archivo = tmp / ".ocr_secret_key"
        k1 = auth.cargar_clave_firma(None, archivo)
        k2 = auth.cargar_clave_firma(None, archivo)
        assert k1 == k2 and len(k1) >= auth.MIN_LARGO_CLAVE and k1 != auth.CLAVE_PUBLICA_ANTIGUA
        otro = tmp / "otro"
        assert auth.cargar_clave_firma(None, otro) != k1, "cada instalacion debe tener su propia clave"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_varios_procesos_arrancando_a_la_vez_terminan_con_la_misma_clave():
    tmp = Path(tempfile.mkdtemp())
    try:
        archivo = tmp / ".ocr_secret_key"
        claves = []
        hilos = [threading.Thread(target=lambda: claves.append(auth.cargar_clave_firma(None, archivo))) for _ in range(12)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=10)
        assert len(claves) == 12 and len(set(claves)) == 1, f"claves distintas: {len(set(claves))}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_un_archivo_de_clave_corrupto_aborta_el_arranque():
    tmp = Path(tempfile.mkdtemp())
    try:
        archivo = tmp / ".ocr_secret_key"
        archivo.write_text("abc", encoding="utf-8")
        try:
            auth.cargar_clave_firma(None, archivo)
        except RuntimeError as exc:
            assert "demasiado corto" in str(exc)
        else:
            raise AssertionError("debia abortar")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_un_token_firmado_con_la_clave_publica_antigua_no_vale():
    forjado = jwt.encode(
        {"sub": "admin", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        auth.CLAVE_PUBLICA_ANTIGUA, algorithm=auth.ALGORITHM,
    )
    assert auth._decode_token(forjado) is None
    status, _ = esperar_http(llamar_asgi("GET", "/api/auth/me", {"Authorization": f"Bearer {forjado}"}))
    assert status == 401, status
    valido = auth.crear_token({"sub": "ana"})
    assert auth._decode_token(valido) == "ana"


# --------------------------------------------------------------------------- #
# Política de contraseñas
# --------------------------------------------------------------------------- #

def test_politica_de_contrasenas():
    v = password_policy.validar
    assert "al menos" in v("ana", "corta1")
    assert "común" in v("ana", "password123")
    assert "nombre de usuario" in v("ana", "Mi-ana-Clave-9")
    assert "repetitiva" in v("ana", "aaaaaaaaa1" + "a")
    assert "Combina" in v("ana", "12345678901")
    assert v("ana", PASSWORD_BUENA) is None


def test_contrasenas_conocidas_estan_prohibidas():
    for p in ("admin123", " Admin123 ", "ADMIN", "123456", "password"):
        assert password_policy.es_prohibida(p), p
    assert not password_policy.es_prohibida(PASSWORD_BUENA)


def test_la_contrasena_generada_cumple_la_politica_y_es_aleatoria():
    a, b = password_policy.generar(), password_policy.generar()
    assert a != b and len(a) == 16
    assert password_policy.validar("admin", a) is None


# --------------------------------------------------------------------------- #
# Límite de intentos
# --------------------------------------------------------------------------- #

class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_cinco_fallos_bloquean_a_ese_usuario_desde_esa_ip():
    r = Reloj()
    th = LoginThrottle(reloj=r)
    for _ in range(MAX_FALLOS_USUARIO - 1):
        th.registrar_fallo("1.1.1.1", "ana")
        assert th.segundos_bloqueado("1.1.1.1", "ana") == 0
    th.registrar_fallo("1.1.1.1", "ana")
    assert th.segundos_bloqueado("1.1.1.1", "ana") > 0
    assert th.segundos_bloqueado("1.1.1.1", "ANA ") > 0, "no distingue mayusculas ni espacios"
    assert th.segundos_bloqueado("1.1.1.1", "luis") == 0, "otro usuario no se ve afectado"
    assert th.segundos_bloqueado("2.2.2.2", "ana") == 0, "otra IP no se ve afectada"


def test_el_bloqueo_expira_y_un_exito_limpia_el_contador():
    r = Reloj()
    th = LoginThrottle(reloj=r)
    for _ in range(MAX_FALLOS_USUARIO):
        th.registrar_fallo("1.1.1.1", "ana")
    assert th.segundos_bloqueado("1.1.1.1", "ana") > 0
    r.t += BLOQUEO_S + 1
    assert th.segundos_bloqueado("1.1.1.1", "ana") == 0

    for _ in range(MAX_FALLOS_USUARIO - 1):
        th.registrar_fallo("1.1.1.1", "ana")
    th.registrar_exito("1.1.1.1", "ana")
    th.registrar_fallo("1.1.1.1", "ana")
    assert th.segundos_bloqueado("1.1.1.1", "ana") == 0, "tras un exito el conteo vuelve a empezar"


def test_rociar_una_contrasena_sobre_muchos_usuarios_bloquea_la_ip():
    th = LoginThrottle(reloj=Reloj())
    for i in range(MAX_FALLOS_IP):
        th.registrar_fallo("9.9.9.9", f"usuario{i}")
    assert th.segundos_bloqueado("9.9.9.9", "cualquiera") > 0
    assert th.segundos_bloqueado("8.8.8.8", "cualquiera") == 0


def test_fallos_viejos_fuera_de_la_ventana_no_cuentan():
    r = Reloj()
    th = LoginThrottle(reloj=r)
    for _ in range(MAX_FALLOS_USUARIO - 1):
        th.registrar_fallo("1.1.1.1", "ana")
    r.t += 16 * 60                                    # pasa la ventana de 15 minutos
    th.registrar_fallo("1.1.1.1", "ana")
    assert th.segundos_bloqueado("1.1.1.1", "ana") == 0


# --------------------------------------------------------------------------- #
# Usuario inicial (init_db)
# --------------------------------------------------------------------------- #

def test_base_vacia_crea_admin_con_contrasena_aleatoria_que_no_es_admin123():
    with entorno_temporal() as (_, _, sesion), capturar_logs() as logs:
        database.init_db()
        db = sesion()
        admin = db.query(Usuario).filter_by(username="admin").one()
        assert not admin.verify_password("admin123")
        mostrada = [m for n, m in logs.registros if "USUARIO INICIAL CREADO" in m]
        assert len(mostrada) == 1, "la contraseña inicial se muestra una vez en el registro"
        password = mostrada[0].split("contraseña:")[1].split()[0]
        assert admin.verify_password(password) and password_policy.validar("admin", password) is None
        db.close()


def test_admin_inicial_con_password_de_entorno():
    with entorno_temporal() as (_, _, sesion), capturar_logs() as logs:
        os.environ["OCR_ADMIN_PASSWORD"] = "Entorno-Segura-77"
        try:
            database.init_db()
        finally:
            os.environ.pop("OCR_ADMIN_PASSWORD")
        db = sesion()
        assert db.query(Usuario).filter_by(username="admin").one().verify_password("Entorno-Segura-77")
        assert not any("Entorno-Segura-77" in m for _, m in logs.registros), "no debe escribir la contraseña en el registro"
        db.close()


def test_password_de_entorno_debil_aborta_el_arranque():
    with entorno_temporal():
        os.environ["OCR_ADMIN_PASSWORD"] = "admin123"
        try:
            database.init_db()
        except RuntimeError as exc:
            assert "OCR_ADMIN_PASSWORD" in str(exc)
        else:
            raise AssertionError("debia abortar")
        finally:
            os.environ.pop("OCR_ADMIN_PASSWORD")


def test_cuenta_existente_con_admin123_genera_alerta_critica_y_no_se_toca():
    with entorno_temporal() as (_, _, sesion), capturar_logs() as logs:
        db = sesion()
        crear_usuario(db, "admin", "admin123")
        db.close()
        database.init_db()
        assert any(n >= logging.CRITICAL and "admin123" in m for n, m in logs.registros), logs.registros
        db = sesion()
        assert db.query(Usuario).count() == 1, "no debe crear otro usuario ni borrar el existente"
        db.close()


# --------------------------------------------------------------------------- #
# Endpoint de login
# --------------------------------------------------------------------------- #

def test_login_correcto_emite_token_valido():
    with entorno_temporal() as (_, _, sesion), throttle_limpio():
        db = sesion()
        crear_usuario(db, "ana")
        r = auth_router.login(falso_request(), formulario("ana", PASSWORD_BUENA), db)
        assert r.username == "ana" and auth._decode_token(r.access_token) == "ana"
        db.close()


def test_credenciales_incorrectas_dan_401_y_tras_cinco_el_login_se_bloquea_incluso_con_la_clave_correcta():
    with entorno_temporal() as (_, _, sesion), throttle_limpio():
        db = sesion()
        crear_usuario(db, "ana")
        for _ in range(MAX_FALLOS_USUARIO):
            try:
                auth_router.login(falso_request(), formulario("ana", "mala"), db)
            except HTTPException as exc:
                assert exc.status_code == 401
        try:
            auth_router.login(falso_request(), formulario("ana", PASSWORD_BUENA), db)
        except HTTPException as exc:
            assert exc.status_code == 429 and int(exc.headers["Retry-After"]) > 0
        else:
            raise AssertionError("debia estar bloqueado")
        # y desde otra IP el mismo usuario si puede entrar
        assert auth_router.login(falso_request("10.9.9.9"), formulario("ana", PASSWORD_BUENA), db).username == "ana"
        db.close()


def test_usuario_inexistente_responde_igual_y_gasta_el_mismo_trabajo_que_uno_real():
    llamadas = []
    original = Usuario.verify_dummy
    Usuario.verify_dummy = staticmethod(lambda p: llamadas.append(p))
    try:
        with entorno_temporal() as (_, _, sesion), throttle_limpio():
            db = sesion()
            crear_usuario(db, "ana")
            try:
                auth_router.login(falso_request(), formulario("noexiste", "cualquiera"), db)
            except HTTPException as exc:
                inexistente = (exc.status_code, exc.detail)
            try:
                auth_router.login(falso_request(), formulario("ana", "mala"), db)
            except HTTPException as exc:
                existente = (exc.status_code, exc.detail)
            assert inexistente == existente, "el mensaje no debe revelar si el usuario existe"
            assert llamadas == ["cualquiera"], "debe verificar contra un hash ficticio"
            db.close()
    finally:
        Usuario.verify_dummy = original


def test_cuenta_con_contrasena_conocida_no_puede_ingresar():
    with entorno_temporal() as (_, _, sesion), throttle_limpio():
        db = sesion()
        crear_usuario(db, "admin", "admin123")
        try:
            auth_router.login(falso_request(), formulario("admin", "admin123"), db)
        except HTTPException as exc:
            assert exc.status_code == 403 and "gestionar_usuarios.py" in exc.detail
        else:
            raise AssertionError("no debia emitir token con admin123")
        db.close()


def test_cuenta_desactivada_no_ingresa():
    with entorno_temporal() as (_, _, sesion), throttle_limpio():
        db = sesion()
        crear_usuario(db, "ana", activo=False)
        try:
            auth_router.login(falso_request(), formulario("ana", PASSWORD_BUENA), db)
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("debia rechazar")
        db.close()


# --------------------------------------------------------------------------- #
# CORS y cabeceras
# --------------------------------------------------------------------------- #

def test_no_hay_cors_abierto_por_defecto():
    assert not any(m.cls.__name__ == "CORSMiddleware" for m in main.app.user_middleware)
    status, headers = esperar_http(llamar_asgi(
        "OPTIONS", "/api/comparar", {"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}))
    assert "access-control-allow-origin" not in headers, headers


def test_cabeceras_de_seguridad_y_sin_cache_en_la_api():
    status, h = esperar_http(llamar_asgi("GET", "/api/health"))
    assert status == 200
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store"
    status, h = esperar_http(llamar_asgi("GET", "/login.html"))
    assert status == 200 and h["x-content-type-options"] == "nosniff"
    assert "no-store" not in h.get("cache-control", ""), "las paginas estaticas si pueden cachearse"


def test_la_api_exige_autenticacion():
    for ruta in ("/api/historial", "/api/auth/me"):
        status, _ = esperar_http(llamar_asgi("GET", ruta))
        assert status == 401, (ruta, status)


# --------------------------------------------------------------------------- #
# Borrado de archivos
# --------------------------------------------------------------------------- #

def _comparacion(db, user, storage, nombre):
    pdf, xls = storage / f"{nombre}_doc.pdf", storage / f"{nombre}_inscripcion.xls"
    pdf.write_bytes(b"%PDF-1.4 cedulas")
    xls.write_bytes(b"excel")
    comp = Comparacion(id=nombre, usuario_id=user.id, nombre_pdf="a.pdf", nombre_excel="b.xls",
                       ruta_pdf=str(pdf), ruta_excel=str(xls))
    db.add(comp)
    db.commit()
    return pdf, xls


def test_eliminar_un_registro_borra_sus_archivos_pero_no_los_de_otros():
    with entorno_temporal() as (_, storage, sesion):
        db = sesion()
        ana = crear_usuario(db, "ana")
        pdf1, xls1 = _comparacion(db, ana, storage, "uno")
        pdf2, xls2 = _comparacion(db, ana, storage, "dos")
        historial_router.eliminar_comparacion("uno", current_user=ana, db=db)
        assert not pdf1.exists() and not xls1.exists(), "los archivos del registro eliminado deben desaparecer"
        assert pdf2.exists() and xls2.exists(), "los de otro registro no se tocan"
        assert db.query(Comparacion).filter_by(id="uno").first() is None
        db.close()


def test_eliminar_tolera_archivos_que_ya_no_existen():
    with entorno_temporal() as (_, storage, sesion):
        db = sesion()
        ana = crear_usuario(db, "ana")
        pdf, xls = _comparacion(db, ana, storage, "uno")
        pdf.unlink()
        historial_router.eliminar_comparacion("uno", current_user=ana, db=db)     # no debe fallar
        assert not xls.exists()
        db.close()


def test_un_usuario_no_puede_eliminar_registros_de_otro():
    with entorno_temporal() as (_, storage, sesion):
        db = sesion()
        ana, luis = crear_usuario(db, "ana"), crear_usuario(db, "luis")
        pdf, xls = _comparacion(db, ana, storage, "uno")
        try:
            historial_router.eliminar_comparacion("uno", current_user=luis, db=db)
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("debia dar 404")
        assert pdf.exists() and xls.exists()
        db.close()


def test_una_ruta_manipulada_en_la_base_no_puede_borrar_fuera_de_storage():
    with entorno_temporal() as (tmp, storage, sesion):
        fuera = tmp / "importante.txt"
        fuera.write_text("no borrar", encoding="utf-8")
        db = sesion()
        ana = crear_usuario(db, "ana")
        db.add(Comparacion(id="x", usuario_id=ana.id, nombre_pdf="a", nombre_excel="b",
                           ruta_pdf=str(fuera), ruta_excel=str(tmp / ".." / ".." / "importante.txt")))
        db.commit()
        historial_router.eliminar_comparacion("x", current_user=ana, db=db)
        assert fuera.exists(), "no debe borrar archivos fuera de storage/"
        db.close()


def test_rutas_guardadas_en_otra_maquina_se_resuelven_por_nombre():
    with entorno_temporal() as (_, storage, _):
        assert database.ruta_en_storage(r"C:\Users\otro\storage\abc_doc.pdf") == storage / "abc_doc.pdf"
        assert database.ruta_en_storage("/srv/ocr/backend/storage/abc_doc.pdf") == storage / "abc_doc.pdf"
        assert database.ruta_en_storage("../../etc/passwd") == storage / "passwd"
        assert database.ruta_en_storage("") is None and database.ruta_en_storage(None) is None
        assert database.ruta_en_storage("..") is None


# --------------------------------------------------------------------------- #
# Script de administración
# --------------------------------------------------------------------------- #

def test_gestionar_usuarios_crear_cambiar_y_desactivar():
    with entorno_temporal() as (_, _, sesion):
        db = sesion()
        u = cli.crear_usuario(db, "ana", PASSWORD_BUENA, "Ana Pérez")
        assert u.nombre == "Ana Pérez" and u.verify_password(PASSWORD_BUENA)
        for mal in ("corta", "admin123", "mi-otra-clave-9"):          # corta, conocida, contiene el usuario "otra"
            try:
                cli.crear_usuario(db, "otra", mal)
            except cli.ErrorUsuarios:
                pass
            else:
                raise AssertionError(f"debia rechazar {mal!r}")
        try:
            cli.crear_usuario(db, "ana", PASSWORD_BUENA)
        except cli.ErrorUsuarios as exc:
            assert "Ya existe" in str(exc)
        cli.cambiar_password(db, "ana", "Otra-Clave-Nueva-5")
        assert db.query(Usuario).filter_by(username="ana").one().verify_password("Otra-Clave-Nueva-5")
        try:
            cli.cambiar_password(db, "ana", "admin123")
        except cli.ErrorUsuarios:
            pass
        else:
            raise AssertionError("no debe aceptar admin123")
        assert cli.fijar_activo(db, "ana", False).is_active is False
        assert cli.fijar_activo(db, "ana", True).is_active is True
        try:
            cli.cambiar_password(db, "fantasma", PASSWORD_BUENA)
        except cli.ErrorUsuarios as exc:
            assert "No existe" in str(exc)
        db.close()


def test_gestionar_usuarios_detecta_y_borra_archivos_huerfanos():
    with entorno_temporal() as (_, storage, sesion):
        db = sesion()
        ana = crear_usuario(db, "ana")
        pdf, xls = _comparacion(db, ana, storage, "vigente")
        huerfano1, huerfano2 = storage / "viejo_doc.pdf", storage / "viejo_inscripcion.xls"
        huerfano1.write_bytes(b"x")
        huerfano2.write_bytes(b"x")
        assert [p.name for p in cli.archivos_huerfanos(db)] == sorted([huerfano1.name, huerfano2.name])
        assert pdf.exists() and xls.exists(), "listar no borra nada"
        db.close()


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

"""
database.py - Configuracion de base de datos SQLAlchemy con SQLite.

Modelos:
  - Usuario: usuarios del sistema con autenticacion.
  - Comparacion: historial de comparaciones PDF vs Excel.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, create_engine
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from . import password_policy

logger = logging.getLogger("ocr_system")

# ---------------------------------------------------------------------------
# Configuracion del motor
# ---------------------------------------------------------------------------

_DB_DIR = Path(__file__).resolve().parent.parent          # backend/
_DB_PATH = _DB_DIR / "ocr_system.db"
_STORAGE_DIR = _DB_DIR / "storage"
_STORAGE_DIR.mkdir(exist_ok=True)

DATABASE_URL = f"sqlite:///{_DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},   # necesario para SQLite + FastAPI
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Modelos ORM
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = "usuarios"

    id: int = Column(Integer, primary_key=True, index=True)
    username: str = Column(String(64), unique=True, nullable=False, index=True)
    password_hash: str = Column(String(256), nullable=False)
    nombre: str = Column(String(128), nullable=True)
    is_active: bool = Column(Boolean, default=True, nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    comparaciones = relationship("Comparacion", back_populates="usuario", cascade="all, delete-orphan")

    def verify_password(self, plain: str) -> bool:
        return pwd_context.verify(plain, self.password_hash)

    @staticmethod
    def hash_password(plain: str) -> str:
        return pwd_context.hash(plain)

    @staticmethod
    def verify_dummy(plain: str) -> None:
        """Gasta el mismo tiempo que una verificación real (usuario inexistente): evita enumerar usuarios."""
        global _DUMMY_HASH
        if _DUMMY_HASH is None:
            _DUMMY_HASH = pwd_context.hash("contrasena-ficticia-para-igualar-tiempos")
        pwd_context.verify(plain, _DUMMY_HASH)


_DUMMY_HASH: Optional[str] = None


class Comparacion(Base):
    __tablename__ = "comparaciones"

    id: str = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    usuario_id: int = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    fecha_comparacion: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    # Nombres originales de los archivos subidos
    nombre_pdf: str = Column(String(256), nullable=False)
    nombre_excel: str = Column(String(256), nullable=False)

    # Rutas en disco (relativas a _STORAGE_DIR)
    ruta_pdf: Optional[str] = Column(String(512), nullable=True)
    ruta_excel: Optional[str] = Column(String(512), nullable=True)

    # Resumen estadistico (redundante con JSON para consultas rapidas)
    total_excel: int = Column(Integer, default=0)
    total_pdf_documentos: int = Column(Integer, default=0)
    coinciden: int = Column(Integer, default=0)
    nombre_no_coincide: int = Column(Integer, default=0)
    documento_incompleto: int = Column(Integer, default=0)
    no_encontrado_en_pdf: int = Column(Integer, default=0)
    sobrantes_en_pdf: int = Column(Integer, default=0)
    documentos_sin_numero: int = Column(Integer, default=0)
    tiempo_segundos: Optional[float] = Column(Float, nullable=True)

    # Resultado completo serializado en JSON
    resultado_json: str = Column(Text, nullable=True)

    usuario = relationship("Usuario", back_populates="comparaciones")

    def get_resultado(self) -> dict:
        return json.loads(self.resultado_json) if self.resultado_json else {}

    def set_resultado(self, data: dict) -> None:
        self.resultado_json = json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Inicializacion
# ---------------------------------------------------------------------------

def get_db():
    """Dependencia FastAPI: provee una sesion de BD y la cierra al terminar."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    Crea las tablas y, si la base está vacía, el usuario administrador inicial.

    No hay contraseña por defecto: se usa OCR_ADMIN_PASSWORD si está definida (y cumple la política)
    o se genera una aleatoria que se muestra UNA sola vez en el registro de arranque.
    Si alguna cuenta existente conserva la antigua contraseña "admin123", se avisa con claridad
    (el inicio de sesión de esas cuentas queda bloqueado hasta que la cambien).
    """
    Base.metadata.create_all(bind=engine)

    db: Session = SessionLocal()
    try:
        if db.query(Usuario).count() == 0:
            desde_entorno = os.environ.get("OCR_ADMIN_PASSWORD")
            if desde_entorno:
                motivo = password_policy.validar("admin", desde_entorno)
                if motivo:
                    raise RuntimeError(f"OCR_ADMIN_PASSWORD no es válida: {motivo}")
                password = desde_entorno
            else:
                password = password_policy.generar()

            db.add(Usuario(
                username="admin",
                password_hash=Usuario.hash_password(password),
                nombre="Administrador",
                is_active=True,
            ))
            db.commit()
            if desde_entorno:
                logger.warning("Usuario inicial 'admin' creado con la contraseña de OCR_ADMIN_PASSWORD.")
            else:
                logger.warning(
                    "\n" + "=" * 64 +
                    "\n  USUARIO INICIAL CREADO"
                    "\n  usuario:    admin"
                    "\n  contraseña: %s"
                    "\n  Se muestra UNA sola vez. Anótala y cámbiala con:"
                    "\n    python gestionar_usuarios.py cambiar-password admin"
                    "\n" + "=" * 64, password
                )
            return

        for user in db.query(Usuario).filter(Usuario.is_active.is_(True)).all():
            if any(user.verify_password(p) for p in ("admin123",)):
                logger.critical(
                    "El usuario '%s' sigue usando la contraseña por defecto 'admin123' (pública). "
                    "No podrá iniciar sesión hasta que la cambies:  python gestionar_usuarios.py "
                    "cambiar-password %s", user.username, user.username
                )
    finally:
        db.close()


def ruta_en_storage(ruta: Optional[str]) -> Optional[Path]:
    """
    Ruta real de un archivo guardado, calculada SOLO con su nombre dentro de storage/.
    Así no importa desde dónde se guardó el registro (otra máquina, otra carpeta) y un valor
    manipulado en la base de datos no puede apuntar fuera de storage/.
    """
    if not ruta:
        return None
    nombre = Path(str(ruta).replace("\\", "/")).name
    if not nombre or nombre in (".", ".."):
        return None
    return _STORAGE_DIR / nombre


def get_storage_dir() -> Path:
    return _STORAGE_DIR

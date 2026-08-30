"""
database.py - Configuracion de base de datos SQLAlchemy con SQLite.

Modelos:
  - Usuario: usuarios del sistema con autenticacion.
  - Comparacion: historial de comparaciones PDF vs Excel.
"""
from __future__ import annotations

import json
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
    """Crea las tablas y el usuario admin por defecto si no existe."""
    Base.metadata.create_all(bind=engine)

    db: Session = SessionLocal()
    try:
        admin = db.query(Usuario).filter(Usuario.username == "admin").first()
        if not admin:
            admin = Usuario(
                username="admin",
                password_hash=Usuario.hash_password("admin123"),
                nombre="Administrador",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            import logging
            logging.getLogger("ocr_system").info(
                "Usuario 'admin' creado con contraseña 'admin123'. "
                "Se recomienda cambiarla en cuanto sea posible."
            )
    finally:
        db.close()


def get_storage_dir() -> Path:
    return _STORAGE_DIR

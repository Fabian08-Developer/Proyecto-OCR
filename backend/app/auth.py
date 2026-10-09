"""
auth.py - Logica de autenticacion JWT para el sistema OCR.

Funciones:
  - crear_token(): genera un JWT firmado.
  - get_current_user(): dependencia FastAPI que valida el Bearer token.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .database import Usuario, get_db

# ---------------------------------------------------------------------------
# Configuracion JWT
# ---------------------------------------------------------------------------

logger = logging.getLogger("ocr_system")

# Clave que traían las versiones anteriores: estuvo en un repositorio público, así que
# cualquiera puede firmar tokens con ella. Se rechaza aunque alguien la configure a propósito.
CLAVE_PUBLICA_ANTIGUA = "ocr-system-super-secret-key-2024-cambiar-en-produccion"
MIN_LARGO_CLAVE = 32
_ARCHIVO_CLAVE = Path(__file__).resolve().parent.parent / ".ocr_secret_key"


def cargar_clave_firma(valor_entorno: Optional[str], archivo: Path) -> str:
    """
    Clave con la que se firman los JWT:
      1. Variable de entorno OCR_SECRET_KEY (recomendado en producción).
      2. Si no existe, un archivo local (backend/.ocr_secret_key, fuera de git) con una clave
         aleatoria generada la primera vez. La creación es atómica: con varios workers todos
         terminan usando la misma clave.
    Nunca se usa una clave conocida ni corta: en ese caso se aborta el arranque.
    """
    if valor_entorno:
        if valor_entorno == CLAVE_PUBLICA_ANTIGUA or len(valor_entorno) < MIN_LARGO_CLAVE:
            raise RuntimeError(
                f"OCR_SECRET_KEY no es segura: debe tener al menos {MIN_LARGO_CLAVE} caracteres y no puede ser "
                "la clave de ejemplo. Genera una con:  python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return valor_entorno

    try:
        fd = os.open(str(archivo), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = None

    if fd is not None:                                   # este proceso creó el archivo
        clave = secrets.token_urlsafe(48)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(clave)
        logger.warning(
            "OCR_SECRET_KEY no está definida: se generó una clave de firma aleatoria en %s "
            "(se reutiliza en los próximos arranques; las sesiones se invalidan si la borras).", archivo
        )
        return clave

    for _ in range(40):                                  # otro proceso la está escribiendo: esperar
        clave = archivo.read_text(encoding="utf-8").strip()
        if len(clave) >= MIN_LARGO_CLAVE:
            return clave
        time.sleep(0.05)
    raise RuntimeError(
        f"El archivo de clave {archivo} está vacío o es demasiado corto. Bórralo para que se genere uno nuevo."
    )


SECRET_KEY = cargar_clave_firma(os.environ.get("OCR_SECRET_KEY"), _ARCHIVO_CLAVE)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8   # sesion de 8 horas

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# ---------------------------------------------------------------------------
# Funciones JWT
# ---------------------------------------------------------------------------

def crear_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Crea y firma un JWT con los datos proporcionados."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> Optional[str]:
    """Decodifica el token y retorna el username o None si es invalido."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: Optional[str] = payload.get("sub")
        return username
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Dependencia FastAPI
# ---------------------------------------------------------------------------

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    """
    Dependencia que extrae y valida el usuario del Bearer token.
    Lanza HTTP 401 si el token es invalido o el usuario no existe/esta inactivo.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesión inválida o expirada. Por favor inicia sesión nuevamente.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    username = _decode_token(token)
    if not username:
        raise credentials_exception

    user = db.query(Usuario).filter(Usuario.username == username).first()
    if not user or not user.is_active:
        raise credentials_exception

    return user


def get_current_user_optional(
    token: Optional[str] = Depends(OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)),
    db: Session = Depends(get_db),
) -> Optional[Usuario]:
    """Como get_current_user pero retorna None en lugar de lanzar excepcion."""
    if not token:
        return None
    username = _decode_token(token)
    if not username:
        return None
    user = db.query(Usuario).filter(Usuario.username == username).first()
    return user if user and user.is_active else None

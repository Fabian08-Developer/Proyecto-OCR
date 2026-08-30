"""
auth.py - Logica de autenticacion JWT para el sistema OCR.

Funciones:
  - crear_token(): genera un JWT firmado.
  - get_current_user(): dependencia FastAPI que valida el Bearer token.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .database import Usuario, get_db

# ---------------------------------------------------------------------------
# Configuracion JWT
# ---------------------------------------------------------------------------

# En produccion, usa una variable de entorno para SECRET_KEY
SECRET_KEY = os.environ.get("OCR_SECRET_KEY", "ocr-system-super-secret-key-2024-cambiar-en-produccion")
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

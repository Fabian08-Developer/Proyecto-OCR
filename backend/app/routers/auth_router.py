"""
routers/auth_router.py - Endpoints de autenticacion.

Endpoints:
  POST /api/auth/login  - Recibe usuario/contrasena, retorna JWT.
  GET  /api/auth/me     - Retorna datos del usuario autenticado.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import password_policy
from ..auth import crear_token, get_current_user
from ..database import Usuario, get_db
from ..throttle import login_throttle

logger = logging.getLogger("ocr_system")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas de respuesta
# ---------------------------------------------------------------------------
class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    username: str
    nombre: str | None


class MeResponse(BaseModel):
    id: int
    username: str
    nombre: str | None
    is_active: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/login", response_model=TokenResponse)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Autenticacion con usuario y contrasena.
    Retorna un JWT valido por 8 horas.

    Protecciones: limite de intentos fallidos por IP/usuario (429), tiempos de respuesta
    iguales exista o no el usuario, y rechazo de cuentas con contrasena conocida (403).
    """
    ip = request.client.host if request.client else "desconocida"

    espera = login_throttle.segundos_bloqueado(ip, form_data.username)
    if espera:
        logger.warning("Login bloqueado por demasiados intentos: usuario=%r ip=%s", form_data.username, ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos fallidos. Intenta de nuevo en {-(-espera // 60)} minuto(s).",
            headers={"Retry-After": str(espera)},
        )

    user: Usuario | None = (
        db.query(Usuario).filter(Usuario.username == form_data.username).first()
    )

    if user is None:
        Usuario.verify_dummy(form_data.password)      # mismo costo que una verificacion real
        valido = False
    else:
        valido = user.verify_password(form_data.password)

    if not valido:
        login_throttle.registrar_fallo(ip, form_data.username)
        logger.warning("Login fallido: usuario=%r ip=%s", form_data.username, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta cuenta está desactivada.",
        )

    if password_policy.es_prohibida(form_data.password):
        # Credenciales correctas pero conocidas publicamente (p. ej. la "admin123" de versiones
        # anteriores): no se emite token hasta que un administrador cambie la contrasena.
        logger.warning("Login rechazado: el usuario %r usa una contrasena por defecto/comun.", user.username)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Esta cuenta usa una contraseña por defecto o demasiado común y no puede ingresar. "
                "Un administrador debe cambiarla con: python gestionar_usuarios.py cambiar-password "
                f"{user.username}"
            ),
        )

    login_throttle.registrar_exito(ip, form_data.username)
    token = crear_token(data={"sub": user.username})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        username=user.username,
        nombre=user.nombre,
    )


@router.get("/me", response_model=MeResponse)
def get_me(current_user: Usuario = Depends(get_current_user)):
    """Retorna informacion del usuario actualmente autenticado."""
    return MeResponse(
        id=current_user.id,
        username=current_user.username,
        nombre=current_user.nombre,
        is_active=current_user.is_active,
    )

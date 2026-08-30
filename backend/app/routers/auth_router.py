"""
routers/auth_router.py - Endpoints de autenticacion.

Endpoints:
  POST /api/auth/login  - Recibe usuario/contrasena, retorna JWT.
  GET  /api/auth/me     - Retorna datos del usuario autenticado.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import crear_token, get_current_user
from ..database import Usuario, get_db

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
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Autenticacion con usuario y contrasena.
    Retorna un JWT valido por 8 horas.
    """
    user: Usuario | None = (
        db.query(Usuario).filter(Usuario.username == form_data.username).first()
    )

    if not user or not user.verify_password(form_data.password):
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

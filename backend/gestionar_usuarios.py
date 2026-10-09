"""
gestionar_usuarios.py - Administración de usuarios y limpieza de archivos del sistema OCR.

El sistema no trae pantalla de administración: las cuentas se gestionan con este script,
ejecutado en el servidor (con el mismo entorno virtual y las mismas variables de entorno
que la aplicación).

Uso (desde la carpeta backend):
    python gestionar_usuarios.py listar
    python gestionar_usuarios.py crear <usuario> [--nombre "Nombre Apellido"]
    python gestionar_usuarios.py cambiar-password <usuario>
    python gestionar_usuarios.py desactivar <usuario>
    python gestionar_usuarios.py activar <usuario>
    python gestionar_usuarios.py huerfanos            # lista archivos de storage/ sin registro
    python gestionar_usuarios.py huerfanos --borrar   # y los elimina

Las contraseñas se piden sin eco por teclado. Para automatizar (despliegues) se puede usar la
variable de entorno OCR_NUEVA_PASSWORD. Toda contraseña debe cumplir app/password_policy.py.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy.orm import Session

from app import password_policy
from app.database import Base, Comparacion, SessionLocal, Usuario, engine, get_storage_dir, ruta_en_storage


class ErrorUsuarios(Exception):
    """Error de uso esperado (usuario inexistente, contraseña débil...); se muestra sin traza."""


# --------------------------------------------------------------------------- #
# Operaciones (separadas del manejo de la línea de comandos para poder probarlas)
# --------------------------------------------------------------------------- #

def _buscar(db: Session, username: str) -> Usuario:
    user = db.query(Usuario).filter(Usuario.username == username).first()
    if user is None:
        raise ErrorUsuarios(f"No existe el usuario '{username}'.")
    return user


def crear_usuario(db: Session, username: str, password: str, nombre: Optional[str] = None) -> Usuario:
    username = username.strip()
    if not username:
        raise ErrorUsuarios("El nombre de usuario no puede estar vacío.")
    if db.query(Usuario).filter(Usuario.username == username).first():
        raise ErrorUsuarios(f"Ya existe el usuario '{username}'.")
    motivo = password_policy.validar(username, password)
    if motivo:
        raise ErrorUsuarios(motivo)
    user = Usuario(username=username, nombre=nombre or username, password_hash=Usuario.hash_password(password), is_active=True)
    db.add(user)
    db.commit()
    return user


def cambiar_password(db: Session, username: str, password: str) -> Usuario:
    user = _buscar(db, username)
    motivo = password_policy.validar(user.username, password)
    if motivo:
        raise ErrorUsuarios(motivo)
    user.password_hash = Usuario.hash_password(password)
    db.commit()
    return user


def fijar_activo(db: Session, username: str, activo: bool) -> Usuario:
    user = _buscar(db, username)
    user.is_active = activo
    db.commit()
    return user


def archivos_huerfanos(db: Session) -> List[Path]:
    """Archivos de storage/ que ningún registro del historial referencia (p. ej. de borrados anteriores)."""
    referenciados: Set[str] = set()
    for comp in db.query(Comparacion).all():
        for ruta in (comp.ruta_pdf, comp.ruta_excel):
            p = ruta_en_storage(ruta)
            if p is not None:
                referenciados.add(p.name)
    return sorted(p for p in get_storage_dir().iterdir() if p.is_file() and p.name not in referenciados)


# --------------------------------------------------------------------------- #
# Línea de comandos
# --------------------------------------------------------------------------- #

def _pedir_password(confirmar: bool = True) -> str:
    desde_entorno = os.environ.get("OCR_NUEVA_PASSWORD")
    if desde_entorno:
        return desde_entorno
    primera = getpass.getpass("Nueva contraseña: ")
    if confirmar and getpass.getpass("Repite la contraseña: ") != primera:
        raise ErrorUsuarios("Las contraseñas no coinciden.")
    return primera


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Administración de usuarios del sistema OCR.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar", help="Lista los usuarios")
    c = sub.add_parser("crear", help="Crea un usuario")
    c.add_argument("usuario")
    c.add_argument("--nombre")
    for nombre in ("cambiar-password", "desactivar", "activar"):
        sub.add_parser(nombre).add_argument("usuario")
    h = sub.add_parser("huerfanos", help="Archivos de storage/ sin registro en el historial")
    h.add_argument("--borrar", action="store_true", help="Eliminarlos (por defecto solo se listan)")
    args = ap.parse_args(argv)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if args.cmd == "listar":
            for u in db.query(Usuario).order_by(Usuario.id).all():
                print(f"{u.id:>3}  {u.username:<20} {'activo' if u.is_active else 'DESACTIVADO':<12} {u.nombre or ''}")
        elif args.cmd == "crear":
            crear_usuario(db, args.usuario, _pedir_password(), args.nombre)
            print(f"Usuario '{args.usuario}' creado.")
        elif args.cmd == "cambiar-password":
            cambiar_password(db, args.usuario, _pedir_password())
            print(f"Contraseña de '{args.usuario}' actualizada. Las sesiones abiertas siguen vigentes hasta que expiren (8 h).")
        elif args.cmd in ("desactivar", "activar"):
            fijar_activo(db, args.usuario, args.cmd == "activar")
            print(f"Usuario '{args.usuario}' {'activado' if args.cmd == 'activar' else 'desactivado'}.")
        elif args.cmd == "huerfanos":
            huerfanos = archivos_huerfanos(db)
            if not huerfanos:
                print("No hay archivos huérfanos en storage/.")
            for p in huerfanos:
                print(("Borrando " if args.borrar else "Huérfano: ") + p.name)
                if args.borrar:
                    p.unlink()
            if huerfanos and not args.borrar:
                print(f"\n{len(huerfanos)} archivo(s). Ejecuta con --borrar para eliminarlos.")
    except ErrorUsuarios as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

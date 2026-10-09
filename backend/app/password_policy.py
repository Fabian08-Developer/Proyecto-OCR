"""
password_policy.py - Reglas mínimas para las contraseñas del sistema.

El sistema guarda cédulas escaneadas: una contraseña débil o conocida equivale a dejar
esos documentos al alcance de cualquiera. Estas reglas se aplican al crear o cambiar una
contraseña (gestionar_usuarios.py, creación del usuario inicial) y, en el login, se
rechaza cualquier cuenta que siga usando una contraseña de la lista de prohibidas
(por ejemplo la "admin123" que traían las versiones anteriores).
"""
from __future__ import annotations

import secrets
from typing import Optional

MIN_LARGO = 10

# Contraseñas conocidas o triviales. Se comparan en minúsculas.
PROHIBIDAS = frozenset({
    "admin123", "admin", "admin1234", "administrador", "administrator",
    "password", "password1", "password123", "contrasena", "contraseña", "contrasena123",
    "123456", "1234567", "12345678", "123456789", "1234567890", "0123456789",
    "qwerty", "qwerty123", "abc123", "abc12345", "ocr123", "ocr1234", "ocr12345",
    "sena", "sena123", "colombia", "colombia123", "cambiame", "cambiar", "cambiar123",
})

# Sin caracteres ambiguos (0/O, 1/l/I) para que la contraseña inicial se pueda copiar a mano.
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def es_prohibida(password: str) -> bool:
    """True si la contraseña está en la lista de conocidas/triviales."""
    return (password or "").strip().lower() in PROHIBIDAS


def validar(username: str, password: str) -> Optional[str]:
    """Devuelve el motivo por el que la contraseña no sirve, o None si es aceptable."""
    if password is None or len(password) < MIN_LARGO:
        return f"La contraseña debe tener al menos {MIN_LARGO} caracteres."
    if es_prohibida(password):
        return "Esa contraseña es demasiado común o conocida."
    if username and username.strip().lower() in password.lower():
        return "La contraseña no puede contener el nombre de usuario."
    if len(set(password)) < 4:
        return "La contraseña es demasiado repetitiva."
    if password.isdigit() or password.isalpha():
        return "Combina letras con números u otros símbolos."
    return None


def generar() -> str:
    """Contraseña aleatoria de 16 caracteres (≈ 93 bits) que cumple validar()."""
    while True:
        candidata = "".join(secrets.choice(_ALFABETO) for _ in range(16))
        if validar("", candidata) is None:
            return candidata

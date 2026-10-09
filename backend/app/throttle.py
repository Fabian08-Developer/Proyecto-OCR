"""
throttle.py - Límite de intentos fallidos de inicio de sesión.

Sin esto, un atacante puede probar contraseñas sin freno. Se llevan dos contadores por
origen (IP):
  - por usuario:  MAX_FALLOS_USUARIO fallos en VENTANA_S segundos bloquean ese usuario desde esa IP.
  - por IP:       MAX_FALLOS_IP fallos (cualquier usuario) bloquean esa IP; frena el "rociado" de
                  una contraseña común sobre muchos usuarios.
Mientras dura el bloqueo se rechazan también los intentos con la contraseña correcta.

El estado vive en memoria de cada proceso: con varios workers el límite efectivo es por worker.
Detrás de un proxy (nginx) hay que arrancar uvicorn con --proxy-headers y --forwarded-allow-ips
para que la IP sea la del cliente y no la del proxy; si no, todos comparten el mismo contador.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Tuple

MAX_FALLOS_USUARIO = 5
MAX_FALLOS_IP = 20
VENTANA_S = 15 * 60
BLOQUEO_S = 15 * 60
_MAX_ENTRADAS = 10_000


class LoginThrottle:
    def __init__(self, reloj: Callable[[], float] = time.monotonic):
        self._reloj = reloj
        self._lock = threading.Lock()
        self._fallos: Dict[Tuple[str, str], List[float]] = {}
        self._bloqueado_hasta: Dict[Tuple[str, str], float] = {}

    @staticmethod
    def _claves(ip: str, usuario: str) -> List[Tuple[Tuple[str, str], int]]:
        return [((ip, usuario.strip().lower()), MAX_FALLOS_USUARIO), ((ip, "*"), MAX_FALLOS_IP)]

    def segundos_bloqueado(self, ip: str, usuario: str) -> int:
        """Segundos que faltan para poder intentar de nuevo; 0 si no hay bloqueo."""
        ahora = self._reloj()
        with self._lock:
            restante = 0.0
            for clave, _ in self._claves(ip, usuario):
                hasta = self._bloqueado_hasta.get(clave)
                if hasta is None:
                    continue
                if hasta <= ahora:
                    del self._bloqueado_hasta[clave]
                else:
                    restante = max(restante, hasta - ahora)
            return int(restante) + 1 if restante else 0

    def registrar_fallo(self, ip: str, usuario: str) -> None:
        ahora = self._reloj()
        with self._lock:
            self._purgar(ahora)
            for clave, maximo in self._claves(ip, usuario):
                recientes = [t for t in self._fallos.get(clave, []) if ahora - t < VENTANA_S]
                recientes.append(ahora)
                if len(recientes) >= maximo:
                    self._bloqueado_hasta[clave] = ahora + BLOQUEO_S
                    recientes = []
                self._fallos[clave] = recientes

    def registrar_exito(self, ip: str, usuario: str) -> None:
        """Un ingreso correcto limpia el contador del usuario (no el de la IP)."""
        with self._lock:
            clave = (ip, usuario.strip().lower())
            self._fallos.pop(clave, None)
            self._bloqueado_hasta.pop(clave, None)

    def _purgar(self, ahora: float) -> None:
        if len(self._fallos) + len(self._bloqueado_hasta) < _MAX_ENTRADAS:
            return
        self._fallos = {k: [t for t in v if ahora - t < VENTANA_S] for k, v in self._fallos.items()}
        self._fallos = {k: v for k, v in self._fallos.items() if v}
        self._bloqueado_hasta = {k: v for k, v in self._bloqueado_hasta.items() if v > ahora}


# Instancia compartida por el proceso
login_throttle = LoginThrottle()

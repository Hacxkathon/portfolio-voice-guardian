"""Utilidades HTTP compartidas: sesión con reintentos, backoff y cabeceras."""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from monitoring_produccion import config


def crear_sesion() -> requests.Session:
    """Crea una sesión con reintentos exponenciales ante errores transitorios."""
    reintentos = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )
    adaptador = HTTPAdapter(max_retries=reintentos)
    sesion = requests.Session()
    sesion.mount("https://", adaptador)
    sesion.mount("http://", adaptador)
    sesion.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept-Language": "es-ES,es;q=0.9",
    })
    return sesion
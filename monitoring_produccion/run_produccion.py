"""
run_produccion.py — Ejecuta los tres pasos en secuencia: RSS → descarga → filtro.

    python -m monitoring_produccion.run_produccion --once
    python -m monitoring_produccion.run_produccion            # bucle cada 15 min
"""
from __future__ import annotations

import argparse
import logging
import time

from monitoring_produccion import config
from monitoring_produccion.downloader import ejecutar_descargas
from monitoring_produccion.heuristic_filter import cargar_isins, ejecutar_filtro
from monitoring_produccion.http_utils import crear_sesion
from monitoring_produccion.monitor_db import abrir_monitor_db
from monitoring_produccion.rss_monitor import ejecutar_ciclo

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Bucle principal de producción."""
    ap = argparse.ArgumentParser(description="Monitoring Engine (producción): RSS → descarga → filtro")
    ap.add_argument("--once", action="store_true", help="un solo ciclo y termina")
    ap.add_argument("--intervalo", type=int, default=config.INTERVALO_POLLING_S)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    config.configurar_logging(logging.DEBUG if args.verbose else logging.INFO)
    config.DIR_INCOMING.mkdir(parents=True, exist_ok=True)
    config.DIR_DEMO_DOCS.mkdir(parents=True, exist_ok=True)
    conn = abrir_monitor_db()
    sesion = crear_sesion()
    try:
        while True:
            ejecutar_ciclo(conn, sesion)
            ejecutar_descargas(conn, sesion)
            ejecutar_filtro(conn, cargar_isins())
            if args.once:
                break
            log.info("Próximo ciclo en %d s", args.intervalo)
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        log.info("Interrumpido por el usuario")
    finally:
        conn.close()
        sesion.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
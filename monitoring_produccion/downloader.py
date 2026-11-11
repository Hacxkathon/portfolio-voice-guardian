"""
downloader.py — Paso 2 del Monitoring Engine (Producción).

Para cada (noticia, activo) en estado PENDIENTE_DESCARGA:
  1. Abre la página intermedia de la CNMV (URL del feed, siguiendo redirecciones).
  2. Localiza el enlace al visor de documentos (/webservices/verdocumento/ver?...)
     dentro del bloque del registro (ignora los .pdf del menú del portal).
  3. Descarga el PDF en streaming a un fichero .part (escritura atómica), validando
     la cabecera %PDF-, el tamaño máximo y calculando el SHA-256.
  4. Comprueba duplicados (DOCUMENTS en solo lectura + monitor.db).
  5. Renombra a [ASSET_ID]_[CANAL]_[AAAAMMDD]_[NREG].pdf y marca DESCARGADO.

Uso (desde la raíz del proyecto):
    python -m monitoring_produccion.downloader --once -v
    python -m monitoring_produccion.downloader            # bucle cada 15 min
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from bs4 import BeautifulSoup

from monitoring_produccion import config
from monitoring_produccion.http_utils import crear_sesion
from monitoring_produccion.monitor_db import (
    Estado,
    abrir_guardian_solo_lectura,
    abrir_monitor_db,
    actualizar_item,
    hash_existe_en_documents,
    obtener_items,
)

log = logging.getLogger(__name__)

_SELECTOR_ENLACE_DOC = 'a[href*="/webservices/verdocumento/ver"]'


class ErrorReintentable(Exception):
    """Fallo probablemente transitorio (red, página sin enlace aún...). Se reintenta."""


class ErrorDescartable(Exception):
    """Fallo definitivo (el recurso no es un PDF, tamaño excesivo...). No se reintenta."""


# =============================================================================
# Utilidades de nombre y fecha
# =============================================================================
def _parsear_iso(texto: str | None) -> datetime | None:
    """Convierte la fecha ISO guardada en BD a datetime (o None si no es válida)."""
    if not texto:
        return None
    try:
        fecha = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)


def fecha_local_madrid(fecha: datetime | None) -> str:
    """Fecha AAAAMMDD en hora de Madrid. Sin tzdata (Windows) cae a UTC con un aviso."""
    base = fecha or datetime.now(timezone.utc)
    try:
        return base.astimezone(ZoneInfo("Europe/Madrid")).strftime("%Y%m%d")
    except ZoneInfoNotFoundError:
        log.warning("Sin base de zonas horarias (pip install tzdata); se usa la fecha UTC")
        return base.astimezone(timezone.utc).strftime("%Y%m%d")


def construir_nombre_fichero(
    asset_id: str, feed: str, publicado: datetime | None, nreg: str | None, guid_hash: str
) -> str:
    """
    Formato [ASSET_ID]_[CANAL]_[AAAAMMDD]_[NREG].pdf
    Compatible con file_scanner.extraer_asset_id (toma las 3 primeras partes).
    """
    asset_seguro = re.sub(r"[^A-Za-z0-9_\-]", "_", asset_id)
    identificador = re.sub(r"[^A-Za-z0-9]", "", nreg) if nreg else guid_hash[:8]
    return f"{asset_seguro}_{feed}_{fecha_local_madrid(publicado)}_{identificador}.pdf"


# =============================================================================
# Scraping de la página intermedia
# =============================================================================
def obtener_pagina(sesion: requests.Session, url: str) -> tuple[str, bytes]:
    """Descarga la página intermedia. Devuelve (URL final tras redirecciones, contenido)."""
    try:
        r = sesion.get(url, timeout=config.TIMEOUT_HTTP, allow_redirects=True)
    except requests.RequestException as exc:
        raise ErrorReintentable(f"fallo de red abriendo la página intermedia: {exc}") from exc
    if r.status_code != 200:
        raise ErrorReintentable(f"HTTP {r.status_code} en la página intermedia")
    return r.url, r.content


def extraer_enlace_pdf(contenido: bytes | str, url_base: str, nreg: str | None) -> str:
    """
    Localiza el enlace al documento dentro del bloque del registro.
    Prefiere el registro cuyo 'Número de registro' coincide con nreg.
    """
    sopa = BeautifulSoup(contenido, "lxml")
    candidatos: list[tuple[str, bool]] = []  # (url absoluta, coincide con nreg)

    for registro in sopa.select("article.block-content-pdf li.blocks-single"):
        enlaces = registro.select(_SELECTOR_ENLACE_DOC)
        if not enlaces:
            continue
        texto = " ".join(registro.get_text(" ").split())
        coincide = bool(nreg) and re.search(
            rf"N[uú]mero de registro:\s*{re.escape(str(nreg))}\b", texto
        ) is not None
        for a in enlaces:
            href = str(a.get("href", "")).strip()
            if href:
                candidatos.append((urljoin(url_base, href), coincide))

    if not candidatos:  # respaldo: cualquier enlace al visor en toda la página
        for a in sopa.select(_SELECTOR_ENLACE_DOC):
            href = str(a.get("href", "")).strip()
            if href:
                candidatos.append((urljoin(url_base, href), False))

    if not candidatos:
        raise ErrorReintentable("no se encontró el enlace al documento en la página intermedia")

    preferidos = [u for u, coincide in candidatos if coincide] or [u for u, _ in candidatos]
    if len(set(preferidos)) > 1:
        log.warning("La página lista %d documentos; se descarga el primero", len(set(preferidos)))
    return preferidos[0]


# =============================================================================
# Descarga del PDF
# =============================================================================
def descargar_pdf(
    sesion: requests.Session, url: str, referer: str, destino_part: Path
) -> tuple[str, int]:
    """
    Descarga en streaming a 'destino_part' (.part). Valida cabecera %PDF- y tamaño.
    Devuelve (sha256, bytes). Si falla, borra el .part.
    """
    cabeceras = {"Referer": referer, "Accept": "application/pdf,*/*;q=0.8"}
    destino_part.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    total = 0
    cabecera_validada = False
    completado = False
    try:
        with sesion.get(url, headers=cabeceras, timeout=config.TIMEOUT_HTTP, stream=True) as r:
            if r.status_code != 200:
                raise ErrorReintentable(f"HTTP {r.status_code} al descargar el documento")
            tipo = r.headers.get("Content-Type", "desconocido")
            with destino_part.open("wb") as fichero:
                for trozo in r.iter_content(chunk_size=64 * 1024):
                    if not trozo:
                        continue
                    if not cabecera_validada:
                        if b"%PDF-" not in trozo[:1024]:
                            raise ErrorDescartable(f"el recurso no es un PDF (Content-Type={tipo})")
                        cabecera_validada = True
                    total += len(trozo)
                    if total > config.MAX_BYTES_PDF:
                        raise ErrorDescartable(f"el PDF supera {config.MAX_BYTES_PDF} bytes")
                    hasher.update(trozo)
                    fichero.write(trozo)
        if not cabecera_validada:
            raise ErrorReintentable("respuesta vacía al descargar el documento")
        completado = True
    except requests.RequestException as exc:
        raise ErrorReintentable(f"fallo de red descargando el PDF: {exc}") from exc
    finally:
        if not completado:
            destino_part.unlink(missing_ok=True)
    return hasher.hexdigest(), total


# =============================================================================
# Procesamiento de cada elemento pendiente
# =============================================================================
def _motivo_duplicado(
    conn: sqlite3.Connection,
    guardian: sqlite3.Connection | None,
    sha256: str,
    guid_hash: str,
    asset_id: str,
) -> str | None:
    """Devuelve el motivo si el PDF ya existe (DOCUMENTS o monitor.db); None si es nuevo."""
    if guardian is not None:
        try:
            if hash_existe_en_documents(guardian, sha256):
                return "duplicado: el hash ya existe en DOCUMENTS"
        except sqlite3.Error:
            log.warning("No se pudo consultar DOCUMENTS; se omite esa comprobación", exc_info=True)
    fila = conn.execute(
        "SELECT asset_id FROM feed_items WHERE pdf_sha256 = ? AND estado IN (?, ?) "
        "AND NOT (guid_hash = ? AND asset_id = ?) LIMIT 1",
        (sha256, Estado.DESCARGADO.value, Estado.APROBADO.value, guid_hash, asset_id),
    ).fetchone()
    if fila is not None:
        return f"duplicado: el mismo PDF ya se descargó para {fila['asset_id']}"
    return None


def _registrar_fallo(conn: sqlite3.Connection, fila: sqlite3.Row, mensaje: str) -> Estado:
    """Incrementa intentos; tras MAX_INTENTOS_DESCARGA pasa a ERROR, si no sigue pendiente."""
    intentos = int(fila["intentos"]) + 1
    estado = Estado.ERROR if intentos >= config.MAX_INTENTOS_DESCARGA else Estado.PENDIENTE_DESCARGA
    with conn:
        actualizar_item(
            conn, fila["guid_hash"], fila["asset_id"],
            intentos=intentos, ultimo_error=mensaje[:500], estado=estado,
        )
    log.warning(
        "[%s] %s: %s (intento %d/%d) → %s",
        fila["feed"], fila["asset_id"], mensaje, intentos, config.MAX_INTENTOS_DESCARGA, estado.value,
    )
    return estado


def procesar_pendiente(
    conn: sqlite3.Connection,
    sesion: requests.Session,
    guardian: sqlite3.Connection | None,
    fila: sqlite3.Row,
) -> Estado:
    """Procesa un (noticia, activo). Devuelve el estado final en que queda."""
    guid, asset = fila["guid_hash"], fila["asset_id"]
    parte: Path | None = None
    try:
        url_final, contenido = obtener_pagina(sesion, fila["link"])
        url_pdf = extraer_enlace_pdf(contenido, url_final, fila["nreg"])

        nombre = construir_nombre_fichero(
            asset, fila["feed"], _parsear_iso(fila["publicado_utc"]), fila["nreg"], guid
        )
        destino = config.DIR_INCOMING / nombre
        parte = destino.with_name(destino.name + ".part")

        sha256, tamano = descargar_pdf(sesion, url_pdf, url_final, parte)

        motivo = _motivo_duplicado(conn, guardian, sha256, guid, asset)
        if motivo:
            with conn:
                actualizar_item(conn, guid, asset, estado=Estado.RECHAZADO,
                                pdf_url=url_pdf, pdf_sha256=sha256, filtro_motivo=motivo)
            log.info("[%s] %s: %s", fila["feed"], asset, motivo)
            return Estado.RECHAZADO

        os.replace(parte, destino)  # atómico: nadie ve un PDF a medias
        with conn:
            actualizar_item(conn, guid, asset, estado=Estado.DESCARGADO, pdf_url=url_pdf,
                            pdf_sha256=sha256, ruta_tmp=str(destino), ultimo_error=None)
        log.info("[%s] %s: PDF descargado (%d bytes) → %s", fila["feed"], asset, tamano, nombre)
        return Estado.DESCARGADO

    except ErrorDescartable as exc:
        with conn:
            actualizar_item(conn, guid, asset, estado=Estado.RECHAZADO,
                            ultimo_error=str(exc)[:500], filtro_motivo=f"descarga: {exc}")
        log.warning("[%s] %s: rechazado en descarga: %s", fila["feed"], asset, exc)
        return Estado.RECHAZADO
    except ErrorReintentable as exc:
        return _registrar_fallo(conn, fila, str(exc))
    except OSError as exc:  # disco lleno, permisos, fichero bloqueado...
        return _registrar_fallo(conn, fila, f"error de disco: {exc}")
    finally:
        if parte is not None:
            parte.unlink(missing_ok=True)  # si ya se renombró no existe: inofensivo


@dataclass
class EstadisticasDescarga:
    """Contadores de un ciclo de descargas."""
    procesados: int = 0
    descargados: int = 0
    rechazados: int = 0
    reintentos: int = 0
    errores: int = 0


def _abrir_guardian_opcional() -> sqlite3.Connection | None:
    """guardian.db en solo lectura; si no está disponible se continúa sin esa comprobación."""
    try:
        return abrir_guardian_solo_lectura()
    except (FileNotFoundError, sqlite3.Error) as exc:
        log.warning("guardian.db no disponible (%s); no se comprobará DOCUMENTS", exc)
        return None


def ejecutar_descargas(
    conn: sqlite3.Connection,
    sesion: requests.Session,
    limite: int = config.MAX_DESCARGAS_POR_CICLO,
) -> EstadisticasDescarga:
    """Procesa los elementos en PENDIENTE_DESCARGA. Un fallo no detiene al resto."""
    est = EstadisticasDescarga()
    pendientes = obtener_items(conn, Estado.PENDIENTE_DESCARGA, limite)
    if not pendientes:
        log.info("No hay elementos pendientes de descarga")
        return est

    guardian = _abrir_guardian_opcional()
    try:
        for fila in pendientes:
            est.procesados += 1
            try:
                resultado = procesar_pendiente(conn, sesion, guardian, fila)
            except sqlite3.Error:
                est.errores += 1
                log.exception("Error de BD procesando %s/%s", fila["guid_hash"][:8], fila["asset_id"])
                continue
            if resultado is Estado.DESCARGADO:
                est.descargados += 1
            elif resultado is Estado.RECHAZADO:
                est.rechazados += 1
            elif resultado is Estado.PENDIENTE_DESCARGA:
                est.reintentos += 1
            else:
                est.errores += 1
            time.sleep(config.PAUSA_ENTRE_PETICIONES_S)
    finally:
        if guardian is not None:
            guardian.close()
    log.info("Ciclo de descargas → %s", est)
    return est


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI."""
    ap = argparse.ArgumentParser(description="Descargador de PDFs de la CNMV (producción)")
    ap.add_argument("--once", action="store_true", help="ejecuta un solo ciclo y termina")
    ap.add_argument("--intervalo", type=int, default=config.INTERVALO_POLLING_S,
                    help="segundos entre ciclos (por defecto 900)")
    ap.add_argument("-v", "--verbose", action="store_true", help="logs detallados")
    args = ap.parse_args(argv)

    config.configurar_logging(logging.DEBUG if args.verbose else logging.INFO)
    config.DIR_INCOMING.mkdir(parents=True, exist_ok=True)
    conn = abrir_monitor_db()
    sesion = crear_sesion()
    try:
        while True:
            ejecutar_descargas(conn, sesion)
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
"""
rss_monitor.py — Paso 1 del Monitoring Engine (Producción).

Embudo de coste cero-tokens (de más barato a más caro):
  1. Dedupe por hash del GUID (seen_items).
  2. Cruce de la entidad/ISIN con ASSETS (descarta casi todo).
  3. Filtro de categoría de la propia descripción del RSS (ruido conocido).
Lo que sobrevive queda en feed_items con estado PENDIENTE_DESCARGA,
listo para downloader.py.

Uso (desde la raíz del proyecto):
    python -m monitoring_produccion.rss_monitor --once --dry-run   # prueba sin persistir
    python -m monitoring_produccion.rss_monitor --once             # un ciclo
    python -m monitoring_produccion.rss_monitor                    # bucle cada 15 min
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from lxml import etree

from monitoring_produccion import config
from monitoring_produccion.http_utils import crear_sesion
from monitoring_produccion.monitor_db import (
    Activo,
    Estado,
    ItemFeed,
    abrir_guardian_solo_lectura,
    abrir_monitor_db,
    cargar_activos,
    insertar_feed_item,
    registrar_categoria,
    registrar_visto,
)

log = logging.getLogger(__name__)

# =============================================================================
# Normalización de texto
# =============================================================================
_RE_SUFIJOS_COMPUESTOS = re.compile(r"\b(?:S A U|S A|S L U|S L|S E|N V|A G)\b")
_SUFIJOS_SUELTOS = frozenset({"SICAV", "FI", "FIL", "PLC", "SGIIC", "SAU", "LTD", "INC", "CORP", "SIL", "SCA"})
_RE_ISIN = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")
_RE_DESCRIPCION = re.compile(
    r"^\s*(?P<hora>\d{1,2}:\d{2})\s+(?P<fecha>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<categoria>.+?)\s+-\s+(?P<resumen>.*)$",
    re.DOTALL,
)
_RE_PREFIJO_FECHA = re.compile(r"^\s*\d{1,2}:\d{2}\s+\d{2}/\d{2}/\d{4}\s*")
_PATRONES_RUIDO = tuple(re.compile(p) for p in config.PATRONES_CATEGORIA_RUIDO)


def normalizar_texto(texto: str) -> str:
    """MAYÚSCULAS, sin tildes, solo [A-Z0-9] separados por un espacio."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return re.sub(r"[^A-Z0-9]+", " ", sin_tildes.upper()).strip()


def normalizar_nombre_entidad(texto: str) -> str:
    """Normaliza y elimina sufijos legales (S.A., SICAV, FI, PLC...) para comparar nombres."""
    base = _RE_SUFIJOS_COMPUESTOS.sub(" ", normalizar_texto(texto))
    return " ".join(t for t in base.split() if t not in _SUFIJOS_SUELTOS)


# =============================================================================
# Cruce con ASSETS
# =============================================================================
@dataclass(frozen=True, slots=True)
class CandidatoCruce:
    """Activo candidato y método por el que se ha cruzado."""
    asset_id: str
    metodo: str  # isin | exacto | alias | parcial | fuzzy


_PRIORIDAD_METODO: dict[str, int] = {"isin": 0, "exacto": 1, "alias": 1, "parcial": 2, "fuzzy": 3}


class CruzadorActivos:
    """Cruza el nombre/ISIN de una noticia con los activos de la cartera."""

    def __init__(
        self,
        activos: list[Activo],
        aliases: dict[str, list[str]] | None = None,
        umbral_fuzzy: float = config.UMBRAL_FUZZY,
    ) -> None:
        self._umbral = umbral_fuzzy
        self._por_isin: dict[str, str] = {
            a.isin.strip().upper(): a.asset_id for a in activos if a.isin
        }
        # (asset_id, nombre_normalizado, origen)
        self._nombres: list[tuple[str, str, str]] = []
        ids_validos = {a.asset_id for a in activos}
        for a in activos:
            nombre = normalizar_nombre_entidad(a.nombre)
            if nombre:
                self._nombres.append((a.asset_id, nombre, "exacto"))
        for asset_id, lista in (aliases or {}).items():
            if asset_id not in ids_validos:
                log.warning("aliases.json: asset_id desconocido '%s' (ignorado)", asset_id)
                continue
            for alias in lista:
                nombre = normalizar_nombre_entidad(alias)
                if nombre:
                    self._nombres.append((asset_id, nombre, "alias"))

    def _comparar(self, entidad: str, nombre: str, origen: str) -> str | None:
        """Compara dos nombres normalizados y devuelve el método de cruce (o None)."""
        if entidad == nombre:
            return origen
        # Coincidencia parcial por secuencia de tokens (p. ej. SICAV paraguas vs subfondo).
        # Exige >= 2 tokens para evitar falsos positivos con nombres genéricos.
        if len(entidad.split()) >= 2 and len(nombre.split()) >= 2:
            if f" {entidad} " in f" {nombre} " or f" {nombre} " in f" {entidad} ":
                return "parcial"
        if SequenceMatcher(None, entidad, nombre).ratio() >= self._umbral:
            return "fuzzy"
        return None

    def cruzar(self, entidad: str, texto_extra: str = "") -> list[CandidatoCruce]:
        """Devuelve los activos candidatos (ISIN > nombre exacto/alias > parcial > fuzzy)."""
        isins = _RE_ISIN.findall(f"{entidad} {texto_extra}".upper())
        por_isin = {self._por_isin[i] for i in isins if i in self._por_isin}
        if por_isin:
            return [CandidatoCruce(a, "isin") for a in sorted(por_isin)]

        entidad_norm = normalizar_nombre_entidad(entidad)
        if not entidad_norm:
            return []
        mejores: dict[str, str] = {}
        for asset_id, nombre, origen in self._nombres:
            metodo = self._comparar(entidad_norm, nombre, origen)
            if metodo is None:
                continue
            previo = mejores.get(asset_id)
            if previo is None or _PRIORIDAD_METODO[metodo] < _PRIORIDAD_METODO[previo]:
                mejores[asset_id] = metodo
        return [CandidatoCruce(a, m) for a, m in sorted(mejores.items())]


def cargar_aliases(ruta: Path = config.RUTA_ALIASES) -> dict[str, list[str]]:
    """Carga aliases.json ({asset_id: [nombres alternativos]}). Tolerante a errores."""
    if not ruta.is_file():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("No se pudo leer %s: %s", ruta, exc)
        return {}
    if not isinstance(datos, dict):
        log.error("%s debe contener un objeto JSON {asset_id: [alias]}", ruta)
        return {}
    return {
        k: [a for a in v if isinstance(a, str) and a.strip()]
        for k, v in datos.items()
        if isinstance(k, str) and isinstance(v, list)
    }


# =============================================================================
# Lectura y parseo del feed
# =============================================================================
def descargar_feed(sesion: requests.Session, url: str) -> bytes:
    """Descarga el XML con timeout y reintentos (feedparser no tiene timeout propio)."""
    respuesta = sesion.get(url, timeout=config.TIMEOUT_HTTP)
    respuesta.raise_for_status()
    return respuesta.content


def _con_feedparser(contenido: bytes) -> list[dict[str, str]]:
    """Parseo principal. Si la estructura es sospechosa devuelve [] para activar el respaldo."""
    parseado = feedparser.parse(contenido)
    resultado: list[dict[str, str]] = []
    for e in parseado.entries:
        link = (e.get("link") or "").strip()
        titulo = (e.get("title") or "").strip()
        if not link or not titulo:
            return []
        resultado.append({
            "titulo": titulo,
            "link": link,
            "guid": (e.get("id") or link).strip(),
            "pubdate": (e.get("published") or "").strip(),
            "descripcion": e.get("summary") or e.get("description") or "",
        })
    return resultado


def _etiqueta_local(etiqueta: str) -> str:
    """Quita el namespace y pasa a minúsculas (el feed usa <Channel>, <Title>...)."""
    return etiqueta.rsplit("}", 1)[-1].lower()


def _con_lxml(contenido: bytes) -> list[dict[str, str]]:
    """Parser de respaldo tolerante a mayúsculas/estructura no estándar (sin entidades externas)."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    try:
        raiz = etree.fromstring(contenido, parser=parser)
    except etree.XMLSyntaxError:
        return []
    if raiz is None:
        return []
    resultado: list[dict[str, str]] = []
    for nodo in raiz.iter():
        if not isinstance(nodo.tag, str) or _etiqueta_local(nodo.tag) != "item":
            continue
        campos = {
            _etiqueta_local(h.tag): (h.text or "").strip()
            for h in nodo if isinstance(h.tag, str)
        }
        link, titulo = campos.get("link", ""), campos.get("title", "")
        if not link or not titulo:
            log.warning("Item sin título/link en el feed (omitido)")
            continue
        resultado.append({
            "titulo": titulo,
            "link": link,
            "guid": campos.get("guid") or link,
            "pubdate": campos.get("pubdate", ""),
            "descripcion": campos.get("description", ""),
        })
    return resultado

def _desenvolver_envoltorio_asmx(contenido: bytes) -> bytes | None:
    """
    Los servicios ASMX a veces devuelven <string xmlns="...">&lt;rss ...&gt;</string>.
    Si es el caso, devuelve el XML interior ya desescapado; si no, None.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    try:
        raiz = etree.fromstring(contenido, parser=parser)
    except etree.XMLSyntaxError:
        return None
    if raiz is None or _etiqueta_local(raiz.tag) != "string":
        return None
    interior = (raiz.text or "").strip()
    if "<" not in interior:
        return None
    return interior.encode("utf-8")

def extraer_entradas_crudas(contenido: bytes) -> list[dict[str, str]]:
    """feedparser → lxml → desenvolver ASMX. Distingue canal vacío de contenido ilegible."""
    entradas = _con_feedparser(contenido)
    if entradas:
        return entradas
    entradas = _con_lxml(contenido)
    if entradas:
        log.warning("feedparser no devolvió entradas válidas; se usó el parser de respaldo (lxml)")
        return entradas

    # Feed válido pero sin <item>: situación normal (p. ej. sin noticias hoy).
    minusculas = contenido.lower()
    if b"<channel" in minusculas and b"<item" not in minusculas:
        log.info("El canal es válido pero no contiene noticias en este momento")
        return []

    interior = _desenvolver_envoltorio_asmx(contenido)
    if interior is None:
        log.error("Contenido sin entradas reconocibles. Inicio de la respuesta: %r", contenido[:200])
        return []
    log.warning("Respuesta ASMX envuelta en <string>; reprocesando el XML interior")
    return _con_feedparser(interior) or _con_lxml(interior)

    interior = _desenvolver_envoltorio_asmx(contenido)
    if interior is None:
        log.error("Contenido sin entradas reconocibles. Inicio de la respuesta: %r", contenido[:200])
        return []
    log.warning("Respuesta ASMX envuelta en <string>; reprocesando el XML interior")
    return _con_feedparser(interior) or _con_lxml(interior)


def parsear_descripcion(html: str) -> tuple[str | None, str]:
    """
    Extrae (categoría, resumen). Soporta los dos formatos de la CNMV:
      HR : '<b>hora fecha</b> Categoría - <BR/><BR/>resumen'
      OIR: '<b>hora fecha</b> Categoría<BR/><BR/>resumen'
    El separador fiable es el <BR/>; si no existe, se intenta el formato antiguo ' - '.
    """
    sopa = BeautifulSoup(html or "", "lxml")
    for salto in sopa.find_all("br"):
        salto.replace_with("\n")
    partes = [" ".join(p.split()) for p in sopa.get_text().split("\n")]
    partes = [p for p in partes if p]
    if not partes:
        return None, ""

    if len(partes) >= 2:
        cabecera = _RE_PREFIJO_FECHA.sub("", partes[0]).strip()
        categoria = re.sub(r"\s*-\s*$", "", cabecera).strip() or None
        return categoria, " ".join(partes[1:])

    # Sin <BR/>: formato 'hora fecha Categoría - resumen' en una sola línea.
    coincidencia = _RE_DESCRIPCION.match(partes[0])
    if coincidencia:
        return coincidencia.group("categoria").strip(), coincidencia.group("resumen").strip()
    return None, partes[0]


def parsear_fecha_rfc822(texto: str) -> datetime | None:
    """Convierte una fecha RFC-822 (pubDate) a datetime UTC con zona horaria."""
    if not texto:
        return None
    try:
        fecha = parsedate_to_datetime(texto)
    except (TypeError, ValueError):
        return None
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return fecha.astimezone(timezone.utc)


def construir_item(feed: str, cruda: dict[str, str]) -> ItemFeed | None:
    """Normaliza una entrada cruda. Devuelve None si no tiene los datos mínimos."""
    link = cruda.get("link", "").strip()
    entidad = cruda.get("titulo", "").strip()
    if not link or not entidad:
        return None
    guid = (cruda.get("guid") or link).strip()
    consulta = {k.lower(): v for k, v in parse_qs(urlparse(link).query).items()}
    nreg = consulta.get("nreg", [None])[0]
    categoria, resumen = parsear_descripcion(cruda.get("descripcion", ""))
    return ItemFeed(
        guid_hash=hashlib.sha256(guid.encode("utf-8")).hexdigest(),
        feed=feed,
        nreg=nreg,
        entidad=entidad,
        link=link,
        categoria=categoria,
        resumen=resumen,
        publicado_utc=parsear_fecha_rfc822(cruda.get("pubdate", "")),
    )


def motivo_ruido_categoria(categoria: str | None, resumen: str) -> str | None:
    """Devuelve el patrón de ruido que coincide (o None si la noticia puede ser relevante)."""
    texto = normalizar_texto(f"{categoria or ''} {resumen}")
    for patron in _PATRONES_RUIDO:
        if patron.search(texto):
            return patron.pattern
    return None


# =============================================================================
# Procesamiento
# =============================================================================
@dataclass
class EstadisticasFeed:
    """Contadores de un ciclo para un canal."""
    leidos: int = 0
    nuevos: int = 0
    duplicados: int = 0
    antiguos: int = 0
    invalidos: int = 0
    sin_cruce: int = 0
    pendientes: int = 0
    descartados_categoria: int = 0
    errores: int = 0
    error_red: str | None = None


def _procesar_item(
    conn: sqlite3.Connection,
    cruzador: CruzadorActivos,
    item: ItemFeed,
    limite_antiguedad: datetime,
    est: EstadisticasFeed,
) -> None:
    """Dedupe → cruce → filtro de categoría. Se ejecuta dentro de una transacción."""
    if not registrar_visto(conn, item):
        est.duplicados += 1
        return
    est.nuevos += 1
    registrar_categoria(conn, item.feed, item.categoria)

    if item.publicado_utc is not None and item.publicado_utc < limite_antiguedad:
        est.antiguos += 1
        return

    candidatos = cruzador.cruzar(item.entidad, item.resumen)
    if not candidatos:
        est.sin_cruce += 1
        log.debug("[%s] sin cruce: '%s' | %s | %s",
                  item.feed, item.entidad, item.categoria or "sin categoría", item.resumen[:90])

    motivo = motivo_ruido_categoria(item.categoria, item.resumen)
    estado = Estado.DESCARTADO_CATEGORIA if motivo else Estado.PENDIENTE_DESCARGA
    for c in candidatos:
        insertar_feed_item(conn, item, c.asset_id, c.metodo, estado)
        nivel = logging.WARNING if c.metodo in ("parcial", "fuzzy") else logging.INFO
        log.log(
            nivel,
            "[%s] '%s' → %s (cruce=%s%s) estado=%s | %s",
            item.feed, item.entidad, c.asset_id, c.metodo,
            ", REVISAR" if c.metodo in ("parcial", "fuzzy") else "",
            estado.value, item.categoria or "sin categoría",
        )
    if motivo:
        est.descartados_categoria += len(candidatos)
    else:
        est.pendientes += len(candidatos)


def procesar_entradas(
    nombre_feed: str,
    crudas: list[dict[str, str]],
    conn: sqlite3.Connection,
    cruzador: CruzadorActivos,
    ahora: datetime | None = None,
    max_antiguedad_h: int = config.MAX_ANTIGUEDAD_HORAS,
) -> EstadisticasFeed:
    """Procesa las entradas de un canal. Cada noticia en su propia transacción."""
    ahora = ahora or datetime.now(timezone.utc)
    limite = ahora - timedelta(hours=max_antiguedad_h)
    est = EstadisticasFeed(leidos=len(crudas))
    for cruda in crudas:
        item = construir_item(nombre_feed, cruda)
        if item is None:
            est.invalidos += 1
            continue
        try:
            with conn:  # commit si todo va bien, rollback si falla
                _procesar_item(conn, cruzador, item, limite, est)
        except sqlite3.Error:
            est.errores += 1
            log.exception("Error de BD procesando '%s' (%s)", item.entidad, item.link)
    return est


def ejecutar_ciclo(
    conn: sqlite3.Connection,
    sesion: requests.Session,
    feeds: dict[str, str] | None = None,
) -> dict[str, EstadisticasFeed]:
    """Ejecuta un ciclo completo sobre todos los canales. Nunca lanza por un canal caído."""
    feeds = feeds or config.FEEDS_CNMV
    try:
        guardian = abrir_guardian_solo_lectura()
    except (FileNotFoundError, sqlite3.Error) as exc:
        log.error("No se puede abrir guardian.db: %s", exc)
        return {}
    try:
        activos = cargar_activos(guardian)
    except sqlite3.Error:
        log.exception("No se pudo leer la tabla ASSETS")
        return {}
    finally:
        guardian.close()
    if not activos:
        log.warning("ASSETS está vacía: nada que vigilar")
        return {}

    cruzador = CruzadorActivos(activos, cargar_aliases())
    resultados: dict[str, EstadisticasFeed] = {}
    for nombre, url in feeds.items():
        try:
            contenido = descargar_feed(sesion, url)
            crudas = extraer_entradas_crudas(contenido)
            resultados[nombre] = procesar_entradas(nombre, crudas, conn, cruzador)
        except requests.RequestException as exc:
            log.error("Canal %s: fallo de red: %s", nombre, exc)
            resultados[nombre] = EstadisticasFeed(error_red=str(exc))
        except Exception:  # noqa: BLE001 - un canal roto no debe tumbar el bucle
            log.exception("Canal %s: error inesperado", nombre)
            resultados[nombre] = EstadisticasFeed(errores=1)
        else:
            log.info("Canal %s → %s", nombre, resultados[nombre])
        time.sleep(config.PAUSA_ENTRE_PETICIONES_S)
    return resultados


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI."""
    ap = argparse.ArgumentParser(description="Monitor RSS CNMV (producción)")
    ap.add_argument("--once", action="store_true", help="ejecuta un solo ciclo y termina")
    ap.add_argument("--dry-run", action="store_true", help="BD en memoria: no persiste nada")
    ap.add_argument("--intervalo", type=int, default=config.INTERVALO_POLLING_S,
                    help="segundos entre ciclos (por defecto 900)")
    ap.add_argument("-v", "--verbose", action="store_true", help="muestra también las noticias sin cruce")
    args = ap.parse_args(argv)

    config.configurar_logging(logging.DEBUG if args.verbose else logging.INFO)
    conn = abrir_monitor_db(":memory:" if args.dry_run else config.RUTA_MONITOR_DB)
    sesion = crear_sesion()
    try:
        while True:
            ejecutar_ciclo(conn, sesion)
            if args.once or args.dry_run:
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

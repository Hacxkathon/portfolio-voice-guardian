"""
Capa de acceso a datos de la Fase de Producción.

- monitor.db  : BD propia (lectura/escritura). Estado de cada noticia.
- guardian.db : BD del proyecto, SOLO LECTURA (ASSETS y DOCUMENTS).

Convención: las funciones de escritura NO hacen commit; el llamador agrupa
operaciones con `with conn:` para que sean transaccionales.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from monitoring_produccion import config

log = logging.getLogger(__name__)


class Estado(str, Enum):
    """Máquina de estados de cada (noticia, activo)."""
    PENDIENTE_DESCARGA = "PENDIENTE_DESCARGA"
    DESCARTADO_CATEGORIA = "DESCARTADO_CATEGORIA"
    DESCARGADO = "DESCARGADO"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class Activo:
    """Fila de ASSETS de guardian.db."""
    asset_id: str
    isin: str
    nombre: str
    tipo: str


@dataclass(frozen=True, slots=True)
class ItemFeed:
    """Noticia normalizada leída de un canal RSS."""
    guid_hash: str
    feed: str
    nreg: str | None
    entidad: str
    link: str
    categoria: str | None
    resumen: str
    publicado_utc: datetime | None


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS seen_items (
    guid_hash     TEXT PRIMARY KEY,
    feed          TEXT NOT NULL,
    nreg          TEXT,
    entidad       TEXT NOT NULL,
    categoria     TEXT,
    link          TEXT NOT NULL,
    publicado_utc TEXT,
    primera_vez   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feed_items (
    guid_hash      TEXT NOT NULL,
    asset_id       TEXT NOT NULL,
    feed           TEXT NOT NULL,
    nreg           TEXT,
    entidad        TEXT NOT NULL,
    categoria      TEXT,
    resumen        TEXT,
    link           TEXT NOT NULL,
    publicado_utc  TEXT,
    metodo_cruce   TEXT NOT NULL,
    estado         TEXT NOT NULL,
    intentos       INTEGER NOT NULL DEFAULT 0,
    ultimo_error   TEXT,
    pdf_url        TEXT,
    pdf_sha256     TEXT,
    ruta_tmp       TEXT,
    ruta_final     TEXT,
    filtro_score   REAL,
    filtro_motivo  TEXT,
    creado_en      TEXT NOT NULL DEFAULT (datetime('now')),
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guid_hash, asset_id)
);
CREATE INDEX IF NOT EXISTS idx_feed_items_estado ON feed_items(estado);

CREATE TABLE IF NOT EXISTS categorias_vistas (
    feed       TEXT NOT NULL,
    categoria  TEXT NOT NULL,
    veces      INTEGER NOT NULL DEFAULT 1,
    ultima_vez TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (feed, categoria)
);
"""

_COLUMNAS_ACTUALIZABLES = frozenset({
    "estado", "intentos", "ultimo_error", "pdf_url", "pdf_sha256",
    "ruta_tmp", "ruta_final", "filtro_score", "filtro_motivo",
})


def abrir_monitor_db(ruta: Path | str = config.RUTA_MONITOR_DB) -> sqlite3.Connection:
    """Abre (y crea si hace falta) monitor.db. Acepta ':memory:' para pruebas/dry-run."""
    if str(ruta) != ":memory:":
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ruta), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # varios procesos leyendo/escribiendo
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_ESQUEMA)
    conn.commit()
    return conn


def abrir_guardian_solo_lectura(ruta: Path = config.RUTA_GUARDIAN_DB) -> sqlite3.Connection:
    """Abre guardian.db en modo SOLO LECTURA (imposible modificarla por accidente)."""
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe guardian.db en {ruta}")
    conn = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def cargar_activos(conn_guardian: sqlite3.Connection) -> list[Activo]:
    """Lee los activos de la tabla ASSETS."""
    filas = conn_guardian.execute("SELECT asset_id, isin, name, asset_type FROM ASSETS").fetchall()
    return [Activo(f["asset_id"], f["isin"], f["name"], f["asset_type"]) for f in filas]


def hash_existe_en_documents(conn_guardian: sqlite3.Connection, sha256: str) -> bool:
    """True si el hash ya está en DOCUMENTS.content_hash (UNIQUE en la demo)."""
    fila = conn_guardian.execute(
        "SELECT 1 FROM DOCUMENTS WHERE content_hash = ? LIMIT 1", (sha256,)
    ).fetchone()
    return fila is not None


def registrar_visto(conn: sqlite3.Connection, item: ItemFeed) -> bool:
    """Registra la noticia como vista. Devuelve True si era NUEVA (dedupe atómico)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO seen_items "
        "(guid_hash, feed, nreg, entidad, categoria, link, publicado_utc) VALUES (?,?,?,?,?,?,?)",
        (item.guid_hash, item.feed, item.nreg, item.entidad, item.categoria, item.link,
         item.publicado_utc.isoformat() if item.publicado_utc else None),
    )
    return cur.rowcount == 1


def registrar_categoria(conn: sqlite3.Connection, feed: str, categoria: str | None) -> None:
    """Contabiliza las categorías vistas para afinar después las listas de filtrado."""
    conn.execute(
        "INSERT INTO categorias_vistas (feed, categoria) VALUES (?, ?) "
        "ON CONFLICT(feed, categoria) DO UPDATE SET veces = veces + 1, ultima_vez = datetime('now')",
        (feed, categoria or "(sin categoria)"),
    )


def insertar_feed_item(
    conn: sqlite3.Connection, item: ItemFeed, asset_id: str, metodo: str, estado: Estado
) -> None:
    """Crea la unidad de trabajo (noticia, activo)."""
    conn.execute(
        "INSERT OR IGNORE INTO feed_items "
        "(guid_hash, asset_id, feed, nreg, entidad, categoria, resumen, link, publicado_utc, "
        " metodo_cruce, estado) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (item.guid_hash, asset_id, item.feed, item.nreg, item.entidad, item.categoria,
         item.resumen, item.link,
         item.publicado_utc.isoformat() if item.publicado_utc else None,
         metodo, estado.value),
    )


def obtener_items(conn: sqlite3.Connection, estado: Estado, limite: int = 50) -> list[sqlite3.Row]:
    """Devuelve las unidades de trabajo en un estado dado (las más antiguas primero)."""
    return conn.execute(
        "SELECT * FROM feed_items WHERE estado = ? ORDER BY creado_en LIMIT ?",
        (estado.value, limite),
    ).fetchall()


def actualizar_item(conn: sqlite3.Connection, guid_hash: str, asset_id: str, **campos: Any) -> None:
    """Actualiza columnas permitidas de feed_items (lista blanca contra inyección SQL)."""
    invalidas = set(campos) - _COLUMNAS_ACTUALIZABLES
    if invalidas:
        raise ValueError(f"Columnas no actualizables: {sorted(invalidas)}")
    if isinstance(campos.get("estado"), Estado):
        campos["estado"] = campos["estado"].value
    asignaciones = ", ".join(f"{col} = ?" for col in campos)
    conn.execute(
        f"UPDATE feed_items SET {asignaciones}, actualizado_en = datetime('now') "
        "WHERE guid_hash = ? AND asset_id = ?",
        (*campos.values(), guid_hash, asset_id),
    )
    
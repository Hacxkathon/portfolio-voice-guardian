"""
Construye catalogo.db a partir de los CSV de la carpeta datos/.

    python -m catalogo.construir      (desde la carpeta pvg_ui)

Los CSV son la fuente editable (se abren con Excel: separador ';', decimales con coma).
Si cambias un CSV, vuelve a ejecutar este script y la base de datos se regenera entera.
Solo usa la librería estándar de Python (sin pandas), así que funciona con Smart App Control.
"""

from __future__ import annotations

import csv
import sqlite3
import unicodedata
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATOS = BASE / "datos"
DB = BASE / "catalogo.db"

ESQUEMA = """
CREATE TABLE plataformas (
    nombre                TEXT PRIMARY KEY,   -- igual que store.PLATAFORMAS
    tipo                  TEXT,
    sede                  TEXT,
    regulador             TEXT,
    garantia              TEXT,
    productos             TEXT,
    comision_acciones     TEXT,
    comision_etf          TEXT,
    comision_fondos       TEXT,
    custodia              TEXT,
    cambio_divisa         TEXT,
    remuneracion_efectivo TEXT,
    otras_condiciones     TEXT,
    opera_en_espana       INTEGER,            -- 1 sí / 0 no
    aviso                 TEXT,
    web                   TEXT,
    fuente_url            TEXT,
    verificado            TEXT,               -- fecha de la última revisión
    fiabilidad            TEXT                -- alta / media / baja
);

CREATE TABLE tipos_producto (
    tipo                TEXT PRIMARY KEY,     -- igual que store.TIPOS
    identificador       TEXT,
    documento_vigilado  TEXT,
    donde_se_publica    TEXT,
    url_fuente_oficial  TEXT,
    que_cambios_vigilar TEXT
);

CREATE TABLE instrumentos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    isin                 TEXT UNIQUE,         -- NULL en cripto, letras genéricas y cuentas
    ticker               TEXT,
    nombre               TEXT NOT NULL,
    nombre_corto         TEXT,
    tipo                 TEXT REFERENCES tipos_producto(tipo),
    subtipo              TEXT,
    emisor               TEXT,
    mercado              TEXT,
    divisa               TEXT,
    pais                 TEXT,
    sector               TEXT,
    indice               TEXT,
    distribucion         TEXT,
    ter_pct              REAL,                -- gastos corrientes anuales (%)
    comision_gestion_pct REAL,
    inversion_minima_eur REAL,
    alias                TEXT,
    busqueda             TEXT,                -- texto normalizado para buscar (generado)
    isin_valido          INTEGER,             -- 1 si el dígito de control es correcto (generado)
    fuente_url           TEXT,
    verificado           TEXT,
    fiabilidad           TEXT,                -- alta / media / baja / ficticio
    notas                TEXT
);

CREATE TABLE eventos (
    id             TEXT PRIMARY KEY,
    ambito         TEXT,                      -- 'instrumento' o 'plataforma'
    isin           TEXT,
    plataforma     TEXT,
    event_type     TEXT,                      -- fees, dividends, condiciones, regulacion
    titulo         TEXT,
    valor_anterior TEXT,
    valor_nuevo    TEXT,
    unidad         TEXT,
    delta_pp       REAL,                      -- variación en puntos (para impact_eur de la app)
    fecha_efectiva TEXT,
    resumen        TEXT,
    fuente_url     TEXT,
    es_simulado    INTEGER                    -- 1 = escenario de demo, 0 = hecho real con fuente
);

CREATE INDEX idx_inst_tipo ON instrumentos(tipo);
CREATE INDEX idx_inst_ticker ON instrumentos(ticker);
CREATE INDEX idx_ev_isin ON eventos(isin);
CREATE INDEX idx_ev_plat ON eventos(plataforma);
"""

NUMERICAS = {"ter_pct", "comision_gestion_pct", "inversion_minima_eur", "delta_pp", "opera_en_espana", "es_simulado"}


def normalizar(texto: str) -> str:
    """'Telefónica, S.A.' -> 'telefonica, s.a.' (minúsculas y sin acentos)."""
    t = unicodedata.normalize("NFKD", texto or "").lower()
    return "".join(c for c in t if not unicodedata.combining(c))


def isin_valido(isin: str) -> bool:
    """Comprueba el formato y el dígito de control (algoritmo de Luhn sobre letras convertidas a números)."""
    isin = (isin or "").strip().upper()
    if len(isin) != 12 or not isin[:2].isalpha() or not isin[-1].isdigit() or not isin.isalnum():
        return False
    digitos = "".join(str(int(c, 36)) for c in isin[:-1])   # A=10, B=11 ... Z=35
    total = 0
    for i, d in enumerate(reversed(digitos)):
        n = int(d)
        if i % 2 == 0:            # desde la derecha, se duplica una posición sí y otra no
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return (10 - total % 10) % 10 == int(isin[-1])


def leer(nombre: str) -> list[dict]:
    with open(DATOS / nombre, encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f, delimiter=";"))
    for fila in filas:
        for k, v in fila.items():
            v = (v or "").strip()
            if k in NUMERICAS:
                fila[k] = float(v.replace(".", "").replace(",", ".")) if v and "," in v else (float(v) if v else None)
            else:
                fila[k] = v or None
    return filas


def construir() -> None:
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(ESQUEMA)

    def insertar(tabla: str, filas: list[dict]) -> None:
        if not filas:
            return
        cols = list(filas[0].keys())
        con.executemany(f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [[f[c] for c in cols] for f in filas])

    insertar("plataformas", leer("plataformas.csv"))
    insertar("tipos_producto", leer("tipos_producto.csv"))

    instrumentos = leer("instrumentos.csv")
    for f in instrumentos:
        partes = [f.get("nombre"), f.get("nombre_corto"), f.get("ticker"), f.get("isin"), f.get("alias"), f.get("emisor")]
        f["busqueda"] = " | ".join(normalizar(p) for p in partes if p)
        f["isin_valido"] = int(isin_valido(f["isin"])) if f.get("isin") else None
    insertar("instrumentos", instrumentos)
    insertar("eventos", leer("eventos.csv"))
    con.commit()

    n = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
         for t in ("plataformas", "tipos_producto", "instrumentos", "eventos")}
    malos = con.execute("SELECT nombre, isin FROM instrumentos WHERE isin_valido = 0 AND fiabilidad != 'ficticio'").fetchall()
    con.close()
    print(f"catalogo.db creado: {n}")
    if malos:
        print("ATENCIÓN, ISIN con dígito de control incorrecto:", malos)


if __name__ == "__main__":
    construir()

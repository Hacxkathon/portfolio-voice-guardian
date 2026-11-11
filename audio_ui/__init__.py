"""
Catálogo de productos, plataformas y eventos para Portfolio Voice Guardian.

Uso desde la app (solo librería estándar, sin pandas):

    import catalogo

    catalogo.buscar("repsol")                 # autocompletar al añadir una posición
    catalogo.por_isin("ES0173516115")         # ficha completa del producto
    catalogo.isin_valido("ES00000000001")     # False -> avisar al usuario
    catalogo.plataforma("Openbank")           # comisiones y condiciones
    catalogo.documento_para_tipo("ETF")       # qué documento vigilamos y dónde se publica
    catalogo.eventos_para(isins=[...], plataformas=[...])   # alertas que afectan a una cartera

Si catalogo.db no existe, se construye solo a partir de los CSV de datos/.
Todas las funciones devuelven dict o listas de dict, y nunca lanzan excepción por datos ausentes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .construir import DB, construir, isin_valido, normalizar

__all__ = ["buscar", "por_isin", "isin_valido", "plataforma", "plataformas", "plataformas_activas_en_espana",
           "documento_para_tipo", "eventos_para", "eventos", "comprobar_cartera"]


def _con() -> sqlite3.Connection:
    if not DB.exists():
        construir()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _filas(sql: str, params: Iterable = ()) -> list[dict]:
    con = _con()
    try:
        return [dict(r) for r in con.execute(sql, tuple(params))]
    finally:
        con.close()


# ---------------------------------------------------------------- productos

def buscar(texto: str, tipo: Optional[str] = None, limite: int = 10) -> list[dict]:
    """Busca por nombre, ticker, ISIN o alias, sin importar mayúsculas ni acentos.
    Primero los que empiezan por el texto, después los que lo contienen."""
    q = normalizar(texto).strip()
    if not q:
        return []
    sql = ("SELECT *, CASE WHEN busqueda LIKE ? THEN 0 ELSE 1 END AS orden "
           "FROM instrumentos WHERE busqueda LIKE ?")
    params: list = [f"{q}%", f"%{q}%"]
    if tipo:
        sql += " AND tipo = ?"
        params.append(tipo)
    sql += " ORDER BY orden, CASE fiabilidad WHEN 'ficticio' THEN 1 ELSE 0 END, nombre_corto LIMIT ?"
    params.append(limite)
    return _filas(sql, params)


def por_isin(isin: str) -> Optional[dict]:
    filas = _filas("SELECT * FROM instrumentos WHERE UPPER(isin) = UPPER(?)", [(isin or "").strip()])
    return filas[0] if filas else None


# ---------------------------------------------------------------- plataformas

def plataforma(nombre: str) -> Optional[dict]:
    filas = _filas("SELECT * FROM plataformas WHERE LOWER(nombre) = LOWER(?)", [(nombre or "").strip()])
    return filas[0] if filas else None


def plataformas() -> list[dict]:
    return _filas("SELECT * FROM plataformas ORDER BY nombre")


def plataformas_activas_en_espana() -> list[str]:
    """Nombres para el desplegable 'Plataforma o exchange' (excluye las que ya no operan en España)."""
    return [f["nombre"] for f in _filas("SELECT nombre FROM plataformas WHERE opera_en_espana = 1 ORDER BY nombre")]


# ---------------------------------------------------------------- documentos y eventos

def documento_para_tipo(tipo: str) -> Optional[dict]:
    filas = _filas("SELECT * FROM tipos_producto WHERE tipo = ?", [tipo])
    return filas[0] if filas else None


def eventos(incluir_simulados: bool = True) -> list[dict]:
    sql = "SELECT * FROM eventos" + ("" if incluir_simulados else " WHERE es_simulado = 0")
    return _filas(sql + " ORDER BY es_simulado, fecha_efectiva DESC")


def eventos_para(isins: Iterable[str] = (), plataformas: Iterable[str] = (),
                 incluir_simulados: bool = True) -> list[dict]:
    """Eventos que afectan a los productos (por ISIN) o a las plataformas indicadas."""
    isins = [i.upper() for i in isins if i]
    plats = [p.lower() for p in plataformas if p]
    if not isins and not plats:
        return []
    cond = []
    params: list = []
    if isins:
        cond.append(f"UPPER(isin) IN ({', '.join('?' * len(isins))})")
        params += isins
    if plats:
        cond.append(f"LOWER(plataforma) IN ({', '.join('?' * len(plats))})")
        params += plats
    sql = f"SELECT * FROM eventos WHERE ({' OR '.join(cond)})"
    if not incluir_simulados:
        sql += " AND es_simulado = 0"
    return _filas(sql + " ORDER BY es_simulado, fecha_efectiva DESC", params)


def comprobar_cartera(posiciones: list[dict]) -> list[dict]:
    """Revisa las posiciones de store.py ({plataforma, tipo, nombre, isin, ...}) y devuelve avisos:
    ISIN inválido, ISIN desconocido con sugerencias, plataforma que ya no opera en España."""
    avisos = []
    for p in posiciones:
        isin = (p.get("isin") or "").strip().upper()
        if p.get("tipo") in ("Criptomoneda", "Depósito o cuenta remunerada"):
            pass
        elif isin and len(isin) == 12 and not isin_valido(isin):
            avisos.append({"id": p.get("id"), "tipo": "isin_invalido",
                           "mensaje": f"El ISIN {isin} no es válido (dígito de control incorrecto).",
                           "sugerencias": buscar(p.get("nombre", ""), limite=3)})
        elif isin and len(isin) != 12 and p.get("tipo") not in ("Criptomoneda",):
            avisos.append({"id": p.get("id"), "tipo": "isin_invalido",
                           "mensaje": f"Un ISIN tiene 12 caracteres; '{isin}' tiene {len(isin)}.",
                           "sugerencias": buscar(p.get("nombre", ""), limite=3)})
        elif isin and not por_isin(isin):
            avisos.append({"id": p.get("id"), "tipo": "isin_desconocido",
                           "mensaje": f"El ISIN {isin} es válido pero no está en el catálogo.", "sugerencias": []})
        elif not isin:
            avisos.append({"id": p.get("id"), "tipo": "sin_isin", "mensaje": "Falta el ISIN.",
                           "sugerencias": buscar(p.get("nombre", ""), limite=3)})
        plat = plataforma(p.get("plataforma", ""))
        if plat and not plat["opera_en_espana"]:
            avisos.append({"id": p.get("id"), "tipo": "plataforma", "mensaje": plat["aviso"], "sugerencias": []})
    return avisos

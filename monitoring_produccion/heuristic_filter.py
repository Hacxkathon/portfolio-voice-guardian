"""
heuristic_filter.py — Paso 3 del Monitoring Engine (Producción).

Filtro determinista de coste cero-tokens. Para cada elemento en estado DESCARGADO:
  1. Extrae el texto del PDF con PyMuPDF (primeras N páginas) y lo normaliza.
  2. Puntúa con reglas positivas (OPA, comisiones, dividendos, fusión...) y
     negativas (nombramientos, domicilio, derechos de voto...).
  3. Si pasa → mueve el PDF a data/demo_docs/ (atómico). Si no → lo borra.
Todas las decisiones quedan auditadas en feed_items (filtro_score, filtro_motivo).

Uso (desde la raíz del proyecto):
    python -m monitoring_produccion.heuristic_filter --probar ruta\\documento.pdf
    python -m monitoring_produccion.heuristic_filter --once --dry-run
    python -m monitoring_produccion.heuristic_filter --once
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from monitoring_produccion import config
from monitoring_produccion.monitor_db import (
    Estado,
    abrir_guardian_solo_lectura,
    abrir_monitor_db,
    actualizar_item,
    cargar_activos,
    obtener_items,
)

log = logging.getLogger(__name__)

PESO_REGLA_FUERTE = 4   # una regla con peso >= 4 aprueba por sí sola
BONUS_ISIN = 2          # el ISIN del activo aparece en el documento
PENALIZACION_CRUCE_DUDOSO = 2  # cruce parcial/fuzzy sin ISIN confirmado: umbral más exigente


# =============================================================================
# Reglas (se aplican sobre texto en minúsculas y SIN tildes)
# =============================================================================
@dataclass(frozen=True, slots=True)
class Regla:
    """Regla de puntuación. peso > 0 suma; peso < 0 resta."""
    nombre: str
    patron: re.Pattern[str]
    peso: int
    evento: str  # clase de evento que sugiere (fees, dividends, strategy, debt, regulation, guidance, ruido)


def _r(nombre: str, regex: str, peso: int, evento: str) -> Regla:
    return Regla(nombre, re.compile(regex), peso, evento)


REGLAS: tuple[Regla, ...] = (
    # --- fees: impacto en € calculado por impact/engine.py -----------------------------
    _r("cambio_comisiones",
       r"comisi(?:on|ones)[^.]{0,100}(?:modific|incremen|aument|reduc|baja|elev|pasa|nuev)"
       r"|(?:modific|incremen|aument|reduc|baj|elev|nuev)\w*[^.]{0,60}comisi(?:on|ones)"
       r"|(?:management|ongoing|performance) fees?[^.]{0,80}(?:increas|decreas|reduc|chang|revis)",
       5, "fees"),
    _r("comisiones",
       r"comisi(?:on|ones) de (?:gestion|deposito|suscripcion|reembolso|exito|custodia)"
       r"|\bcomisiones\b|management fees?|performance fees?", 3, "fees"),
    _r("ter_gastos", r"\bter\b|gastos corrientes|ongoing charges?|total expense ratio", 3, "fees"),
    # --- dividends: impacto en € calculado por impact/engine.py ------------------------
    _r("cambio_dividendo",
       r"(?:suspen|recort|reduc|elimin|cancel|incremen|aument)\w*[^.]{0,60}dividend\w*"
       r"|dividend\w*[^.]{0,60}(?:suspen|recort|reduc|elimin|cancel|incremen|aument)", 5, "dividends"),
    _r("dividendos",
       r"dividend\w*|\bcupon(?:es)?\b|distribucion de (?:beneficios|resultados|reservas)"
       r"|remuneracion al accionista", 3, "dividends"),
    # --- strategy: eventos corporativos / cambios de política (alerta de asesor) -------
    _r("opa", r"\bopa\b|oferta publica de (?:adquisicion|venta|intercambio)|take-?over|tender offer", 6, "strategy"),
    _r("fusion_escision", r"\bfusion\w*|\babsorcion\b|\bescision\b|\bmerger\b|\bspin-?off\b", 5, "strategy"),
    _r("liquidacion_suspension",
       r"\bliquidacion\b|\bdisolucion\b|suspension (?:temporal )?de (?:los )?reembolsos"
       r"|\bliquidation\b|winding[ -]up", 5, "strategy"),
    _r("politica_inversion",
       r"politica de inversion|vocacion inversora|objetivo de inversion"
       r"|cambio de (?:gestora|depositario|sociedad gestora)|investment (?:policy|objective)"
       r"|indicador de riesgo|perfil de riesgo", 4, "strategy"),
    _r("operaciones_corporativas",
       r"venta de (?:la |una )?(?:participacion|filial|negocio|activos)"
       r"|adquisicion de (?:la |una )?(?:participacion|filial|sociedad|compania)"
       r"|joint venture|desinversion", 3, "strategy"),
    _r("reembolsos_liquidez",
       r"reembolsos?[^.]{0,80}(?:superior\w*|supera\w*|exced\w+|mas del)"
       r"|reduccion del capital en circulacion|solicitudes? de reembolso"
       r"|\bgates?\b|side pocket|iliquid\w+", 3, "strategy"),
    # --- debt -------------------------------------------------------------------------
    _r("cambio_rating",
       r"(?:rebaja|bajada|degradacion|mejora|revision)[^.]{0,40}(?:rating|calificacion)"
       r"|\bdowngrade\b|\bupgrade\b", 4, "debt"),
    _r("deuda",
       r"emision de (?:deuda|bonos|obligaciones|participaciones preferentes)|refinanciacion"
       r"|calificacion crediticia|\bcovenants?\b|amortizacion anticipada|concurso de acreedores"
       r"|\bimpago\b|\bdefault\b", 3, "debt"),
    # --- regulation -------------------------------------------------------------------
    _r("sancion",
       r"expediente sancionador|\bsancion\w*|incumplimiento (?:normativo|regulatorio)"
       r"|requerimiento (?:de la )?cnmv", 4, "regulation"),
    _r("regulacion", r"\bmifid\b|\bregulat\w+|\bsupervis\w+", 2, "regulation"),
    # --- guidance / resultados -------------------------------------------------------
    _r("ajuste_previsiones",
       r"profit warning|(?:rebaj|revis|ajust|actualiz|modific)\w*[^.]{0,40}"
       r"(?:previsiones|guidance|objetivos (?:de|para)|perspectivas)", 5, "guidance"),
    _r("resultados",
       r"resultados? (?:del|de los|de la|correspondientes|trimestral\w*|anuales?|semestral\w*|consolidad\w*)"
       r"|beneficio neto|\bebitda\b|cifra de negocios|\bguidance\b|\bprevisiones\b", 3, "guidance"),
    # --- ruido administrativo (restan) ------------------------------------------------
    _r("derechos_voto",
       r"total numero de derechos de voto|numero total de derechos de voto|derechos de voto y de capital",
       -5, "ruido"),
    _r("accionistas_patrimonio", r"numero de accionistas y patrimonio|patrimonio alcanzado", -5, "ruido"),
    _r("nombramientos", r"\bnombramientos?\b|\bdimision\w*|\bcooptacion\b|\breeleccion\b", -3, "ruido"),
    _r("domicilio", r"cambio de domicilio|traslado del domicilio|domicilio social", -3, "ruido"),
    _r("autocartera", r"operaciones? de autocartera|informacion sobre autocartera|contrato de liquidez", -3, "ruido"),
    _r("participaciones_significativas",
       r"participaciones? significativ\w+|derechos de voto atribuidos", -3, "ruido"),
    _r("junta", r"convocatoria de (?:la )?junta|orden del dia", -2, "ruido"),
    _r("remuneracion_directivos",
       r"plan de incentivos|retribucion(?:es)? (?:de los |a los )?(?:consejeros|directivos)"
       r"|politica de remuneraciones", -2, "ruido"),
)


# =============================================================================
# Extracción y evaluación
# =============================================================================
class ErrorPDF(Exception):
    """El PDF no se puede leer (corrupto, cifrado...)."""


@dataclass(frozen=True, slots=True)
class ResultadoFiltro:
    """Resultado auditable de evaluar un PDF."""
    aprobado: bool
    puntuacion: int
    motivo: str
    reglas: tuple[str, ...]
    eventos: tuple[str, ...]
    paginas: int
    caracteres: int
    isin_confirmado: bool
    requiere_revision: bool
    extracto: str


_RE_INVISIBLES = re.compile(r"[\u200b-\u200d\u2060\ufeff\u00ad]")  # espacios de ancho cero, BOM, guion blando


def normalizar_para_filtro(texto: str) -> str:
    """Minúsculas, sin tildes ni caracteres invisibles; une palabras partidas por guion y colapsa espacios."""
    texto = _RE_INVISIBLES.sub("", texto)
    texto = re.sub(r"-\s*\n\s*", "", texto)
    texto = unicodedata.normalize("NFKD", texto.lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip()


def extraer_texto(ruta: Path) -> tuple[str, int]:
    """Devuelve (texto normalizado de las primeras N páginas, nº total de páginas)."""
    try:
        with pymupdf.open(str(ruta)) as doc:
            if doc.needs_pass:
                raise ErrorPDF("PDF cifrado")
            total = doc.page_count
            partes = [doc.load_page(n).get_text("text") for n in range(min(total, config.MAX_PAGINAS_FILTRO))]
    except ErrorPDF:
        raise
    except (RuntimeError, ValueError, OSError) as exc:
        raise ErrorPDF(f"PDF ilegible: {exc}") from exc
    return normalizar_para_filtro("\n".join(partes)), total


def evaluar_texto(
    texto: str,
    paginas: int,
    isin: str | None = None,
    metodo_cruce: str | None = None,
    contexto: str = "",
) -> ResultadoFiltro:
    """
    Aplica las reglas al texto del PDF (ya normalizado) más el contexto del RSS
    (categoría + resumen), que en avisos cortos suele ser más informativo que el PDF.
    """
    extracto = texto[:300]
    if len(texto) < config.MIN_CHARS_TEXTO:  # la capa de texto se valora solo sobre el PDF
        return ResultadoFiltro(
            aprobado=True, puntuacion=0, paginas=paginas, caracteres=len(texto),
            motivo="REVISAR: sin capa de texto suficiente (¿PDF escaneado?); se aprueba por precaución",
            reglas=(), eventos=(), isin_confirmado=False, requiere_revision=True, extracto=extracto,
        )

    texto_total = f"{texto} {normalizar_para_filtro(contexto)}" if contexto.strip() else texto
    coincidencias = [r for r in REGLAS if r.patron.search(texto_total)]
    puntos = sum(r.peso for r in coincidencias)
    isin_ok = bool(isin) and isin.lower() in texto  # type: ignore[union-attr]
    if isin_ok:
        puntos += BONUS_ISIN

    umbral = config.UMBRAL_APROBACION_FILTRO
    if metodo_cruce in ("parcial", "fuzzy") and not isin_ok:
        umbral += PENALIZACION_CRUCE_DUDOSO

    fuertes = [r for r in coincidencias if r.peso >= PESO_REGLA_FUERTE]
    if fuertes:
        aprobado, razon = True, "regla fuerte: " + ", ".join(r.nombre for r in fuertes)
    elif puntos >= umbral:
        aprobado, razon = True, f"puntos {puntos} >= umbral {umbral}"
    else:
        aprobado, razon = False, f"puntos {puntos} < umbral {umbral}"

    etiquetas = tuple(f"{r.nombre}({r.peso:+d})" for r in coincidencias)
    eventos = tuple(sorted({r.evento for r in coincidencias if r.peso > 0}))
    motivo = (
        f"{'APROBADO' if aprobado else 'RECHAZADO'}: {razon} | reglas: "
        f"{', '.join(etiquetas) or 'ninguna'} | isin={'sí' if isin_ok else 'no'}"
    )
    return ResultadoFiltro(
        aprobado=aprobado, puntuacion=puntos, motivo=motivo, reglas=etiquetas, eventos=eventos,
        paginas=paginas, caracteres=len(texto), isin_confirmado=isin_ok,
        requiere_revision=False, extracto=extracto,
    )


def evaluar_pdf(
    ruta: Path, isin: str | None = None, metodo_cruce: str | None = None, contexto: str = ""
) -> ResultadoFiltro:
    """Extrae el texto del PDF y lo evalúa junto al contexto del RSS."""
    texto, paginas = extraer_texto(ruta)
    return evaluar_texto(texto, paginas, isin, metodo_cruce, contexto)


# =============================================================================
# Movimiento de ficheros
# =============================================================================
def mover_atomico(origen: Path, destino: Path) -> None:
    """
    Mueve el PDF sin que el escáner de la demo vea nunca un fichero a medias.
    Mismo disco: os.replace (atómico). Distinto disco: copia a .part + renombrado.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(origen, destino)
        return
    except OSError as exc:
        log.warning("os.replace falló (%s); se usa copia segura mediante .part", exc)
    parte = destino.with_name(destino.name + ".part")
    try:
        shutil.copy2(origen, parte)
        os.replace(parte, destino)
    finally:
        parte.unlink(missing_ok=True)
    origen.unlink(missing_ok=True)


# =============================================================================
# Procesamiento de la cola
# =============================================================================
@dataclass
class EstadisticasFiltro:
    """Contadores de un ciclo del filtro."""
    procesados: int = 0
    aprobados: int = 0
    rechazados: int = 0
    pendientes: int = 0
    errores: int = 0


def cargar_isins() -> dict[str, str]:
    """{asset_id: isin} desde guardian.db (solo lectura). Si no está disponible, {}."""
    try:
        guardian = abrir_guardian_solo_lectura()
    except (FileNotFoundError, sqlite3.Error) as exc:
        log.warning("guardian.db no disponible (%s); no se podrá confirmar el ISIN", exc)
        return {}
    try:
        return {a.asset_id: a.isin for a in cargar_activos(guardian)}
    except sqlite3.Error:
        log.warning("No se pudo leer ASSETS; no se podrá confirmar el ISIN", exc_info=True)
        return {}
    finally:
        guardian.close()


def _rechazar(
    conn: sqlite3.Connection, fila: sqlite3.Row, ruta: Path, motivo: str, puntuacion: int | None, dry_run: bool
) -> Estado:
    """Archiva (o borra) el PDF temporal y registra el rechazo con su motivo."""
    log.info("[%s] %s: %s", fila["feed"], fila["asset_id"], motivo)
    if dry_run:
        return Estado.RECHAZADO
    if config.CONSERVAR_RECHAZADOS:
        try:
            mover_atomico(ruta, config.DIR_RECHAZADOS / ruta.name)
            log.info("[%s] %s: PDF archivado en %s", fila["feed"], fila["asset_id"], config.DIR_RECHAZADOS)
        except OSError:
            log.warning("No se pudo archivar el PDF rechazado; se borra", exc_info=True)
            ruta.unlink(missing_ok=True)
    else:
        ruta.unlink(missing_ok=True)
    with conn:
        actualizar_item(conn, fila["guid_hash"], fila["asset_id"], estado=Estado.RECHAZADO,
                        filtro_score=puntuacion, filtro_motivo=motivo[:500], ruta_tmp=None)
    return Estado.RECHAZADO


def _procesar_descargado(
    conn: sqlite3.Connection, fila: sqlite3.Row, isin: str | None, dry_run: bool
) -> Estado:
    """Evalúa un PDF descargado y lo mueve a demo_docs o lo borra."""
    guid, asset = fila["guid_hash"], fila["asset_id"]
    ruta = Path(fila["ruta_tmp"] or "")
    if not ruta.is_file():
        log.error("[%s] %s: no existe el fichero temporal %s", fila["feed"], asset, ruta)
        if not dry_run:
            with conn:
                actualizar_item(conn, guid, asset, estado=Estado.ERROR,
                                ultimo_error=f"no existe el temporal {ruta}"[:500])
        return Estado.ERROR

    try:
        contexto = f"{fila['categoria'] or ''} {fila['resumen'] or ''}"
        res = evaluar_pdf(ruta, isin, fila["metodo_cruce"], contexto)
    except ErrorPDF as exc:
        return _rechazar(conn, fila, ruta, f"RECHAZADO: {exc}", None, dry_run)

    if not res.aprobado:
        return _rechazar(conn, fila, ruta, res.motivo, res.puntuacion, dry_run)

    nivel = logging.WARNING if res.requiere_revision else logging.INFO
    log.log(nivel, "[%s] %s: %s (eventos: %s)", fila["feed"], asset, res.motivo, ", ".join(res.eventos) or "-")
    if dry_run:
        return Estado.APROBADO

    destino = config.DIR_DEMO_DOCS / ruta.name
    if destino.exists():
        return _rechazar(conn, fila, ruta, f"RECHAZADO: ya existe {destino.name} en demo_docs", res.puntuacion, dry_run)
    try:
        mover_atomico(ruta, destino)
    except OSError:
        # Fichero bloqueado, disco lleno...: se queda DESCARGADO y se reintenta en el siguiente ciclo.
        log.exception("[%s] %s: no se pudo mover a demo_docs (se reintentará)", fila["feed"], asset)
        return Estado.DESCARGADO
    with conn:
        actualizar_item(conn, guid, asset, estado=Estado.APROBADO, ruta_final=str(destino), ruta_tmp=None,
                        filtro_score=res.puntuacion, filtro_motivo=res.motivo[:500])
    log.info("[%s] %s: PDF aprobado y movido a %s", fila["feed"], asset, destino)
    return Estado.APROBADO


def ejecutar_filtro(
    conn: sqlite3.Connection,
    isins: dict[str, str],
    dry_run: bool = False,
    limite: int = config.MAX_ITEMS_FILTRO,
) -> EstadisticasFiltro:
    """Procesa los elementos DESCARGADO. Un fallo no detiene al resto."""
    est = EstadisticasFiltro()
    filas = obtener_items(conn, Estado.DESCARGADO, limite)
    if not filas:
        log.info("No hay PDFs pendientes de filtrar")
        return est
    for fila in filas:
        est.procesados += 1
        try:
            estado = _procesar_descargado(conn, fila, isins.get(fila["asset_id"]), dry_run)
        except sqlite3.Error:
            est.errores += 1
            log.exception("Error de BD filtrando %s/%s", fila["guid_hash"][:8], fila["asset_id"])
            continue
        if estado is Estado.APROBADO:
            est.aprobados += 1
        elif estado is Estado.RECHAZADO:
            est.rechazados += 1
        elif estado is Estado.DESCARGADO:
            est.pendientes += 1
        else:
            est.errores += 1
    log.info("Ciclo del filtro%s → %s", " (dry-run)" if dry_run else "", est)
    return est


# =============================================================================
# CLI
# =============================================================================
def probar_archivo(
    ruta: Path, isin: str | None, metodo: str | None, contexto: str = "", texto_completo: bool = False
) -> int:
    """Evalúa un PDF suelto (sin BD) para calibrar reglas y comprobar la extracción de texto."""
    try:
        res = evaluar_pdf(ruta, isin, metodo, contexto)
    except ErrorPDF as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Decisión        : {'APROBADO' if res.aprobado else 'RECHAZADO'}"
          f"{'  (REVISAR)' if res.requiere_revision else ''}")
    print(f"Puntuación      : {res.puntuacion}")
    print(f"Páginas / chars : {res.paginas} / {res.caracteres}")
    print(f"Eventos         : {', '.join(res.eventos) or '-'}")
    print(f"Reglas          : {', '.join(res.reglas) or '-'}")
    print(f"ISIN confirmado : {'sí' if res.isin_confirmado else 'no'}")
    print(f"Motivo          : {res.motivo}")
    if texto_completo:
        print(f"Texto completo  : {extraer_texto(ruta)[0][:4000]!r}")
    else:
        print(f"Extracto        : {res.extracto!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI."""
    ap = argparse.ArgumentParser(description="Filtro heurístico de PDFs (producción)")
    ap.add_argument("--probar", metavar="RUTA", help="evalúa un PDF suelto y termina (no toca la BD)")
    ap.add_argument("--isin", help="con --probar: ISIN del activo a confirmar en el texto")
    ap.add_argument("--metodo", help="con --probar: método de cruce (exacto, alias, parcial, fuzzy...)")
    ap.add_argument("--once", action="store_true", help="ejecuta un solo ciclo y termina")
    ap.add_argument("--dry-run", action="store_true", help="evalúa y registra en log, sin mover/borrar/escribir")
    ap.add_argument("--intervalo", type=int, default=config.INTERVALO_POLLING_S)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--contexto", default="", help="con --probar: categoría/resumen del RSS a sumar")
    ap.add_argument("--texto-completo", action="store_true", help="con --probar: imprime el texto extraído")
    args = ap.parse_args(argv)

    config.configurar_logging(logging.DEBUG if args.verbose else logging.INFO)
    if args.probar:
        return probar_archivo(Path(args.probar), args.isin, args.metodo, args.contexto, args.texto_completo)

    if not args.dry_run:
        config.DIR_DEMO_DOCS.mkdir(parents=True, exist_ok=True)
    conn = abrir_monitor_db()
    try:
        while True:
            ejecutar_filtro(conn, cargar_isins(), dry_run=args.dry_run)
            if args.once or args.dry_run:
                break
            log.info("Próximo ciclo en %d s", args.intervalo)
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        log.info("Interrumpido por el usuario")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
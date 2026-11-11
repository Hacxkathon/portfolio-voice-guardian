"""
Todo lo que la app saca del catálogo (carpeta catalogo/): fichas de producto y de plataforma,
revisión automática de la cartera, "Novedades que te afectan" y el documento que vigilamos.

Regla de oro: si el catálogo no se puede cargar, DISPONIBLE = False, cada función devuelve
vacío y la app se ve exactamente igual que antes. Nada de aquí puede tumbar la demo.

Las consultas se cachean con st.cache_data: el catálogo solo cambia si alguien
regenera catalogo.db, así que no hace falta preguntar a SQLite en cada clic.
"""

from __future__ import annotations

import functools
import hashlib
import re
from datetime import date
from typing import Optional
from urllib.parse import urlparse

import streamlit as st

import voice
from ui import ICON, Ctx, esc, eur, num, plural, show, slug, voice_id_for, voice_override

state = st.session_state

# --------------------------------------------------------------------------
# Carga segura del catálogo
# --------------------------------------------------------------------------

try:
    import catalogo as _cat
    from catalogo.construir import DB as _DB, construir as _construir
    if not _DB.exists():          # primera vez (o alguien lo borró): se genera desde los CSV
        _construir()
    _cat.plataformas()            # prueba rápida: si la base está rota, salta aquí
    DISPONIBLE = True
except Exception:                 # noqa: BLE001  (cualquier fallo = la app sigue sin catálogo)
    _cat = None
    DISPONIBLE = False

TTL = 3600
TIPOS_CON_COMISION = ("Fondo de inversión", "ETF", "Plan de pensiones")


def _seguro(default):
    """Decorador: si la función falla, devuelve `default` en vez de lanzar el error."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            if not DISPONIBLE:
                return default() if callable(default) else default
            try:
                return fn(*a, **k)
            except Exception:     # noqa: BLE001
                return default() if callable(default) else default
        return wrapper
    return deco


# Consultas cacheadas (una por función del catálogo)
@_seguro(list)
@st.cache_data(ttl=TTL, show_spinner=False)
def buscar(texto: str, limite: int = 8) -> list[dict]:
    return _cat.buscar(texto, limite=limite)


@_seguro(None)
@st.cache_data(ttl=TTL, show_spinner=False)
def por_isin(isin: str) -> Optional[dict]:
    return _cat.por_isin(isin)


@_seguro(None)
@st.cache_data(ttl=TTL, show_spinner=False)
def plataforma(nombre: str) -> Optional[dict]:
    return _cat.plataforma(nombre)


@_seguro(list)
@st.cache_data(ttl=TTL, show_spinner=False)
def plataformas_activas() -> list[str]:
    return _cat.plataformas_activas_en_espana()


@_seguro(list)
@st.cache_data(ttl=TTL, show_spinner=False)
def todas_las_plataformas() -> list[str]:
    """Incluye las que ya no operan en España (Binance), para reconocerlas si alguien las escribe."""
    return [p["nombre"] for p in _cat.plataformas()]


def nombre_oficial_plataforma(texto: str) -> str:
    """'myinv' -> 'MyInvestor'. Si no hay una única coincidencia, deja el texto tal cual."""
    t = (texto or "").strip().casefold()
    if not t:
        return texto
    nombres = todas_las_plataformas()
    for cond in (lambda n: n.casefold() == t, lambda n: n.casefold().startswith(t), lambda n: t in n.casefold()):
        hits = [n for n in nombres if cond(n)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return texto
    return texto


@_seguro(None)
@st.cache_data(ttl=TTL, show_spinner=False)
def documento(tipo: str) -> Optional[dict]:
    return _cat.documento_para_tipo(tipo)


@_seguro(list)
@st.cache_data(ttl=TTL, show_spinner=False)
def _eventos_para(isins: tuple, plats: tuple) -> list[dict]:
    return _cat.eventos_para(isins=list(isins), plataformas=list(plats))


@_seguro(list)
@st.cache_data(ttl=TTL, show_spinner=False)
def _comprobar(posiciones: tuple) -> list[dict]:
    return _cat.comprobar_cartera([dict(p) for p in posiciones])


@_seguro(list)
@st.cache_data(ttl=TTL, show_spinner=False)
def todos_los_productos() -> list[dict]:
    """Los 93 productos, para el buscador del formulario (sin ficticios al final)."""
    return _cat._filas("SELECT * FROM instrumentos ORDER BY CASE fiabilidad WHEN 'ficticio' THEN 1 ELSE 0 END, "
                       "CASE tipo WHEN 'Acción' THEN 0 WHEN 'ETF' THEN 1 WHEN 'Fondo de inversión' THEN 2 ELSE 3 END, "
                       "nombre_corto")


def isin_valido(isin: str) -> bool:
    if not DISPONIBLE:
        return bool(re.match(r"^[A-Z]{2}[A-Z0-9]{9}\d$", (isin or "").upper()))
    try:
        return _cat.isin_valido(isin)
    except Exception:             # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Pequeños formatos
# --------------------------------------------------------------------------

def fecha_es(iso: Optional[str]) -> str:
    """'2026-07-28' -> '28/07/2026'."""
    try:
        y, m, d = (int(x) for x in str(iso)[:10].split("-"))
        return f"{d:02d}/{m:02d}/{y}"
    except (ValueError, TypeError):
        return ""


def _enlaces(fuente: Optional[str]) -> str:
    """Convierte 'url1 | url2' en enlaces cortos ('bolsasymercados.es ↗'). Si no es una URL, texto."""
    if not fuente:
        return ""
    partes = [p.strip() for p in fuente.split("|") if p.strip()]
    out = []
    for p in partes:
        if p.startswith("http"):
            dominio = urlparse(p).netloc.removeprefix("www.")
            out.append(f'<a href="{esc(p)}" target="_blank" rel="noopener">{esc(dominio)} ↗</a>')
        else:
            out.append(f"<span>{esc(p)}</span>")
    return " · ".join(out)


def _pie(fuente: Optional[str], verificado: Optional[str], fiabilidad: Optional[str]) -> str:
    fia = {"alta": "Fiabilidad alta", "media": "Fiabilidad media", "baja": "Fiabilidad baja: consulta la tarifa oficial",
           "ficticio": "Producto de demostración"}.get(fiabilidad or "", "")
    trozos = []
    if fuente:
        trozos.append(f"<span>Fuente: {_enlaces(fuente)}</span>")
    if verificado:
        trozos.append(f'<span class="ok">{ICON["check"]}Verificado el {fecha_es(verificado)}</span>')
    if fia:
        trozos.append(f'<span class="fia {esc(fiabilidad)}">{esc(fia)}</span>')
    return f'<div class="foot">{"".join(trozos)}</div>' if trozos else ""


def _limpia_titulo(t: str) -> str:
    return re.sub(r"^\[SIMULACI[ÓO]N\]\s*", "", t or "")


def _es_demo(isin: str) -> bool:
    p = por_isin(isin) if isin else None
    return bool(p and p.get("fiabilidad") == "ficticio")


def isin_ok(code: str) -> bool:
    """¿Podemos vigilar este código? ISIN con dígito de control correcto o producto de demo."""
    code = (code or "").strip().upper()
    return len(code) == 12 and (isin_valido(code) or _es_demo(code))


# --------------------------------------------------------------------------
# Producto del catálogo para una posición
# --------------------------------------------------------------------------

def producto(pos: dict) -> Optional[dict]:
    """Busca la posición en el catálogo: por ISIN; si es cripto o no tiene ISIN, por ticker o nombre.
    Si el ISIN está mal escrito no adivinamos: eso lo resuelve la revisión de cartera."""
    if not DISPONIBLE:
        return None
    code = (pos.get("isin") or "").strip().upper()
    if code:
        p = por_isin(code)
        if p:
            return p
        for r in buscar(code, limite=5):
            if (r.get("ticker") or "").upper() == code:
                return r
        return None
    nombre = (pos.get("nombre") or "").strip().casefold()
    for r in buscar(nombre, limite=5):
        if nombre in {(r.get("nombre") or "").casefold(), (r.get("nombre_corto") or "").casefold()}:
            return r
    return None


def comision_pct(prod: dict) -> Optional[float]:
    """Comisión anual del producto: TER si existe; si no, la de gestión."""
    for k in ("ter_pct", "comision_gestion_pct"):
        if prod.get(k) is not None:
            return float(prod[k])
    return None


def etiqueta(prod: dict) -> str:
    """Texto del buscador: 'Repsol · REP · ES0173516115 · Acción'."""
    partes = [prod.get("nombre_corto") or prod.get("nombre"), prod.get("ticker"), prod.get("isin"), prod.get("tipo")]
    return " · ".join(p for p in partes if p)


# --------------------------------------------------------------------------
# B) Ficha del producto
# --------------------------------------------------------------------------

def ficha_producto(pos: dict) -> str:
    prod = producto(pos)
    if not prod:
        code = (pos.get("isin") or "").strip()
        why = ("Su ISIN no aparece en el catálogo o está mal escrito: mira la revisión de arriba."
               if code else "No tiene ISIN ni ticker, así que no podemos identificarlo con seguridad.")
        return (f'<div class="pvg-fic"><div class="hd"><div class="ico">?</div><div><b>{esc(pos["nombre"])}</b>'
                f'<small>No está en nuestro catálogo</small></div></div>'
                f'<div class="note">{esc(why)} Lo seguimos vigilando con los datos que nos diste.</div></div>')
    sub = " · ".join(x for x in (prod.get("subtipo"), prod.get("sector"), prod.get("mercado"), prod.get("divisa")) if x)
    datos = [("Emisor", prod.get("emisor")), ("Ticker", prod.get("ticker")), ("ISIN", prod.get("isin")),
             ("País", prod.get("pais")), ("Índice", prod.get("indice")), ("Distribución", prod.get("distribucion"))]
    pct = comision_pct(prod)
    if pct is not None:
        datos.append(("Comisión anual", f"{num(pct)} %" + (" (TER)" if prod.get("ter_pct") is not None else " (gestión)")))
    if prod.get("inversion_minima_eur"):
        datos.append(("Inversión mínima", eur(prod["inversion_minima_eur"])))
    dl = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in datos if v)
    dinero = ""
    if pct is not None and pos.get("importe"):
        coste = pos["importe"] * pct / 100
        dinero = (f'<div class="money">Con <b>{eur(pos["importe"])}</b> pagas unos <em>{eur(coste, 2)}</em> al año '
                  f'en comisiones<small>{eur(pos["importe"])} × {num(pct)} % · ya descontado del valor del producto</small></div>')
    nota = prod.get("notas") or ""
    nota = "" if "seed.py" in nota else nota          # notas internas del equipo, no para el usuario
    demo = prod.get("fiabilidad") == "ficticio"
    palabras = [w for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", prod.get("nombre_corto") or "") if w[0].isupper()]
    ini = esc((prod.get("ticker") or "").split(" ")[0][:5] or "".join(w[0] for w in palabras[:2]) or "?")
    return f"""<div class="pvg-fic">
<div class="hd"><div class="ico">{ini}</div><div><b>{esc(prod.get('nombre'))}</b><small>{esc(sub)}</small></div></div>
{'<div class="alert amber">Producto inventado para la demo del equipo: sus datos no son reales.</div>' if demo else ''}
<dl>{dl}</dl>
{dinero}
{f'<div class="note">{esc(nota)}</div>' if nota else ''}
{_pie(prod.get('fuente_url'), prod.get('verificado'), prod.get('fiabilidad'))}
</div>"""


# --------------------------------------------------------------------------
# C) Ficha de la plataforma
# --------------------------------------------------------------------------

def ficha_plataforma(nombre: str) -> str:
    pl = plataforma(nombre)
    if not pl:
        return (f'<div class="pvg-fic"><div class="hd"><div class="ico">{ICON["briefcase"]}</div><div>'
                f'<b>{esc(nombre)}</b><small>No está en nuestro catálogo de plataformas</small></div></div></div>')
    sub = " · ".join(x for x in (pl.get("tipo"), pl.get("sede")) if x)
    datos = [("Regulador", pl.get("regulador")), ("Garantía", pl.get("garantia")),
             ("Acciones", pl.get("comision_acciones")), ("ETF", pl.get("comision_etf")),
             ("Fondos", pl.get("comision_fondos")), ("Custodia", pl.get("custodia")),
             ("Cambio de divisa", pl.get("cambio_divisa")), ("Remuneración del efectivo", pl.get("remuneracion_efectivo")),
             ("Otras condiciones", pl.get("otras_condiciones"))]
    dl = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in datos if v)
    aviso = ""
    if not pl.get("opera_en_espana"):
        aviso = f'<div class="alert red"><b>Ya no opera en España.</b> {esc(pl.get("aviso") or "")}</div>'
    elif pl.get("aviso"):
        aviso = f'<div class="alert amber">{esc(pl["aviso"])}</div>'
    return f"""<div class="pvg-fic">
<div class="hd"><div class="ico">{ICON["briefcase"]}</div><div><b>{esc(pl['nombre'])}</b><small>{esc(sub)}</small></div></div>
{aviso}
<dl>{dl}</dl>
{_pie(pl.get('fuente_url'), pl.get('verificado'), pl.get('fiabilidad'))}
</div>"""


def linea_plataforma(nombre: str) -> str:
    """Una línea para el formulario: 'Banco de España y CNMV · FGD hasta 100.000 €'."""
    pl = plataforma(nombre) if nombre else None
    if not pl:
        return ""
    if not pl.get("opera_en_espana"):
        return f'<div class="pvg-hint red">{esc(pl.get("aviso") or "Esta plataforma ya no opera en España.")}</div>'
    return (f'<div class="pvg-hint">{ICON["check"]}Regulada por {esc(pl.get("regulador") or "—")} · '
            f'{esc(pl.get("garantia") or "")}</div>')


# --------------------------------------------------------------------------
# A) Validación del ISIN en vivo
# --------------------------------------------------------------------------

def explicar_isin(code: str, tipo: Optional[str]) -> str:
    """HTML con un check verde o la explicación de por qué el código no vale."""
    code = (code or "").strip().upper()
    if not code:
        return ""
    if tipo == "Criptomoneda":
        if re.fullmatch(r"[A-Z0-9]{2,6}", code):
            return f'<div class="pvg-hint">{ICON["check"]}Ticker de cripto: las criptomonedas no tienen ISIN.</div>'
        return '<div class="pvg-hint amber">Para criptos usa el ticker: BTC, ETH, SOL…</div>'
    if len(code) != 12:
        return f'<div class="pvg-hint amber">Un ISIN tiene 12 caracteres y has escrito {len(code)}.</div>'
    if not code[:2].isalpha():
        return '<div class="pvg-hint amber">Un ISIN empieza por las 2 letras del país (ES, IE, US…).</div>'
    if not code.isalnum():
        return '<div class="pvg-hint amber">Un ISIN solo tiene letras y números, sin espacios ni guiones.</div>'
    prod = por_isin(code)
    if prod and prod.get("fiabilidad") == "ficticio":
        return f'<div class="pvg-hint">{ICON["check"]}Producto de demostración del equipo: {esc(prod["nombre"])}.</div>'
    if not isin_valido(code):
        return ('<div class="pvg-hint amber">El último dígito (el de control) no cuadra con los anteriores: '
                'seguramente hay una errata. Cópialo de la ficha del producto.</div>')
    if prod:
        return f'<div class="pvg-hint">{ICON["check"]}ISIN válido · {esc(prod["nombre"])}</div>'
    return f'<div class="pvg-hint">{ICON["check"]}ISIN válido. No está en el catálogo, pero lo vigilamos igual.</div>'


# --------------------------------------------------------------------------
# D) Revisión automática de la cartera
# --------------------------------------------------------------------------

def revision(posiciones: list[dict]) -> list[dict]:
    """Avisos de comprobar_cartera(), sin el fondo de demo del equipo (su ISIN es inválido a propósito)."""
    if not DISPONIBLE or not posiciones:
        return []
    claves = ("id", "plataforma", "tipo", "nombre", "isin")
    avisos = _comprobar(tuple(tuple((k, p.get(k)) for k in claves) for p in posiciones))
    demo = {p["id"] for p in posiciones if _es_demo((p.get("isin") or "").upper())}
    return [a for a in avisos if not (a["id"] in demo and a["tipo"] != "plataforma")]


def _corregir(ctx: Ctx, pid: str, sug: dict) -> None:
    for p in ctx.cuenta["particular"]["posiciones"]:
        if p["id"] == pid:
            p["isin"] = sug["isin"]
            if sug.get("tipo"):
                p["tipo"] = sug["tipo"]
            if p.get("comision") is None and comision_pct(sug) is not None:
                p["comision"] = comision_pct(sug)
    import store
    store.save(ctx.cuenta)
    st.toast(f"Corregido: {sug.get('nombre_corto') or sug['nombre']} · {sug['isin']}", icon="✅")


def revision_cartera(ctx: Ctx) -> None:
    """Banner arriba de Mi cartera. Sin avisos, una línea verde discreta."""
    if not DISPONIBLE:
        return
    pos = ctx.cuenta["particular"]["posiciones"]
    if not pos:
        return
    avisos = revision(pos)
    if not avisos:
        show(f'<div class="pvg-revok">{ICON["check"]}Revisión automática: tus '
             f'{plural(len(pos), "producto está bien identificado", "productos están bien identificados")} '
             f'y tus plataformas operan en España.</div>')
        return
    nombre = {p["id"]: p for p in pos}
    with st.container(key="review"):
        show(f'<div class="pvg-revhd"><b>Revisión automática de tu cartera</b>'
             f'<span>{plural(len(avisos), "cosa por revisar", "cosas por revisar")}: sin esto, '
             f'podrías no recibir avisos.</span></div>')
        for i, a in enumerate(avisos):
            p = nombre.get(a["id"], {})
            cls = "red" if a["tipo"] == "plataforma" else ""
            pista = next((s.get("notas") for s in a.get("sugerencias", []) if not s.get("isin") and s.get("notas")), "")
            doc = documento(p.get("tipo", "")) if a["tipo"] == "sin_isin" else None
            if pista and a["tipo"] == "sin_isin":
                a["mensaje"] += f" {pista}: cópialo de tu cuenta para que podamos vigilarlo."
            elif doc and doc.get("identificador"):
                a["mensaje"] += (f" Para un {p.get('tipo', '').lower()} necesitamos: {doc['identificador']}. "
                                 "Cópialo de tu cuenta para que podamos vigilarlo.")
            etiquetas = {"isin_invalido": "ISIN inválido", "isin_desconocido": "ISIN desconocido",
                         "sin_isin": "Sin ISIN", "plataforma": "Plataforma"}
            show(f'<div class="pvg-revrow {cls}"><span class="tag">{etiquetas.get(a["tipo"], "Aviso")}</span>'
                 f'<div><b>{esc(p.get("nombre", ""))} · {esc(p.get("plataforma", ""))}</b>'
                 f'<span>{esc(a["mensaje"])}</span></div></div>')
            sugs = [s for s in a.get("sugerencias", []) if s.get("isin") and s.get("fiabilidad") != "ficticio"]
            if sugs:
                cols = st.columns(len(sugs[:2]) + 1)
                for j, s in enumerate(sugs[:2]):
                    cols[j].button(f"Corregir con {s['isin']} ({s['nombre']})", key=f"fix_{a['id']}_{i}_{j}",
                                   icon=":material/auto_fix_high:", type="primary" if j == 0 else "secondary",
                                   on_click=_corregir, args=(ctx, a["id"], s), width="stretch")


# --------------------------------------------------------------------------
# E) Novedades que te afectan
# --------------------------------------------------------------------------

def novedades_para(ctx: Ctx) -> list[dict]:
    """Eventos del catálogo cruzados con mis ISIN y plataformas. A cada uno le pongo las posiciones
    afectadas, el importe y el impacto en € si es calculable. Si un ISIN ya tiene alerta del
    equipo, no lo repito (sería el mismo cambio contado dos veces)."""
    if not DISPONIBLE:
        return []
    pos = ctx.cuenta["particular"]["posiciones"]
    isins = tuple(sorted({(p.get("isin") or "").upper() for p in pos if p.get("isin")}))
    plats = tuple(sorted({p["plataforma"] for p in pos if p.get("plataforma")}))
    if not isins and not plats:
        return []
    del_equipo = {a.isin.upper() for a in ctx.snap.alerts}
    out = []
    for ev in _eventos_para(isins, plats):
        if ev.get("isin") and ev["isin"].upper() in del_equipo:
            continue
        if ev["ambito"] == "instrumento":
            mias = [p for p in pos if (p.get("isin") or "").upper() == (ev.get("isin") or "").upper()]
        else:
            mias = [p for p in pos if p["plataforma"].casefold() == (ev.get("plataforma") or "").casefold()]
        if not mias:
            continue
        importe = sum(p["importe"] for p in mias)
        impacto = None
        if ev.get("unidad") == "% anual" and ev.get("delta_pp") is not None and ev["ambito"] == "instrumento":
            impacto = importe * float(ev["delta_pp"]) / 100
        out.append({**ev, "mias": mias, "importe": importe, "impacto": impacto})
    return out


def _valor(v: Optional[str]) -> str:
    return esc(v or "—")


def _fecha_evento(ev: dict) -> str:
    f = ev.get("fecha_efectiva")
    if not f:
        return "Escenario de prueba, sin fecha"
    return ("En vigor desde el " if f <= date.today().isoformat() else "A partir del ") + fecha_es(f)


def _redondeo_hablado(x: float) -> str:
    """Cifra para leer en voz alta: 'unos 225 euros', 'menos de un euro'."""
    x = abs(x)
    if x < 1:
        return "menos de un euro"
    n = round(x) if x < 1000 else round(x, -1)
    return f"unos {int(n):,} euros".replace(",", ".")


def _miles(x: float) -> str:
    return f"{int(round(x)):,}".replace(",", ".")


def _porc(v: Optional[str]) -> str:
    """'0,19%' -> '0,19'  (la palabra 'por ciento' la pone voice.prepare_for_speech)."""
    return (v or "").replace("%", "").strip()


def texto_hablado(ev: dict, nombre: str, perfil: str) -> str:
    """Lo que dice la voz: sin ISIN, sin barras, cifras redondeadas y en español."""
    pro = perfil == "profesional"
    if ev["ambito"] == "instrumento":
        cat = producto(ev["mias"][0])
        prod = (cat or {}).get("nombre_corto") or ev["mias"][0]["nombre"]
    else:
        prod = ev.get("plataforma") or ""
    prod = re.sub(r"\s*\([^)]*\)", "", prod).rstrip(". ")
    plats = " y ".join(sorted({p["plataforma"] for p in ev["mias"]}))
    hola = (f"{nombre}. " if pro else f"Hola {nombre}. ") if nombre else ("" if pro else "Hola. ")
    sim = ("Ojo: esto es una simulación para probar la aplicación, no una noticia real. " if ev.get("es_simulado") else "")
    donde = (f"Tienes {_miles(ev['importe'])} euros en {plats}. " if ev["ambito"] == "instrumento"
             else f"Tienes {_miles(ev['importe'])} euros en {plats}"
                  + (f", repartidos en {len(ev['mias'])} productos. " if len(ev["mias"]) > 1 else ". "))
    ant, nue = _porc(ev.get("valor_anterior")), _porc(ev.get("valor_nuevo"))
    if ev.get("impacto") is not None:
        sube = ev["impacto"] > 0
        if pro:
            cuerpo = (f"{prod}: la comisión anual pasa del {ant} % al {nue} %, "
                      f"{num(abs(ev['delta_pp']))} puntos {'más' if sube else 'menos'}. {donde}"
                      f"Impacto: {_redondeo_hablado(ev['impacto'])} {'más' if sube else 'menos'} al año.")
        else:
            cuerpo = (f"Una novedad sobre {prod}. {donde}La comisión que te cobran cada año "
                      f"{'sube' if sube else 'baja'}: pasa del {ant} al {nue} %. Para ti son "
                      f"{_redondeo_hablado(ev['impacto'])} {'más' if sube else 'menos'} al año. "
                      + ("No tienes que hacer nada hoy, pero merece la pena compararlo con productos parecidos."
                         if sube else "Es una buena noticia y no tienes que hacer nada."))
    elif ev.get("event_type") == "dividends":
        recorte = abs(float(ev.get("delta_pp") or 0))
        cuerpo = ((f"Una novedad sobre {prod}. " if not pro else f"{prod}. ") + donde
                  + f"El dividendo pasa del {ant} al {nue} % de lo que se pagaba, un recorte del {_porc(num(recorte, 0))} %. "
                  + ("No podemos decirte cuántos euros son porque depende del dividendo que pague la empresa."
                     if not pro else "El importe en euros depende del dividendo por acción, que no tenemos."))
    else:
        titulo = _limpia_titulo(ev.get("titulo", ""))
        cuerpo = ((f"Una novedad de {prod}. " if not pro else "") + donde + f"{titulo}. {ev.get('resumen') or ''}")
    txt = hola + sim + cuerpo
    txt = re.sub(r"\b([A-Za-zÁÉÍÓÚáéíóúñ]+)/([A-Za-zÁÉÍÓÚáéíóúñ]+)\b", r"\1 o \2", txt)   # "o/a" -> "o a"
    txt = re.sub(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", "", txt)                                   # ningún ISIN
    txt = txt.replace("(", "").replace(")", "").replace(";", ",")
    return re.sub(r"\s+", " ", txt).strip()


def _escuchar(ctx: Ctx, ev: dict, perfil: str, nombre: str) -> None:
    """Botón 'Escúchalo' + reproductor. Reutiliza la caché de audio de voice.py."""
    texto = texto_hablado(ev, nombre, perfil)
    vid, ov = voice_id_for(ctx, perfil), voice_override(perfil)
    key = f"nov_audio_{ev['id']}_{hashlib.md5((vid + voice.settings_tag(ov) + texto).encode()).hexdigest()[:10]}"
    if key not in state:
        cached = voice.cached_audio(texto, vid, ctx.model_id, perfil, ov)
        if cached:
            state[key] = cached
    if state.get(key):
        st.audio(state[key], format="audio/mpeg", autoplay=state.pop("nov_autoplay", None) == key)
        return
    if st.button("Escúchalo", key=f"btn_{key}", icon=":material/graphic_eq:", disabled=not ctx.api_key,
                 help=None if ctx.api_key else "Falta la clave de ElevenLabs en .streamlit/secrets.toml"):
        with st.spinner("Grabando con ElevenLabs…"):
            try:
                state[key] = voice.synthesize(texto, vid, perfil, ctx.api_key, ctx.model_id, ov).audio
                state["nov_autoplay"] = key
                st.rerun()
            except voice.VoiceError as exc:
                st.error(str(exc), icon="🔇")


def novedad_html(ev: dict) -> str:
    sim = bool(ev.get("es_simulado"))
    tag = ('<span class="tag sim">Simulación</span>' if sim else '<span class="tag real">Hecho real</span>')
    kind = {"fees": "Comisión", "dividends": "Dividendo", "condiciones": "Condiciones",
            "regulacion": "Regulación"}.get(ev.get("event_type"), "Cambio")
    if ev["ambito"] == "instrumento":
        quien = f'{ev["mias"][0]["nombre"]} · {", ".join(sorted({p["plataforma"] for p in ev["mias"]}))}'
    else:
        quien = f'{ev.get("plataforma") or ""} · {", ".join(p["nombre"] for p in ev["mias"][:3])}'
        quien += "…" if len(ev["mias"]) > 3 else ""
    if ev.get("impacto") is not None:
        cls = "up" if ev["impacto"] > 0 else "down"
        impacto = (f'<div class="imp {cls}"><b>{"+" if ev["impacto"] > 0 else "−"}{eur(abs(ev["impacto"]), 2)}</b>'
                   f'<span>{"más" if ev["impacto"] > 0 else "menos"} al año</span>'
                   f'<small>{eur(ev["importe"])} × {num(abs(ev["delta_pp"]))} puntos</small></div>')
    elif ev.get("event_type") == "dividends" and ev.get("delta_pp") is not None:
        impacto = (f'<div class="imp amber"><b>{num(ev["delta_pp"], 0)} %</b><span>de dividendo</span>'
                   f'<small>Importe en € no disponible: depende del dividendo</small></div>')
    else:
        impacto = (f'<div class="imp neutral"><b>{eur(ev["importe"])}</b><span>en {esc(ev.get("plataforma") or "")}</span>'
                   f'<small>{plural(len(ev["mias"]), "producto afectado", "productos afectados")}</small></div>')
    unidad = f' <small>{esc(ev["unidad"])}</small>' if ev.get("unidad") else ""
    fuente = ("" if sim else f'<div class="src">Fuente: {_enlaces(ev.get("fuente_url"))}</div>')
    simnote = ""
    if sim and "noticia real" not in (ev.get("resumen") or "").lower():
        simnote = '<div class="simnote">Escenario inventado para probar la app. No es una noticia real.</div>'
    elif sim:
        simnote = '<div class="simnote">Escenario de prueba: no lo trates como información real.</div>'
    return f"""<div class="pvg-nov {'sim' if sim else 'real'}">
<div class="top">{tag}<span class="kind">{kind}</span><span class="who">{esc(quien)}</span><span class="when">{esc(_fecha_evento(ev))}</span></div>
<div class="body"><div>
<div class="tt">{esc(_limpia_titulo(ev.get('titulo', '')))}</div>
<div class="chg"><span class="old">{_valor(ev.get('valor_anterior'))}</span><span class="arr">→</span><span class="new">{_valor(ev.get('valor_nuevo'))}</span>{unidad}</div>
<div class="sum">{esc(ev.get('resumen') or '')}</div>
{simnote}{fuente}
</div>{impacto}</div>
</div>"""


def novedades(ctx: Ctx) -> None:
    """Sección 'Novedades que te afectan' (Inicio). Distinta de las alertas del equipo."""
    evs = novedades_para(ctx)
    if not evs:
        return
    me = ctx.cuenta["particular"]
    reales = sum(1 for e in evs if not e.get("es_simulado"))
    show(f'<div class="pvg-h2" id="novedades" style="margin-top:30px">Novedades que te afectan</div>'
         f'<div class="pvg-h2sub">Cambios del catálogo cruzados con tus productos y plataformas: '
         f'{plural(reales, "hecho real con fuente", "hechos reales con fuente")} y '
         f'{plural(len(evs) - reales, "simulación", "simulaciones")} de prueba.</div>')
    with st.container(key="novs"):
        for ev in evs:
            with st.container(key=f"nov_{slug(ev['id'])}"):
                show(novedad_html(ev))
                _escuchar(ctx, ev, me.get("perfil", "principiante"), me.get("nombre", ""))


# --------------------------------------------------------------------------
# F) Qué documento vigilamos
# --------------------------------------------------------------------------

def documento_html(tipo: str) -> Optional[tuple[str, str]]:
    """(qué leemos, qué cambios vigilamos) en HTML, o None si no hay catálogo."""
    d = documento(tipo)
    if not d:
        return None
    url = (d.get("url_fuente_oficial") or "").split("|")[0].strip()
    donde = esc(d.get("donde_se_publica") or "")
    if url:
        donde = f'<a href="{esc(url)}" target="_blank" rel="noopener">{donde} ↗</a>'
    return (f'{esc(d.get("documento_vigilado") or "")}<small>Se publica en {donde}</small>',
            f'<small style="color:var(--ink-2)">{esc(d.get("que_cambios_vigilar") or "")}</small>')


# --------------------------------------------------------------------------
# G) Resumen para Inicio
# --------------------------------------------------------------------------

def resumen(ctx: Ctx, go) -> None:
    if not DISPONIBLE:
        return
    pos = ctx.cuenta["particular"]["posiciones"]
    if not pos:
        return
    verificados = sum(1 for p in pos if (pr := producto(p)) and pr.get("fiabilidad") != "ficticio")
    avisos = len(revision(pos))
    novs = len(novedades_para(ctx))
    a, b = st.columns([5, 1.1], vertical_alignment="center")
    with a:
        show(f"""<div class="pvg-sum"><span class="t">{ICON["check"]}Tu cartera</span>
<span><b>{verificados}</b>de {plural(len(pos), 'producto verificado', 'productos verificados')} en el catálogo</span>
<span class="{'warn' if avisos else ''}"><b>{avisos}</b>{'aviso' if avisos == 1 else 'avisos'} de revisión</span>
<span class="{'new' if novs else ''}"><b>{novs}</b>{'novedad' if novs == 1 else 'novedades'}</span></div>""")
    with b:
        st.button("Revisar cartera" if avisos else "Ver cartera", key="sum_go", icon=":material/arrow_forward:",
                  on_click=go, args=("cartera",), width="stretch", type="primary" if avisos else "secondary")

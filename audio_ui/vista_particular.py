"""
Pantallas del modo PARTICULAR (inversor que gestiona su propio dinero).

  inicio      -> "Mis avisos": qué ha cambiado en mis productos, cuánto me cuesta y una nota de voz
  cartera     -> dónde tengo el dinero (plataforma/exchange) y en qué productos; añadir y borrar
  documentos  -> qué vigilamos de cada producto y en qué estado está
  ajustes     -> mi nombre, el tono de los avisos y borrar mis datos

Los datos del particular se guardan en user_data/cuenta.json (store.py).
Las alertas salen del mismo pipeline que el modo gestor: si el ISIN de un
producto coincide con el de una alerta, ese cambio te afecta.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

import streamlit as st

import avatar
import store
from data_source import Alert
from vista_gestor import format_picker
from ui import (DIVIDEND_EVENTS, FEE_EVENTS, Ctx, bars_html, cost_html, del_, document_html, esc, eur,
                fund_short, headline, impact_eur, num, page_header, plural, show, slug, voice_phone, when)

state = st.session_state
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
TONOS = {"principiante": "Cercano, sin tecnicismos", "profesional": "Técnico y directo"}


def go(view: str) -> None:
    st.query_params["vista"] = view


def _me(ctx: Ctx) -> dict:
    return ctx.cuenta["particular"]


def _save(ctx: Ctx) -> None:
    store.save(ctx.cuenta)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def matches_for(ctx: Ctx) -> list[tuple[Alert, list[dict]]]:
    """Alertas que afectan a mis productos: mismo ISIN (o mismo nombre si no puse ISIN)."""
    out = []
    for a in ctx.snap.alerts:
        mine = [p for p in _me(ctx)["posiciones"]
                if (p.get("isin") and p["isin"].upper() == a.isin.upper())
                or (not p.get("isin") and _norm(p["nombre"]) == _norm(a.fund_name))]
        if mine:
            out.append((a, mine))
    return out


def hit_ids(ctx: Ctx) -> set[str]:
    return {p["id"] for _, ps in matches_for(ctx) for p in ps}


# --------------------------------------------------------------------------
# El mensaje de voz para el particular (plantilla determinista: las cifras
# salen de Python, no de la IA)
# --------------------------------------------------------------------------

def message_for(nombre: str, perfil: str, alert: Alert, mine: list[dict]) -> str:
    amount = sum(p["importe"] for p in mine)
    cost = sum(impact_eur(alert, p["importe"]) for p in mine)
    plats = " y ".join(sorted({p["plataforma"] for p in mine}))
    fund = fund_short(alert.fund_name)
    old, new = num(alert.old_value), num(alert.new_value)
    is_fund = any(p["tipo"] == "Fondo de inversión" for p in mine)
    if alert.event_type in FEE_EVENTS:
        if perfil == "profesional":
            txt = (f"{nombre}, aviso sobre {alert.fund_name}. La comisión de gestión pasa del {old} % al {new} %, "
                   f"{num(abs(alert.delta_pp))} puntos más. Sobre tu posición de {eur(amount)} en {plats}, "
                   f"el sobrecoste es de {eur(cost, 2)} al año. Conviene comparar con alternativas de menor coste")
            return txt + (", y recuerda que el traspaso entre fondos no tributa." if is_fund else ".")
        txt = (f"Hola {nombre}. Te cuento un cambio en el {fund}, donde tienes {eur(amount)} en {plats}. "
               f"La gestora va a cobrar más cada año por gestionarlo: pasa del {old} al {new} %. "
               f"Para ti son unos {eur(round(cost))} más al año. Tu dinero no corre peligro y no tienes que hacer "
               f"nada hoy, pero merece la pena mirar fondos parecidos que cobren menos")
        return txt + (". Y tranquilo: cambiar de un fondo a otro no paga impuestos." if is_fund else ".")
    if alert.event_type in DIVIDEND_EVENTS:
        return (f"Hola {nombre}. {fund} recorta su dividendo: del {old} al {new} %. Con tus {eur(amount)} "
                f"en {plats}, cobrarás unos {eur(round(abs(cost)))} menos al año.")
    return (f"Hola {nombre}. Hay un cambio en {alert.fund_name}: {alert.title.lower()}, del {old} al {new} %. "
            f"Con tus {eur(amount)} en {plats}, el impacto estimado es de {eur(round(abs(cost)))} al año.")


# --------------------------------------------------------------------------
# Mis avisos
# --------------------------------------------------------------------------

def _onboarding(ctx: Ctx) -> None:
    page_header("Bienvenido a tu vigilante financiero",
                "Apunta dónde tienes tu dinero y en qué productos. Leemos la letra pequeña por ti y, si algo "
                "cambia, te avisamos con una nota de voz y en euros.")
    a, _ = st.columns([1.1, 1])
    with a:
        with st.form("f_onboarding"):
            show('<div class="pvg-label">Antes de empezar</div>')
            nombre = st.text_input("¿Cómo te llamas?", placeholder="Tu nombre")
            perfil = st.radio("¿Cómo prefieres que te contemos los cambios?", list(TONOS),
                              format_func=TONOS.get, horizontal=True)
            if st.form_submit_button("Empezar", type="primary"):
                if not nombre.strip():
                    st.error("Escribe tu nombre para personalizar los avisos.")
                else:
                    _me(ctx).update(nombre=nombre.strip(), perfil=perfil)
                    _save(ctx)
                    st.rerun()


def _load_example(ctx: Ctx) -> None:
    _me(ctx)["posiciones"] = [store.new_position(**p) for p in store.EJEMPLO]
    _save(ctx)


def inicio(ctx: Ctx) -> None:
    me = _me(ctx)
    if not me["nombre"]:
        _onboarding(ctx)
        return
    pos = me["posiciones"]
    if not pos:
        page_header("Todavía no vigilamos nada", "Añade tu primera inversión: el fondo, ETF, acción o cripto y la "
                    "plataforma donde la tienes. Tarda menos de un minuto.", hello=f"Hola, {me['nombre']}")
        a, b, _ = st.columns([1, 1, 2])
        a.button("Añadir mi primera inversión", type="primary", icon=":material/add:", width="stretch",
                 on_click=go, args=("cartera",))
        b.button("Probar con una cartera de ejemplo", icon=":material/science:", width="stretch",
                 on_click=_load_example, args=(ctx,))
        return

    found = matches_for(ctx)
    total = sum(p["importe"] for p in pos)
    plats = {p["plataforma"] for p in pos}
    if not found:
        page_header("Todo en orden", f"Vigilamos tus {plural(len(pos), 'producto', 'productos')} en "
                    f"{plural(len(plats), 'plataforma', 'plataformas')} ({eur(total)}). Ningún cambio te afecta "
                    f"por ahora. Última revisión: {when(ctx.loaded_at)}.", hello=f"Hola, {me['nombre']}")
        st.button("Ver qué vigilamos", icon=":material/description:", on_click=go, args=("documentos",))
        return

    ids = [a.alert_id for a, _ in found]
    if state.get("p_sel_alert") not in ids:
        state["p_sel_alert"] = ids[0]
    if len(found) > 1:
        st.segmented_control("Tus avisos", ids, key="p_sel_alert",
                             format_func=lambda i: fund_short(ctx.snap.alert(i).fund_name))
    alert, mine = next((a, m) for a, m in found if a.alert_id == state["p_sel_alert"])
    amount = sum(p["importe"] for p in mine)
    cost = sum(impact_eur(alert, p["importe"]) for p in mine)
    where = " y ".join(sorted({p["plataforma"] for p in mine}))
    hello = (f"Hola, {me['nombre']} · {plural(len(found), 'cambio te afecta', 'cambios te afectan')}")
    rows = [(p["plataforma"], f"{eur(p['importe'])} invertidos", impact_eur(alert, p["importe"])) for p in mine]
    per = ("más al año en comisiones" if alert.event_type in FEE_EVENTS and cost >= 0 else
           "menos de dividendo al año" if alert.event_type in DIVIDEND_EVENTS else "de impacto al año")
    show(f"""
<div class="pvg-hello">{esc(hello)}</div>
<section class="pvg-hero">
  <div>
    <div class="pvg-h1">{esc(headline(alert))}</div>
    <div class="pvg-lede">Tienes <b>{eur(amount)}</b> en este producto en <b>{esc(where)}</b>.
      Esto es lo que dice el documento y lo que significa para tu bolsillo.</div>
    {document_html(alert)}
  </div>
  {cost_html("¿Cuánto te cuesta?", cost, per, rows, alert, base_word="dinero invertido")}
</section>""")

    show('<div class="pvg-h2">Tu aviso, en tu idioma</div>'
         f'<div class="pvg-h2sub">Tono: {TONOS.get(me["perfil"], "")}. Puedes cambiarlo en Ajustes.</div>')
    with st.container(key="detail"):
        info, phone = st.columns([1.55, 1], gap="large")
        with info:
            is_fund = any(p["tipo"] == "Fondo de inversión" for p in mine)
            tips = [
                ("1", "No tienes que hacer nada hoy. El cambio se aplica más adelante y tu dinero no corre peligro."),
                ("2", f"Compara con productos parecidos: con {eur(amount)}, cada décima de comisión son "
                      f"{eur(amount * 0.001, 2)} al año."),
            ]
            if is_fund:
                tips.append(("3", "En España, pasar de un fondo de inversión a otro (traspaso) no paga impuestos."))
            tips.append((str(len(tips) + 1), "Si tienes asesor, compártele este aviso: lleva la página y la cita exacta."))
            facts = (f'<div><b>{eur(amount)}</b>invertidos</div>'
                     f'<div><b>{amount / sum(p["importe"] for p in pos) * 100:.0f} %</b>de tu cartera</div>'
                     f'<div class="red"><b>{eur(abs(cost), 2)}</b>{per}</div>')
            show(f"""
<div class="pvg-facts" style="margin-top:0">{facts}</div>
<div class="pvg-label">Qué puedes hacer</div>
<div class="pvg-tips">{''.join(f'<div><i>{n}</i><span>{esc(t)}</span></div>' for n, t in tips)}</div>
<div class="pvg-label">Dónde lo tienes</div>
<div class="pvg-holdings">{''.join(f'<div class="hit"><span>{esc(p["nombre"])} · {esc(p["plataforma"])}</span><span>{eur(p["importe"])}</span></div>' for p in mine)}</div>
<div class="pvg-listnote" style="border:0;padding-left:0">Información, no recomendación de inversión.</div>""")
        with phone:
            msg = message_for(me["nombre"], me["perfil"], alert, mine)
            scope = f"p_{alert.alert_id}"
            if format_picker(scope) == "Avatar IA":
                audio = avatar.avatar_block(ctx, avatar.AvatarCtx(
                    scope=scope, mode="particular", persona="Guardian", persona_role="tu vigilante",
                    nombre=me["nombre"], perfil=me["perfil"], alert=alert, amount=amount, cost=cost, where=where,
                    is_fund=any(p["tipo"] == "Fondo de inversión" for p in mine),
                    portfolio=", ".join(f'{p["nombre"]} ({p["plataforma"]}) {eur(p["importe"])}' for p in pos),
                    message=msg))
            else:
                audio = voice_phone(ctx, msg, me["perfil"], scope, "Portfolio Voice Guardian",
                                    "Portfolio Voice Guardian", f"<span>{when(alert.detected_at).split(', ')[-1]}</span>")
            with st.container(key="actions_p"):
                if audio:
                    st.download_button("Descargar MP3", audio, file_name=f"aviso_{slug(alert.fund_name)}.mp3",
                                       mime="audio/mpeg", width="stretch", on_click="ignore", key="p_dl")
                show(f'<div class="pvg-voice">{"Voz cercana, ritmo pausado" if me["perfil"] == "principiante" else "Voz sobria, tono profesional"}</div>')


# --------------------------------------------------------------------------
# Mi cartera
# --------------------------------------------------------------------------

def _delete(ctx: Ctx, pid: str) -> None:
    me = _me(ctx)
    me["posiciones"] = [p for p in me["posiciones"] if p["id"] != pid]
    _save(ctx)


def cartera(ctx: Ctx) -> None:
    me = _me(ctx)
    pos = me["posiciones"]
    total = sum(p["importe"] for p in pos)
    hit = hit_ids(ctx)
    page_header("Mi cartera", "Dónde tienes tu dinero y en qué. Con el ISIN podemos leer los documentos oficiales "
                "de cada producto; sin él, la vigilancia es más limitada.")
    show(f"""<div class="pvg-kpis">
  <div class="pvg-kpi"><span>Total invertido</span><b>{eur(total)}</b></div>
  <div class="pvg-kpi"><span>Productos</span><b>{len(pos)}</b></div>
  <div class="pvg-kpi"><span>Plataformas</span><b>{len({p['plataforma'] for p in pos})}</b></div>
  <div class="pvg-kpi {'red' if hit else ''}"><span>Con cambios</span><b>{len(hit)}</b></div>
</div>""")

    left, right = st.columns([1, 1.25], gap="large")
    with left:
        _add_form(ctx, expanded=not pos)
        if pos:
            by_plat, by_type = defaultdict(float), defaultdict(float)
            for p in pos:
                by_plat[p["plataforma"]] += p["importe"]
                by_type[p["tipo"]] += p["importe"]
            with st.container(key="card_split"):
                show(f'<div class="pvg-label">Dónde está tu dinero</div>'
                     f'{bars_html(sorted(by_plat.items(), key=lambda kv: -kv[1]), total)}'
                     f'<div class="pvg-label" style="margin-top:24px">En qué tipo de producto</div>'
                     f'{bars_html(sorted(by_type.items(), key=lambda kv: -kv[1]), total)}')
    with right:
        with st.container(key="card_postable"):
            show('<div class="pvg-label">Tus inversiones</div>')
            if not pos:
                show('<div class="pvg-brief" style="margin:0">Aún no has añadido nada. Usa el formulario o '
                     'carga una cartera de ejemplo para ver cómo funciona.</div>')
                st.button("Cargar cartera de ejemplo", icon=":material/science:", on_click=_load_example, args=(ctx,))
            with st.container(key="postable"):
                for p in sorted(pos, key=lambda p: -p["importe"]):
                    a, b, c = st.columns([3.2, 1.3, 0.35], vertical_alignment="center")
                    if p["id"] in hit:
                        chip = '<span class="pvg-chip red">Cambio detectado</span>'
                    elif p.get("isin") and ISIN_RE.match(p["isin"].upper()):
                        chip = '<span class="pvg-chip ok">Vigilando</span>'
                    else:
                        chip = '<span class="pvg-chip grey">Vigilancia limitada</span>'
                    fee = f" · comisión {num(p['comision'])} %" if p.get("comision") is not None else ""
                    a.markdown(f'<div class="pvg-postrow"><span><b>{esc(p["nombre"])}</b>'
                               f'<small>{esc(p["plataforma"])} · {esc(p["tipo"])}'
                               f'{" · " + esc(p["isin"]) if p.get("isin") else ""}{fee}</small></span></div>',
                               unsafe_allow_html=True)
                    b.markdown(f'<div style="text-align:right;font-weight:600">{eur(p["importe"])}'
                               f'<div>{chip}</div></div>', unsafe_allow_html=True)
                    c.button("Borrar", key=f"del_{p['id']}", icon=":material/delete:", type="tertiary",
                             help=f"Borrar {p['nombre']}", on_click=_delete, args=(ctx, p["id"]))


def _add_form(ctx: Ctx, expanded: bool) -> None:
    with st.expander("Añadir una inversión", expanded=expanded, icon=":material/add_circle:"):
        with st.form("f_add", clear_on_submit=True, border=False):
            c1, c2 = st.columns(2)
            plataforma = c1.selectbox("Plataforma o exchange", store.PLATAFORMAS, index=None,
                                      placeholder="MyInvestor, Binance…", accept_new_options=True,
                                      help="Si no está en la lista, escríbela.")
            tipo = c2.selectbox("Tipo de producto", store.TIPOS, index=None, placeholder="Fondo, ETF, cripto…")
            nombre = st.text_input("Nombre del producto", placeholder="p. ej. Fondo Global Equilibrado, clase A")
            c3, c4 = st.columns(2)
            isin = c3.text_input("ISIN o ticker", placeholder="ES0000000001",
                                 help="El ISIN son 12 caracteres (2 letras + 10). Lo ves en la ficha del producto "
                                      "en tu app. Para criptos, el ticker (BTC, ETH…).")
            importe = c4.number_input("Importe invertido (€)", min_value=0.0, step=100.0, value=None,
                                      placeholder="0")
            c5, c6 = st.columns(2)
            comision = c5.number_input("Comisión anual (%)", min_value=0.0, max_value=10.0, step=0.05, value=None,
                                       placeholder="Opcional", help="La que pone en el folleto o en tu app (TER).")
            fecha = c6.date_input("Fecha de compra", value=None, max_value=date.today(), format="DD/MM/YYYY")
            if st.form_submit_button("Añadir a mi cartera", type="primary", icon=":material/add:"):
                errors = []
                if not plataforma:
                    errors.append("la plataforma")
                if not tipo:
                    errors.append("el tipo de producto")
                if not nombre.strip():
                    errors.append("el nombre")
                if not importe:
                    errors.append("el importe")
                if errors:
                    st.error("Falta " + ", ".join(errors) + ".")
                    return
                code = isin.strip().upper()
                if len(code) == 12 and not ISIN_RE.match(code):
                    st.warning("Ese ISIN no parece válido. Lo guardamos igualmente: revísalo si no te llegan avisos.")
                _me(ctx)["posiciones"].append(store.new_position(
                    plataforma=plataforma, tipo=tipo, nombre=nombre.strip(), isin=code, importe=float(importe),
                    comision=comision, fecha=fecha.isoformat() if fecha else None))
                _save(ctx)
                st.toast(f"Añadido: {nombre.strip()}", icon="✅")
                st.rerun()


# --------------------------------------------------------------------------
# Qué vigilamos
# --------------------------------------------------------------------------

def documentos(ctx: Ctx) -> None:
    pos = _me(ctx)["posiciones"]
    hit = hit_ids(ctx)
    page_header("Qué vigilamos por ti", "Para cada producto leemos su documentación oficial. Si cambia una "
                "comisión, un dividendo o la política de inversión, te avisamos con la cita exacta.")
    if not pos:
        show('<div class="pvg-empty"><b>Nada que vigilar todavía</b>Añade tus inversiones en Mi cartera.</div>')
        st.button("Ir a Mi cartera", type="primary", on_click=go, args=("cartera",))
        return
    rows = []
    for p in sorted(pos, key=lambda p: (p["id"] not in hit, -p["importe"])):
        code = (p.get("isin") or "").upper()
        if p["id"] in hit:
            chip = '<span class="pvg-chip red">Cambio detectado</span>'
        elif p["tipo"] in ("Criptomoneda", "Depósito o cuenta remunerada"):
            chip = '<span class="pvg-chip grey">Próximamente</span>'
        elif ISIN_RE.match(code):
            chip = '<span class="pvg-chip ok">Vigilando</span>'
        else:
            chip = '<span class="pvg-chip wait">Falta el ISIN</span>'
        rows.append(f'<tr><td>{esc(p["nombre"])}<small>{esc(p["plataforma"])}{" · " + esc(code) if code else ""}</small></td>'
                    f'<td>{esc(store.DOCUMENTO_POR_TIPO.get(p["tipo"], "Documentación del producto"))}</td>'
                    f'<td>{chip}</td></tr>')
    with st.container(key="card_watch"):
        show(f'<table class="pvg-table"><tr><th>Producto</th><th>Qué leemos</th><th>Estado</th></tr>{"".join(rows)}</table>')
    found = matches_for(ctx)
    if found:
        show('<div class="pvg-h2" style="margin-top:26px">Cambios detectados</div>')
        for a, _ in found:
            show(document_html(a, small=True))
            st.button("Ver lo que significa para mí", key=f"pgo_{slug(a.alert_id)}", icon=":material/arrow_forward:",
                      on_click=lambda i=a.alert_id: (state.__setitem__("p_sel_alert", i), go("inicio")))


# --------------------------------------------------------------------------
# Ajustes
# --------------------------------------------------------------------------

def ajustes(ctx: Ctx) -> None:
    me = _me(ctx)
    page_header("Ajustes", "Tu cuenta y cómo quieres que te avisemos.")
    a, b = st.columns(2, gap="large")
    with a:
        with st.form("f_part"):
            show('<div class="pvg-label">Tu cuenta</div>')
            nombre = st.text_input("Tu nombre", value=me["nombre"])
            perfil = st.radio("Tono de los avisos", list(TONOS), format_func=TONOS.get,
                              index=list(TONOS).index(me.get("perfil", "principiante")), horizontal=True)
            if st.form_submit_button("Guardar", type="primary"):
                me.update(nombre=nombre.strip(), perfil=perfil)
                _save(ctx)
                st.toast("Guardado", icon="✅")
    with b:
        with st.container(key="card_danger"):
            show('<div class="pvg-label">Tus datos</div><div class="pvg-brief" style="margin-bottom:12px">'
                 'Tu cartera se guarda solo en este equipo (user_data/cuenta.json).</div>')
            st.button("Cargar cartera de ejemplo", icon=":material/science:", on_click=_load_example, args=(ctx,))
            sure = st.checkbox("Quiero borrar mi nombre y todas mis inversiones")
            if st.button("Borrar mis datos", icon=":material/delete_forever:", disabled=not sure):
                ctx.cuenta["particular"] = {"nombre": "", "perfil": "principiante", "posiciones": []}
                _save(ctx)
                st.rerun()

"""
Pantallas del modo GESTOR (asesor financiero con cartera de clientes).

  avisos      -> qué ha pasado, a quién afecta, cuánto cuesta y la nota de voz para cada cliente
  clientes    -> todos los clientes con su cartera y sus avisos
  documentos  -> los fondos que vigilamos y el historial de cambios detectados
  ajustes     -> nombre del asesor y fuente de datos
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

import avatar
import store
import voice
from data_source import Alert, Client, Impact
from ui import (ICON, VOICE_LABEL, Ctx, cost_html, del_, document_html, esc, eur, first_name,
                fund_short, headline, in_words, initials, page_header, plural, show, slug,
                voice_id_for, voice_phone, when)

state = st.session_state


def go(view: str) -> None:
    st.query_params["vista"] = view


def open_alert(alert_id: str, client_id: str | None = None) -> None:
    """Salta a Avisos con esa alerta (y ese cliente) seleccionados."""
    state["sel_alert"] = alert_id
    if client_id:
        state[f"sel_{alert_id}"] = client_id
    go("avisos")


# --------------------------------------------------------------------------
# Aprobaciones (se guardan en disco: sobreviven a recargar la página)
# --------------------------------------------------------------------------

def _akey(imp: Impact) -> str:
    return f"{imp.alert_id}|{imp.client_id}"


def approved_at(ctx: Ctx, imp: Impact) -> str | None:
    return ctx.cuenta["gestor"]["aprobaciones"].get(_akey(imp))


def approve(ctx: Ctx, imp: Impact) -> None:
    ctx.cuenta["gestor"]["aprobaciones"][_akey(imp)] = datetime.now().strftime("%H:%M")
    store.save(ctx.cuenta)
    st.toast(f"Aviso a {first_name(ctx.snap.clients[imp.client_id].name)} aprobado", icon="✅")


def unapprove(ctx: Ctx, imp: Impact) -> None:
    ctx.cuenta["gestor"]["aprobaciones"].pop(_akey(imp), None)
    store.save(ctx.cuenta)


def pending_count(ctx: Ctx) -> int:
    return sum(1 for i in ctx.snap.impacts
               if i.client_id in ctx.snap.clients and not approved_at(ctx, i))


def watched_funds(ctx: Ctx) -> dict[str, dict]:
    """ISIN -> {nombre, clientes, patrimonio}: los fondos que tienen nuestros clientes."""
    funds: dict[str, dict] = {}
    for c in ctx.snap.clients.values():
        for h in c.holdings:
            if not h.isin or h.isin in ("—", "-"):
                continue
            f = funds.setdefault(h.isin, {"nombre": h.fund_name, "clientes": set(), "patrimonio": 0.0})
            f["clientes"].add(c.client_id)
            f["patrimonio"] += h.amount_eur
    return funds


# --------------------------------------------------------------------------
# Avisos
# --------------------------------------------------------------------------

def avisos(ctx: Ctx) -> None:
    snap = ctx.snap
    if not snap.alerts:
        show(f"""<div class="pvg-empty" style="margin-top:40px"><b>Todo en orden</b>
Ninguna alerta nueva en los {len(watched_funds(ctx))} fondos que vigilamos. Te avisaremos en cuanto cambie algo.</div>""")
        return

    alert_ids = [a.alert_id for a in snap.alerts]
    if state.get("sel_alert") not in alert_ids:
        state["sel_alert"] = alert_ids[0]
    if len(alert_ids) > 1:
        st.selectbox(f"{len(alert_ids)} alertas activas", alert_ids, key="sel_alert",
                     format_func=lambda a: f"{snap.alert(a).title} · {snap.alert(a).fund_name}")
    alert = snap.alert(state["sel_alert"])
    impacts = [i for i in snap.impacts_for(alert.alert_id) if i.client_id in snap.clients]
    impacts.sort(key=lambda i: -i.annual_cost_eur)

    n = len(impacts)
    pending = sum(1 for i in impacts if not approved_at(ctx, i))
    if n == 0:
        lede = "Ninguno de tus clientes tiene este fondo. <b>No hace falta avisar a nadie.</b>"
    else:
        who = "Uno de tus clientes tiene" if n == 1 else f"{in_words(n).capitalize()} de tus clientes tienen"
        if pending == 0:
            todo = "<b>Todos los avisos están aprobados.</b>"
        elif pending == 1:
            todo = "<b>Hay un aviso listo</b> para que lo revises y lo envíes."
        else:
            todo = f"<b>Hay {in_words(pending)} avisos listos</b> para que los revises y los envíes."
        lede = f"{who} este fondo. {todo}"

    total = sum(i.annual_cost_eur for i in impacts)
    rows = [(snap.clients[i.client_id].name, f"{eur(i.exposure_eur)} en el fondo", i.annual_cost_eur) for i in impacts]
    per = f"{'más' if total >= 0 else 'menos'} al año, entre {plural(n, 'cliente', 'clientes')}"
    show(f"""
<section class="pvg-hero">
  <div>
    <div class="pvg-h1">{esc(headline(alert))}</div>
    <div class="pvg-lede">{lede}</div>
    {document_html(alert)}
  </div>
  {cost_html("¿Cuánto les cuesta a tus clientes?", total, per, rows, alert)}
</section>""")

    show('<div class="pvg-h2">Avisos para revisar</div>'
         '<div class="pvg-h2sub">Cada cliente recibe el mensaje en su idioma: claro para quien empieza, '
         'técnico para banca privada.</div>')
    if not impacts:
        show('<div class="pvg-empty"><b>Nadie a quien avisar</b>Ningún cliente tiene este producto en cartera.</div>')
        return

    ids = [i.client_id for i in impacts]
    selected = state.get(f"sel_{alert.alert_id}")
    if selected not in ids:
        selected = ids[0]
    left, right = st.columns([1, 2.9], gap="medium")
    with left:
        _client_list(ctx, alert, impacts, selected)
    with right:
        imp = next(i for i in impacts if i.client_id == selected)
        _client_detail(ctx, alert, imp, snap.clients[selected])


def _select(alert_id: str, cid: str) -> None:
    state[f"sel_{alert_id}"] = cid


def _client_list(ctx: Ctx, alert: Alert, impacts: list[Impact], selected: str) -> None:
    with st.container(key="clientlist"):
        for imp in impacts:
            c = ctx.snap.clients[imp.client_id]
            chip = ('<span class="pvg-chip ok">✓ Aprobado</span>' if approved_at(ctx, imp)
                    else '<span class="pvg-chip wait">Pendiente de revisar</span>')
            with st.container(key=f"cli_{slug(imp.client_id)}"):
                show(f"""
<div class="pvg-item {'sel' if imp.client_id == selected else ''}">
  <span class="ini">{initials(c.name)}</span>
  <span><span class="nm">{esc(c.name)}</span><span class="pf">{esc(c.profile_label)}</span>{chip}</span>
  <span class="amt">{eur(imp.annual_cost_eur, 2)}<small>al año</small></span>
</div>""")
                st.button(f"Abrir aviso de {c.name}", key=f"open_{slug(alert.alert_id)}_{slug(imp.client_id)}",
                          on_click=_select, args=(alert.alert_id, imp.client_id))
    show('<div class="pvg-listnote">Ningún aviso sale sin tu aprobación.</div>')


def _client_detail(ctx: Ctx, alert: Alert, imp: Impact, client: Client) -> None:
    holdings = "".join(
        f'<div class="{"hit" if h.isin == alert.isin else ""}"><span>{esc(h.fund_name)}</span><span>{eur(h.amount_eur)}</span></div>'
        for h in client.holdings
    ) or '<div><span style="color:var(--ink-3)">Sin posiciones cargadas.</span></div>'
    share = imp.exposure_eur / client.total_eur * 100 if client.total_eur else 0
    with st.container(key="detail"):
        info, phone = st.columns([1.55, 1], gap="large")
        with info:
            show(f"""
<div class="pvg-who2"><b>{esc(client.name)}</b><span>Cartera de {eur(client.total_eur)}</span></div>
<div class="pvg-facts">
  <div><b>{eur(imp.exposure_eur)}</b>en el fondo afectado</div>
  <div><b>{share:.0f} %</b>de su cartera</div>
  <div class="red"><b>{eur(imp.annual_cost_eur, 2)}</b>de coste extra al año</div>
</div>
<div class="pvg-label">Resumen para ti</div>
<div class="pvg-brief">{esc(imp.advisor_summary)}</div>
<div class="pvg-label">Su cartera</div>
<div class="pvg-holdings">{holdings}</div>""")
        with phone:
            _phone(ctx, imp, client)


def _fmt_changed(scope: str) -> None:
    if state.get(f"fmt_{slug(scope)}") == "Avatar IA":
        state[f"av_autoplay_{slug(scope)}"] = True   # al cambiar al avatar, empieza a hablar


def format_picker(scope: str) -> str:
    """Selector "Nota de voz / Avatar IA" encima del móvil."""
    key = f"fmt_{slug(scope)}"
    st.segmented_control("Formato del aviso", ["Nota de voz", "Avatar IA"], default="Nota de voz", key=key,
                         label_visibility="collapsed", on_change=_fmt_changed, args=(scope,))
    return state.get(key) or "Nota de voz"


def _reset_text(key: str, original: str) -> None:
    state[key] = original


def _phone(ctx: Ctx, imp: Impact, client: Client) -> None:
    name = first_name(client.name)
    advisor = ctx.cuenta["gestor"]["nombre"] or "Tu asesor"
    text_key = f"msg_{slug(imp.alert_id)}_{slug(imp.client_id)}"
    state.setdefault(text_key, imp.client_message)
    message = state[text_key]
    if not message.strip():
        show('<div class="pvg-empty">El pipeline aún no ha generado el mensaje para este cliente.</div>')
        return

    ok_at = approved_at(ctx, imp)
    scope = f"{imp.alert_id}_{imp.client_id}"
    fmt = format_picker(scope)
    if fmt == "Avatar IA":
        a = ctx.snap.alert(imp.alert_id)
        audio = avatar.avatar_block(ctx, avatar.AvatarCtx(
            scope=scope, mode="gestor", persona=advisor, persona_role="tu asesor", nombre=name,
            perfil=client.profile, alert=a, amount=imp.exposure_eur, cost=imp.annual_cost_eur,
            portfolio=", ".join(f"{h.fund_name} {eur(h.amount_eur)}" for h in client.holdings),
            advisor_note=imp.advisor_summary, advisor=advisor, message=message))
    else:
        meta = f'<span class="ok">{ok_at} <b>✓✓</b></span>' if ok_at else "<span>Borrador</span>"
        audio = voice_phone(ctx, message, client.profile, scope, advisor, f"{advisor}, tu asesor", meta)

    with st.container(key="actions"):
        if ok_at:
            show(f'<div class="pvg-approved">{ICON["check"]} Aprobado a las {ok_at}</div>')
        else:
            st.button(f"Aprobar y enviar a {name}", type="primary" if audio else "secondary",
                      width="stretch", key=f"ok_{text_key}", on_click=approve, args=(ctx, imp))
        b1, b2 = st.columns(2)
        with b1:
            with st.popover("Editar texto", width="stretch"):
                st.text_area("Mensaje que leerá la voz", key=text_key, height=220)
                st.caption("Así lo pronunciará la voz:")
                st.caption(voice.prepare_for_speech(message))
                st.button("Restaurar el original", key=f"reset_{text_key}", icon=":material/undo:",
                          on_click=_reset_text, args=(text_key, imp.client_message))
        with b2:
            if audio:
                st.download_button("Descargar MP3", audio, file_name=f"aviso_{slug(client.name)}_{slug(imp.alert_id)}.mp3",
                                   mime="audio/mpeg", width="stretch", key=f"dl_{text_key}", on_click="ignore")
            else:
                st.button("Descargar MP3", disabled=True, width="stretch", key=f"dl_{text_key}")
        if ok_at:
            st.button("Deshacer aprobación", type="tertiary", width="stretch",
                      key=f"undo_{text_key}", on_click=unapprove, args=(ctx, imp))
        show(f'<div class="pvg-voice">{VOICE_LABEL.get(client.profile, "Voz personalizada")}</div>')


# --------------------------------------------------------------------------
# Clientes
# --------------------------------------------------------------------------

def clientes(ctx: Ctx) -> None:
    snap = ctx.snap
    aum = sum(c.total_eur for c in snap.clients.values())
    page_header("Tus clientes",
                f"{plural(len(snap.clients), 'cliente', 'clientes')} · {eur(aum)} en cartera. "
                "Pulsa un cliente con aviso para revisar su mensaje.")
    q = st.text_input("Buscar cliente", placeholder="Nombre, fondo o ISIN…", label_visibility="collapsed",
                      icon=":material/search:")
    alerts_by_isin = {a.isin: a for a in snap.alerts}

    def matches(c: Client) -> bool:
        if not q:
            return True
        text = " ".join([c.name, c.profile_label] + [f"{h.fund_name} {h.isin}" for h in c.holdings]).lower()
        return q.lower() in text

    shown = [c for c in snap.clients.values() if matches(c)]
    if not shown:
        show('<div class="pvg-empty"><b>Sin resultados</b>Ningún cliente coincide con la búsqueda.</div>')
        return

    cols = st.columns(2, gap="medium")
    for n, c in enumerate(sorted(shown, key=lambda c: -c.total_eur)):
        imps = [i for i in snap.impacts_of_client(c.client_id)]
        cost = sum(i.annual_cost_eur for i in imps)
        pend = sum(1 for i in imps if not approved_at(ctx, i))
        if not imps:
            chip = '<span class="pvg-chip ok">Sin cambios</span>'
        elif pend:
            chip = f'<span class="pvg-chip wait">{plural(pend, "aviso pendiente", "avisos pendientes")}</span>'
        else:
            chip = '<span class="pvg-chip ok">✓ Avisos aprobados</span>'
        holdings = "".join(
            f'<div class="{"hit" if h.isin in alerts_by_isin else ""}"><span>{esc(h.fund_name)}</span>'
            f'<span>{eur(h.amount_eur)}</span></div>' for h in c.holdings)
        with cols[n % 2]:
            with st.container(key=f"card_cli_{slug(c.client_id)}"):
                show(f"""
<div class="pvg-item" style="padding:0 0 14px">
  <span class="ini">{initials(c.name)}</span>
  <span><span class="nm">{esc(c.name)}</span><span class="pf">{esc(c.profile_label)}</span>{chip}</span>
  <span class="amt" style="color:var(--ink)">{eur(c.total_eur)}<small>en cartera</small></span>
</div>
<div class="pvg-facts" style="margin:4px 0 16px;padding-bottom:16px">
  <div><b>{len(c.holdings)}</b>posiciones</div>
  <div><b>{len(imps)}</b>{'cambio detectado' if len(imps) == 1 else 'cambios detectados'}</div>
  <div class="{'red' if cost else ''}"><b>{eur(cost, 2)}</b>de coste extra al año</div>
</div>
<div class="pvg-holdings">{holdings or '<div><span>Sin posiciones cargadas.</span></div>'}</div>""")
                if imps:
                    i0 = max(imps, key=lambda i: i.annual_cost_eur)
                    st.button(f"Revisar su aviso", key=f"goto_{slug(c.client_id)}", icon=":material/notifications:",
                              type="primary" if pend else "secondary",
                              on_click=open_alert, args=(i0.alert_id, c.client_id))


# --------------------------------------------------------------------------
# Documentos vigilados
# --------------------------------------------------------------------------

def documentos(ctx: Ctx) -> None:
    snap = ctx.snap
    funds = watched_funds(ctx)
    changed = {a.isin for a in snap.alerts}
    page_header("Documentos vigilados",
                "Leemos los folletos y documentos de datos fundamentales de cada fondo que tienen tus clientes. "
                "Cuando algo cambia, lo verás aquí con la página y la cita exacta.")
    total = sum(f["patrimonio"] for f in funds.values())
    show(f"""<div class="pvg-kpis">
  <div class="pvg-kpi"><span>Fondos vigilados</span><b>{len(funds)}</b></div>
  <div class="pvg-kpi red"><span>Con cambios</span><b>{len(changed & set(funds))}</b></div>
  <div class="pvg-kpi"><span>Patrimonio vigilado</span><b>{eur(total)}</b></div>
  <div class="pvg-kpi"><span>Última revisión</span><b>{when(ctx.loaded_at)}</b></div>
</div>""")

    left, right = st.columns([1.25, 1], gap="large")
    with left:
        rows = "".join(
            f'<tr><td>{esc(f["nombre"])}<small>{esc(isin)}</small></td>'
            f'<td class="n">{len(f["clientes"])}</td><td class="n">{eur(f["patrimonio"])}</td>'
            f'<td>{"<span class=\'pvg-chip red\'>Cambio detectado</span>" if isin in changed else "<span class=\'pvg-chip ok\'>Sin cambios</span>"}</td></tr>'
            for isin, f in sorted(funds.items(), key=lambda kv: (kv[0] not in changed, -kv[1]["patrimonio"])))
        with st.container(key="card_funds"):
            show(f"""<div class="pvg-label">Fondos en cartera de tus clientes</div>
<table class="pvg-table"><tr><th>Fondo</th><th class="n">Clientes</th><th class="n">Patrimonio</th><th>Estado</th></tr>{rows}</table>""")
    with right:
        show('<div class="pvg-label" style="margin-top:4px">Cambios detectados</div>')
        if not snap.alerts:
            show('<div class="pvg-empty"><b>Sin cambios</b>Ningún documento ha cambiado.</div>')
        for a in snap.alerts:
            n = len([i for i in snap.impacts_for(a.alert_id) if i.client_id in snap.clients])
            with st.container(key=f"card_alert_{slug(a.alert_id)}"):
                show(f"""<div class="pvg-alertrow">
  <span><b>{esc(headline(a))}</b><small>{esc(a.evidence.document)} · pág. {a.evidence.page} · detectado {when(a.detected_at)}</small></span>
  <span class="pvg-chip red">{plural(n, 'cliente', 'clientes')}</span>
  <q>{esc(a.evidence.quote)}</q>
</div>""")
                st.button("Ver avisos", key=f"see_{slug(a.alert_id)}", icon=":material/arrow_forward:",
                          on_click=open_alert, args=(a.alert_id,))


# --------------------------------------------------------------------------
# Ajustes
# --------------------------------------------------------------------------

def ajustes(ctx: Ctx, refresh) -> None:
    page_header("Ajustes", "Tu cuenta de gestor y de dónde salen los datos.")
    g = ctx.cuenta["gestor"]
    a, b = st.columns(2, gap="large")
    with a:
        with st.form("f_gestor"):
            show('<div class="pvg-label">Tu cuenta</div>')
            nombre = st.text_input("Tu nombre (firma los audios)", value=g["nombre"])
            if st.form_submit_button("Guardar", type="primary"):
                g["nombre"] = nombre.strip() or "Tu asesor"
                store.save(ctx.cuenta)
                st.toast("Guardado", icon="✅")
        with st.container(key="card_approvals"):
            n = len(g["aprobaciones"])
            show(f'<div class="pvg-label">Avisos aprobados</div><div class="pvg-brief" style="margin-bottom:12px">'
                 f'Has aprobado {plural(n, "aviso", "avisos")}. Las aprobaciones se guardan en este equipo.</div>')
            if st.button("Volver a marcar todo como pendiente", disabled=not n, icon=":material/restart_alt:"):
                g["aprobaciones"] = {}
                store.save(ctx.cuenta)
                st.rerun()
    with b:
        with st.container(key="card_source"):
            show('<div class="pvg-label">Fuente de datos</div>')
            st.text_input("Ruta a la base de datos SQLite", key="db_path", placeholder="p. ej. ../db/guardian.db",
                          help="Vacío = datos de demostración (Laura y Martín).", persist_state="session")
            st.caption(f"Usando: **{ctx.snap.source_name}** · revisado {when(ctx.loaded_at)}")
            if ctx.snap.warning:
                st.warning(ctx.snap.warning)
            if st.button("Buscar alertas nuevas", icon=":material/refresh:"):
                refresh()

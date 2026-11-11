"""
Portfolio Voice Guardian — Streamlit + ElevenLabs.

Ejecutar:  streamlit run app.py

Este fichero solo decide QUÉ pantalla se pinta. Cada pantalla vive en su módulo:

  vista_comun.py       bienvenida (elegir modo) y Voces
  vista_gestor.py      modo gestor: Avisos, Clientes, Documentos vigilados, Ajustes
  vista_particular.py  modo particular: Mis avisos, Mi cartera, Qué vigilamos, Ajustes
  ui.py                estilo y piezas compartidas (folleto, panel de coste, móvil con nota de voz)
  store.py             datos guardados en este equipo (cartera del particular, aprobaciones)

El modo y la pantalla van en la URL (?modo=gestor&vista=clientes): al recargar
la página sigues donde estabas.
"""

from __future__ import annotations

import os
from datetime import datetime

import streamlit as st

import store
import vista_comun
import vista_gestor
import vista_particular
import voice
from data_source import Snapshot, load_snapshot
from ui import ICON, Ctx, esc, initials, inject_css, plural, show, when

st.set_page_config(page_title="Portfolio Voice Guardian", page_icon="🛡️", layout="wide",
                   initial_sidebar_state="collapsed")
inject_css()
state = st.session_state


# --------------------------------------------------------------------------
# Configuración y datos
# --------------------------------------------------------------------------

def secret(name: str, default: str = "") -> str:
    """Lee de st.secrets o de variables de entorno, sin romper si no hay secrets."""
    try:
        if name in st.secrets and str(st.secrets[name]).strip():   # vacío = no definido
            return str(st.secrets[name])
    except Exception:  # noqa: BLE001 — no hay secrets.toml
        pass
    return os.getenv(name, default)


@st.cache_data(ttl=10, show_spinner=False)
def get_snapshot(db_path: str) -> tuple[Snapshot, str]:
    # ttl=10: si el pipeline escribe una alerta nueva, aparece en ≤10 s al refrescar.
    return load_snapshot(db_path or None), datetime.now().strftime("%Y-%m-%d %H:%M")


def refresh() -> None:
    get_snapshot.clear()
    st.rerun()


api_key = secret("ELEVENLABS_API_KEY")
if api_key.startswith("PEGA_AQUI"):   # plantilla sin rellenar
    api_key = ""
default_voices = dict(voice.DEFAULT_VOICES)
for _prof, _k in (("principiante", "ELEVEN_VOICE_CERCANA"), ("profesional", "ELEVEN_VOICE_PROFESIONAL")):
    if secret(_k):
        default_voices[_prof] = secret(_k)

# Si la voz por defecto no está en tu cuenta de ElevenLabs, se usa la primera que tengas
# (así se reutilizan los audios ya generados y guardados en audio_cache/).
if api_key:
    try:
        _ids = [v["voice_id"] for v in vista_comun._voices(api_key)]
        for _prof in default_voices:
            if _ids and default_voices[_prof] not in _ids:
                default_voices[_prof] = _ids[0]
    except voice.VoiceError:
        pass

# Una vez por sesión: ruta de secrets/entorno. (El campo de Ajustes usa persist_state y puede
# crear "db_path" vacío al empezar la sesión, por eso no basta con mirar si existe.)
if "db_init" not in state:
    state["db_init"] = True
    if not state.get("db_path"):
        state["db_path"] = secret("PVG_DB_PATH", "")
if "cuenta" not in state:
    state["cuenta"] = store.load()
    if secret("PVG_ASESOR") and state["cuenta"]["gestor"]["nombre"] == store.DEFAULT["gestor"]["nombre"]:
        state["cuenta"]["gestor"]["nombre"] = secret("PVG_ASESOR")

snap, loaded_at = get_snapshot(state["db_path"])
ctx = Ctx(snap=snap, loaded_at=loaded_at, api_key=api_key, model_id=secret("ELEVEN_MODEL", voice.MODEL_ID),
          cuenta=state["cuenta"], default_voices=default_voices)


# --------------------------------------------------------------------------
# Modo (gestor / particular) y pantalla
# --------------------------------------------------------------------------

NAV = {
    "gestor": [("avisos", "Avisos", ":material/notifications:"),
               ("clientes", "Clientes", ":material/group:"),
               ("documentos", "Documentos vigilados", ":material/description:"),
               ("voces", "Voces", ":material/mic:"),
               ("ajustes", "Ajustes", ":material/settings:")],
    "particular": [("inicio", "Mis avisos", ":material/notifications:"),
                   ("cartera", "Mi cartera", ":material/account_balance_wallet:"),
                   ("documentos", "Qué vigilamos", ":material/description:"),
                   ("voces", "Voz", ":material/mic:"),
                   ("ajustes", "Ajustes", ":material/settings:")],
}

mode = st.query_params.get("modo")
if mode not in NAV:
    vista_comun.bienvenida()
    st.stop()

views = [v for v, _, _ in NAV[mode]]
view = st.query_params.get("vista")
if view not in views:
    view = views[0]


def go(v: str) -> None:
    st.query_params["vista"] = v


def switch_mode(m: str | None) -> None:
    if m is None:
        st.query_params.clear()
    else:
        vista_comun.enter(m)


# --------------------------------------------------------------------------
# Rail lateral (navegación real) y barra superior
# --------------------------------------------------------------------------

if mode == "gestor":
    badge = vista_gestor.pending_count(ctx)
else:
    badge = len(vista_particular.matches_for(ctx))
if badge:
    # Globo rojo con el número de avisos pendientes sobre la campana
    show(f"""<style>.st-key-nav_{views[0]} button:after{{content:"{badge}";position:absolute;top:5px;right:4px;
min-width:17px;height:17px;padding:0 4px;border-radius:9px;background:#C23B2E;color:#fff;font:700 10.5px/17px Inter,sans-serif;
box-shadow:0 0 0 2px #15241F}}</style><div class="pvg-css"></div>""")

with st.container(key="rail"):
    show(f'<div class="mark" title="Portfolio Voice Guardian">{ICON["mark"]}</div>')
    for v, label, icon in NAV[mode]:
        st.button(label, key=f"nav_{v}", icon=icon, help=label, on_click=go, args=(v,),
                  type="primary" if v == view else "tertiary")

if mode == "gestor":
    who = ctx.cuenta["gestor"]["nombre"] or "Tu asesor"
    role = "Gestor"
    funds = len(vista_gestor.watched_funds(ctx))
    watching = secret("PVG_DOCS_VIGILADOS") and f"{secret('PVG_DOCS_VIGILADOS')} documentos" \
        or plural(funds, "fondo", "fondos")
else:
    who = ctx.cuenta["particular"]["nombre"] or "Mi cuenta"
    role = "Particular"
    watching = plural(len(ctx.cuenta["particular"]["posiciones"]), "producto", "productos")

with st.container(key="topbar"):
    c_brand, c_status, c_acc = st.columns([1.1, 2.0, 0.62], vertical_alignment="center")
    c_brand.markdown('<div class="pvg-brand">Portfolio Voice Guardian'
                     '<small>Leemos la letra pequeña por ' + ("tus clientes" if mode == "gestor" else "ti") +
                     '</small></div>', unsafe_allow_html=True)
    c_status.markdown(
        f'<div class="pvg-status"><span class="live"><i class="pulse"></i>Vigilando {esc(watching)}</span>'
        f'<span>Última revisión: {when(loaded_at)}</span></div>', unsafe_allow_html=True)
    with c_acc:
        with st.popover(who, icon=":material/account_circle:", width="stretch"):
            show(f'<div class="pvg-acc"><span class="avatar">{initials(who)}</span>'
                 f'<span><b>{esc(who)}</b><small>Cuenta de {role.lower()}</small></span></div>')
            st.button("Ajustes de la cuenta", icon=":material/settings:", width="stretch",
                      on_click=go, args=("ajustes",), key="acc_settings")
            other = "particular" if mode == "gestor" else "gestor"
            st.button(f"Cambiar a modo {other}", icon=":material/swap_horiz:", width="stretch",
                      on_click=switch_mode, args=(other,), key="acc_switch")
            st.button("Salir", icon=":material/logout:", width="stretch", type="tertiary",
                      on_click=switch_mode, args=(None,), key="acc_exit")

if snap.warning:
    show(f'<div class="pvg-banner">⚠️ {esc(snap.warning)}</div>')


# --------------------------------------------------------------------------
# La pantalla elegida
# --------------------------------------------------------------------------

if view == "voces":
    vista_comun.voces(ctx, mode)
elif mode == "gestor":
    {"avisos": lambda: vista_gestor.avisos(ctx),
     "clientes": lambda: vista_gestor.clientes(ctx),
     "documentos": lambda: vista_gestor.documentos(ctx),
     "ajustes": lambda: vista_gestor.ajustes(ctx, refresh)}[view]()
else:
    {"inicio": lambda: vista_particular.inicio(ctx),
     "cartera": lambda: vista_particular.cartera(ctx),
     "documentos": lambda: vista_particular.documentos(ctx),
     "ajustes": lambda: vista_particular.ajustes(ctx)}[view]()

show('<div class="pvg-foot">Prototipo de hackathon. '
     + ("Los mensajes los revisa siempre un asesor antes de enviarse." if mode == "gestor"
        else "Información, no recomendación de inversión.") + '</div>')

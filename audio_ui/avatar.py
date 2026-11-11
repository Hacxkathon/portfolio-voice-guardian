"""
Avatar con IA que dice el aviso y responde preguntas (ElevenLabs).

Dos niveles:
  1. Preguntas rápidas: respuestas escritas aquí, en Python, con las cifras
     exactas del cálculo, y convertidas a voz con ElevenLabs (texto a voz).
     Funciona con cualquier clave y queda en audio_cache/ (también sin internet).
  2. Pregúntale lo que quieras: conversación por voz en tiempo real con un
     agente de ElevenLabs (micrófono -> IA -> voz). Necesita que la clave tenga
     el permiso "ElevenLabs Agents" (convai). El agente se crea solo la primera
     vez y la clave nunca llega al navegador: el navegador recibe una URL
     firmada que caduca a los pocos minutos.

La cara del avatar y el movimiento de la boca están en avatar.html.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
import streamlit as st

import voice
from data_source import Alert
from ui import FEE_EVENTS, DIVIDEND_EVENTS, Ctx, eur, fund_short, num, slug, voice_id_for, voice_override, when

API = "https://api.elevenlabs.io/v1"
AGENT_NAME = "Portfolio Voice Guardian · Avatar"
AGENT_LLM = "gemini-2.5-flash"           # rápido: el avatar contesta en ~1 s
AGENT_TTS_MODEL = "eleven_flash_v2_5"    # los agentes en español necesitan un modelo flash/turbo v2.5
TEMPLATE = (Path(__file__).parent / "avatar.html")

state = st.session_state


class AgentError(RuntimeError):
    """Error del agente con mensaje legible para la interfaz."""


# --------------------------------------------------------------------------
# Contexto del avatar
# --------------------------------------------------------------------------

@dataclass
class AvatarCtx:
    scope: str              # identifica alerta + destinatario (para cachés y claves)
    mode: str               # "gestor" (gemelo digital del asesor) | "particular" (tu vigilante)
    persona: str            # nombre que aparece bajo el avatar
    persona_role: str       # "tu asesor" / "tu vigilante"
    nombre: str             # nombre de pila de quien escucha
    perfil: str             # "principiante" | "profesional"
    alert: Alert
    amount: float           # € en el producto afectado
    cost: float             # € al año (calculado en Python)
    where: str = ""         # plataformas o "" si no aplica
    is_fund: bool = True
    portfolio: str = ""     # resumen de la cartera para el agente
    advisor_note: str = ""  # resumen del asesor (modo gestor)
    advisor: str = ""       # nombre del asesor (modo gestor)
    message: str = ""       # el aviso que dice el avatar

    @property
    def usted(self) -> bool:
        return self.mode == "gestor" and self.perfil == "profesional"


def _t(c: AvatarCtx, tu: str, usted: str) -> str:
    return usted if c.usted else tu


# --------------------------------------------------------------------------
# Preguntas rápidas (respuestas deterministas: las cifras salen de Python)
# --------------------------------------------------------------------------

def faqs(c: AvatarCtx) -> list[tuple[str, str]]:
    a, ev = c.alert, c.alert.evidence
    # Cifras exactas, las mismas que el panel de coste (la voz dice "ciento cincuenta y siete con cincuenta")
    cost_r, y5, y10 = eur(abs(c.cost), 2), eur(abs(c.cost) * 5, 2), eur(abs(c.cost) * 10, 2)
    doc = re.sub(r"\s*\([^)]*\)", "", ev.document).strip()   # sin "(60 págs.)": suena raro en voz alta
    out: list[tuple[str, str]] = []
    if a.event_type in FEE_EVENTS:
        out.append(("¿Pierdo dinero?", _t(c,
            f"No, {c.nombre}. Tu dinero sigue ahí y el fondo funciona igual. Lo que cambia es lo que cobra la "
            f"gestora cada año: para ti son unos {cost_r} más al año, que dejan de rendir a tu favor.",
            f"No hay pérdida de capital, {c.nombre}. El impacto es un coste recurrente mayor: "
            f"{num(abs(a.delta_pp))} puntos más de comisión, unos {cost_r} anuales sobre su posición de {eur(c.amount)}.")))
    else:
        out.append(("¿Cuánto me afecta?", _t(c,
            f"Con tus {eur(c.amount)} en este producto, el impacto estimado es de unos {cost_r} al año.",
            f"Sobre su posición de {eur(c.amount)}, el impacto estimado es de {cost_r} anuales.")))
    out.append(("¿Y a largo plazo?", _t(c,
        f"Si no haces nada, en cinco años serían unos {y5} y en diez años unos {y10}. Es un cálculo sencillo, "
        f"sin contar el efecto compuesto.",
        f"A cinco años, unos {y5}; a diez años, unos {y10}. Cálculo lineal, sin efecto compuesto.")))
    if c.mode == "gestor":
        what = _t(c,
            "Lo vemos juntos con calma. Te propondré un par de fondos parecidos que cobran menos, y si decides "
            "cambiar, el traspaso entre fondos no paga impuestos.",
            "Le propondré alternativas de menor coste, por ejemplo una clase del mismo fondo con comisión "
            "inferior. El traspaso entre fondos no tiene impacto fiscal.")
    elif c.is_fund:
        what = _t(c,
            "Tienes tres opciones: quedarte si el fondo te sigue convenciendo, comparar con fondos parecidos que "
            "cobren menos, o pedir el reembolso. En España, pasar de un fondo a otro con un traspaso no paga impuestos.",
            "Mantener, comparar alternativas de menor coste o reembolsar. El traspaso entre fondos no tributa.")
    else:
        what = ("Compara con productos parecidos que cobren menos y decide con calma. Ten en cuenta que vender "
                "puede tener impuestos. Esto es información, no una recomendación.")
    out.append(("¿Qué puedo hacer?", what))
    out.append(("¿De dónde sale esto?",
                f"Sale del documento oficial: {doc}, página {ev.page}. La frase exacta dice: {ev.quote}"))
    return out


# --------------------------------------------------------------------------
# Agente conversacional (ElevenLabs Agents)
# --------------------------------------------------------------------------

def agent_prompt(c: AvatarCtx) -> str:
    a, ev = c.alert, c.alert.evidence
    trato = "de usted" if c.usted else "de tú"
    tono = ("cercano, tranquilo y sin tecnicismos (es principiante)" if c.perfil == "principiante"
            else "profesional, preciso y directo (es inversor con experiencia)")
    who = (f"el avatar con IA de {c.advisor}, su asesor financiero. Hablas en su nombre con su cliente"
           if c.mode == "gestor" else "el vigilante con IA de Portfolio Voice Guardian. Hablas con el inversor")
    lines = [
        f"Eres {who}, {c.nombre}. Le tratas {trato}. Tono: {tono}.",
        "",
        "CONTEXTO VERIFICADO (es lo único que sabes; no inventes nada fuera de esto):",
        f"- Producto: {a.fund_name} (ISIN {a.isin}){f', lo tiene en {c.where}' if c.where else ''}. Importe: {eur(c.amount)}.",
        f"- Cambio detectado: {a.title.lower()}: del {num(a.old_value)} % al {num(a.new_value)} % "
        f"({num(abs(a.delta_pp))} puntos). Detectado {when(a.detected_at)}.",
        f"- Fuente: {ev.document}, página {ev.page}. Cita exacta: \"{ev.quote}\"",
        f"- Impacto calculado por el sistema (no por la IA): {eur(abs(c.cost), 2)} al año; "
        f"{eur(abs(c.cost) * 5, 2)} en 5 años; {eur(abs(c.cost) * 10, 2)} en 10 años (sin efecto compuesto).",
    ]
    if c.portfolio:
        lines.append(f"- Su cartera: {c.portfolio}.")
    if c.advisor_note:
        lines.append(f"- Nota interna del asesor (no la leas literal): {c.advisor_note}")
    lines += [
        f"- Aviso que ya ha escuchado: \"{c.message}\"",
        "",
        "REGLAS:",
        "1. Contesta como en una llamada: 1 a 3 frases, sin listas ni símbolos. Di las cifras como se dicen en voz alta.",
        "2. Usa solo las cifras del contexto. Si te piden un dato que no tienes, dilo con naturalidad"
        + (f" y ofrece que {c.advisor} lo revise." if c.mode == "gestor" else " y sugiere consultarlo en el folleto o con un asesor."),
        "3. No recomiendes comprar ni vender productos concretos. Puedes explicar opciones generales: mantener, "
        "comparar alternativas más baratas o hacer un traspaso entre fondos (en España no tributa).",
        "4. Si preguntan algo ajeno a sus inversiones, redirige con amabilidad.",
        "5. Responde siempre en español. Si te preguntan si eres una persona, di que eres un avatar con IA.",
    ]
    return "\n".join(lines)


def first_message(c: AvatarCtx) -> str:
    return _t(c, f"Aquí estoy, {c.nombre}. ¿Qué quieres saber sobre este cambio?",
              f"Le escucho, {c.nombre}. ¿Qué quiere saber sobre este cambio?")


def _headers(api_key: str) -> dict:
    return {"xi-api-key": api_key, "Content-Type": "application/json"}


def _explain(resp: requests.Response) -> str:
    try:
        d = resp.json().get("detail", {})
        msg = d.get("message") if isinstance(d, dict) else str(d)
    except ValueError:
        msg = resp.text[:200]
    if resp.status_code == 401 and "convai" in str(msg):
        return ("Tu clave de ElevenLabs no tiene permiso para agentes. Actívalo en elevenlabs.io → API Keys → "
                "ElevenLabs Agents (lectura y escritura).")
    return f"ElevenLabs respondió {resp.status_code}: {msg}"


@st.cache_resource(show_spinner=False)
def ensure_agent(api_key: str, default_voice: str) -> str:
    """Devuelve el id del agente del avatar; lo crea la primera vez. Lanza AgentError si no se puede."""
    try:
        r = requests.get(f"{API}/convai/agents", headers=_headers(api_key),
                         params={"search": AGENT_NAME, "page_size": 10}, timeout=20)
    except requests.RequestException as exc:
        raise AgentError(f"No hay conexión con ElevenLabs: {exc.__class__.__name__}") from exc
    if not r.ok:
        raise AgentError(_explain(r))
    for ag in r.json().get("agents", []):
        if ag.get("name") == AGENT_NAME:
            return ag["agent_id"]
    body = {
        "name": AGENT_NAME,
        "tags": ["portfolio-voice-guardian"],
        "conversation_config": {
            "agent": {
                "first_message": "Hola, ¿qué quieres saber?",
                "language": "es",
                "prompt": {"prompt": "Eres un asistente financiero. (El contexto real llega en cada conversación.)",
                           "llm": AGENT_LLM, "temperature": 0.3},
            },
            "tts": {"model_id": AGENT_TTS_MODEL, "voice_id": default_voice},
            "conversation": {"max_duration_seconds": 300},
        },
        "platform_settings": {
            "auth": {"enable_auth": True},   # solo con URL firmada: nadie puede gastar tus créditos sin la app
            "overrides": {"conversation_config_override": {
                "agent": {"prompt": {"prompt": True}, "first_message": True, "language": True},
                "tts": {"voice_id": True},
            }},
        },
    }
    r = requests.post(f"{API}/convai/agents/create", headers=_headers(api_key), data=json.dumps(body), timeout=30)
    if not r.ok:
        raise AgentError(_explain(r))
    return r.json()["agent_id"]


@st.cache_data(ttl=480, show_spinner=False)
def signed_url(api_key: str, agent_id: str, nonce: int) -> str:
    """URL firmada para una conversación (caduca en minutos). `nonce` fuerza una nueva."""
    r = requests.get(f"{API}/convai/conversation/get-signed-url", headers=_headers(api_key),
                     params={"agent_id": agent_id}, timeout=20)
    if not r.ok:
        raise AgentError(_explain(r))
    return r.json()["signed_url"]


def _renew(scope: str) -> None:
    state[f"av_nonce_{scope}"] = state.get(f"av_nonce_{scope}", 0) + 1


# --------------------------------------------------------------------------
# Bloque de Streamlit
# --------------------------------------------------------------------------

def _audio_for(ctx: Ctx, text: str, profile: str) -> Optional[bytes]:
    vid, ov = voice_id_for(ctx, profile), voice_override(profile)
    key = f"avclip_{vid}_{ctx.model_id}_{hashlib.md5((voice.settings_tag(ov) + text).encode()).hexdigest()[:12]}"
    if key not in state:
        cached = voice.cached_audio(text, vid, ctx.model_id, profile, ov)
        if cached:
            state[key] = cached
    return state.get(key)


def _b64(audio: bytes) -> str:
    return "data:audio/mpeg;base64," + base64.b64encode(audio).decode()


def avatar_block(ctx: Ctx, c: AvatarCtx, autoplay: bool = False) -> Optional[bytes]:
    """Pinta el avatar. Devuelve el MP3 del aviso (para el botón de descarga) o None."""
    sc = slug(c.scope)
    items = [("__aviso__", c.message)] + faqs(c)
    clips = {q: _audio_for(ctx, text, c.perfil) for q, text in items}
    missing = [q for q, a in clips.items() if not a]

    if missing:
        n = len(missing)
        st.markdown(
            f'<div class="pvg-avprep"><b>Prepara el avatar</b>Genera la voz del aviso y de las respuestas rápidas '
            f'({n} {"audio" if n == 1 else "audios"}). Se guardan y después funciona al instante, incluso sin internet.</div>',
            unsafe_allow_html=True)
        if st.button("Preparar avatar", icon=":material/face:", type="primary", width="stretch",
                     key=f"avprep_{sc}", disabled=not ctx.api_key,
                     help=None if ctx.api_key else "Falta la clave de ElevenLabs"):
            vid, ov = voice_id_for(ctx, c.perfil), voice_override(c.perfil)
            bar = st.progress(0.0, text="Grabando…")
            try:
                for i, (q, text) in enumerate([it for it in items if it[0] in missing]):
                    voice.synthesize(text, vid, c.perfil, ctx.api_key, ctx.model_id, ov)
                    bar.progress((i + 1) / n, text=f"Grabando {i + 1} de {n}…")
                state[f"av_autoplay_{sc}"] = True
                st.rerun()
            except voice.VoiceError as exc:
                st.error(str(exc), icon="🔇")
        return None

    agent: dict = {"error": "Falta la clave de ElevenLabs."}
    if ctx.api_key:
        try:
            agent_id = ensure_agent(ctx.api_key, voice_id_for(ctx, c.perfil))
            agent = {
                "signedUrl": signed_url(ctx.api_key, agent_id, state.get(f"av_nonce_{sc}", 0)),
                "prompt": agent_prompt(c),
                "firstMessage": first_message(c),
                "voiceId": voice_id_for(ctx, c.perfil),
            }
        except AgentError as exc:
            agent = {"error": str(exc)}
            ensure_agent.clear()   # para reintentar en cuanto se active el permiso

    cfg = {
        "scope": sc,
        "persona": c.persona,
        "role": c.persona_role,
        "outfit": "gestor" if c.mode == "gestor" else "particular",
        "message": {"text": c.message, "src": _b64(clips["__aviso__"])},
        "faqs": [{"q": q, "text": t, "src": _b64(clips[q])} for q, t in faqs(c)],
        "agent": agent,
        "autoplay": bool(autoplay or state.pop(f"av_autoplay_{sc}", False)),
    }
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__CONFIG__*/null", json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/"))
    st.iframe(html, height=610)   # luego se ajusta sola (avatar.html)
    # Botón invisible: el avatar lo pulsa para pedir una URL firmada nueva tras cada conversación.
    st.button("Renovar conversación", key=f"avrenew_{sc}", on_click=_renew, args=(sc,))
    return clips["__aviso__"]

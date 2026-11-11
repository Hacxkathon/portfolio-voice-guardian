"""
Pantallas comunes a los dos modos:

  bienvenida -> elegir si entras como gestor o como particular
  voces      -> elegir y afinar las voces de ElevenLabs y escuchar una prueba
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

import voice
from ui import ICON, Ctx, page_header, show, voice_id_for, voice_override

state = st.session_state

try:
    INTRO_3D = (Path(__file__).parent / "intro3d.html").read_text(encoding="utf-8")
except OSError:   # si falta el fichero, bienvenida sin 3D
    INTRO_3D = ""


def enter(mode: str) -> None:
    st.query_params.from_dict({"modo": mode, "vista": "avisos" if mode == "gestor" else "inicio"})


def bienvenida() -> None:
    if INTRO_3D:
        # Escena 3D a pantalla completa, detrás de todo (ver intro3d.html)
        with st.container(key="intro3d"):
            st.iframe(INTRO_3D, height=1)
    show(f"""
<div class="pvg-welcome">
  <div class="stage"><div class="logo">{ICON["mark"]}</div></div>
  <h1>Leemos la letra pequeña por ti</h1>
  <p>Detectamos los cambios en los documentos de tus inversiones, los traducimos a euros
     y te los contamos con una nota de voz. ¿Cómo vas a usarlo?</p>
</div>""")
    _, a, b, _ = st.columns([0.5, 2, 2, 0.5], gap="medium")
    cards = [
        (a, "gestor", ICON["briefcase"], "Soy gestor o asesor",
         "Vigila las carteras de todos tus clientes y avísales antes de que te llamen.",
         ["Tus clientes y sus carteras", "Impacto en euros por cliente", "Nota de voz personalizada para cada uno",
          "Nada se envía sin tu aprobación"], "Entrar como gestor"),
        (b, "particular", ICON["person"], "Soy inversor particular",
         "Tienes dinero en varios sitios y no puedes leerte cada folleto. Lo hacemos nosotros.",
         ["Fondos, ETF, acciones y cripto", "En cualquier banco, bróker o exchange",
          "Cuánto te cuesta cada cambio, en euros", "Avisos en audio, en tu idioma"], "Entrar como particular"),
    ]
    for col, mode, icon, title, desc, bullets, cta in cards:
        with col:
            with st.container(key=f"pick_{mode}"):
                show(f"""<div class="pvg-pick"><div class="ic">{icon}</div><b>{title}</b><p>{desc}</p>
<ul>{''.join(f'<li>{x}</li>' for x in bullets)}</ul><span class="go">{cta}</span></div>""")
                st.button(cta, key=f"enter_{mode}", on_click=enter, args=(mode,))
    show('<div class="pvg-foot pvg-wfoot" style="text-align:center">Prototipo de hackathon · información, no recomendación de inversión.</div>')


SAMPLE = {
    "principiante": "Hola, soy tu vigilante financiero. Cuando cambie algo en tus fondos, te lo contaré así, con calma y sin tecnicismos.",
    "profesional": "Buenas tardes. Le informaré de cada modificación relevante en sus productos, con el impacto exacto en euros.",
}


def voces(ctx: Ctx, mode: str) -> None:
    page_header("Voces", "Elige cómo suenan los avisos. Cada perfil tiene su voz: cercana para quien empieza, "
                "sobria para inversores profesionales. Los cambios se aplican al generar un audio nuevo.")
    voices: list[dict] = []
    if ctx.api_key:
        try:
            voices = _voices(ctx.api_key)
        except voice.VoiceError as exc:
            st.warning(f"No se pudo leer la lista de voces: {exc}")
    else:
        st.info("Sin clave de ElevenLabs: solo se pueden reproducir audios ya generados. "
                "Añade `ELEVENLABS_API_KEY` en `.streamlit/secrets.toml`.", icon="🔇")

    profiles = (("principiante", "Voz cercana", "Para principiantes y perfiles conservadores"),
                ("profesional", "Voz sobria", "Para banca privada e inversores profesionales"))
    cols = st.columns(2, gap="large")
    for col, (profile, title, sub) in zip(cols, profiles):
        d = voice.VOICE_SETTINGS[profile]
        with col:
            with st.container(key=f"card_voice_{profile}"):
                show(f'<div class="pvg-who2"><b>{title}</b><span>{sub}</span></div>')
                if voices:
                    ids = [v["voice_id"] for v in voices]
                    names = {v["voice_id"]: v["name"] for v in voices}
                    current = voice_id_for(ctx, profile)
                    if state.get(f"voice_{profile}") not in ids:
                        state[f"voice_{profile}"] = current if current in ids else ids[0]
                    st.selectbox("Voz", ids, key=f"voice_{profile}", format_func=lambda i, n=names: n.get(i, i),
                                 persist_state="session")
                st.slider("Estabilidad", 0.0, 1.0, d["stability"], 0.05, key=f"stab_{profile}", persist_state="session",
                          help="Baja = más emoción y variación. Alta = más constante y seria.")
                st.slider("Parecido a la voz original", 0.0, 1.0, d["similarity_boost"], 0.05, key=f"sim_{profile}",
                          persist_state="session")
                st.slider("Expresividad", 0.0, 1.0, d["style"], 0.05, key=f"sty_{profile}", persist_state="session",
                          help="Exagera el estilo de la voz. Por encima de 0,4 puede sonar teatral.")
                st.slider("Velocidad", 0.7, 1.2, d["speed"], 0.05, key=f"spd_{profile}", persist_state="session")
                text = st.text_area("Frase de prueba", SAMPLE[profile], key=f"sample_{profile}", height=90)
                if st.button("Escuchar prueba", key=f"try_{profile}", icon=":material/play_arrow:", type="primary",
                             disabled=not ctx.api_key):
                    with st.spinner("Generando…"):
                        try:
                            res = voice.synthesize(text, voice_id_for(ctx, profile), profile, ctx.api_key,
                                                   ctx.model_id, voice_override(profile))
                            state[f"try_audio_{profile}"] = res.audio
                        except voice.VoiceError as exc:
                            st.error(str(exc), icon="🔇")
                if state.get(f"try_audio_{profile}"):
                    st.audio(state[f"try_audio_{profile}"], format="audio/mpeg")
    st.caption(f"Modelo: `{ctx.model_id}` · los audios se guardan en `audio_cache/` para no pagar dos veces "
               "y para que el demo funcione sin internet.")


@st.cache_data(ttl=60, show_spinner=False)
def _voices(api_key: str) -> list[dict]:
    return voice.list_voices(api_key)

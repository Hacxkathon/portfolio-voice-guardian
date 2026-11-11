# Portfolio Voice Guardian · Pantalla y voz (parte de Mariano)

## Quién soy y cómo quiero trabajar
- Soy Mariano, estudiante de 2.º de Data and Business Analytics (UC3M). Esto es mi parte de un hackathon de finanzas.
- **Quiero escribir yo el código para aprender.** Modo tutor por defecto:
  1. Dame cada cambio en **bloques pequeños** (máx. ~20 líneas) e indica en qué archivo y en qué línea va.
  2. **Explica qué hace cada línea** en lenguaje sencillo, y define cualquier término técnico (CSS, HTML, Streamlit, Python) la primera vez que aparezca.
  3. **Espera** a que lo escriba y lo pruebe antes de pasar al siguiente bloque.
  4. **No edites los archivos tú** salvo que te diga explícitamente "hazlo tú".
  5. Si te pego un error, explícame la causa antes de darme la solución.
- Respóndeme en español.

## El proyecto
- App de Streamlit para asesores financieros: detecta cambios en documentos de fondos (p. ej. subida de comisión),
  calcula el impacto en euros por cliente y genera un mensaje de voz personalizado con ElevenLabs.
- Equipo: Jorge (base de datos y pipeline), Xoan (análisis con Claude), Vanesa (cálculos), Denisa (vigilancia de documentos), yo (pantalla y voz).
- Repo del equipo: Hacxkathon/portfolio-voice-guardian. Esta carpeta irá en `audio_ui/` del repo.

## Archivos
- `app.py`: el "router". Lee `?modo=gestor|particular&vista=...` de la URL, pinta el rail lateral (botones reales) y la barra superior, y llama a la pantalla. Sin `modo` muestra la bienvenida.
- `ui.py`: estilo (CSS), formatos (`eur`, `when`…), la hoja del folleto, el panel de coste, el móvil con la nota de voz (`voice_phone`) y `impact_eur` (cálculo determinista).
- `vista_gestor.py`: Avisos, Clientes, Documentos vigilados, Ajustes. `vista_particular.py`: Mis avisos, Mi cartera, Qué vigilamos, Ajustes. `vista_comun.py`: bienvenida y Voces.
- `avatar.py` + `avatar.html`: avatar con IA (cara SVG con boca sincronizada con el audio). Preguntas rápidas = respuestas deterministas en Python + TTS; "Pregúntale lo que quieras" = agente de ElevenLabs (se crea solo; necesita permiso convai en la clave; URL firmada, la clave no va al navegador).
- `intro3d.html`: intro 3D (Three.js) de la bienvenida, detrás de las tarjetas.
- `store.py`: datos guardados en `user_data/cuenta.json` (nombre del gestor y aprobaciones; nombre, tono y cartera del particular). En `.gitignore`.
- `data_source.py`: datos. `demo_snapshot()` = datos de prueba; `SQL` + `sqlite_snapshot()` = base de datos de Jorge (`db/guardian.db`, tablas CLIENTS, PORTFOLIOS, ASSETS, HOLDINGS, EVENTS, EVENT_IMPACTS, BRIEFINGS).
- `voice.py`: ElevenLabs por API REST (`requests`), con caché de MP3 en `audio_cache/` y limpieza del texto (`prepare_for_speech`).
- `.streamlit/secrets.toml`: clave de ElevenLabs y ajustes. **Nunca mostrar, copiar ni subir su contenido.**
- `INICIAR_APP.bat`: crea el entorno `.venv`, instala `requirements.txt` y arranca `streamlit run app.py`.

## Restricciones técnicas (importantes)
- Windows con Python 3.14 en `.venv`. **Smart App Control** bloquea librerías con partes compiladas no reconocidas
  (ya bloqueó pandas). **No añadas pandas, numpy, altair, plotly ni similares**: gráficos con HTML/CSS puro.
- Dependencias permitidas: `streamlit`, `requests` y librerías de Python puro.
- La app no debe caerse nunca en el demo: ante cualquier fallo, mostrar datos de demo y un aviso.

## Objetivo actual: diseño "10 veces más profesional"
1. Ocultar lo que delata a Streamlit (botón Deploy, menú, cabecera por defecto).
2. Identidad visual: logo, tipografía Inter, paleta de 3-4 colores, espaciado coherente.
3. Jerarquía: en 3 segundos se ve qué ha pasado, a quién afecta y cuánto cuesta.
4. "Momento wow": vista previa del mensaje en un móvil, como nota de voz de WhatsApp, con animaciones suaves.
5. Detalles de producto real: estados vacíos, hora de última revisión, estado de aprobación del mensaje.

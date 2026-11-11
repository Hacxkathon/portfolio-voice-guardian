# Portfolio Voice Guardian · Pantalla y Voz (Streamlit + ElevenLabs)

Panel del asesor: ve las alertas detectadas, la **prueba** (página y cita textual del documento),
el **impacto en euros** de cada cliente y escucha el **mensaje de voz** personalizado con un clic.

## Arrancar en 2 minutos

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # y pega la clave de ElevenLabs
streamlit run app.py
```

Sin clave ni base de datos también arranca: usa los datos de demo (Laura y Martín) y
reproduce los audios que ya estén en `audio_cache/`.

## Ficheros

| Fichero | Qué hace |
|---|---|
| `app.py` | La pantalla. Solo pinta; no calcula ni consulta nada directamente. |
| `data_source.py` | **Único punto de contacto con la base de datos.** Datos demo + lector SQLite. |
| `voice.py` | ElevenLabs: genera el MP3, lo guarda en caché y explica los errores en castellano. |
| `pregenerate_audio.py` | Genera todos los audios antes del demo (ver abajo). |

## Conexión con la base de datos de Jorge

1. Poner la ruta en la barra lateral o en `PVG_DB_PATH` (secrets.toml).
2. Si los nombres de tablas/columnas no coinciden, **solo se cambia el diccionario `SQL`**
   al principio de `data_source.py`. Cada consulta tiene que devolver las columnas con los
   alias indicados (`client_id`, `annual_cost_eur`, `quote`, `page`, …).
3. Si la base de datos falla, la app **no se cae**: muestra los datos de demo y un aviso.
4. El botón «Buscar alertas nuevas» relee la base de datos; además se refresca sola cada 10 s,
   así que cuando el vigilante de Denisa dispare el pipeline, la alerta aparece en pantalla.

## Voz

- Modelo `eleven_multilingual_v2` (necesario para que el español suene natural).
- Dos voces según perfil: **cercana** para principiantes (Laura) y **sobria** para profesionales
  (Martín). Se eligen en la barra lateral entre las voces de la cuenta.
- Cada audio se guarda en `audio_cache/` (mismo texto + misma voz = mismo fichero):
  no se gastan créditos dos veces y suena al instante.
- `prepare_for_speech()` convierte «0,45%» → «0,45 por ciento», «€» → «euros», «pb» → «puntos básicos».

## Antes de la presentación (importante)

```bash
python pregenerate_audio.py
```

Deja todos los audios listos. Si el wifi del evento falla, el demo funciona igual.

## Guion de demo (60 segundos)

1. **El problema** (cabecera): «Una gestora publica 60 páginas para subir una comisión. Nadie lo lee.»
2. **La alerta**: 1,20 % → 1,65 %. Señalar la cita y la página 14: «No es una opinión de la IA, es la frase exacta del folleto.»
3. **Los euros**: 2.407,50 € al año entre dos clientes. «El cálculo lo hace Python, no la IA.»
4. **Laura** → ▶ voz cercana, sin tecnicismos.
5. **Martín** → ▶ mismo cambio, voz y lenguaje profesional (ISIN, puntos básicos, clase institucional).
6. Cierre: «Misma noticia, dos clientes, dos mensajes. El asesor solo revisa y envía.»

> Nota: los ISIN de los datos de demo son ficticios.

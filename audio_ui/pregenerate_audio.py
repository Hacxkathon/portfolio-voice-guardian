"""
Genera de antemano todos los audios del demo y los deja en audio_cache/.
Ejecutar la noche antes:  python pregenerate_audio.py
Así el día de la presentación los ▶ suenan al instante, aunque falle el wifi.
"""
import os
import sys

import voice
from data_source import load_snapshot

api_key = os.getenv("ELEVENLABS_API_KEY")
if not api_key:
    try:
        import tomllib
        with open(".streamlit/secrets.toml", "rb") as f:
            api_key = tomllib.load(f).get("ELEVENLABS_API_KEY")
    except FileNotFoundError:
        pass
model_id = os.getenv("ELEVEN_MODEL")
try:
    import tomllib
    with open(".streamlit/secrets.toml", "rb") as f:
        _sec = tomllib.load(f)
    model_id = model_id or _sec.get("ELEVEN_MODEL")
    for prof, k in (("principiante", "ELEVEN_VOICE_CERCANA"), ("profesional", "ELEVEN_VOICE_PROFESIONAL")):
        if _sec.get(k):
            voice.DEFAULT_VOICES[prof] = _sec[k]
except FileNotFoundError:
    pass
if not api_key:
    sys.exit("Falta ELEVENLABS_API_KEY (variable de entorno o .streamlit/secrets.toml).")

snap = load_snapshot(os.getenv("PVG_DB_PATH") or None)
if snap.warning:
    print("AVISO:", snap.warning)

for imp in snap.impacts:
    client = snap.clients.get(imp.client_id)
    if not client:
        continue
    vid = voice.DEFAULT_VOICES.get(client.profile, voice.DEFAULT_VOICES["principiante"])
    try:
        res = voice.synthesize(imp.client_message, vid, client.profile, api_key, model_id)
        estado = "ya estaba en caché" if res.from_cache else "generado"
        print(f"✓ {client.name} ({imp.alert_id}): {estado} -> {res.path.name}")
    except voice.VoiceError as exc:
        print(f"✗ {client.name} ({imp.alert_id}): {exc}")

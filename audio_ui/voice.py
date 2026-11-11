"""
Voz con ElevenLabs para Portfolio Voice Guardian.

- Llama a la API REST de ElevenLabs (POST /v1/text-to-speech/{voice_id}).
- Guarda cada audio en disco (audio_cache/). Mismo texto + misma voz = mismo
  fichero: no se paga dos veces y el demo funciona aunque falle el wifi.
- Voz distinta según el perfil del cliente: cercana para principiantes,
  sobria para profesionales.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

API_BASE = "https://api.elevenlabs.io/v1"
MODEL_ID = "eleven_multilingual_v2"      # por defecto; se puede cambiar a "eleven_v3" en secrets (ELEVEN_MODEL)
OUTPUT_FORMAT = "mp3_44100_128"
CACHE_DIR = Path(__file__).parent / "audio_cache"

# Voces por defecto (voces predefinidas de ElevenLabs). Se pueden cambiar
# desde la barra lateral o con variables de entorno / secrets.
DEFAULT_VOICES = {
    "principiante": os.getenv("ELEVEN_VOICE_CERCANA", "EXAVITQu4vr4xnSDxMaL"),
    "profesional": os.getenv("ELEVEN_VOICE_PROFESIONAL", "JBFqnCBsd6RMkjVDRZzb"),
}

# Ajustes por perfil: más estabilidad = tono más sobrio y constante.
VOICE_SETTINGS = {
    "principiante": {"stability": 0.45, "similarity_boost": 0.80, "style": 0.25,
                     "speed": 0.95, "use_speaker_boost": True},
    "profesional": {"stability": 0.65, "similarity_boost": 0.80, "style": 0.05,
                    "speed": 1.0, "use_speaker_boost": True},
}


class VoiceError(RuntimeError):
    """Error con mensaje ya legible para mostrar en la interfaz."""


@dataclass
class VoiceResult:
    audio: bytes
    path: Path
    from_cache: bool


def _es_number(m: re.Match) -> str:
    """'2,250.00' (formato inglés) -> '2.250,00' (formato español)."""
    return m.group(0).replace(",", "X").replace(".", ",").replace("X", ".")


def prepare_for_speech(text: str) -> str:
    """Retoques para que la voz suene natural: cifras, símbolos y códigos."""
    # Códigos que nadie dice en voz alta: (ISIN: LU0998877665)
    text = re.sub(r"\s*\(?ISIN:?\s*[A-Z]{2}[A-Z0-9]{9}\d\)?", "", text)
    # Números en formato inglés -> español
    text = re.sub(r"\d{1,3}(?:,\d{3})+\.\d{2}\b", _es_number, text)
    text = re.sub(r"\b(\d+)\.(\d{1,2})\b(?=\s*(?:€|eur|%|puntos))", r"\1,\2", text)
    # Céntimos a cero: "2.250,00 euros" -> "2.250 euros"
    text = re.sub(r"(\d),00\b", r"\1", text)
    text = text.replace("Estimado/a", "Estimado").replace("SL ", "").replace("S.L. ", "")
    text = re.sub(r"(\d)\s*%", r"\1 por ciento", text)
    text = re.sub(r"(\d)\s*€", r"\1 euros", text)
    text = re.sub(r"\bpb\b", "puntos básicos", text)
    text = re.sub(r"\bpp\b", "puntos porcentuales", text)
    return re.sub(r"\s+", " ", text).strip()


V3_STABILITY = (0.0, 0.5, 1.0)   # Eleven v3 solo acepta estos tres valores


def _settings_for(model_id: str, profile: str, override: Optional[dict] = None) -> dict:
    base = dict(VOICE_SETTINGS.get(profile, VOICE_SETTINGS["principiante"]))
    if override:
        base.update(override)
    if model_id == "eleven_v3":
        # v3: la emoción se marca con etiquetas [warmly], [calm]...; estabilidad discreta
        st = min(V3_STABILITY, key=lambda v: abs(v - base.get("stability", 0.5)))
        return {"stability": st, "speed": base.get("speed", 1.0)}
    return base


def settings_tag(settings: Optional[dict]) -> str:
    """Huella corta de los ajustes, para que cambiar un ajuste genere un audio nuevo."""
    return json.dumps(settings or {}, sort_keys=True)


def _cache_path(text: str, voice_id: str, model_id: str = MODEL_ID,
                settings: Optional[dict] = None) -> Path:
    raw = f"{model_id}|{voice_id}|{settings_tag(settings)}|{text}"
    key = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return CACHE_DIR / f"{key}.mp3"


def cached_audio(text: str, voice_id: str, model_id: str = MODEL_ID,
                 profile: str = "principiante", override: Optional[dict] = None) -> Optional[bytes]:
    if model_id != "eleven_v3":
        text = re.sub(r"\[[^\]]+\]\s*", "", text)
    settings = _settings_for(model_id, profile, override)
    path = _cache_path(prepare_for_speech(text), voice_id, model_id, settings)
    return path.read_bytes() if path.exists() else None


def synthesize(text: str, voice_id: str, profile: str, api_key: Optional[str],
               model_id: Optional[str] = None, override: Optional[dict] = None,
               timeout: int = 90) -> VoiceResult:
    """Devuelve el MP3 del texto. Usa caché si existe; si no, llama a la API."""
    model_id = model_id or MODEL_ID
    if model_id != "eleven_v3":
        text = re.sub(r"\[[^\]]+\]\s*", "", text)   # las etiquetas solo las entiende v3
    spoken = prepare_for_speech(text)
    settings = _settings_for(model_id, profile, override)
    path = _cache_path(spoken, voice_id, model_id, settings)
    if path.exists():
        return VoiceResult(path.read_bytes(), path, from_cache=True)

    if not api_key:
        raise VoiceError("Falta la clave de ElevenLabs (ELEVENLABS_API_KEY).")

    try:
        resp = requests.post(
            f"{API_BASE}/text-to-speech/{voice_id}",
            params={"output_format": OUTPUT_FORMAT},
            headers={"xi-api-key": api_key, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"},
            json={
                "text": spoken,
                "model_id": model_id,
                "voice_settings": settings,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise VoiceError(f"No hay conexión con ElevenLabs: {exc}") from exc

    if resp.status_code != 200:
        raise VoiceError(_explain_error(resp))

    CACHE_DIR.mkdir(exist_ok=True)
    path.write_bytes(resp.content)
    return VoiceResult(resp.content, path, from_cache=False)


def list_voices(api_key: str, timeout: int = 15) -> list[dict]:
    """Voces disponibles en la cuenta: [{'voice_id', 'name', 'labels'}]."""
    try:
        resp = requests.get(f"{API_BASE}/voices", headers={"xi-api-key": api_key}, timeout=timeout)
    except requests.RequestException as exc:
        raise VoiceError(f"No hay conexión con ElevenLabs: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise VoiceError(_explain_error(resp))
    return [
        {"voice_id": v["voice_id"], "name": v.get("name", v["voice_id"]),
         "labels": v.get("labels") or {}}
        for v in resp.json().get("voices", [])
    ]


def _explain_error(resp: requests.Response) -> str:
    try:
        detail = resp.json().get("detail")
        msg = detail.get("message") if isinstance(detail, dict) else str(detail)
    except ValueError:
        msg = resp.text[:200]
    hints = {
        401: "Clave de ElevenLabs incorrecta o caducada.",
        402: "La cuenta de ElevenLabs no tiene créditos suficientes.",
        404: "Esa voz no existe en la cuenta: elige otra en la barra lateral.",
        422: "Petición no válida (revisa el texto o la voz).",
        429: "Demasiadas peticiones seguidas: espera unos segundos.",
    }
    return f"{hints.get(resp.status_code, 'Error de ElevenLabs')} [{resp.status_code}] {msg or ''}".strip()

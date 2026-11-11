"""
Datos locales de la cuenta (lo que no viene del pipeline del equipo).

  * Gestor:     su nombre y qué avisos ha aprobado.
  * Particular: su nombre, el tono de voz que prefiere y su cartera
                (dónde tiene el dinero y en qué productos).

Se guarda en user_data/cuenta.json para que no se pierda al recargar la página.
Es un prototipo de un solo usuario: en producción esto iría en la base de datos.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

PATH = Path(__file__).parent / "user_data" / "cuenta.json"

DEFAULT = {
    "gestor": {"nombre": "Carlos Ruiz", "aprobaciones": {}},
    "particular": {"nombre": "", "perfil": "principiante", "posiciones": []},
}

PLATAFORMAS = [
    "MyInvestor", "Indexa Capital", "Trade Republic", "DEGIRO", "Interactive Brokers", "XTB",
    "eToro", "Revolut", "Renta 4", "Finizens", "Openbank", "ING", "BBVA", "Santander",
    "CaixaBank", "Bankinter", "Binance", "Coinbase", "Kraken", "Bit2Me", "Tesoro Público",
]

TIPOS = [
    "Fondo de inversión", "ETF", "Acción", "Criptomoneda", "Bono o letra",
    "Plan de pensiones", "Depósito o cuenta remunerada", "Otro",
]

# Qué documento vigilamos de cada tipo de producto
DOCUMENTO_POR_TIPO = {
    "Fondo de inversión": "Folleto y documento de datos fundamentales (DFI)",
    "ETF": "Folleto y KID del ETF",
    "Acción": "Hechos relevantes y resultados ante el regulador",
    "Criptomoneda": "Condiciones y comisiones de la plataforma",
    "Bono o letra": "Condiciones de la emisión",
    "Plan de pensiones": "Documento de datos fundamentales del plan",
    "Depósito o cuenta remunerada": "Condiciones del contrato",
    "Otro": "Documentación del producto",
}

EJEMPLO = [
    {"plataforma": "MyInvestor", "tipo": "Fondo de inversión", "nombre": "Fondo Global Equilibrado, clase A",
     "isin": "ES0000000001", "importe": 12000.0, "comision": 1.20},
    {"plataforma": "Trade Republic", "tipo": "ETF", "nombre": "iShares Core MSCI World UCITS ETF",
     "isin": "IE00B4L5Y983", "importe": 8500.0, "comision": 0.20},
    {"plataforma": "Interactive Brokers", "tipo": "Acción", "nombre": "Apple Inc.",
     "isin": "US0378331005", "importe": 2300.0, "comision": None},
    {"plataforma": "Binance", "tipo": "Criptomoneda", "nombre": "Bitcoin",
     "isin": "BTC", "importe": 3200.0, "comision": None},
    {"plataforma": "Tesoro Público", "tipo": "Bono o letra", "nombre": "Letras del Tesoro 12 meses",
     "isin": "", "importe": 5000.0, "comision": None},
]


def load() -> dict:
    """Lee la cuenta. Si el fichero no existe o está roto, empieza de cero (nunca se cae)."""
    data = json.loads(json.dumps(DEFAULT))
    try:
        saved = json.loads(PATH.read_text(encoding="utf-8"))
        for k in data:
            data[k].update(saved.get(k, {}))
    except (OSError, ValueError):
        pass
    return data


def save(data: dict) -> None:
    PATH.parent.mkdir(exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PATH)   # se escribe aparte y se cambia de golpe: nunca queda a medias


def new_position(**fields) -> dict:
    return {"id": uuid.uuid4().hex[:8], **fields}

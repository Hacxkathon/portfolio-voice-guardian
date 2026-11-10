from __future__ import annotations
import logging
import re
import unicodedata
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from db.database import get_connection, get_holdings_by_asset
from schemas import FinancialEventExtraction

log = logging.getLogger("impact.engine")

CENT = Decimal("0.01")
HUNDRED = Decimal(100)

# Eventos que, con revisión MiFID II sugerida, afectan a cualquier perfil (no solo al conservador)
PROFILE_SENSITIVE_EVENTS = {"strategy", "debt", "regulation", "guidance"}
COST_OBJECTIVE = re.compile(r"\bter\b|coste|comision|gastos")
INCOME_OBJECTIVE = re.compile(r"renta|ingreso|dividend|cupon")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _eur(value: Decimal) -> str:
    """Formato español: 2.250,00 €"""
    s = f"{_money(value):,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def _normalize(text: Optional[str]) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _annual_income(holding: dict, previous_value: Optional[str]) -> Optional[Decimal]:
    """
    Rentas anuales estimadas de la posición, deducidas de `previous_value`:
      "Dividendo 0.45 €/acción"            -> quantity * 0.45
      "Rentabilidad por dividendo 4.2%"    -> market_value_eur * 4.2 / 100
    Devuelve None si no se puede inferir (la BD no guarda dividendos por separado).
    """
    if not previous_value:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(%|€|eur)", previous_value, flags=re.IGNORECASE)
    if not m:
        return None
    number = _dec(m.group(1).replace(",", "."))
    if m.group(2) == "%":
        return _dec(holding["market_value_eur"]) * number / HUNDRED
    return _dec(holding["quantity"]) * number


# --------------------------------------------------------------------------- #
# Cálculo por tipo de evento
# --------------------------------------------------------------------------- #
def _fees_impact(h: dict, delta: Optional[float]) -> tuple[Optional[Decimal], str]:
    if delta is None:
        return None, "El documento no aporta una variación numérica de comisiones."
    exposure = _dec(h["market_value_eur"])
    # sobrecoste_anual = exposure_eur * (numeric_delta_pct / 100)
    impact = _money(exposure * _dec(delta) / HUNDRED)
    verb = "Sobrecoste" if impact >= 0 else "Ahorro"
    return impact, (
        f"El activo representa un {h['portfolio_weight_pct']:.2f}% de la cartera "
        f"({_eur(exposure)}). {verb} anual estimado por el cambio de {delta:+.2f}% "
        f"en comisiones: {_eur(abs(impact))}."
    )


def _dividend_impact(h: dict, event: FinancialEventExtraction) -> tuple[Optional[Decimal], str]:
    income = _annual_income(h, event.previous_value)
    if event.numeric_delta_pct is None or income is None:
        return None, (
            "No cuantificable: faltan las rentas anuales previas o la variación porcentual "
            "del dividendo. Requiere revisión manual."
        )
    # numeric_delta_pct es negativo en un recorte (-20.0) -> pérdida positiva
    loss = _money(income * (-_dec(event.numeric_delta_pct)) / HUNDRED)
    new_income = income - loss
    return loss, (
        f"Rentas anuales estimadas de {_eur(income)} pasan a {_eur(new_income)} "
        f"({event.numeric_delta_pct:+.2f}%): {'pérdida' if loss >= 0 else 'ganancia'} "
        f"de {_eur(abs(loss))} al año."
    )


# --------------------------------------------------------------------------- #
# Idoneidad MiFID II
# --------------------------------------------------------------------------- #
def _advisor_reasons(
    event: FinancialEventExtraction, h: dict, impact: Optional[Decimal]
) -> list[str]:
    reasons: list[str] = []
    profile = h["risk_tolerance"]
    objectives = _normalize(h.get("investment_objectives"))

    if event.mifid_review_suggested:
        if profile == "CONSERVATIVE":
            reasons.append("el cambio puede desvirtuar el perfil conservador del cliente")
        elif event.event_type in PROFILE_SENSITIVE_EVENTS:
            reasons.append(f"el cambio ({event.event_type}) puede alterar la idoneidad del perfil")

    if profile == "CONSERVATIVE" and event.materiality in {"HIGH", "CRITICAL"}:
        reasons.append(f"materialidad {event.materiality} en una cartera conservadora")

    if event.event_type == "fees" and impact and impact > 0 and COST_OBJECTIVE.search(objectives):
        reasons.append("el cliente tiene como objetivo la optimización de costes")

    if event.event_type == "dividends":
        if impact is None:
            reasons.append("impacto en rentas no cuantificable automáticamente")
        elif impact > 0 and INCOME_OBJECTIVE.search(objectives):
            reasons.append("el cliente busca rentas periódicas y estas se reducen")

    return reasons


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #
def calculate_event_impact(asset_id: str, event: FinancialEventExtraction) -> list[dict]:
    """
    Devuelve una fila por posición (cartera) expuesta al activo, con las columnas de
    EVENT_IMPACTS (salvo event_id, que se añade en save_event_impacts).
    """
    impacts: list[dict] = []

    for h in get_holdings_by_asset(asset_id):
        if event.event_type == "fees":
            impact, text = _fees_impact(h, event.numeric_delta_pct)
        elif event.event_type == "dividends":
            impact, text = _dividend_impact(h, event)
        else:
            impact, text = None, (
                f"Evento de tipo '{event.event_type}' sin impacto económico directo "
                "cuantificable; ver implicaciones cualitativas."
            )

        reasons = _advisor_reasons(event, h, impact)
        if reasons:
            text += " Acción del asesor: " + "; ".join(reasons) + "."

        impacts.append(
            {
                "client_id": h["client_id"],
                "portfolio_id": h["portfolio_id"],
                "exposure_eur": float(_money(_dec(h["market_value_eur"]))),
                "exposure_weight_pct": h["portfolio_weight_pct"],
                "estimated_annual_impact_eur": float(impact) if impact is not None else None,
                "impact_explanation": text,
                "requires_advisor_action": 1 if reasons else 0,
            }
        )

    log.info(
        "Impacto calculado: %d posiciones, %d requieren asesor.",
        len(impacts),
        sum(i["requires_advisor_action"] for i in impacts),
    )
    return impacts


def save_event_impacts(event_id: str, impacts: list[dict]) -> None:
    """Persiste en EVENT_IMPACTS (idempotente: reemplaza los impactos previos del evento).
    El evento debe existir ya en EVENTS por la clave foránea."""
    rows = [{**i, "event_id": event_id} for i in impacts]
    with closing(get_connection()) as conn, conn:
        conn.execute("DELETE FROM EVENT_IMPACTS WHERE event_id = ?", (event_id,))
        conn.executemany(
            """
            INSERT INTO EVENT_IMPACTS (
                event_id, client_id, portfolio_id, exposure_eur, exposure_weight_pct,
                estimated_annual_impact_eur, impact_explanation, requires_advisor_action
            ) VALUES (
                :event_id, :client_id, :portfolio_id, :exposure_eur, :exposure_weight_pct,
                :estimated_annual_impact_eur, :impact_explanation, :requires_advisor_action
            )
            """,
            rows,
        )

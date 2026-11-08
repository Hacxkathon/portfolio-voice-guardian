import json
import uuid
from typing import Dict, Any, List
from db.database import get_connection, get_holdings_by_asset, get_asset_by_id
from schemas import FinancialEventExtraction, EvidenceCitation


def run_end_to_end_pipeline(
    asset_id: str,
    new_doc_text: str = "",
    old_doc_text: str = "",
    is_simulation: bool = True
) -> None:
    """
    Orquestador principal (Pasos 1 al 11):
    1. Detección (Denisa)
    2. Análisis con Claude estructurado (Xoan)
    3. Persistencia del evento en SQLite (Jorge)
    4. Cálculo determinista de impacto patrimonial en Python (Vanesa)
    5. Redacción de briefings personalizados por nivel de inversor (Mariano)
    """
    conn = get_connection()
    cur = conn.cursor()
    event_id = f"EVT_{uuid.uuid4().hex[:8].upper()}"

    print("\n" + "=" * 60)
    print(f"[PIPELINE] 1. Disparador activado para el activo: {asset_id}")
    print("=" * 60)

    asset = get_asset_by_id(asset_id)
    if not asset:
        print(f"[ERROR] Activo {asset_id} no encontrado en la base de datos.")
        conn.close()
        return

    # ------------------------------------------------------------------
    # PASO A: Mock estructurado idéntico a la respuesta de Claude (Xoan)
    # ------------------------------------------------------------------
    print("[PIPELINE] 2. Procesando extracción de evento financiero...")
    extracted_event = FinancialEventExtraction(
        event_type="fees",
        materiality="HIGH",
        title="Incremento extraordinario de comisión fija de gestión (TER) del +0.45%",
        previous_value="1.20% anual",
        new_value="1.65% anual",
        numeric_delta_pct=0.45,
        financial_implications=(
            "Incremento recurrente directo del drag de costes del fondo. "
            "Afecta negativamente al cumplimiento del objetivo de rentabilidad neta esperada."
        ),
        mifid_review_suggested=True,
        limitations_and_uncertainty=(
            "El folleto no detalla si existen clases institucionales exentas de este incremento."
        ),
        evidence=[
            EvidenceCitation(
                page_number=4,
                exact_quote=(
                    "La comisión fija de gestión del compartimento se modifica "
                    "del 1,20% al 1,65% anual sobre patrimonio."
                ),
                section="Costes y Gastos Corrientes"
            )
        ]
    )

    # ------------------------------------------------------------------
    # PASO B: Guardar Evento en SQLite (Jorge)
    # ------------------------------------------------------------------
    cur.execute("""
        INSERT INTO EVENTS (
            event_id, asset_id, old_doc_id, new_doc_id, event_type, materiality,
            title, previous_value, new_value, numeric_delta_pct,
            financial_implications, mifid_review_suggested, evidence_json, is_simulated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        event_id,
        asset_id,
        "DOC_FD_V1",
        "DOC_FD_V2",
        extracted_event.event_type,
        extracted_event.materiality,
        extracted_event.title,
        extracted_event.previous_value,
        extracted_event.new_value,
        extracted_event.numeric_delta_pct,
        extracted_event.financial_implications,
        1 if extracted_event.mifid_review_suggested else 0,
        json.dumps([e.model_dump() for e in extracted_event.evidence], ensure_ascii=False),
        1 if is_simulation else 0
    ))
    conn.commit()
    print(f"[PIPELINE] 3. Evento {event_id} registrado: '{extracted_event.title}'")

    # ------------------------------------------------------------------
    # PASO C: Motor de Impacto Patrimonial (Vanesa)
    # Cálculo determinista en Python sobre posiciones reales
    # ------------------------------------------------------------------
    print("[PIPELINE] 4. Cruzando con carteras y calculando impacto patrimonial...")
    exposed_clients = get_holdings_by_asset(asset_id)

    if not exposed_clients:
        print("[PIPELINE] Ningún cliente tiene exposición directa a este activo.")
        conn.close()
        return

    impact_records = []
    for client in exposed_clients:
        exposure = client["market_value_eur"]
        weight = client["portfolio_weight_pct"]
        delta_pct = extracted_event.numeric_delta_pct or 0.0
        delta_decimal = delta_pct / 100.0

        # Cálculo exacto de sobrecoste anual en euros
        annual_impact_eur = round(exposure * delta_decimal, 2)

        explanation = (
            f"Exposición de {exposure:,.2f} € ({weight:.2f}% de la cartera). "
            f"La subida de {delta_pct:+.2f}% genera un sobrecoste anual estimado de {annual_impact_eur:,.2f} €."
        )

        cur.execute("""
            INSERT INTO EVENT_IMPACTS (
                event_id, client_id, portfolio_id, exposure_eur,
                exposure_weight_pct, estimated_annual_impact_eur,
                impact_explanation, requires_advisor_action
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            event_id,
            client["client_id"],
            client["portfolio_id"],
            exposure,
            weight,
            annual_impact_eur,
            explanation,
            1 if extracted_event.mifid_review_suggested else 0
        ))
        impact_records.append((client, annual_impact_eur))

    conn.commit()
    print(f"[PIPELINE] 5. {len(impact_records)} clientes impactados calculados y guardados.")

    # ------------------------------------------------------------------
    # PASO D: Generación de Guiones por Perfil (Mariano)
    # ------------------------------------------------------------------
    print("[PIPELINE] 6. Generando briefings adaptados por nivel de conocimiento...")
    for client, impact_eur in impact_records:
        level = client["knowledge_level"]
        name = client["client_name"]

        if level == "BEGINNER":
            investor_script = (
                f"Hola {name}. Te informamos de que el fondo {asset['name']} en tu cartera "
                f"ha actualizado sus gastos de gestión. Esto supondrá un coste aproximado de {impact_eur:.2f} euros al año. "
                f"Tu inversión sigue funcionando con normalidad, pero tu asesor revisará si conviene mantenerlo "
                f"o valorar otras alternativas sin comisiones extra."
            )
        else:  # PRIVATE_BANKING o EXPERIENCED
            investor_script = (
                f"Estimado/a {name}, le notificamos un ajuste de costes en {asset['name']} (ISIN: {asset['isin']}). "
                f"La gestora ha incrementado la comisión fija en {extracted_event.numeric_delta_pct:.2f} puntos porcentuales. "
                f"Sobre su posición actual de {client['market_value_eur']:,.2f} €, el arrastre anual estimado asciende a {impact_eur:,.2f} €. "
                f"Su asesor ha marcado la posición para evaluar la migración a una clase limpia institucional."
            )

        advisor_summary = (
            f"Alerta MiFID II para {name}: incremento de {extracted_event.numeric_delta_pct}% en costes. "
            f"Arrastre anual de {impact_eur:,.2f} €. Acción sugerida: evaluar idoneidad y rotación."
        )

        cur.execute("""
            INSERT INTO BRIEFINGS (
                event_id, client_id, advisor_technical_summary,
                investor_personalized_script, approval_status
            ) VALUES (?, ?, ?, ?, ?);
        """, (
            event_id,
            client["client_id"],
            advisor_summary,
            investor_script,
            "PENDING_REVIEW"
        ))

    conn.commit()
    conn.close()
    print("[PIPELINE] 7. Ciclo completado. Briefings listos en SQLite para el Dashboard.")


if __name__ == "__main__":
    run_end_to_end_pipeline(
        asset_id="AST_FD_01",
        new_doc_text="Modificación de folleto de fondo...",
        old_doc_text="Folleto anterior...",
        is_simulation=True
    )

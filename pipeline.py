import json
import uuid
from db.database import get_connection, get_holdings_by_asset, get_asset_by_id
from impact.engine import calculate_event_impact, save_event_impacts
from analysis.claude_analyzer import analyze_document_with_claude

def run_end_to_end_pipeline(
    asset_id: str,
    new_doc_text: str = "",
    old_doc_text: str = "",
    is_simulation: bool = True,
    new_doc_id: str = "DOC_FD_V2",
    old_doc_id: str | None = None,
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
    print("[PIPELINE] 2. Procesando extracción de evento con Claude...")
    extracted_event = analyze_document_with_claude(
        new_doc_path=new_doc_text,
        old_doc_path=old_doc_text
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
        old_doc_id,
        new_doc_id,
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
    
    # 1. Usar el motor de Vanesa para calcular el impacto inteligente
    impact_records_dicts = calculate_event_impact(asset_id, extracted_event)
    
    if not impact_records_dicts:
        print("[PIPELINE] Ningún cliente tiene exposición directa a este activo.")
        conn.close()
        return

    # 2. Usar la función de Vanesa para guardarlo en base de datos
    save_event_impacts(event_id, impact_records_dicts)
    
    print(f"[PIPELINE] 5. {len(impact_records_dicts)} clientes impactados calculados y guardados por el motor de Vanesa.")
    
    # ------------------------------------------------------------------
    # PASO D: Generación de Guiones por Perfil (Mariano)
    # ------------------------------------------------------------------
    print("[PIPELINE] 6. Generando briefings adaptados por nivel de conocimiento...")
    
    # 1. Recuperamos los datos de los clientes desde la BD (para tener sus nombres y niveles)
    exposed_clients = get_holdings_by_asset(asset_id)

    # 2. Iteramos sobre los impactos calculados por Vanesa en el Paso C
    for record in impact_records_dicts:
        # Extraemos el impacto en euros y buscamos los datos completos del cliente
        impact_eur = record["estimated_annual_impact_eur"]
        client = next(c for c in exposed_clients if c["client_id"] == record["client_id"])

        level = client["knowledge_level"]
        name = client["client_name"]

        # --- FIX: Formateo seguro para evitar que explote si el impacto es None ---
        impact_texto = f"un coste aproximado de {round(impact_eur)} euros al año" if impact_eur is not None else "implicaciones en el nivel de riesgo, sin coste directo"

        if level == "BEGINNER":
            investor_script = (
                f"Hola {name}. Te informamos de un cambio en el fondo {asset['name']} en tu cartera. "
                f"Esto supondrá {impact_texto}. "
                f"Tu inversión sigue funcionando con normalidad, pero tu asesor revisará si conviene mantenerlo."
            )
        else:  # PRIVATE_BANKING o EXPERIENCED
            investor_script = (
                f"Buenos días, {name}. Le notificamos un ajuste en {asset['name']}. "
                f"El análisis refleja {impact_texto}. "
                f"Su asesor ha marcado la posición para evaluar la idoneidad."
            )

        advisor_summary = (
            f"Alerta MiFID II para {name}. Impacto: {impact_texto}. "
            f"Acción sugerida: evaluar idoneidad y rotación."
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
    # Pasamos las rutas reales de los PDFs de prueba que tenías para el Escenario 3
    run_end_to_end_pipeline(
        asset_id="AST_FD_01",
        new_doc_text="data/demo_docs/escenario3_hecho_relevante_v2.pdf",
        old_doc_text="data/demo_docs/escenario3_folleto_v1.pdf",
        is_simulation=True
    )

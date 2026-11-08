import json
from database import DB_PATH, get_connection, init_db


def seed_data() -> None:
    init_db()
    conn = get_connection()
    cur = conn.cursor()

    # 1. CLIENTES (Perfil conservador novato vs Institucional/Privada)
    clients = [
        (
            "CLI_01",
            "Laura Gómez",
            "BEGINNER",
            "CONSERVATIVE",
            "Preservación de capital y generación de rentas periódicas estables sin sobresaltos.",
            "Carlos Ruiz (Asesor)",
        ),
        (
            "CLI_02",
            "Patrimonios Ibéricos SL (Martín Vega)",
            "PRIVATE_BANKING",
            "MODERATE",
            "Optimización de costes/TER, diversificación patrimonial y crecimiento compuesto a largo plazo.",
            "Carlos Ruiz (Asesor)",
        ),
    ]
    cur.executemany("INSERT OR REPLACE INTO CLIENTS VALUES (?, ?, ?, ?, ?, ?);", clients)

    # 2. CARTERAS
    portfolios = [
        ("PORT_01", "CLI_01", "Cartera Rentas Conservadora", 85000.0),
        ("PORT_02", "CLI_02", "Cartera Global Institucional", 1250000.0),
    ]
    cur.executemany("INSERT OR REPLACE INTO PORTFOLIOS VALUES (?, ?, ?, ?);", portfolios)

    # 3. ACTIVOS (Acción cotizada + Fondo UCITS)
    assets = [
        (
            "AST_EQ_01",
            "Iberia Telecom Infra SA",
            "IBTI.MC",
            "ES0140609019",
            "EQUITY",
            "Telecommunications",
        ),
        (
            "AST_FD_01",
            "EuroValue Core Equity UCITS Fund",
            "EVCEU",
            "LU0998877665",
            "UCITS_FUND",
            "Global Equity Fund",
        ),
    ]
    cur.executemany("INSERT OR REPLACE INTO ASSETS VALUES (?, ?, ?, ?, ?, ?);", assets)

    # 4. POSICIONES (HOLDINGS)
    holdings = [
        (1, "PORT_01", "AST_EQ_01", 1500, 15000.0, 17.65),
        (2, "PORT_01", "AST_FD_01", 350, 35000.0, 41.18),
        (3, "PORT_02", "AST_EQ_01", 18000, 180000.0, 14.40),
        (4, "PORT_02", "AST_FD_01", 5000, 500000.0, 40.00),
    ]
    cur.executemany("INSERT OR REPLACE INTO HOLDINGS VALUES (?, ?, ?, ?, ?, ?);", holdings)

    # 5. DOCUMENTOS PREVIOS Y NUEVOS (Ejemplo Escenario 3: Folleto KIID)
    documents = [
        (
            "DOC_FD_V1",
            "AST_FD_01",
            "Prospectus 2025-Q4",
            "2025-10-15",
            "https://funds.example.com/evceu/kiid_v1.pdf",
            "hash_evceu_v1_abc123",
            "Gestión y comisiones corrientes del fondo: 1.20% anual sobre el patrimonio neto.",
            0,
        ),
        (
            "DOC_FD_V2",
            "AST_FD_01",
            "Prospectus 2026-Q4 Update",
            "2026-11-01",
            "https://funds.example.com/evceu/kiid_v2.pdf",
            "hash_evceu_v2_xyz789",
            "Modificación de costes: La comisión de gestión fija se incrementa al 1.65% anual aplicable desde el 1 de enero.",
            1,
        ),
    ]
    cur.executemany("INSERT OR REPLACE INTO DOCUMENTS VALUES (?, ?, ?, ?, ?, ?, ?, ?);", documents)

    # 6. EVENTO EJEMPLO PROCESADO
    evidence = [
        {
            "page_number": 4,
            "exact_quote": "La comisión de gestión fija se incrementa al 1.65% anual aplicable desde el 1 de enero.",
            "section": "Gastos Corrientes y Comisiones",
        }
    ]
    events = [
        (
            "EVT_2026_001",
            "AST_FD_01",
            "DOC_FD_V1",
            "DOC_FD_V2",
            "fees",
            "HIGH",
            "Incremento del +0.45% en la comisión de gestión anual (TER)",
            "TER 1.20%",
            "TER 1.65%",
            0.45,
            "Incremento directo del coste recurrente del fondo. Reduce la rentabilidad neta anual compuesta.",
            1,
            json.dumps(evidence, ensure_ascii=False),
            1,
        )
    ]
    cur.executemany(
        """
        INSERT OR REPLACE INTO EVENTS (
            event_id, asset_id, old_doc_id, new_doc_id, event_type, materiality,
            title, previous_value, new_value, numeric_delta_pct,
            financial_implications, mifid_review_suggested, evidence_json, is_simulated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        events,
    )

    # 7. IMPACTOS FINANCIEROS CALCULADOS (Cálculo determinista en Python)
    impacts = [
        (
            1,
            "EVT_2026_001",
            "CLI_01",
            "PORT_01",
            35000.0,
            41.18,
            157.50,
            "El fondo representa un 41.18% de la cartera. El sobrecoste anual estimado es de 157,50 €.",
            1,
        ),
        (
            2,
            "EVT_2026_001",
            "CLI_02",
            "PORT_02",
            500000.0,
            40.00,
            2250.00,
            "Exposición de medio millón de euros (40% de la cartera). Sobrecoste anual directo de 2.250,00 €/año en comisiones.",
            1,
        ),
    ]
    cur.executemany(
        """
        INSERT OR REPLACE INTO EVENT_IMPACTS (
            impact_id, event_id, client_id, portfolio_id,
            exposure_eur, exposure_weight_pct, estimated_annual_impact_eur,
            impact_explanation, requires_advisor_action
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        impacts,
    )

    # 8. BRIEFINGS PREVIOS
    briefings = [
        (
            1,
            "EVT_2026_001",
            "CLI_01",
            "Alerta de costes: El fondo EuroValue incrementa su comisión en un 0.45%. En la cartera de Laura supone 157 €/año. Dado su perfil conservador y el 41% de peso, valorar alternativas con menor coste.",
            "Hola Laura, te contacto para informarte de un cambio en el fondo EuroValue de tu cartera. La entidad gestora ha subido los gastos anuales de mantenimiento. En tu inversión supone unos 157 euros al año. Tu capital sigue seguro, pero he preparado un par de opciones similares con menores comisiones para comentarlas en nuestra próxima llamada.",
            None,
            "APPROVED",
        ),
        (
            2,
            "EVT_2026_001",
            "CLI_02",
            "Impacto material de comisiones: Subida de 45 bps en TER de EuroValue Core Equity. Exposición de 500k€ genera un arrastre negativo de 2.250€ anuales de alfa. Revisión de clase de acción (clase limpia/institucional) o rotación a ETF equivalente requerida.",
            "Estimado Martín, hemos registrado una modificación en el folleto del fondo EuroValue Core Equity, incrementando la comisión de gestión en 45 puntos básicos hasta el 1,65% anual. Con una posición actual de 500.000 €, este ajuste representa un sobrecoste anual de 2.250 €. Recomendamos revisar la migración a una clase institucional o reemplazo por un vehículo indexado antes del cierre fiscal.",
            None,
            "PENDING_REVIEW",
        ),
    ]
    cur.executemany(
        """
        INSERT OR REPLACE INTO BRIEFINGS (
            briefing_id, event_id, client_id, advisor_technical_summary,
            investor_personalized_script, audio_file_path, approval_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        briefings,
    )

    conn.commit()
    conn.close()
    print(f"[SEED] Datos semilla cargados con éxito en {DB_PATH}")


if __name__ == "__main__":
    seed_data()

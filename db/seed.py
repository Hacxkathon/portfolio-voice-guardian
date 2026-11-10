import sys
from pathlib import Path

# Permite ejecutar el script tanto desde la raíz como desde la carpeta db/
try:
    from database import DB_PATH, get_connection, init_db
except ImportError:
    from db.database import DB_PATH, get_connection, init_db


def seed_data() -> None:
    init_db()
    conn = get_connection()
    cur = conn.cursor()

    # 0. LIMPIEZA INICIAL: vacía las tablas para arrancar en un estado limpio
    tablas = [
        "BRIEFINGS",
        "EVENT_IMPACTS",
        "EVENTS",
        "DOCUMENTS",
        "HOLDINGS",
        "PORTFOLIOS",
        "CLIENTS",
        "ASSETS",
    ]
    for tabla in tablas:
        cur.execute(f"DELETE FROM {tabla};")

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

    # 5. DOCUMENTO HISTÓRICO BASE (Solo V1: V2 lo creará el trigger de Denisa)
    documents = [
        (
            "DOC_FD_V1",                                  # doc_id
            "AST_FD_01",                                  # asset_id
            "PROSPECTUS",                                 # doc_type (Añadido)
            "V1",                                         # version
            "2025-10-15",                                 # publication_date
            "https://funds.example.com/evceu/kiid_v1.pdf",# raw_url_or_path
            "hash_evceu_v1_abc123",                       # content_hash
            "Gestión y comisiones corrientes del fondo: 1.20% anual sobre el patrimonio neto.", # extracted_text
            "PROCESSED",                                  # processing_status
        ),
    ]
    
    # ¡Añadimos los nombres exactos de las columnas para evitar errores!
    cur.executemany("""
        INSERT OR REPLACE INTO DOCUMENTS 
        (doc_id, asset_id, doc_type, version, publication_date, raw_url_or_path, content_hash, extracted_text, processing_status) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, documents)


    conn.commit()
    conn.close()
    print(f"[SEED] Base de datos reinicializada en estado base en {DB_PATH}")


if __name__ == "__main__":
    seed_data()



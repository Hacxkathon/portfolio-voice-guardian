import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ruta absoluta para que funcione independientemente de desde dónde se ejecute el script
DB_PATH = Path(__file__).resolve().parent / "guardian.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS CLIENTS (
        client_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        knowledge_level TEXT CHECK(knowledge_level IN ('BEGINNER', 'EXPERIENCED', 'PRIVATE_BANKING')) NOT NULL,
        risk_tolerance TEXT CHECK(risk_tolerance IN ('CONSERVATIVE', 'MODERATE', 'AGGRESSIVE')) NOT NULL,
        investment_objectives TEXT NOT NULL,
        advisor_name TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS PORTFOLIOS (
        portfolio_id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        name TEXT NOT NULL,
        total_value_eur REAL NOT NULL,
        FOREIGN KEY (client_id) REFERENCES CLIENTS(client_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ASSETS (
        asset_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        ticker TEXT,
        isin TEXT UNIQUE NOT NULL,
        asset_type TEXT CHECK(asset_type IN ('EQUITY', 'UCITS_FUND')) NOT NULL,
        sector TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS HOLDINGS (
        holding_id INTEGER PRIMARY KEY AUTOINCREMENT,
        portfolio_id TEXT NOT NULL,
        asset_id TEXT NOT NULL,
        quantity REAL NOT NULL,
        market_value_eur REAL NOT NULL,
        portfolio_weight_pct REAL NOT NULL,
        FOREIGN KEY (portfolio_id) REFERENCES PORTFOLIOS(portfolio_id) ON DELETE CASCADE,
        FOREIGN KEY (asset_id) REFERENCES ASSETS(asset_id)
    );

    CREATE TABLE IF NOT EXISTS DOCUMENTS (
        doc_id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL,
        version_label TEXT NOT NULL,
        publication_date TEXT NOT NULL,
        source_url TEXT,
        content_hash TEXT UNIQUE NOT NULL,
        extracted_text TEXT NOT NULL,
        is_simulated INTEGER DEFAULT 0,
        FOREIGN KEY (asset_id) REFERENCES ASSETS(asset_id)
    );

    CREATE TABLE IF NOT EXISTS EVENTS (
        event_id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL,
        old_doc_id TEXT,
        new_doc_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        materiality TEXT NOT NULL,
        title TEXT NOT NULL,
        previous_value TEXT,
        new_value TEXT,
        numeric_delta_pct REAL,
        financial_implications TEXT NOT NULL,
        mifid_review_suggested INTEGER DEFAULT 0,
        evidence_json TEXT NOT NULL,
        is_simulated INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (asset_id) REFERENCES ASSETS(asset_id),
        FOREIGN KEY (old_doc_id) REFERENCES DOCUMENTS(doc_id),
        FOREIGN KEY (new_doc_id) REFERENCES DOCUMENTS(doc_id)
    );

    CREATE TABLE IF NOT EXISTS EVENT_IMPACTS (
        impact_id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        client_id TEXT NOT NULL,
        portfolio_id TEXT NOT NULL,
        exposure_eur REAL NOT NULL,
        exposure_weight_pct REAL NOT NULL,
        estimated_annual_impact_eur REAL,
        impact_explanation TEXT NOT NULL,
        requires_advisor_action INTEGER DEFAULT 1,
        FOREIGN KEY (event_id) REFERENCES EVENTS(event_id) ON DELETE CASCADE,
        FOREIGN KEY (client_id) REFERENCES CLIENTS(client_id),
        FOREIGN KEY (portfolio_id) REFERENCES PORTFOLIOS(portfolio_id)
    );

    CREATE TABLE IF NOT EXISTS BRIEFINGS (
        briefing_id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        client_id TEXT NOT NULL,
        advisor_technical_summary TEXT NOT NULL,
        investor_personalized_script TEXT NOT NULL,
        audio_file_path TEXT,
        approval_status TEXT CHECK(approval_status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')) DEFAULT 'PENDING_REVIEW',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (event_id) REFERENCES EVENTS(event_id) ON DELETE CASCADE,
        FOREIGN KEY (client_id) REFERENCES CLIENTS(client_id)
    );
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Esquema inicializado en: {DB_PATH}")


# --- Métodos de consulta para Vanesa, Mariano y Xoan ---

def get_holdings_by_asset(asset_id: str) -> List[Dict[str, Any]]:
    """Devuelve qué carteras y clientes tienen exposición a un activo."""
    conn = get_connection()
    query = """
    SELECT 
        c.client_id,
        c.name AS client_name,
        c.knowledge_level,
        c.risk_tolerance,
        c.investment_objectives,
        p.portfolio_id,
        p.name AS portfolio_name,
        p.total_value_eur,
        h.quantity,
        h.market_value_eur,
        h.portfolio_weight_pct
    FROM HOLDINGS h
    JOIN PORTFOLIOS p ON h.portfolio_id = p.portfolio_id
    JOIN CLIENTS c ON p.client_id = c.client_id
    WHERE h.asset_id = ?;
    """
    rows = conn.execute(query, (asset_id,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_clients() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM CLIENTS;").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_asset_by_id(asset_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM ASSETS WHERE asset_id = ?;", (asset_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


if __name__ == "__main__":
    init_db()

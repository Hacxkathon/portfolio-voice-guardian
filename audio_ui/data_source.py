"""
Capa de datos de la interfaz de Portfolio Voice Guardian.

La interfaz SOLO habla con este fichero. Si cambia el esquema de la base de
datos de Jorge o el formato de salida del pipeline, se toca aquí y en ningún
otro sitio.

Dos fuentes:
  * DemoSource   -> los datos del documento de avances (Laura y Martín).
                    Funciona siempre, sin base de datos: es el seguro del demo.
  * SQLiteSource -> lee la base de datos real del proyecto. Si algo falla
                    (ruta, tabla o columna distinta) devuelve None y la app
                    cae en DemoSource avisando en la barra lateral.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------
# Modelo de datos que consume la interfaz
# --------------------------------------------------------------------------

@dataclass
class Holding:
    fund_name: str
    isin: str
    amount_eur: float


@dataclass
class Client:
    client_id: str
    name: str
    profile: str            # "principiante" | "profesional"
    profile_label: str      # texto corto para mostrar
    total_eur: float
    holdings: list[Holding] = field(default_factory=list)


@dataclass
class Evidence:
    page: int
    quote: str
    document: str


@dataclass
class Alert:
    alert_id: str
    detected_at: str
    event_type: str         # "subida_comision", "recorte_dividendo", ...
    fund_name: str
    isin: str
    title: str
    old_value: float        # en %
    new_value: float        # en %
    evidence: Evidence

    @property
    def delta_pp(self) -> float:
        """Variación en puntos porcentuales (1,20% -> 1,65% = 0,45 pp)."""
        return round(self.new_value - self.old_value, 4)


@dataclass
class Impact:
    alert_id: str
    client_id: str
    exposure_eur: float
    annual_cost_eur: float
    advisor_summary: str    # texto escrito para el asesor
    client_message: str     # texto que se convierte en voz para el cliente


@dataclass
class Snapshot:
    clients: dict[str, Client]
    alerts: list[Alert]
    impacts: list[Impact]
    source_name: str
    warning: Optional[str] = None

    def impacts_for(self, alert_id: str) -> list[Impact]:
        return [i for i in self.impacts if i.alert_id == alert_id]

    def impacts_of_client(self, client_id: str) -> list[Impact]:
        return [i for i in self.impacts if i.client_id == client_id]

    def alert(self, alert_id: str) -> Alert:
        return next(a for a in self.alerts if a.alert_id == alert_id)


# --------------------------------------------------------------------------
# Fuente 1: datos de demostración (documento de avances de Jorge)
# --------------------------------------------------------------------------

# En el demo la alerta se "detecta" unos minutos antes de arrancar la app,
# para que la pantalla diga "Detectado hoy, HH:MM" como en producción.
_DEMO_DETECTED_AT = (datetime.now() - timedelta(minutes=23)).strftime("%Y-%m-%d %H:%M")


def demo_snapshot() -> Snapshot:
    laura = Client(
        client_id="laura",
        name="Laura Gómez",
        profile="principiante",
        profile_label="Principiante, perfil conservador",
        total_eur=85_000,
        holdings=[
            Holding("Fondo Global Equilibrado, clase A", "ES0000000001", 35_000),
            Holding("Fondo Renta Fija Corto Plazo", "ES0000000002", 30_000),
            Holding("Cuenta remunerada", "—", 20_000),
        ],
    )
    martin = Client(
        client_id="martin",
        name="Martín Vega",
        profile="profesional",
        profile_label="Banca privada, perfil moderado",
        total_eur=1_250_000,
        holdings=[
            Holding("Fondo Global Equilibrado, clase A", "ES0000000001", 500_000),
            Holding("Fondo Renta Variable Europa, clase I", "ES0000000003", 450_000),
            Holding("Letras del Tesoro 12M", "ES0L00000000", 300_000),
        ],
    )

    alert = Alert(
        alert_id="ALT-001",
        detected_at=_DEMO_DETECTED_AT,
        event_type="subida_comision",
        fund_name="Fondo Global Equilibrado, clase A",
        isin="ES0000000001",
        title="Sube la comisión de gestión",
        old_value=1.20,
        new_value=1.65,
        evidence=Evidence(
            page=14,
            quote=("La comisión de gestión sobre patrimonio de la clase A "
                   "pasará del 1,20% al 1,65% anual."),
            document="Folleto actualizado del fondo (60 págs.)",
        ),
    )

    impacts = [
        Impact(
            alert_id="ALT-001",
            client_id="laura",
            exposure_eur=35_000,
            annual_cost_eur=round(35_000 * 0.45 / 100, 2),
            advisor_summary=(
                "Laura tiene 35.000 € en el Fondo Global Equilibrado (clase A). "
                "La gestora sube la comisión de gestión del 1,20% al 1,65% "
                "(+0,45 puntos). Coste adicional estimado: 157,50 € al año. "
                "Perfil principiante y conservador: conviene una llamada "
                "tranquilizadora, sin tecnicismos, y ofrecerle revisar "
                "alternativas con comisiones más bajas."
            ),
            client_message=(
                "Hola Laura, te habla tu asesor. Te llamo por un cambio en uno de "
                "tus fondos, el Fondo Global Equilibrado. Lo primero: tu dinero "
                "está seguro y no tienes que hacer nada hoy. La gestora ha "
                "decidido subir lo que cobra cada año por gestionar el fondo. "
                "Para ti, eso supone unos 157 euros más al año. Me gustaría "
                "comentarlo contigo con calma y enseñarte un par de opciones "
                "más baratas. Cuando te venga bien, me llamas y lo vemos juntos."
            ),
        ),
        Impact(
            alert_id="ALT-001",
            client_id="martin",
            exposure_eur=500_000,
            annual_cost_eur=round(500_000 * 0.45 / 100, 2),
            advisor_summary=(
                "Martín mantiene 500.000 € en el Fondo Global Equilibrado, "
                "clase A (ISIN ES0000000001). La comisión de gestión pasa del "
                "1,20% al 1,65% (+45 pb), lo que supone un arrastre de "
                "rentabilidad de 2.250 € anuales. Con este volumen accede a "
                "clases institucionales: proponer traspaso a clase I, sin "
                "coste fiscal por ser traspaso entre fondos."
            ),
            client_message=(
                "Buenas tardes, Martín. Le informo de una modificación en el "
                "Fondo Global Equilibrado, clase A. La gestora eleva la comisión "
                "de gestión del uno coma veinte al uno coma sesenta y cinco por "
                "ciento, cuarenta y cinco puntos básicos más. Sobre su posición "
                "de quinientos mil euros, el arrastre de rentabilidad es de "
                "dos mil doscientos cincuenta euros al año. Por volumen, puede "
                "acceder a la clase institucional del mismo fondo, con una "
                "comisión inferior y mediante traspaso sin impacto fiscal. "
                "Le envío el detalle por escrito para que lo valore."
            ),
        ),
    ]

    return Snapshot(
        clients={c.client_id: c for c in (laura, martin)},
        alerts=[alert],
        impacts=impacts,
        source_name="Datos de demostración",
    )


# --------------------------------------------------------------------------
# Fuente 2: base de datos SQLite real del proyecto (db/guardian.db de Jorge)
# --------------------------------------------------------------------------
# Tablas de Jorge (db/database.py): CLIENTS, PORTFOLIOS, ASSETS, HOLDINGS,
# DOCUMENTS, EVENTS, EVENT_IMPACTS y BRIEFINGS. El pipeline del equipo escribe
# la alerta en EVENTS, el coste en EVENT_IMPACTS y los dos textos en BRIEFINGS.

SQL = {
    # Un cliente puede tener varias carteras: se suma su valor total.
    "clients": """
        SELECT c.client_id                         AS client_id,
               c.name                              AS name,
               c.knowledge_level                   AS level,
               c.risk_tolerance                    AS risk,
               COALESCE(SUM(p.total_value_eur), 0) AS total_eur
        FROM CLIENTS c
        LEFT JOIN PORTFOLIOS p ON p.client_id = c.client_id
        GROUP BY c.client_id
    """,
    "holdings": """
        SELECT p.client_id         AS client_id,
               a.name              AS fund_name,
               a.isin              AS isin,
               h.market_value_eur  AS amount_eur
        FROM HOLDINGS h
        JOIN PORTFOLIOS p ON p.portfolio_id = h.portfolio_id
        JOIN ASSETS a     ON a.asset_id     = h.asset_id
        ORDER BY h.market_value_eur DESC
    """,
    "alerts": """
        SELECT e.event_id          AS alert_id,
               e.created_at        AS detected_at,
               e.event_type        AS event_type,
               a.name              AS fund_name,
               a.isin              AS isin,
               e.title             AS title,
               e.previous_value    AS previous_value,
               e.new_value         AS new_value,
               e.numeric_delta_pct AS delta_pct,
               e.evidence_json     AS evidence_json,
               e.is_simulated      AS is_simulated,
               COALESCE(d.doc_type, 'Documento de la gestora') AS document
        FROM EVENTS e
        JOIN ASSETS a         ON a.asset_id = e.asset_id
        LEFT JOIN DOCUMENTS d ON d.doc_id   = e.new_doc_id
        ORDER BY e.created_at DESC
    """,
    # Coste en € (EVENT_IMPACTS) + los dos textos del pipeline (BRIEFINGS).
    "impacts": """
        SELECT i.event_id                                   AS alert_id,
               i.client_id                                  AS client_id,
               i.exposure_eur                               AS exposure_eur,
               COALESCE(i.estimated_annual_impact_eur, 0)   AS annual_cost_eur,
               COALESCE(b.advisor_technical_summary, i.impact_explanation) AS advisor_summary,
               COALESCE(b.investor_personalized_script, '') AS client_message
        FROM EVENT_IMPACTS i
        LEFT JOIN BRIEFINGS b ON b.event_id = i.event_id AND b.client_id = i.client_id
    """,
}

# Niveles de Jorge -> tono de voz y etiqueta que muestra la pantalla
LEVEL_TO_PROFILE = {"BEGINNER": "principiante", "EXPERIENCED": "profesional", "PRIVATE_BANKING": "profesional"}
LEVEL_LABELS = {"BEGINNER": "Principiante", "EXPERIENCED": "Inversor con experiencia", "PRIVATE_BANKING": "Banca privada"}
RISK_LABELS = {"CONSERVATIVE": "perfil conservador", "MODERATE": "perfil moderado", "AGGRESSIVE": "perfil agresivo"}
DOC_LABELS = {"PROSPECTUS": "Folleto del fondo", "KIID": "Documento de datos fundamentales",
              "EARNINGS_REPORT": "Informe de resultados"}

# Si no se indica ruta y la app está dentro del repo (audio_ui/), se usa db/guardian.db
REPO_DB = Path(__file__).resolve().parent.parent / "db" / "guardian.db"


def _local_time(raw) -> str:
    """SQLite guarda CURRENT_TIMESTAMP en UTC ('2026-11-11 22:20:05'): lo pasamos a la hora local."""
    text = str(raw or "")[:19]
    try:
        utc = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        return utc.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text[:16]


def _first_number(text: Optional[str]) -> Optional[float]:
    """'1.20% anual' -> 1.2 ; '1,65 %' -> 1.65 ; None si no hay número."""
    if not text:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(text))
    return float(m.group(0).replace(",", ".")) if m else None


def sqlite_snapshot(db_path: str) -> Snapshot:
    """Lee la base de datos real. Lanza excepción si algo no cuadra."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"No existe la base de datos: {db_path}")

    con = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = {k: [dict(r) for r in con.execute(q)] for k, q in SQL.items()}
    finally:
        con.close()

    clients: dict[str, Client] = {}
    for r in rows["clients"]:
        cid = str(r["client_id"])
        level = str(r["level"])
        label = LEVEL_LABELS.get(level, level.title())
        if r.get("risk") in RISK_LABELS:
            label += ", " + RISK_LABELS[r["risk"]]
        clients[cid] = Client(client_id=cid, name=r["name"], profile=LEVEL_TO_PROFILE.get(level, "principiante"),
                              profile_label=label, total_eur=float(r["total_eur"]))
    for r in rows["holdings"]:
        cid = str(r["client_id"])
        if cid in clients:
            clients[cid].holdings.append(Holding(r["fund_name"], r["isin"], float(r["amount_eur"])))

    alerts = []
    for r in rows["alerts"]:
        try:
            ev = (json.loads(r["evidence_json"] or "[]") or [{}])[0]
        except (ValueError, TypeError):
            ev = {}
        old, new = _first_number(r["previous_value"]), _first_number(r["new_value"])
        if old is None or new is None:            # sin cifras: al menos la variación
            old, new = 0.0, float(r["delta_pct"] or 0)
        doc = DOC_LABELS.get(r["document"], r["document"])
        if ev.get("section"):
            doc = f"{doc} · {ev['section']}"
        title = r["title"]
        if r.get("is_simulated"):
            title = f"{title} (simulación)"
        alerts.append(Alert(
            alert_id=str(r["alert_id"]),
            detected_at=_local_time(r["detected_at"]),
            event_type=r["event_type"],
            fund_name=r["fund_name"],
            isin=r["isin"],
            title=title,
            old_value=old,
            new_value=new,
            evidence=Evidence(int(ev.get("page_number") or 0), ev.get("exact_quote") or "(sin cita)", doc),
        ))

    impacts = [
        Impact(alert_id=str(r["alert_id"]), client_id=str(r["client_id"]), exposure_eur=float(r["exposure_eur"]),
               annual_cost_eur=float(r["annual_cost_eur"]), advisor_summary=r["advisor_summary"],
               client_message=r["client_message"])
        for r in rows["impacts"]
    ]
    if not alerts:
        raise ValueError("la base de datos todavía no tiene alertas (falta ejecutar el pipeline)")

    return Snapshot(clients, alerts, impacts, source_name=f"SQLite · {os.path.basename(db_path)}")


def load_snapshot(db_path: Optional[str]) -> Snapshot:
    """Intenta la base de datos real; si falla, datos de demo + aviso."""
    if not db_path and REPO_DB.exists():      # dentro del repo del equipo: se conecta solo
        db_path = str(REPO_DB)
    if db_path:
        try:
            return sqlite_snapshot(db_path)
        except Exception as exc:  # noqa: BLE001 — el demo no puede caerse
            snap = demo_snapshot()
            snap.warning = f"No se pudo leer la base de datos ({exc}). Mostrando datos de demostración."
            return snap
    return demo_snapshot()

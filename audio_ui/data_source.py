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

import os
import sqlite3
from datetime import datetime, timedelta
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
# Fuente 2: base de datos SQLite real del proyecto
# --------------------------------------------------------------------------
# AJUSTAR AQUÍ si los nombres de tablas/columnas de Jorge son distintos.
# Cada consulta debe devolver EXACTAMENTE las columnas con estos alias.

SQL = {
    "clients": """
        SELECT id            AS client_id,
               nombre        AS name,
               perfil        AS profile,
               patrimonio    AS total_eur
        FROM clientes
    """,
    "holdings": """
        SELECT c.cliente_id  AS client_id,
               f.nombre      AS fund_name,
               f.isin        AS isin,
               p.importe     AS amount_eur
        FROM posiciones p
        JOIN carteras c ON c.id = p.cartera_id
        JOIN fondos   f ON f.id = p.fondo_id
    """,
    "alerts": """
        SELECT id            AS alert_id,
               fecha         AS detected_at,
               tipo_evento   AS event_type,
               fondo_nombre  AS fund_name,
               isin          AS isin,
               titulo        AS title,
               valor_anterior AS old_value,
               valor_nuevo   AS new_value,
               pagina        AS page,
               cita_textual  AS quote,
               documento     AS document
        FROM alertas
        ORDER BY fecha DESC
    """,
    "impacts": """
        SELECT alerta_id     AS alert_id,
               cliente_id    AS client_id,
               exposicion    AS exposure_eur,
               coste_anual   AS annual_cost_eur,
               resumen_asesor AS advisor_summary,
               mensaje_cliente AS client_message
        FROM impactos
    """,
}

PROFILE_LABELS = {
    "principiante": "Ahorrador principiante · perfil conservador",
    "profesional": "Inversor profesional",
}


def sqlite_snapshot(db_path: str) -> Snapshot:
    """Lee la base de datos real. Lanza excepción si algo no cuadra."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"No existe la base de datos: {db_path}")

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = {k: [dict(r) for r in con.execute(q)] for k, q in SQL.items()}
    finally:
        con.close()

    clients: dict[str, Client] = {}
    for r in rows["clients"]:
        cid = str(r["client_id"])
        profile = str(r["profile"]).lower()
        clients[cid] = Client(
            client_id=cid,
            name=r["name"],
            profile=profile,
            profile_label=PROFILE_LABELS.get(profile, profile.capitalize()),
            total_eur=float(r["total_eur"]),
        )
    for r in rows["holdings"]:
        cid = str(r["client_id"])
        if cid in clients:
            clients[cid].holdings.append(
                Holding(r["fund_name"], r["isin"], float(r["amount_eur"])))

    alerts = [
        Alert(
            alert_id=str(r["alert_id"]),
            detected_at=str(r["detected_at"]),
            event_type=r["event_type"],
            fund_name=r["fund_name"],
            isin=r["isin"],
            title=r["title"],
            old_value=float(r["old_value"]),
            new_value=float(r["new_value"]),
            evidence=Evidence(int(r["page"]), r["quote"], r["document"]),
        )
        for r in rows["alerts"]
    ]
    impacts = [
        Impact(
            alert_id=str(r["alert_id"]),
            client_id=str(r["client_id"]),
            exposure_eur=float(r["exposure_eur"]),
            annual_cost_eur=float(r["annual_cost_eur"]),
            advisor_summary=r["advisor_summary"],
            client_message=r["client_message"],
        )
        for r in rows["impacts"]
    ]
    if not alerts:
        raise ValueError("La base de datos no tiene alertas todavía.")

    return Snapshot(clients, alerts, impacts, source_name=f"SQLite · {os.path.basename(db_path)}")


def load_snapshot(db_path: Optional[str]) -> Snapshot:
    """Intenta la base de datos real; si falla, datos de demo + aviso."""
    if db_path:
        try:
            return sqlite_snapshot(db_path)
        except Exception as exc:  # noqa: BLE001 — el demo no puede caerse
            snap = demo_snapshot()
            snap.warning = f"No se pudo leer la base de datos ({exc}). Mostrando datos de demostración."
            return snap
    return demo_snapshot()

"""
Configuración central de la Fase de Producción (monitoring_produccion).

Rutas y parámetros sobreescribibles con variables de entorno para no tocar
el código durante el hackathon (por ejemplo PROD_OUTPUT_DIR).
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

DIR_MODULO: Final[Path] = Path(__file__).resolve().parent
RAIZ_PROYECTO: Final[Path] = DIR_MODULO.parent


def _ruta_env(nombre: str, por_defecto: Path) -> Path:
    """Lee una ruta de una variable de entorno o devuelve el valor por defecto."""
    valor = os.getenv(nombre)
    return Path(valor).expanduser().resolve() if valor else por_defecto


def _entero_env(nombre: str, por_defecto: int) -> int:
    """Lee un entero de una variable de entorno; si es inválido usa el valor por defecto."""
    try:
        return int(os.getenv(nombre, str(por_defecto)))
    except ValueError:
        return por_defecto


# --- Rutas -------------------------------------------------------------------
RUTA_GUARDIAN_DB: Final[Path] = _ruta_env("GUARDIAN_DB_PATH", RAIZ_PROYECTO / "db" / "guardian.db")
RUTA_MONITOR_DB: Final[Path] = _ruta_env("MONITOR_DB_PATH", DIR_MODULO / "monitor.db")
# Carpeta de salida: la que vigila el escáner de la demo. Configurable para
# no contaminar la demo en directo con documentos reales.
DIR_DEMO_DOCS: Final[Path] = _ruta_env("PROD_OUTPUT_DIR", RAIZ_PROYECTO / "data" / "demo_docs")
# Carpeta temporal en el MISMO disco que demo_docs (necesario para os.replace atómico).
DIR_INCOMING: Final[Path] = _ruta_env("PROD_INCOMING_DIR", RAIZ_PROYECTO / "data" / ".incoming")
RUTA_ALIASES: Final[Path] = DIR_MODULO / "aliases.json"
DIR_LOGS: Final[Path] = DIR_MODULO / "logs"
DIR_RECHAZADOS: Final[Path] = _ruta_env("PROD_REJECTED_DIR", RAIZ_PROYECTO / "data" / ".rechazados")
# Si es True, los PDFs rechazados se archivan en DIR_RECHAZADOS (útil para calibrar); si no, se borran.
CONSERVAR_RECHAZADOS: Final[bool] = os.getenv("PROD_CONSERVAR_RECHAZADOS", "1") != "0"

# --- Canales RSS de la CNMV --------------------------------------------------
FEEDS_CNMV: Final[dict[str, str]] = {
    "IIP": "https://www.cnmv.es/portal/informacion-privilegiada/RSS.asmx/GetNoticiasCNMV",   # Información privilegiada
    "OIR": "https://www.cnmv.es/portal/Otra-Informacion-Relevante/RSS.asmx/GetNoticiasCNMV",  # Otra información relevante
    "HR": "https://www.cnmv.es/portal/hr/hechosrelevantes.asmx/GetNoticiasCNMV",              # Hechos relevantes de IIC
}

# --- HTTP --------------------------------------------------------------------
USER_AGENT: Final[str] = os.getenv(
    "PROD_USER_AGENT",
    "Mozilla/5.0 (compatible; PortfolioVoiceGuardian/0.1; hackathon)",
)
TIMEOUT_HTTP: Final[tuple[float, float]] = (5.0, 20.0)  # (conexión, lectura) en segundos
PAUSA_ENTRE_PETICIONES_S: Final[float] = 1.0            # cortesía con el servidor

# --- Polling y filtros -------------------------------------------------------
INTERVALO_POLLING_S: Final[int] = _entero_env("PROD_INTERVALO_S", 900)  # ttl del feed = 15 min
MAX_ANTIGUEDAD_HORAS: Final[int] = _entero_env("PROD_MAX_ANTIGUEDAD_H", 72)
UMBRAL_FUZZY: Final[float] = 0.90
# --- Descargas ---------------------------------------------------------------
MAX_INTENTOS_DESCARGA: Final[int] = 3                 # tras 3 fallos de red pasa a ERROR
MAX_BYTES_PDF: Final[int] = 50 * 1024 * 1024          # 50 MB: protección ante respuestas absurdas
MAX_DESCARGAS_POR_CICLO: Final[int] = 20

# --- Filtro heurístico -------------------------------------------------------
UMBRAL_APROBACION_FILTRO: Final[int] = _entero_env("PROD_UMBRAL_FILTRO", 3)
MAX_PAGINAS_FILTRO: Final[int] = 40     # páginas leídas como máximo (tus PDFs suelen tener < 20)
MIN_CHARS_TEXTO: Final[int] = 50        # por debajo se considera PDF sin capa de texto
MAX_ITEMS_FILTRO: Final[int] = 50

# Categorías conocidas como ruido (sobre texto normalizado: MAYÚSCULAS, sin tildes).
# Se ampliará con la tabla categorias_vistas tras observar el feed real.
PATRONES_CATEGORIA_RUIDO: Final[tuple[str, ...]] = (
    r"NUMERO DE ACCIONISTAS Y PATRIMONIO",
    r"PATRIMONIO ALCANZADO",
    r"TOTAL NUMERO DE DERECHOS DE VOTO",
)


def configurar_logging(nivel: int = logging.INFO) -> None:
    """Configura el logging raíz (consola + fichero rotativo). Idempotente."""
    raiz = logging.getLogger()
    if raiz.handlers:
        return
    DIR_LOGS.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    fichero = RotatingFileHandler(
        DIR_LOGS / "monitoring_produccion.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fichero.setFormatter(formato)
    raiz.setLevel(nivel)
    raiz.addHandler(consola)
    raiz.addHandler(fichero)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
# Portfolio Voice Guardian

---

## Visión del Proyecto

Los cambios en folletos regulados (KIID/PRIIPs) o hechos relevantes suelen tardar semanas en procesarse. Esto genera **sobrecostes imprevistos para los inversores** y **riesgos de incumplimiento sobrevenido de idoneidad (MiFID II)**.

**Portfolio Voice Guardian** es una plataforma que:
1. **Monitoriza** fuentes regulatorias (canales RSS de la CNMV).
2. **Cuantifica matemáticamente** el impacto patrimonial directo sobre las carteras.
3. **Genera briefings** adaptados al perfil del inversor (texto para el asesor y audio sintetizado con IA para el cliente).

---

## Arquitectura del Sistema

```text
[Canal RSS CNMV] ──► [trigger.py / rss_monitor.py] ──► [db/guardian.db & monitor.db]
                                │
                                ▼
                    [downloader.py & heuristic_filter.py]
                                │
                                ▼
                    [analysis/claude_analyzer.py]
                    (Diferencial V1 vs V2 con LLM)
                                │
                                ▼
                    [engine/impact_engine.py]
                    (Cálculo determinista de costes)
                                │
                                ▼
                    [audio_ui/app.py]
                    (Dashboard Streamlit + ElevenLabs)
```

---

## Requisitos del Sistema
* **Python 3.10+**
* **Dependencias principales:** `requests`, `feedparser`, `beautifulsoup4`, `lxml`, `pymupdf`, `tzdata`, `pytest`

---

## Instalación y Configuración

1. **Clonar el repositorio e instalar dependencias:**
   ```bash
   pip install requests feedparser beautifulsoup4 lxml pymupdf tzdata pytest
   ```

2. **Variables de Entorno (Opcionales):**
   Puedes configurar las rutas y comportamientos del sistema mediante las siguientes variables de entorno:
   * `PROD_OUTPUT_DIR`: Carpeta de salida para documentos aprobados (por defecto `data/demo_docs`).
   * `PROD_INCOMING_DIR`: Carpeta temporal para descargas en curso.
   * `PROD_REJECTED_DIR`: Carpeta de cuarentena para descartes.
   * `PROD_CONSERVAR_RECHAZADOS`: `1` para conservar documentos descartados, `0` para eliminación automática.
   * `GUARDIAN_DB_PATH`: Ruta a la base de datos principal (`guardian.db`, en solo lectura).
   * `MONITOR_DB_PATH`: Ruta a la base de datos de producción (`monitor.db`).
   * `PROD_INTERVALO_S`: Intervalo de pausa entre ciclos de escaneo (por defecto `900` segundos).

---

## Ejecución y Pruebas

### 1. Pruebas Unitarias y Validación Integrada
Ejecuta la suite de pruebas sin necesidad de conexión a la red:
```bash
python -m pytest monitoring_produccion/tests -q
```

### 2. Ejecución por Fases (Modo Dry-Run / Individual)
Puedes probar cada componente del pipeline por separado:

* **Monitor RSS:**
  ```bash
  python -m monitoring_produccion.rss_monitor --once --dry-run -v
  ```
* **Descargador de documentos:**
  ```bash
  python -m monitoring_produccion.downloader --once -v
  ```
* **Filtro heurístico:**
  ```bash
  python -m monitoring_produccion.heuristic_filter --once --dry-run
  ```
* **Prueba individual del filtro heurístico sobre un PDF local:**
  ```bash
  python -m monitoring_produccion.heuristic_filter --probar ruta/doc.pdf --texto-completo
  ```

### 3. Ejecución Completa en Producción
Para iniciar el ciclo de monitorización y análisis automatizado en bucle continuo:
```bash
python -m monitoring_produccion.run_produccion
```

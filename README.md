# Portfolio Voice Guardian

Supervisión regulatoria continua, cálculo determinista de impacto de costes y generación automatizada de briefings de audio personalizados (MiFID II).

---

## Visión del Proyecto

Los cambios en folletos regulados (KIID/PRIIPs) o hechos relevantes suelen tardar semanas en procesarse. Esto genera **sobrecostes imprevistos para los inversores** y **riesgos de incumplimiento sobrevenido de idoneidad (MiFID II)**.

**Portfolio Voice Guardian** es una plataforma que:
1. **Monitoriza** fuentes regulatorias (canales RSS de la CNMV).
2. **Cuantifica matemáticamente** el impacto patrimonial directo sobre las carteras.
3. **Genera briefings** adaptados al perfil del inversor (texto para el asesor y audio sintetizado con IA para el cliente).

---

## Arquitectura del Sistema

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

---

## Caso de Uso (Demo: Escenario 3)

Detección de una subida de comisión fija de gestión (**+0.45% / 45 bps**) en el fondo *EuroValue Core Equity UCITS* (`LU0998877665`) y cruce automático con carteras reales:

| Inversor | Perfil MiFID | Exposición | Impacto Anual (€/año) | Canal y Tono de Briefing |
| :--- | :--- | :---: | :---: | :--- |
| **Laura Gómez** | Conservador / Principiante | 35.000 € | **+157,50 €** | Audio WhatsApp (Didáctico y cercano) |
| **Martín Vega** *(Patrimonios Ibéricos)* | Moderado / Banca Privada | 500.000 € | **+2.250,00 €** | Portal Web (Técnico e institucional) |

---

## Monitoring Engine (monitoring_produccion/ y monitoring_demo/)

El Monitoring Engine es la puerta de entrada de Portfolio Voice Guardian: detecta qué comunicados afectan a los activos de las carteras y entrega al motor de IA solo los documentos con posible impacto económico. Existen dos versiones que comparten el mismo punto de encuentro, la carpeta `data/demo_docs/`:

| | monitoring_produccion/ | monitoring_demo/ |
|---|---|---|
| **Propósito** | Conectar con el mundo real (CNMV) | Demo fiable ante el jurado, sin depender de la red |
| **Entrada** | Canales RSS oficiales de la CNMV | Un PDF colocado a mano en `data/demo_docs/` |
| **Ejecución** | Bucle continuo (cada 15 min) | Escaneo bajo demanda: `python -m monitoring_demo.trigger` |
| **Filtros** | Dedupe + cruce con ASSETS + categoría + heurístico | Dedupe por hash SHA-256 contra DOCUMENTS |
| **Tokens de IA**| 0 (todo determinista) | 0 |
| **Salida** | PDFs relevantes en `data/demo_docs/` | Registro en DOCUMENTS + llamada a pipeline |

```mermaid
flowchart TD
    RSS["CNMV · RSS IIP / OIR / HR"] --> M["rss_monitor.py<br/>dedupe · cruce con ASSETS · filtro de categoría"]
    M -->|PENDIENTE_DESCARGA| D["downloader.py<br/>página intermedia → PDF · SHA-256"]
    D -->|"DESCARGADO (data/.incoming)"| F["heuristic_filter.py<br/>reglas y puntuación"]
    F -->|APROBADO| OUT["data/demo_docs/"]
    F -->|RECHAZADO| Q["data/.rechazados/"]
    DROP["PDF colocado a mano<br/>(modo demo)"] --> OUT
    OUT --> T["trigger.py<br/>file_scanner · deduplication · registro en DOCUMENTS"]
    T --> P["run_end_to_end_pipeline<br/>Claude → motor de impacto → UI/audio"]

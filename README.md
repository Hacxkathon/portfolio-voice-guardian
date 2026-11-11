# Portfolio Voice Guardian

> **Supervisión regulatoria continua, cálculo determinista de impacto de costes y generación automatizada de briefings de audio personalizados (MiFID II).**

---

## Visión del Proyecto

Los cambios en folletos regulados (KIID/PRIIPs) o hechos relevantes suelen tardar semanas en procesarse. Esto genera **sobrecostes imprevistos para los inversores** y **riesgos de incumplimiento sobrevenido de idoneidad (MiFID II)**.

**Portfolio Voice Guardian** es una plataforma que:
1. **Monitoriza** fuentes regulatorias.
2. **Cuantifica matemáticamente** el impacto patrimonial directo sobre las carteras.
3. **Genera briefings** adaptados al perfil del inversor (texto para el asesor y audio sintetizado con IA para el cliente).

---

## Arquitectura del Sistema

```text
[Canal RSS CNMV] ──► [trigger.py] ──► [db/guardian.db]
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

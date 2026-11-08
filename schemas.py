from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class EvidenceCitation(BaseModel):
    page_number: Optional[int] = Field(
        None, description="Número de página del documento donde aparece el cambio"
    )
    exact_quote: str = Field(
        ..., description="Cita literal extraída del documento que sirve como evidencia irrefutable"
    )
    section: Optional[str] = Field(
        None, description="Sección o capítulo del documento (ej. 'Gastos y Comisiones')"
    )


class FinancialEventExtraction(BaseModel):
    event_type: Literal[
        "dividends", "fees", "debt", "strategy", "results", "regulation", "guidance"
    ] = Field(..., description="Categoría financiera del cambio detectado")
    
    materiality: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(
        ..., description="Severidad o relevancia del impacto financiero"
    )
    
    title: str = Field(
        ..., description="Titular conciso del hecho detectado (ej. 'Subida de comisión de gestión')"
    )
    
    previous_value: Optional[str] = Field(
        None, description="Valor anterior en el documento previo (ej. 'TER 1.20%')"
    )
    
    new_value: Optional[str] = Field(
        None, description="Nuevo valor en el documento publicado (ej. 'TER 1.65%')"
    )
    
    numeric_delta_pct: Optional[float] = Field(
        None,
        description="Cambio numérico porcentual si aplica (+0.45 para +0.45% en comisiones o -20.0 para recorte)",
    )
    
    financial_implications: str = Field(
        ...,
        description="Explicación técnica detallada dirigida al asesor sobre las consecuencias del cambio",
    )
    
    mifid_review_suggested: bool = Field(
        ...,
        description="True si la modificación puede alterar la idoneidad o el perfil de riesgo/coste",
    )
    
    limitations_and_uncertainty: str = Field(
        ...,
        description="Aclaración explícita sobre supuestos, datos no verificados o limitaciones del documento",
    )
    
    evidence: List[EvidenceCitation] = Field(
        ..., min_length=1, description="Lista de citas y páginas que respaldan la conclusión"
    )

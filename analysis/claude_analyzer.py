import os
import pymupdf
import anthropic
from dotenv import load_dotenv

# Importar el esquema Pydantic creado por el equipo
from schemas import FinancialEventExtraction

# Cargar las claves del archivo .env
load_dotenv()

# Inicializar el cliente de Anthropic
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def extract_text_from_pdf(pdf_path: str) -> list[dict]:
    """Extrae texto de un PDF conservando el número de página."""
    doc = pymupdf.open(pdf_path)
    extracted_pages = []
    
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        text = page.get_text("text")
        
        extracted_pages.append({
            "page_number": page_num + 1,
            "text": text.strip()
        })
        
    return extracted_pages

def analyze_document_with_claude(new_doc_path: str, old_doc_path: str | None = None) -> FinancialEventExtraction:
    """
    Orquestador principal: procesa los PDFs y decide el prompt dinámico
    para Claude según si existe o no un documento anterior.
    """
    # 1. Extraer el texto del documento nuevo (siempre obligatorio)
    new_text_pages = extract_text_from_pdf(new_doc_path)
    
    # 2. Lógica condicional del Prompt según la arquitectura de Denisa
    if old_doc_path is not None:
        old_text_pages = extract_text_from_pdf(old_doc_path)
        base_prompt = "Compara el documento A con el B y dime qué ha cambiado."
        user_content = f"Documento A (Antiguo):\n{old_text_pages}\n\nDocumento B (Nuevo):\n{new_text_pages}"
    else:
        base_prompt = "Analiza este documento y extrae los eventos financieros relevantes." 
        user_content = f"Documento a analizar:\n{new_text_pages}"

    # 3. Restricciones estrictas (Contrato JSON y Trazabilidad)
    system_instructions = (
        f"{base_prompt}\n\n"
        f"IMPORTANTE: Tu respuesta debe ser ÚNICA y ESTRICTAMENTE un JSON válido que cumpla "
        f"con el siguiente esquema Pydantic:\n{FinancialEventExtraction.model_json_schema()}\n\n"
        f"Debes rellenar obligatoriamente el campo 'evidence' con la cita textual exacta y "
        f"la página del documento de la que proviene el cambio, para garantizar la trazabilidad." 
    )

# 4. Llamada a la API usando Tool Calling (Structured Outputs)
    response = client.messages.create(
        model="claude-sonnet-4-6",   # modelo vigente
        max_tokens=2048,
        system=system_instructions,
        tools=[
            {
                "name": "extract_financial_event",
                "description": "Extrae los datos financieros según el esquema requerido.",
                "input_schema": FinancialEventExtraction.model_json_schema()
            }
        ],
    tool_choice={"type": "tool", "name": "extract_financial_event"},
    messages=[
            {"role": "user", "content": user_content}
        ]
    )
    
    # Extraer el JSON generado por la herramienta y validarlo con Pydantic
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_financial_event":
            # Devuelve directamente el modelo Pydantic instanciado
            return FinancialEventExtraction(**block.input)
            
    raise ValueError("Claude no devolvió el formato esperado.")
    
if __name__ == "__main__":
    # Asegúrate de que las rutas apuntan a donde están tus PDFs
    pdf_antiguo = "data/demo_docs/escenario3_folleto_v1.pdf"
    pdf_nuevo = "data/demo_docs/escenario3_hecho_relevante_v2.pdf"
    
    print("Iniciando análisis comparativo con Claude...")
    resultado = analyze_document_with_claude(
        new_doc_path=pdf_nuevo,
        old_doc_path=pdf_antiguo
    )
    
    print("\n--- RESULTADO DE LA EXTRACCIÓN (Pydantic Model) ---")
    print(resultado.model_dump_json(indent=2))

from pathlib import Path
from typing import TypedDict

from monitoring.deduplication import (
    calculate_file_hash,
    is_document_processed,
)

class NuevoDocumento(TypedDict):
    """Estructura que representa un documento nuevo detectado."""
    file_path: str
    asset_id: str
    content_hash: str

def extraer_asset_id(file_path: Path) -> str:
    """
    Extrae el asset_id del nombre de un archivo PDF.
    
    Busca el formato estándar de 3 bloques (ej: AST_FD_01_prospectus.pdf).
    Si el nombre es más corto, usa el nombre completo sin extensión como fallback.
    """
    partes_nombre = file_path.stem.split("_")

    # Si tiene al menos 3 partes, unimos las tres primeras (ej: AST_FD_01)
    if len(partes_nombre) >= 3:
        return "_".join(partes_nombre[:3])
    
    # Fallback flexible para nombres cortos como "AST_FD_01.pdf"
    return file_path.stem

def escanear_nuevos_documentos(
    folder_path: str,
    db_path: str
) -> list[NuevoDocumento]:
    """
    Escanea un directorio en busca de PDFs no procesados.
    No inserta en base de datos; solo identifica candidatos para el trigger.
    """
    carpeta = Path(folder_path)
    nuevos_documentos: list[NuevoDocumento] = []

    # 1. Validación del directorio
    try:
        if not carpeta.exists():
            raise FileNotFoundError(f"El directorio '{folder_path}' no existe.")
        
        if not carpeta.is_dir():
            raise NotADirectoryError(f"'{folder_path}' no es un directorio válido.")

        archivos_pdf = sorted([
            archivo for archivo in carpeta.iterdir()
            if archivo.is_file() and archivo.suffix.lower() == ".pdf"
        ])
    except OSError as error:
        raise OSError(f"No se pudo escanear el directorio '{folder_path}': {error}") from error

    # 2. Procesamiento individual de cada PDF
    for pdf in archivos_pdf:
        try:
            asset_id = extraer_asset_id(pdf)
            # Usamos resolve() para obtener la ruta absoluta segura
            ruta_absoluta = str(pdf.resolve())
            content_hash = calculate_file_hash(ruta_absoluta)
            
        except (OSError, ValueError) as error:
            print(f"[ERROR DE ARCHIVO] No se pudo procesar '{pdf.name}': {error}")
            continue # Ignoramos este archivo pero continuamos con el resto

        # 3. Verificación en base de datos
        # Si esto falla, el RuntimeError subirá y detendrá el programa (comportamiento deseado)
        ya_procesado = is_document_processed(content_hash, db_path)

        if not ya_procesado:
            print(f"[NUEVO DOCUMENTO] {pdf.name} | Activo asociado: {asset_id}")
            
            nuevos_documentos.append({
                "file_path": ruta_absoluta,
                "asset_id": asset_id,
                "content_hash": content_hash,
            })

    return nuevos_documentos
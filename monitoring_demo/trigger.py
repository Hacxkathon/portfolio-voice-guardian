import sqlite3
import uuid
from datetime import date
from typing import TypedDict, Optional

from monitoring_demo.file_scanner import escanear_nuevos_documentos
from pipeline import run_end_to_end_pipeline

class InformacionDocumento(TypedDict):
    """Estructura con la información de un documento nuevo."""
    file_path: str
    asset_id: str
    content_hash: str


def obtener_ruta_documento_anterior(
    db_path: str,
    asset_id: str
) -> Optional[str]:
    """
    Recupera la ruta del documento anterior más reciente registrado.
    De momento, como la tabla DOCUMENTS no guarda la ruta local física, 
    devuelve None hasta que se necesite añadir ese campo para desarrollo.
    """
    return None


def registrar_documento(
    db_path: str,
    doc_info: InformacionDocumento
) -> str:
    """
    Registra el documento detectado en la tabla DOCUMENTS.
    Genera un UUID e inserta los datos iniciales (el texto se extrae después).
    """
    nuevo_id = uuid.uuid4().hex
    fecha_publicacion = date.today().isoformat()

    connection: Optional[sqlite3.Connection] = None

    try:
        connection = sqlite3.connect(db_path)
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO DOCUMENTS (
                doc_id,  
                asset_id,
                doc_type,
                version,
                publication_date,
                content_hash,
                extracted_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nuevo_id,
                doc_info["asset_id"],
                "Desconocido",
                "NUEVA_VERSION",
                fecha_publicacion,
                doc_info["content_hash"],
                ""
            )
        )

        connection.commit()
        print(f"[DOCUMENTO REGISTRADO] {doc_info['file_path']} | ID: {nuevo_id}")
        
        return nuevo_id

    except sqlite3.Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"No se pudo registrar el documento en la base de datos: {error}") from error

    finally:
        if connection is not None:
            connection.close()


def ejecutar_trigger(
    folder_path: str,
    db_path: str
) -> None:
    """
    Ejecuta el orquestador principal. Escanea, registra y activa la IA.
    """
    print("\n========== MOTOR DE MONITORIZACIÓN ==========")
    print(f"Escaneando directorio: {folder_path}")

    try:
        # Usamos el nombre de la función en español que definimos en tu file_scanner.py
        documentos_nuevos = escanear_nuevos_documentos(folder_path, db_path)

        if not documentos_nuevos:
            print("[INFO] No se detectaron documentos nuevos.")
            print("=============================================\n")
            return

        print(f"[INFO] Se han detectado {len(documentos_nuevos)} documento(s) nuevo(s).")

        for doc_info in documentos_nuevos:
            try:
                # Recuperar la versión anterior si existiera
                old_doc_path = obtener_ruta_documento_anterior(db_path, doc_info["asset_id"])

                # Registrar formalmente en SQLite
                registrar_documento(db_path, doc_info)

                # Activar el pipeline de IA
                run_end_to_end_pipeline(
                    asset_id=doc_info["asset_id"],
                    # Nota: Por ahora pasamos las rutas en lugar del texto crudo,
                    # Xoan extraerá el texto de los PDFs por dentro.
                    new_doc_text=doc_info["file_path"],
                    old_doc_text=old_doc_path if old_doc_path else "",
                    is_simulation=False # 
                )

            except (RuntimeError, OSError) as error:
                print(f"[ERROR] No se pudo procesar '{doc_info['file_path']}': {error}")
                continue

        print("========== MONITORIZACIÓN COMPLETADA ==========\n")

    except (RuntimeError, OSError) as error:
        raise RuntimeError(f"La ejecución del motor falló: {error}") from error


# Bloque para probar la demo manualmente desde la terminal
if __name__ == "__main__":
    RUTA_CARPETA = "data/demo_docs"
    RUTA_DB = "db/guardian.db"
    
    ejecutar_trigger(RUTA_CARPETA, RUTA_DB)
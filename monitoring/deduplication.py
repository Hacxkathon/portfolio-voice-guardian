import hashlib
import sqlite3
from typing import Optional


def calculate_file_hash(file_path: str) -> str:
    """
    Calcula el hash SHA-256 de un archivo local.
    Lee el archivo en modo binario procesándolo en bloques de 8192 bytes
    para evitar saturar la memoria RAM.

    Args:
        file_path: Ruta al archivo local.

    Returns:
        El hash SHA-256 en formato hexadecimal.

    Raises:
        OSError: Si el archivo no existe o hay problemas de permisos/lectura.
    """
    sha256 = hashlib.sha256()

    try:
        with open(file_path, "rb") as file:
            for chunk in iter(lambda: file.read(8192), b""):
                sha256.update(chunk)

        return sha256.hexdigest()

    except OSError as error:
        raise OSError(
            f"No se pudo calcular el hash del archivo '{file_path}': {error}"
        ) from error


def calculate_rss_hash(url: str, item_id: str) -> str:
    """
    Calcula un hash SHA-256 único para un elemento de un feed RSS de la CNMV.
    Usa la URL y el ID separados por un pipeline (|) para evitar colisiones.
    Ignora la fecha de publicación para prevenir falsos positivos.

    Args:
        url: Enlace del documento en el feed RSS.
        item_id: Identificador único (guid) del elemento RSS.

    Returns:
        El hash SHA-256 en formato hexadecimal.

    Raises:
        ValueError: Si la URL o el ID están vacíos.
    """
    if not url or not item_id:
        raise ValueError("Es obligatorio proporcionar tanto 'url' como 'item_id'.")

    # El uso de '|' evita colisiones entre URLs e IDs parecidos
    unique_identifier = f"{url}|{item_id}"
    encoded_identifier = unique_identifier.encode("utf-8")

    sha256 = hashlib.sha256()
    sha256.update(encoded_identifier)

    return sha256.hexdigest()


def is_document_processed(hash_value: str, db_path: str) -> bool:
    """
    Comprueba si un documento ya ha sido registrado en la base de datos SQLite.

    Args:
        hash_value: Hash SHA-256 del documento o elemento RSS.
        db_path: Ruta al archivo de la base de datos SQLite.

    Returns:
        True si el documento ya existe (duplicado), False si es nuevo.

    Raises:
        ValueError: Si el valor del hash está vacío.
        RuntimeError: Si ocurre un error al consultar la base de datos.
    """
    if not hash_value:
        raise ValueError("El valor del hash no puede estar vacío.")

    connection: Optional[sqlite3.Connection] = None
    cursor: Optional[sqlite3.Cursor] = None

    try:
        connection = sqlite3.connect(db_path)
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT 1
            FROM DOCUMENTS
            WHERE content_hash = ?
            LIMIT 1
            """,
            (hash_value,)
        )

        return cursor.fetchone() is not None

    except sqlite3.Error as error:
        raise RuntimeError(
            f"Error de base de datos al comprobar el hash en '{db_path}': {error}"
        ) from error

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
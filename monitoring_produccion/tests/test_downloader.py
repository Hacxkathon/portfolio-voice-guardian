"""Tests del downloader: extracción del enlace y descarga con una sesión simulada."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from monitoring_produccion import config
from monitoring_produccion.downloader import (
    ErrorReintentable,
    construir_nombre_fichero,
    extraer_enlace_pdf,
    procesar_pendiente,
)
from monitoring_produccion.monitor_db import (
    Estado,
    ItemFeed,
    abrir_monitor_db,
    actualizar_item,
    insertar_feed_item,
    obtener_items,
)

URL_PAGINA = "https://www.cnmv.es/Portal/Otra-Informacion-Relevante/Resultado-OIR.aspx?nreg=43616"
URL_PDF = "https://www.cnmv.es/webservices/verdocumento/ver?t=%7ba92e0340-7642-4b7c-9280-14924c101f50%7d"

# Incluye un .pdf del menú del portal (ruido) y el bloque real del registro.
HTML_OIR = f"""
<html><body>
<li class="sub-nav menu-lst-lv1"><a href="/DocPortal/Publicaciones/Ciberseguridad/Ciber.pdf"
   target="_blank"><span>Ciberseguridad</span></a></li>
<article class="block-content-pdf block-content">
  <p class="TituloDatos">ACS, ACTIVIDADES DE CONSTRUCCION Y SERVICIOS, S.A.</p>
  <ul id="listaElementosPrimernivel">
    <li class="blocks-single"><ul class="col-12 single">
      <li class="fecha-con-hora">11/11/2026</li>
      <li class="padding-r resumen"><a id="x_subtituloRegistroEnlace" href="{URL_PDF}"
          target="_blank"><span>Plan de Incentivos</span></a></li>
      <li class="padding-r resumen">Número de registro: 43616</li>
    </ul></li>
  </ul>
</article></body></html>
"""

HTML_SIN_ENLACE = "<html><body><article class='block-content-pdf'><p>Sin documentos</p></article></body></html>"


class _RespuestaFalsa:
    """Respuesta HTTP mínima que imita lo que usa el downloader."""

    def __init__(self, contenido: bytes, status: int = 200, tipo: str = "application/pdf", url: str = "") -> None:
        self.content = contenido
        self.status_code = status
        self.headers = {"Content-Type": tipo}
        self.url = url

    def iter_content(self, chunk_size: int = 1):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


class _SesionFalsa:
    def __init__(self, respuestas: dict[str, _RespuestaFalsa]) -> None:
        self._respuestas = respuestas

    def get(self, url: str, **kwargs):
        return self._respuestas[url]


def test_extrae_el_enlace_correcto_ignorando_el_menu() -> None:
    assert extraer_enlace_pdf(HTML_OIR, URL_PAGINA, "43616") == URL_PDF


def test_resuelve_enlaces_relativos_con_la_url_final() -> None:
    html = ('<article class="block-content-pdf"><ul><li class="blocks-single">'
            '<a href="/webservices/verdocumento/ver?t=abc">doc</a>'
            "<li>Número de registro: 1</li></li></ul></article>")
    assert extraer_enlace_pdf(html, URL_PAGINA, "1") == "https://www.cnmv.es/webservices/verdocumento/ver?t=abc"


def test_prefiere_el_registro_con_el_nreg_correcto() -> None:
    html = ('<article class="block-content-pdf"><ul>'
            '<li class="blocks-single"><a href="/webservices/verdocumento/ver?t=otro">x</a> Número de registro: 99</li>'
            '<li class="blocks-single"><a href="/webservices/verdocumento/ver?t=bueno">y</a> Número de registro: 43616</li>'
            "</ul></article>")
    assert extraer_enlace_pdf(html, URL_PAGINA, "43616").endswith("t=bueno")


def test_sin_enlace_es_reintentable() -> None:
    with pytest.raises(ErrorReintentable):
        extraer_enlace_pdf(HTML_SIN_ENLACE, URL_PAGINA, "43616")


def test_nombre_de_fichero_compatible_con_el_scanner() -> None:
    nombre = construir_nombre_fichero(
        "AST_EQ_01", "OIR", datetime(2026, 11, 11, 18, 7, tzinfo=timezone.utc), "43616", "abcdef123456"
    )
    assert nombre == "AST_EQ_01_OIR_20261111_43616.pdf"
    assert "_".join(Path(nombre).stem.split("_")[:3]) == "AST_EQ_01"  # lógica de file_scanner


@pytest.fixture
def conn():
    c = abrir_monitor_db(":memory:")
    yield c
    c.close()


def _crear_pendiente(conn):
    item = ItemFeed(
        guid_hash="g1" * 32, feed="OIR", nreg="43616", entidad="ACS", link=URL_PAGINA,
        categoria="Otra información relevante", resumen="Plan de Incentivos",
        publicado_utc=datetime(2026, 11, 11, 18, 7, 57, tzinfo=timezone.utc),
    )
    with conn:
        insertar_feed_item(conn, item, "AST_EQ_01", "alias", Estado.PENDIENTE_DESCARGA)
    return obtener_items(conn, Estado.PENDIENTE_DESCARGA)[0]


def test_descarga_completa(conn, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DIR_INCOMING", tmp_path)
    pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
    sesion = _SesionFalsa({
        URL_PAGINA: _RespuestaFalsa(HTML_OIR.encode("utf-8"), tipo="text/html", url=URL_PAGINA),
        URL_PDF: _RespuestaFalsa(pdf),
    })
    estado = procesar_pendiente(conn, sesion, None, _crear_pendiente(conn))

    assert estado is Estado.DESCARGADO
    fila = conn.execute("SELECT * FROM feed_items").fetchone()
    assert Path(fila["ruta_tmp"]).name == "AST_EQ_01_OIR_20261111_43616.pdf"
    assert Path(fila["ruta_tmp"]).read_bytes() == pdf
    assert fila["pdf_sha256"] == hashlib.sha256(pdf).hexdigest()
    assert not list(tmp_path.glob("*.part"))  # no quedan temporales


def test_recurso_que_no_es_pdf_se_rechaza(conn, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DIR_INCOMING", tmp_path)
    sesion = _SesionFalsa({
        URL_PAGINA: _RespuestaFalsa(HTML_OIR.encode("utf-8"), tipo="text/html", url=URL_PAGINA),
        URL_PDF: _RespuestaFalsa(b"<html>Error</html>", tipo="text/html"),
    })
    assert procesar_pendiente(conn, sesion, None, _crear_pendiente(conn)) is Estado.RECHAZADO
    assert list(tmp_path.iterdir()) == []


def test_reintentos_y_paso_a_error(conn, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DIR_INCOMING", tmp_path)
    sesion = _SesionFalsa({URL_PAGINA: _RespuestaFalsa(HTML_SIN_ENLACE.encode(), tipo="text/html", url=URL_PAGINA)})

    fila = _crear_pendiente(conn)
    assert procesar_pendiente(conn, sesion, None, fila) is Estado.PENDIENTE_DESCARGA

    # Simulamos que ya se han agotado los intentos previos: el siguiente fallo es definitivo.
    with conn:
        actualizar_item(conn, fila["guid_hash"], fila["asset_id"], intentos=config.MAX_INTENTOS_DESCARGA - 1)
    fila = obtener_items(conn, Estado.PENDIENTE_DESCARGA)[0]
    assert procesar_pendiente(conn, sesion, None, fila) is Estado.ERROR
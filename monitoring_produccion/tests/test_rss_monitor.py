"""Tests del monitor RSS con fragmentos del XML real de la CNMV."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from monitoring_produccion.monitor_db import Activo, abrir_monitor_db
from monitoring_produccion.rss_monitor import (
    CruzadorActivos,
    _con_lxml,
    construir_item,
    extraer_entradas_crudas,
    motivo_ruido_categoria,
    normalizar_nombre_entidad,
    parsear_descripcion,
    procesar_entradas,
)

_ITEM = """
<item>
<Title>{titulo}</Title>
<link><![CDATA[ https://www.cnmv.es/Portal/HR/ResultadoBusquedaHR.aspx?division=2&nreg={nreg}&th=H ]]></link>
<guid isPermaLink="true"><![CDATA[ https://www.cnmv.es/Portal/HR/ResultadoBusquedaHR.aspx?division=2&nreg={nreg}&th=H ]]></guid>
<pubDate>Wed, 11 Nov 2026 12:35:13 GMT</pubDate>
<description><![CDATA[ <p><b>13:35  11/11/2026  </b> {categoria} - <BR/><BR/>{resumen}</p> ]]></description>
</item>
"""

XML_MUESTRA: bytes = (
    '<rss xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="2.0"><Channel>'
    "<title>CNMV - Hechos relevantes</title><ttl>15</ttl>"
    + _ITEM.format(titulo="BLACKROCK STRATEGIC FUNDS", nreg=317634,
                   categoria="Número de accionistas y patrimonio de IIC extranjeras",
                   resumen="La entidad comunica número de accionistas y patrimonio")
    + _ITEM.format(titulo="KUTXABANK 0/100 CARTERAS, FI", nreg=317632,
                   categoria="Reembolsos/Reducción del capital en circulación de IIC superior al 20%",
                   resumen="Reembolsos superiores al 20% del patrimonio")
    + _ITEM.format(titulo="SANTANDER OBJETIVO 11 MESES SEP-27, FI", nreg=317626,
                   categoria="Otros hechos relevantes", resumen="Patrimonio alcanzado por el fondo")
    + "</Channel></rss>"
).encode("utf-8")

AHORA = datetime(2026, 11, 11, 14, 0, tzinfo=timezone.utc)


def _cruzador() -> CruzadorActivos:
    activos = [
        Activo("AST_FD_01", "ES0123456789", "Kutxabank 0/100 Carteras FI", "FUND"),
        Activo("AST_FD_02", "LU0000000011", "BlackRock Strategic Funds - Global Bond", "FUND"),
    ]
    return CruzadorActivos(activos)


def test_extraccion_con_feed_no_estandar() -> None:
    crudas = extraer_entradas_crudas(XML_MUESTRA)
    assert len(crudas) == 3
    assert crudas[0]["link"].startswith("https://www.cnmv.es/")
    assert crudas[0]["link"] == crudas[0]["link"].strip()


def test_parser_de_respaldo_lxml() -> None:
    crudas = _con_lxml(XML_MUESTRA)
    assert len(crudas) == 3
    assert crudas[1]["titulo"] == "KUTXABANK 0/100 CARTERAS, FI"


def test_parsear_descripcion() -> None:
    categoria, resumen = parsear_descripcion(
        "<p><b>13:35  11/11/2026  </b> Número de accionistas y patrimonio de IIC extranjeras - "
        "<BR/><BR/>La entidad comunica número de accionistas y patrimonio</p>"
    )
    assert categoria == "Número de accionistas y patrimonio de IIC extranjeras"
    assert resumen.startswith("La entidad comunica")


def test_parsear_descripcion_formato_oir() -> None:
    categoria, resumen = parsear_descripcion(
        "<p><b>19:07  11/11/2026  </b> Otra información relevante<BR/><BR/>"
        "Plan de Incentivos a Largo Plazo de Directivos de Grupo ACS durante el periodo 2026-2031</p>"
    )
    assert categoria == "Otra información relevante"
    assert resumen.startswith("Plan de Incentivos")


def test_construir_item_extrae_nreg_y_fecha_utc() -> None:
    item = construir_item("HR", extraer_entradas_crudas(XML_MUESTRA)[0])
    assert item is not None
    assert item.nreg == "317634"
    assert item.publicado_utc is not None and item.publicado_utc.tzinfo is not None


def test_nreg_en_enlace_oir_con_aspx() -> None:
    enlace = "https://www.cnmv.es/Portal/Otra-Informacion-Relevante/Resultado-OIR.aspx?nreg=43616"
    item = construir_item("OIR", {
        "titulo": "ACS, ACTIVIDADES DE CONSTRUCCION Y SERVICIOS, S.A.",
        "link": enlace,
        "guid": enlace,
        "pubdate": "Wed, 11 Nov 2026 18:07:57 GMT",
        "descripcion": "<p><b>19:07  11/11/2026  </b> Otra información relevante<BR/><BR/>"
                       "La Sociedad remite Nota de Prensa correspondiente al 3er trimestre 2026</p>",
    })
    assert item is not None
    assert item.nreg == "43616"
    assert item.categoria == "Otra información relevante"


def test_normalizacion_de_nombres() -> None:
    assert normalizar_nombre_entidad("KUTXABANK 0/100 CARTERAS, FI") == "KUTXABANK 0 100 CARTERAS"
    assert normalizar_nombre_entidad("Banco Santander, S.A.") == "BANCO SANTANDER"


def test_cruce_por_isin_tiene_prioridad() -> None:
    res = _cruzador().cruzar("ENTIDAD DESCONOCIDA", "Fondo con ISIN ES0123456789")
    assert [(c.asset_id, c.metodo) for c in res] == [("AST_FD_01", "isin")]


def test_motivo_ruido() -> None:
    assert motivo_ruido_categoria("Número de accionistas y patrimonio de IIC extranjeras", "") is not None
    assert motivo_ruido_categoria("Modificación de comisiones", "Nueva comisión de gestión") is None


@pytest.fixture
def conn():
    c = abrir_monitor_db(":memory:")
    yield c
    c.close()


def test_flujo_completo_y_dedupe(conn) -> None:
    crudas = extraer_entradas_crudas(XML_MUESTRA)
    est = procesar_entradas("HR", crudas, conn, _cruzador(), ahora=AHORA)
    assert (est.leidos, est.nuevos, est.sin_cruce) == (3, 3, 1)

    filas = conn.execute(
        "SELECT asset_id, metodo_cruce, estado FROM feed_items ORDER BY asset_id"
    ).fetchall()
    assert [tuple(f) for f in filas] == [
        ("AST_FD_01", "exacto", "PENDIENTE_DESCARGA"),
        ("AST_FD_02", "parcial", "DESCARTADO_CATEGORIA"),
    ]

    # Segundo ciclo: todo debe contarse como duplicado y no insertar nada nuevo.
    est2 = procesar_entradas("HR", crudas, conn, _cruzador(), ahora=AHORA)
    assert (est2.duplicados, est2.nuevos) == (3, 0)
    assert conn.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0] == 2
"""Tests del filtro heurístico con PDFs generados al vuelo."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest

from monitoring_produccion import config
from monitoring_produccion.heuristic_filter import ErrorPDF, ejecutar_filtro, evaluar_pdf, normalizar_para_filtro
from monitoring_produccion.monitor_db import (
    Estado,
    ItemFeed,
    abrir_monitor_db,
    actualizar_item,
    insertar_feed_item,
)


def _crear_pdf(ruta: Path, texto: str) -> Path:
    doc = pymupdf.open()
    pagina = doc.new_page()
    if texto:
        pagina.insert_textbox(pymupdf.Rect(50, 50, 545, 790), texto, fontsize=11)
    doc.save(str(ruta))
    doc.close()
    return ruta


def test_aprueba_por_regla_fuerte(tmp_path: Path) -> None:
    pdf = _crear_pdf(tmp_path / "a.pdf", "La gestora anuncia una modificacion de la comision de gestion del fondo.")
    res = evaluar_pdf(pdf)
    assert res.aprobado and "fees" in res.eventos


def test_rechaza_ruido_administrativo(tmp_path: Path) -> None:
    pdf = _crear_pdf(tmp_path / "b.pdf", "La sociedad comunica el nombramiento de un nuevo consejero y el cambio de domicilio social.")
    assert not evaluar_pdf(pdf).aprobado


def test_regla_fuerte_gana_a_las_negativas(tmp_path: Path) -> None:
    pdf = _crear_pdf(tmp_path / "c.pdf", "La sociedad anuncia una OPA sobre el 100% del capital. Nombramiento de asesor financiero.")
    assert evaluar_pdf(pdf).aprobado


def test_derechos_de_voto_con_mencion_a_dividendo_se_rechaza(tmp_path: Path) -> None:
    pdf = _crear_pdf(tmp_path / "d.pdf", "Total numero de derechos de voto y de capital. Se menciona el dividendo del ejercicio.")
    assert not evaluar_pdf(pdf).aprobado


def test_pdf_sin_texto_se_aprueba_para_revision(tmp_path: Path) -> None:
    res = evaluar_pdf(_crear_pdf(tmp_path / "e.pdf", ""))
    assert res.aprobado and res.requiere_revision


def test_cruce_parcial_exige_confirmar_el_isin(tmp_path: Path) -> None:
    pdf = _crear_pdf(tmp_path / "f.pdf", "Presentacion de resultados trimestrales del grupo. ISIN ES0123456789.")
    assert not evaluar_pdf(pdf, isin=None, metodo_cruce="parcial").aprobado          # 3 puntos < umbral 5
    assert evaluar_pdf(pdf, isin="ES0123456789", metodo_cruce="parcial").aprobado     # ISIN: +2 y umbral normal
    assert evaluar_pdf(pdf, isin=None, metodo_cruce="exacto").aprobado               # cruce fiable: umbral 3


def test_pdf_corrupto_lanza_error_pdf(tmp_path: Path) -> None:
    roto = tmp_path / "roto.pdf"
    roto.write_bytes(b"esto no es un pdf")
    with pytest.raises(ErrorPDF):
        evaluar_pdf(roto)


@pytest.fixture
def conn():
    c = abrir_monitor_db(":memory:")
    yield c
    c.close()


def _crear_item_descargado(conn, guid: str, asset_id: str, ruta: Path) -> None:
    item = ItemFeed(guid_hash=guid, feed="HR", nreg=guid, entidad="X", link=f"https://x/{guid}",
                    categoria=None, resumen="", publicado_utc=datetime(2026, 11, 11, 12, tzinfo=timezone.utc))
    with conn:
        insertar_feed_item(conn, item, asset_id, "exacto", Estado.PENDIENTE_DESCARGA)
        actualizar_item(conn, guid, asset_id, estado=Estado.DESCARGADO, ruta_tmp=str(ruta))


def test_ignora_caracteres_invisibles() -> None:
    assert normalizar_para_filtro("comi\u200bsion de \u200bgestion") == "comision de gestion"


def test_el_contexto_del_feed_suma_puntos(tmp_path: Path) -> None:
    pdf = _crear_pdf(tmp_path / "g.pdf", "Kutxabank Gestion comunica el siguiente hecho relevante sobre el fondo. " * 2)
    assert not evaluar_pdf(pdf).aprobado
    ctx = "Reembolsos/Reducción del capital en circulación de IIC superior al 20%"
    assert evaluar_pdf(pdf, metodo_cruce="alias", contexto=ctx).aprobado


def test_flujo_bd_mueve_aprobados_y_archiva_rechazados(conn, tmp_path: Path, monkeypatch) -> None:
    incoming, demo, rechazados = tmp_path / "incoming", tmp_path / "demo", tmp_path / "rech"
    incoming.mkdir()
    monkeypatch.setattr(config, "DIR_DEMO_DOCS", demo)
    monkeypatch.setattr(config, "DIR_RECHAZADOS", rechazados)
    monkeypatch.setattr(config, "CONSERVAR_RECHAZADOS", True)
    bueno = _crear_pdf(incoming / "AST_FD_01_HR_20261111_1.pdf", "La gestora comunica la fusion de los fondos y una modificacion de comisiones.")
    malo = _crear_pdf(incoming / "AST_FD_02_HR_20261111_2.pdf", "Nombramiento de consejero y cambio de domicilio social.")
    _crear_item_descargado(conn, "g1", "AST_FD_01", bueno)
    _crear_item_descargado(conn, "g2", "AST_FD_02", malo)

    est = ejecutar_filtro(conn, isins={})

    assert (est.aprobados, est.rechazados) == (1, 1)
    assert (demo / bueno.name).is_file()
    assert (rechazados / malo.name).is_file()
    assert not bueno.exists() and not malo.exists()
    estados = {f["asset_id"]: f["estado"] for f in conn.execute("SELECT asset_id, estado FROM feed_items")}
    assert estados == {"AST_FD_01": "APROBADO", "AST_FD_02": "RECHAZADO"}
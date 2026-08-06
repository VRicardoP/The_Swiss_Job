"""Tests de V.0 — un fallo de descarga NO puede terminar en estado de éxito.

Es el DoD del ticket: antes, un 404 y un feed vacío producían el mismo
resultado observable (`OK 0 ofertas`) y por eso 9 fuentes estuvieron 66 días
mudas sin que saltara nada.
"""

import httpx
import pytest

from config import settings
from models.source_health import (
    OUTCOME_EMPTY,
    OUTCOME_ERROR,
    OUTCOME_OK,
    SourceHealth,
)
from services import source_health
from utils import fetch_diagnostics as diag
from utils.http import fetch_rss, fetch_with_retry


# --------------------------------------------------------------- clasificación


def test_clasifica_error_cuando_no_hay_datos_y_si_hubo_fallo():
    fallos = [diag.FetchIssue(diag.KIND_HTTP, "https://x/feed", status=404)]
    assert diag.classify(0, fallos) == OUTCOME_ERROR


def test_clasifica_empty_cuando_no_hay_datos_ni_fallos():
    """Una fuente que responde y no tiene ofertas es legítima, no un error."""
    assert diag.classify(0, []) == OUTCOME_EMPTY


def test_clasifica_ok_aunque_haya_fallos_parciales():
    """Si trajo ofertas es `ok`: una página caída de N es degradación, no muerte."""
    fallos = [diag.FetchIssue(diag.KIND_HTTP, "https://x/p3", status=500)]
    assert diag.classify(12, fallos) == OUTCOME_OK


# ------------------------------------------------- registro desde los helpers


@pytest.mark.asyncio
async def test_fetch_rss_registra_el_404_en_vez_de_tragarlo():
    """EL test del DoD: el 404 deja rastro, no se disfraza de feed vacío."""

    def handler(request):
        return httpx.Response(404)

    diag.begin()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        texto = await fetch_rss(client, "https://muerto/feed", max_retries=0)

    assert texto is None
    fallos = diag.issues()
    assert len(fallos) == 1
    assert fallos[0].status == 404
    assert fallos[0].kind == diag.KIND_HTTP
    assert diag.classify(0, fallos) == OUTCOME_ERROR


@pytest.mark.asyncio
async def test_fetch_rss_ok_no_registra_nada():
    def handler(request):
        return httpx.Response(200, text="<rss/>")

    diag.begin()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        texto = await fetch_rss(client, "https://vivo/feed", max_retries=0)

    assert texto == "<rss/>"
    assert diag.issues() == []


@pytest.mark.asyncio
async def test_fetch_rss_registra_error_de_red_sin_status():
    def handler(request):
        raise httpx.ConnectError("sin ruta al host")

    diag.begin()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await fetch_rss(client, "https://caido/feed", max_retries=0) is None

    fallos = diag.issues()
    assert len(fallos) == 1
    assert fallos[0].kind == diag.KIND_NETWORK
    assert fallos[0].status is None


@pytest.mark.asyncio
async def test_fetch_with_retry_registra_el_403():
    def handler(request):
        return httpx.Response(403, text="forbidden")

    diag.begin()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await fetch_with_retry(client, "https://x/api", max_retries=0) is None

    fallos = diag.issues()
    assert len(fallos) == 1
    assert fallos[0].status == 403


@pytest.mark.asyncio
async def test_sin_begin_no_registra_y_no_rompe():
    """Una llamada fuera del pipeline (script, test suelto) no se ve afectada."""
    diag._issues.set(None)

    def handler(request):
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await fetch_rss(client, "https://x/feed", max_retries=0) is None

    assert diag.issues() == []


# ------------------------------------------------------------------- persistencia


@pytest.mark.asyncio
async def test_error_incrementa_racha_y_guarda_el_porque(db_session):
    fallos = [diag.FetchIssue(diag.KIND_HTTP, "https://x/feed", status=404)]

    fila = await source_health.record_outcome(
        db_session, "zebis", OUTCOME_ERROR, 0, fallos
    )
    await db_session.flush()

    assert fila.consecutive_errors == 1
    assert fila.last_outcome == OUTCOME_ERROR
    assert "404" in fila.last_error_detail
    assert fila.last_success_at is None


@pytest.mark.asyncio
async def test_exito_limpia_ambas_rachas(db_session):
    fallos = [diag.FetchIssue(diag.KIND_HTTP, "https://x", status=500)]
    await source_health.record_outcome(db_session, "proz", OUTCOME_ERROR, 0, fallos)
    await source_health.record_outcome(db_session, "proz", OUTCOME_ERROR, 0, fallos)
    fila = await source_health.record_outcome(db_session, "proz", OUTCOME_OK, 7, [])
    await db_session.flush()

    assert fila.consecutive_errors == 0
    assert fila.consecutive_empty == 0
    assert fila.last_jobs_count == 7
    assert fila.last_success_at is not None


@pytest.mark.asyncio
async def test_vacio_y_error_cuentan_por_separado(db_session):
    """Un error no incrementa la racha de vacíos: piden acciones distintas."""
    await source_health.record_outcome(db_session, "tes", OUTCOME_EMPTY, 0, [])
    fila = await source_health.record_outcome(
        db_session,
        "tes",
        OUTCOME_ERROR,
        0,
        [diag.FetchIssue(diag.KIND_HTTP, "https://x", status=404)],
    )
    await db_session.flush()

    assert fila.consecutive_empty == 1
    assert fila.consecutive_errors == 1


def test_alerta_por_racha_de_errores():
    fila = SourceHealth(source_key="x", last_error_detail="HTTP 404 en https://x")
    fila.consecutive_errors = settings.SOURCE_HEALTH_ERROR_STREAK
    fila.consecutive_empty = 0

    motivo = source_health.needs_alert(fila)
    assert motivo is not None
    assert "404" in motivo


def test_no_alerta_si_esta_sana():
    fila = SourceHealth(source_key="x")
    fila.consecutive_errors = 0
    fila.consecutive_empty = 0
    assert source_health.needs_alert(fila) is None

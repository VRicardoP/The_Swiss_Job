"""Tests de V.0 — un fallo de descarga NO puede terminar en estado de éxito.

Es el DoD del ticket: antes, un 404 y un feed vacío producían el mismo
resultado observable (`OK 0 ofertas`) y por eso 9 fuentes estuvieron 66 días
mudas sin que saltara nada.
"""

import httpx
import pytest
from sqlalchemy import select

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
async def test_fetch_with_retry_registra_el_200_con_json_null():
    """Fase 3 r2/H1: `response.json()` sobre el cuerpo `null` devuelve None
    por el camino de ÉXITO (sin excepción) — sin registro rompía el contrato
    del que dependen los 20 providers ("el None de fetch_with_retry es un
    fetch fallido cuyo issue ya registró utils.http") y el run salía
    `empty` en silencio en vez de `error` (G1)."""

    def handler(request):
        return httpx.Response(200, content=b"null")

    diag.begin()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await fetch_with_retry(client, "https://x/api", max_retries=0) is None

    fallos = diag.issues()
    assert len(fallos) == 1
    assert fallos[0].kind == diag.KIND_NETWORK
    assert "null" in fallos[0].detail
    assert diag.classify(0, fallos) == OUTCOME_ERROR


@pytest.mark.asyncio
async def test_fetch_with_retry_registra_el_200_no_utf8():
    """Fase 3 r2/H1: un 200 cuyo cuerpo no es UTF-8 hace que response.json()
    lance UnicodeDecodeError — solo se capturaba JSONDecodeError y la
    excepción ESCAPABA del helper, rompiendo el contrato "None ⇒ issue ya
    registrado" y la parte de G1 que prohíbe excepciones de parseo escapando.
    Ahora cae en la rama ValueError (que cubre ambas) y agota reintentos con
    su issue de red registrado."""

    def handler(request):
        return httpx.Response(200, content=b"\xff")

    diag.begin()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await fetch_with_retry(client, "https://x/api", max_retries=0) is None

    fallos = diag.issues()
    assert len(fallos) == 1
    assert fallos[0].kind == diag.KIND_NETWORK
    assert "UnicodeDecodeError" in fallos[0].detail
    assert diag.classify(0, fallos) == OUTCOME_ERROR


@pytest.mark.asyncio
async def test_fetch_with_retry_registra_el_200_no_json():
    """Fase 3 r3/H4 (mata M4): gemelo del caso no-UTF8 con cuerpo UTF-8 que
    no es JSON — response.json() lanza json.JSONDecodeError, la OTRA subclase
    de ValueError que la rama debe cubrir. Fija que la captura no puede
    estrecharse (p. ej. a UnicodeDecodeError) sin perder esta pata del
    contrato "None ⇒ issue ya registrado" (G1)."""

    def handler(request):
        return httpx.Response(200, content=b"not json")

    diag.begin()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await fetch_with_retry(client, "https://x/api", max_retries=0) is None

    fallos = diag.issues()
    assert len(fallos) == 1
    assert fallos[0].kind == diag.KIND_NETWORK
    assert "JSONDecodeError" in fallos[0].detail
    assert diag.classify(0, fallos) == OUTCOME_ERROR


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


# ------------------------------------------- persistencia (VD.3, señal propia)


@pytest.mark.asyncio
async def test_record_storage_alerta_al_segundo_run_sin_guardar(db_session):
    """Descargar N>0 y guardar 0 dos runs seguidos degrada la fuente; uno solo
    puede ser un hipo transitorio de BD y no alerta."""
    m1 = await source_health.record_storage(db_session, "stelle_admin", 7, 0)
    assert m1 is None

    m2 = await source_health.record_storage(db_session, "stelle_admin", 7, 0)
    assert m2 is not None
    assert "guardar" in m2

    fila = (
        await db_session.execute(
            select(SourceHealth).where(SourceHealth.source_key == "stelle_admin")
        )
    ).scalar_one()
    assert fila.consecutive_unstored == 2
    assert fila.last_stored_count == 0


@pytest.mark.asyncio
async def test_record_storage_guardar_algo_resetea_la_racha(db_session):
    await source_health.record_storage(db_session, "schuljobs", 5, 0)
    await source_health.record_storage(db_session, "schuljobs", 5, 3)

    fila = (
        await db_session.execute(
            select(SourceHealth).where(SourceHealth.source_key == "schuljobs")
        )
    ).scalar_one()
    assert fila.consecutive_unstored == 0
    assert fila.last_stored_count == 3


@pytest.mark.asyncio
async def test_record_storage_sin_descargas_no_toca_la_racha(db_session):
    """`fetched == 0` no es información: no guardar nada cuando no había nada
    que guardar no es un defecto de persistencia."""
    await source_health.record_storage(db_session, "myscience", 4, 0)
    await source_health.record_storage(db_session, "myscience", 0, 0)

    fila = (
        await db_session.execute(
            select(SourceHealth).where(SourceHealth.source_key == "myscience")
        )
    ).scalar_one()
    assert fila.consecutive_unstored == 1


@pytest.mark.asyncio
async def test_recuperarse_no_produce_falso_positivo(db_session):
    """F2: una fuente con racha vieja de no-guardados que en ESTE run descarga
    y guarda bien no puede salir degradada: `record_and_alert` corre ANTES de
    persistir y solo informa de SU señal (descarga); la racha la evalúa y
    resetea `record_storage` al final, cuando ya es actual."""
    await source_health.record_storage(db_session, "recovering", 5, 0)
    await source_health.record_storage(db_session, "recovering", 5, 0)  # racha=2

    # Run siguiente: la descarga va bien — sin el arreglo, aquí se reportaba
    # la racha vieja de no-guardados que aún no ha tenido ocasión de resetearse.
    m_fetch = await source_health.record_and_alert(
        db_session, "recovering", OUTCOME_OK, 5, []
    )
    assert m_fetch is None

    # ... y la persistencia también: la racha se resetea sin alertar.
    m_storage = await source_health.record_storage(db_session, "recovering", 5, 5)
    assert m_storage is None

    fila = (
        await db_session.execute(
            select(SourceHealth).where(SourceHealth.source_key == "recovering")
        )
    ).scalar_one()
    assert fila.consecutive_unstored == 0


@pytest.mark.asyncio
async def test_sin_intentos_no_reemite_la_alerta_congelada(db_session):
    """G2-A: una fuente con racha legitima de no-guardados cuyo portal deja de
    publicar (`attempted == 0` en cada run) NO puede seguir emitiendo el mismo
    `FUENTE DEGRADADA` cada dia sin evidencia nueva — solo se alerta en runs
    que tocan la racha. La racha queda en la fila para `needs_alert`."""
    assert await source_health.record_storage(db_session, "ghost", 7, 0) is None
    # Segundo run sin guardar: alerta legitima (racha = umbral).
    assert await source_health.record_storage(db_session, "ghost", 7, 0) is not None

    # El portal deja de publicar: sin intentos no hay informacion nueva.
    for _ in range(3):
        assert await source_health.record_storage(db_session, "ghost", 0, 0) is None

    fila = (
        await db_session.execute(
            select(SourceHealth).where(SourceHealth.source_key == "ghost")
        )
    ).scalar_one()
    # La racha NO se borra: sigue disponible para la lectura global.
    assert fila.consecutive_unstored == 2
    assert source_health.needs_alert(fila) is not None


@pytest.mark.asyncio
async def test_run_con_error_no_duplica_la_fuente_por_racha_vieja(db_session):
    """G2-B: fuente con racha vieja de no-guardados cuyo scraper devuelve []
    por fallos de red — `record_and_alert` ya emite el motivo de descarga; la
    racha vieja de persistencia no puede aportar un SEGUNDO motivo en el mismo
    run (el duplicado en `unhealthy` que F2 cerro)."""
    await source_health.record_storage(db_session, "dupdeg", 5, 0)
    await source_health.record_storage(db_session, "dupdeg", 5, 0)  # racha = 2

    fallos = [diag.FetchIssue(diag.KIND_HTTP, "https://x/feed", status=404)]
    m_fetch = None
    for _ in range(settings.SOURCE_HEALTH_ERROR_STREAK):
        m_fetch = await source_health.record_and_alert(
            db_session, "dupdeg", OUTCOME_ERROR, 0, fallos
        )
    assert m_fetch is not None  # el motivo de DESCARGA, unico del run

    # Mismo run, señal de persistencia con attempted == 0: sin motivo nuevo.
    assert await source_health.record_storage(db_session, "dupdeg", 0, 0) is None


def test_alerta_por_racha_de_no_guardados():
    """El motivo se decide en needs_alert (tercera condición, umbral propio)."""
    fila = SourceHealth(source_key="x")
    fila.consecutive_errors = 0
    fila.consecutive_empty = 0
    fila.consecutive_unstored = settings.SOURCE_HEALTH_UNSTORED_STREAK

    motivo = source_health.needs_alert(fila)
    assert motivo is not None
    assert "guardar" in motivo


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
    fila.consecutive_unstored = 0
    assert source_health.needs_alert(fila) is None

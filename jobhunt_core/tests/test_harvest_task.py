"""Tarea Celery de cosecha (A-04): unit sin BD ni broker."""

from unittest.mock import patch

import jobhunt_core.tasks.harvest as harvest_task
from jobhunt_core.celery_app import celery_app
from jobhunt_core.harvest.sink import (
    _valid_listing,
    canonical_payload,
    content_hash,
    normalize_url,
)
from jobhunt_core.harvest.types import RawListing, ScopeRunResult


def test_task_registered_on_core_queue():
    assert "jobhunt.harvest.run_scope" in celery_app.tasks
    # El namespace jobhunt.harvest.* enruta a core.harvest (aislamiento A-01).
    assert celery_app.conf.task_routes["jobhunt.harvest.*"] == {"queue": "core.harvest"}


def test_task_returns_result_dict():
    async def fake_impl(scope_id):
        return ScopeRunResult(scope_id=scope_id, status="ok", listings=3, pages=2)

    with patch.object(harvest_task, "_run_scope_impl", fake_impl):
        r = harvest_task.run_scope_task.apply(args=["s1"])
    assert r.successful()
    assert r.result == {"scope_id": "s1", "status": "ok", "listings": 3, "pages": 2}


def test_task_partial_and_stale_do_not_retry():
    for status in ("partial", "stale", "skipped"):
        async def fake_impl(scope_id, _s=status):
            return ScopeRunResult(scope_id=scope_id, status=_s)

        with patch.object(harvest_task, "_run_scope_impl", fake_impl):
            r = harvest_task.run_scope_task.apply(args=["s1"])
        assert r.successful()
        assert r.result["status"] == status


def test_task_error_result_retries():
    async def fake_impl(scope_id):
        return ScopeRunResult(scope_id=scope_id, status="error", error="fuente rota")

    with patch.object(harvest_task, "_run_scope_impl", fake_impl):
        r = harvest_task.run_scope_task.apply(args=["s1"])
    assert not r.successful()  # RETRY/FAILURE: no se da por buena una cosecha rota


def test_task_not_found_does_not_retry():
    """Rev. A-04 #5: scope eliminado tras encolar = caso NORMAL permanente —
    'not_found' sin consumir retry."""
    async def fake_impl(scope_id):
        return ScopeRunResult(scope_id=scope_id, status="not_found")

    with patch.object(harvest_task, "_run_scope_impl", fake_impl):
        r = harvest_task.run_scope_task.apply(args=["s1"])
    assert r.successful()
    assert r.result["status"] == "not_found"


def test_task_unknown_provider_fails_without_retry():
    """Rev. A-04 #5: provider desconocido = config PERMANENTE — falla explícito
    sin re-ejecutar (reintentar no lo arregla)."""
    calls = []

    async def fake_impl(scope_id):
        calls.append(scope_id)
        raise KeyError("Provider desconocido: 'nope'")

    with patch.object(harvest_task, "_run_scope_impl", fake_impl):
        r = harvest_task.run_scope_task.apply(args=["s1"])
    assert not r.successful()
    assert len(calls) == 1  # UNA ejecución: sin retry


def test_task_transient_exception_consumes_retry():
    """Contraste con el anterior: un transitorio (HTTP/BD) SÍ reintenta."""
    calls = []

    async def fake_impl(scope_id):
        calls.append(scope_id)
        raise RuntimeError("timeout HTTP")

    with patch.object(harvest_task, "_run_scope_impl", fake_impl):
        r = harvest_task.run_scope_task.apply(args=["s1"])
    assert not r.successful()
    assert len(calls) == 2  # original + 1 retry (max_retries=1)


def test_valid_listing_boundary_limits():
    """Validación de frontera (rev. A-04 #2) — y regresión: espacios/UTF-8 en
    el payload NUNCA cuarentenan (solo NUL y límites del esquema)."""
    ok = RawListing(
        external_id="a", url="https://x/a",
        payload={"title": "desarrollo web", "desc": "señal única — ütf8"},
    )
    assert _valid_listing(ok, canonical_payload(ok.payload))
    for bad in (
        RawListing(external_id="x" * 201, url="https://x/a", payload={}),
        RawListing(external_id="a", url="https://x/" + "u" * 1000, payload={}),
        RawListing(external_id="a", url="https://x/a", payload={}, apply_url="https://x/" + "u" * 1000),
        RawListing(external_id="a", url="https://x/a", payload={"t": "a\x00b"}),
        RawListing(external_id="a\x00b", url="https://x/a", payload={}),
    ):
        assert not _valid_listing(bad, canonical_payload(bad.payload))


def test_normalize_url_canonical():
    assert normalize_url("HTTPS://Example.com/Job/1/") == "https://example.com/Job/1"
    assert normalize_url("https://x/a?id=7#frag") == "https://x/a?id=7"  # query se conserva
    assert normalize_url("https://x/a") == normalize_url("https://x/a/")


def test_content_hash_stable_regardless_of_key_order():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})

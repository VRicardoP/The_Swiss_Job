"""Tarea Celery de cosecha (A-04): unit sin BD ni broker."""

from unittest.mock import patch

import jobhunt_core.tasks.harvest as harvest_task
from jobhunt_core.celery_app import celery_app
from jobhunt_core.harvest import identity
from jobhunt_core.harvest.provider import ProviderConfigError
from jobhunt_core.harvest.providers import UnknownProviderError
from jobhunt_core.harvest.sink import _preprocess, content_hash, normalize_url
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
    """Rev. A-04 #5 + 2ª #3: provider desconocido = config PERMANENTE — falla
    explícito sin re-ejecutar, con excepción CONCRETA (no cualquier KeyError)."""
    calls = []

    async def fake_impl(scope_id):
        calls.append(scope_id)
        raise UnknownProviderError("Provider desconocido: 'nope'")

    with patch.object(harvest_task, "_run_scope_impl", fake_impl):
        r = harvest_task.run_scope_task.apply(args=["s1"])
    assert not r.successful()
    assert len(calls) == 1  # UNA ejecución: sin retry


def test_task_config_error_fails_without_retry():
    """Rev. 2ª #3: params inválidos del provider (ProviderConfigError, p.ej.
    hard_max_pages=0) suben desde el runner y fallan SIN retry."""
    calls = []

    async def fake_impl(scope_id):
        calls.append(scope_id)
        raise ProviderConfigError("hard_max_pages (0) debe ser >= 2")

    with patch.object(harvest_task, "_run_scope_impl", fake_impl):
        r = harvest_task.run_scope_task.apply(args=["s1"])
    assert not r.successful()
    assert len(calls) == 1


def test_task_internal_keyerror_is_transient_and_retries():
    """Rev. 2ª #3: un KeyError INTERNO cualquiera NO es config — se reintenta
    como transitorio (antes se clasificaba mal como permanente)."""
    calls = []

    async def fake_impl(scope_id):
        calls.append(scope_id)
        raise KeyError("bug interno cualquiera")

    with patch.object(harvest_task, "_run_scope_impl", fake_impl):
        r = harvest_task.run_scope_task.apply(args=["s1"])
    assert not r.successful()
    assert len(calls) == 2  # original + 1 retry


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


def test_preprocess_boundary_limits():
    """Cuarentena de frontera (rev. A-04 #2 y 2ª #2) — y regresiones de falso
    positivo: espacios, UTF-8 y un '\\u0000' LITERAL (texto legítimo, sin NUL
    real) NUNCA cuarentenan."""
    ok = RawListing(
        external_id="a", url="https://x/a",
        payload={"title": "desarrollo web", "desc": "señal — ütf8", "lit": "\\u0000"},
    )
    assert _preprocess(ok) is not None
    for bad in (
        RawListing(external_id="x" * 201, url="https://x/a", payload={}),
        RawListing(external_id="a", url="https://x/" + "u" * 1000, payload={}),
        RawListing(external_id="a", url="https://x/a", payload={}, apply_url="https://x/" + "u" * 1000),
        RawListing(external_id="a", url="https://x/a", payload={"t": "a\x00b"}),
        RawListing(external_id="a\x00b", url="https://x/a", payload={}),
        RawListing(external_id="a", url="https://x/a", payload={"n": float("nan")}),  # 2ª: jsonb sin NaN
        RawListing(external_id="a", url="https://[invalid", payload={}),  # 2ª: urlsplit ValueError
        RawListing(external_id="a\ud800", url="https://x/a", payload={}),  # 2ª: surrogate en id
        RawListing(external_id="a", url="https://x/a\ud800", payload={}),  # 2ª: surrogate en url
        RawListing(external_id="a", url="https://x/a", payload={"t": "\ud800"}),
    ):
        assert _preprocess(bad) is None


def test_identity_normalization_pf5():
    """PF.5 portado (A-05): seniority/género por TOKEN, sufijos legales fuera."""
    assert identity.normalize_title("Senior Python Dev (m/w/d)") == "python dev"
    assert identity.normalize_title("International Team Leader") == "international team leader"
    assert identity.normalize_company("ACME AG") == identity.normalize_company("Acme GmbH")
    assert identity.fuzzy_key("Python Dev", "ACME AG") == "python dev|acme"
    assert identity.fuzzy_key("Python Dev", None) is None  # identidad incompleta
    assert identity.fuzzy_key(None, "ACME") is None


def test_identity_recycle_guard_semantics():
    """Guard determinista (A-05): recicla SOLO con ambas empresas presentes y
    distintas; la falta de datos degrada a conservador."""
    assert identity.should_recycle(("t", "ACME AG"), ("t", "Umbrella SA"))
    assert not identity.should_recycle(("t", "ACME AG"), ("t", "acme gmbh"))
    assert not identity.should_recycle(("t", None), ("t", "ACME"))
    assert not identity.should_recycle(("t", "ACME"), ("t", None))
    assert identity.extract_identity("fuente-desconocida", {"title": "x"}) == (None, None)

    identity.register_extractor("rota", lambda p: p["no-existe"])
    try:
        assert identity.extract_identity("rota", {}) == (None, None)  # jamás rompe
    finally:
        identity._EXTRACTORS.pop("rota", None)


def test_identity_non_string_values_degrade_to_none():
    """Auditoría A-05 #2: title/company no-string del feed (número, bool,
    lista, objeto) = identidad ausente — nunca llega a .lower()."""
    identity.register_extractor(
        "tipos", lambda p: (p.get("title"), p.get("company"))
    )
    try:
        assert identity.extract_identity("tipos", {"title": 42, "company": 7}) == (None, None)
        assert identity.extract_identity("tipos", {"title": True, "company": ["x"]}) == (None, None)
        assert identity.extract_identity(
            "tipos", {"title": "ok", "company": {"name": "ACME"}}
        ) == ("ok", None)
    finally:
        identity._EXTRACTORS.pop("tipos", None)


def test_normalize_url_canonical():
    assert normalize_url("HTTPS://Example.com/Job/1/") == "https://example.com/Job/1"
    assert normalize_url("https://x/a?id=7#frag") == "https://x/a?id=7"  # query se conserva
    assert normalize_url("https://x/a") == normalize_url("https://x/a/")


def test_content_hash_stable_regardless_of_key_order():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})

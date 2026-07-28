"""CONTRACT TESTS de la capacidad CATALOGO — A.SEAM (plan §15bis).

El MISMO juego de casos (CASES) se ejecuta contra las DOS implementaciones
del puerto CatalogPort:

- `local`: motor actual sobre la tabla `jobs` (BD de test).
- `core`: cliente HTTP contra un /v1 FAKE cuya forma replica el contrato
  REAL del core (jobhunt_core/api/v1.py + api/schemas.py: VacancyDTO,
  ErrorDTO, auth Bearer key_id.secret, 404 para no-activas). El backend
  legacy no importa jobhunt_core (frontera estricta, plan §21), por eso el
  DTO se replica aqui como dict.

Se afirma forma y semantica equivalentes DONDE EL CONTRATO LO EXIGE:
- detalle (get): mismos title/company/description/location/remote/tags/url/
  source/salario-texto/timestamps; forma JobResponse en ambos; None para
  referencia inexistente o no activa. La IDENTIDAD difiere por contrato
  (hash MD5 legacy vs UUID de vacante core).
- busqueda/stats/fuentes: el /v1 del core NO las expone aun — la cota es
  CatalogUnsupportedError (fijada aqui; al publicarse el endpoint core, estos
  tests pasan a exigir equivalencia tambien ahi).
"""

import hashlib
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.job import Job
from schemas.job import JobResponse, JobSearchResponse, JobStats, SourceInfo
from services.catalog import (
    CatalogSearchParams,
    CatalogUnsupportedError,
    CoreCatalog,
    CoreUnavailableError,
    LocalCatalog,
)

# ---------------------------------------------------------------------------
# Juego de casos compartido
# ---------------------------------------------------------------------------

FIRST_SEEN = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
LAST_SEEN = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)

CASES = {
    "python_zurich": {
        "title": "Python Developer",
        "company": "Acme Corp",
        "description": "Build Python APIs with FastAPI and PostgreSQL",
        "location": "Zurich, ZH",
        "remote": False,
        "tags": ["python", "fastapi"],
        "url": "https://example.com/job/python",
        "source": "test_source",
        "salary_text": "CHF 100'000 - 120'000",
    },
    # Caso minimo: opcionales ausentes (el contrato los declara nullables)
    "devops_remote": {
        "title": "DevOps Engineer",
        "company": "Beta AG",
        "description": None,
        "location": None,
        "remote": True,
        "tags": [],
        "url": "https://example.com/job/devops",
        "source": "adzuna",
        "salary_text": None,
    },
}

# Identidades POR BACKEND (contrato): MD5 en legacy, UUID de vacante en core.
CASE_LOCAL_REFS = {n: hashlib.md5(n.encode()).hexdigest() for n in CASES}
CASE_CORE_REFS = {n: str(uuid.uuid5(uuid.NAMESPACE_URL, n)) for n in CASES}
MISSING_REFS = {"local": "f" * 32, "core": str(uuid.uuid4())}
# Referencia de vacante NO ACTIVA: en legacy fila is_active=False; en el core
# el contrato §2 responde 404 (solo sirve activas) => ambos devuelven None.
INACTIVE_LOCAL_REF = "e" * 32

TEST_CONSUMER_KEY = "testkid.testsecret"

# Campos cuya equivalencia EXIGE el contrato entre ambas implementaciones.
CONTRACT_EQUIVALENT_FIELDS = (
    "title",
    "company",
    "description",
    "location",
    "remote",
    "tags",
    "url",
    "source",
    "salary_original",  # texto libre: `salary` del core / salary_original legacy
    "first_seen_at",
    "last_seen_at",
    "is_active",
)


def _vacancy_dto(name: str) -> dict:
    """VacancyDTO con la forma EXACTA de jobhunt_core/api/schemas.py."""
    case = CASES[name]
    return {
        "id": CASE_CORE_REFS[name],
        "title": case["title"],
        "company": case["company"],
        "description": case["description"],
        "salary": case["salary_text"],
        "tags": case["tags"],
        "location": case["location"],
        "remote": case["remote"],
        "primary_listing": {
            "source": case["source"],
            "url": case["url"],
            "apply_url": None,
            "external_id": f"ext-{name}",
            "first_seen_at": FIRST_SEEN.isoformat(),
            "last_seen_at": LAST_SEEN.isoformat(),
        },
        "listings": [{"source": case["source"], "url": case["url"], "apply_url": None}],
        "translations": [],
    }


def _error_dto(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": {}}


def fake_core_transport() -> httpx.MockTransport:
    """Core /v1 fake fiel al contrato: auth Bearer, 404 ErrorDTO, solo activas."""

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {TEST_CONSUMER_KEY}":
            return httpx.Response(
                401, json=_error_dto("unauthorized", "credencial ausente o invalida")
            )
        assert request.url.path.startswith("/v1/vacancies/"), request.url.path
        vid = request.url.path.rsplit("/", 1)[-1]
        for name, ref in CASE_CORE_REFS.items():
            if ref == vid:
                return httpx.Response(200, json=_vacancy_dto(name))
        return httpx.Response(
            404, json=_error_dto("not_found", "vacante no encontrada")
        )

    return httpx.MockTransport(handler)


def make_core_catalog(
    transport: httpx.MockTransport | None = None,
    bearer: str = TEST_CONSUMER_KEY,
) -> CoreCatalog:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=1.0,
            transport=transport or fake_core_transport(),
        )

    return CoreCatalog(client_factory=factory)


async def seed_local_cases(db: AsyncSession) -> None:
    for name, case in CASES.items():
        db.add(
            Job(
                hash=CASE_LOCAL_REFS[name],
                source=case["source"],
                title=case["title"],
                company=case["company"],
                url=case["url"],
                description=case["description"],
                location=case["location"],
                remote=case["remote"],
                tags=case["tags"],
                salary_original=case["salary_text"],
                first_seen_at=FIRST_SEEN,
                last_seen_at=LAST_SEEN,
                is_active=True,
            )
        )
    # Vacante retirada: el catalogo (ambos backends) no debe servirla.
    db.add(
        Job(
            hash=INACTIVE_LOCAL_REF,
            source="test_source",
            title="Retired Job",
            company="Gone GmbH",
            url="https://example.com/job/retired",
            is_active=False,
        )
    )
    await db.commit()


@pytest.fixture
async def catalogs(db_session):
    """(catalogo, refs_por_caso, ref_inexistente) por implementacion."""
    await seed_local_cases(db_session)
    return {
        "local": (LocalCatalog(db_session), CASE_LOCAL_REFS, MISSING_REFS["local"]),
        "core": (make_core_catalog(), CASE_CORE_REFS, MISSING_REFS["core"]),
    }


# ---------------------------------------------------------------------------
# get(): mismo juego de casos contra ambas implementaciones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("impl", ["local", "core"])
@pytest.mark.parametrize("case_name", list(CASES))
async def test_get_shape_is_job_response(catalogs, impl, case_name):
    """FORMA: el detalle de ambos backends valida como JobResponse."""
    catalog, refs, _ = catalogs[impl]
    result = await catalog.get(refs[case_name])
    assert result is not None
    validated = JobResponse.model_validate(result)
    assert validated.title == CASES[case_name]["title"]
    # timestamps presentes y con zona horaria (contrato de la representacion)
    assert validated.first_seen_at.tzinfo is not None
    assert validated.last_seen_at.tzinfo is not None


@pytest.mark.parametrize("case_name", list(CASES))
async def test_get_semantics_equivalent_local_vs_core(catalogs, case_name):
    """SEMANTICA: campos exigidos por el contrato identicos entre backends."""
    local_catalog, local_refs, _ = catalogs["local"]
    core_catalog, core_refs, _ = catalogs["core"]
    local_job = JobResponse.model_validate(
        await local_catalog.get(local_refs[case_name])
    )
    core_job = JobResponse.model_validate(await core_catalog.get(core_refs[case_name]))
    for field in CONTRACT_EQUIVALENT_FIELDS:
        assert getattr(local_job, field) == getattr(core_job, field), field
    # La identidad difiere POR CONTRATO: MD5 legacy vs UUID de vacante core.
    assert local_job.hash == local_refs[case_name]
    assert core_job.hash == core_refs[case_name]


@pytest.mark.parametrize("impl", ["local", "core"])
async def test_get_missing_returns_none(catalogs, impl):
    catalog, _, missing_ref = catalogs[impl]
    assert await catalog.get(missing_ref) is None


async def test_get_inactive_not_served_by_either(catalogs):
    """Solo vacantes ACTIVAS (contrato §2 core; is_active en legacy)."""
    local_catalog, _, _ = catalogs["local"]
    core_catalog, _, _ = catalogs["core"]
    assert await local_catalog.get(INACTIVE_LOCAL_REF) is None
    # En el core una vacante archivada simplemente no existe en /v1 => 404.
    assert (
        await core_catalog.get(str(uuid.uuid5(uuid.NAMESPACE_URL, "archived"))) is None
    )


async def test_core_get_with_legacy_md5_ref_returns_none():
    """Una ref MD5 legacy (32 hex) PARSEA como UUID sin guiones — el cliente
    debe cortocircuitar por forma canonica: None y CERO peticiones al core.
    El transporte falla el test si recibe CUALQUIER peticion, para que no
    vuelva a pasar por la razon equivocada (antes pasaba via 404 del fake)."""

    def fail_on_any_request(request: httpx.Request) -> httpx.Response:
        pytest.fail(
            f"el cliente core emitio una peticion para una ref MD5 legacy: "
            f"{request.method} {request.url}"
        )

    catalog = make_core_catalog(transport=httpx.MockTransport(fail_on_any_request))
    md5_ref = CASE_LOCAL_REFS["python_zurich"]
    assert len(md5_ref) == 32  # precondicion: forma MD5 legacy, sin guiones
    assert await catalog.get(md5_ref) is None
    # Misma cota para la variante en mayusculas (tambien parsea como UUID).
    assert await catalog.get(md5_ref.upper()) is None


# ---------------------------------------------------------------------------
# search/stats/sources: local sirve; en el core la cota es el contrato /v1
# ---------------------------------------------------------------------------


async def test_search_local_serves_case_set(catalogs):
    local_catalog, _, _ = catalogs["local"]
    result = await local_catalog.search(CatalogSearchParams())
    assert isinstance(result, JobSearchResponse)
    assert result.total == len(CASES)  # la retirada no se sirve
    assert {b.title for b in result.data} == {c["title"] for c in CASES.values()}


async def test_stats_and_sources_local_shapes(catalogs):
    local_catalog, _, _ = catalogs["local"]
    stats = await local_catalog.stats()
    assert isinstance(stats, JobStats)
    assert stats.total_jobs == len(CASES)
    sources = await local_catalog.sources()
    assert all(isinstance(s, SourceInfo) for s in sources)
    assert {s.name for s in sources} == {c["source"] for c in CASES.values()}


@pytest.mark.parametrize("operation", ["search", "stats", "sources"])
async def test_core_catalog_operations_not_in_v1_contract(catalogs, operation):
    """El /v1 del core no expone busqueda/stats/fuentes de catalogo (aun):
    la costura lo declara como CatalogUnsupportedError, no lo simula."""
    core_catalog, _, _ = catalogs["core"]
    with pytest.raises(CatalogUnsupportedError):
        if operation == "search":
            await core_catalog.search(CatalogSearchParams())
        elif operation == "stats":
            await core_catalog.stats()
        else:
            await core_catalog.sources()


# ---------------------------------------------------------------------------
# Contrato de auth y de fallo del cliente core
# ---------------------------------------------------------------------------


async def test_core_rejects_bad_credential_as_unavailable():
    """401 del core (credencial invalida) => sin datos utilizables."""
    catalog = make_core_catalog(bearer="wrongkid.wrongsecret")
    with pytest.raises(CoreUnavailableError):
        await catalog.get(CASE_CORE_REFS["python_zurich"])


async def test_core_without_configured_credential_makes_no_request(monkeypatch):
    """Sin CORE_CONSUMER_KEY no se emite NI UNA peticion (factory default)."""
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    catalog = CoreCatalog()  # factory por defecto (produccion)
    with pytest.raises(CoreUnavailableError):
        await catalog.get(CASE_CORE_REFS["python_zurich"])


async def test_core_down_raises_unavailable():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    catalog = make_core_catalog(transport=httpx.MockTransport(refuse))
    with pytest.raises(CoreUnavailableError):
        await catalog.get(CASE_CORE_REFS["python_zurich"])


@pytest.mark.asyncio
async def test_canary_warn_levels_separate_expected_from_actionable(caplog):
    """2ª rev. A.SEAM: Unsupported (cota /v1, esperado) va a DEBUG; el core
    CAÍDO (CoreUnavailableError) es el ÚNICO WARNING — la señal del canary
    no puede ahogarse en ruido esperado por contrato."""
    import logging

    from services.catalog.core_client import CatalogUnsupportedError, CoreUnavailableError
    from services.catalog.seam import FallbackCatalog

    class _Primary:
        async def search(self, params):
            raise CatalogUnsupportedError("cota /v1")

        async def get(self, job_ref):
            raise CoreUnavailableError("core caido")

    class _Fallback:
        async def search(self, params):
            return {"items": []}

        async def get(self, job_ref):
            return None

    seam = FallbackCatalog(_Primary(), _Fallback())
    with caplog.at_level(logging.DEBUG, logger="services.catalog.seam"):
        await seam.search(None)
        await seam.get("x")
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warns) == 1 and "core caido" in warns[0].getMessage()
    assert len(debugs) == 1 and "cota /v1" in debugs[0].getMessage()

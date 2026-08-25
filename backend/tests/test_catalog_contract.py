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
- busqueda: el /v1 expone GET /vacancies (C-API-R + filtros 5e8e849) — se
  exige equivalencia local vs core en los filtros EXPRESABLES (q, source,
  remote) y total/offset exactos; los filtros sin equivalente (canton,
  language, seniority, contract_type, salary, sort!=newest) son la cota
  CatalogUnsupportedError SIN peticiones de red.
- stats/fuentes: el /v1 del core NO las expone — la cota es
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
from services.catalog.core_client import clear_catalog_feed_cache

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


# Orden DETERMINISTA del feed fake (keyset created_at DESC del contrato).
FEED_ORDER = list(CASES)


def _feed_case_matches(name: str, params: dict) -> bool:
    """Semantica de filtros del /v1 (C-API-R + 5e8e849) sobre los CASES:
    q substring ci title/company; source CSV ci; remote igualdad."""
    case = CASES[name]
    q = params.get("q")
    if q:
        ql = q.lower()
        in_title = ql in case["title"].lower()
        in_company = ql in (case["company"] or "").lower()
        if not in_title and not in_company:
            return False
    source = params.get("source")
    if source:
        tokens = {t.strip().lower() for t in source.split(",") if t.strip()}
        if case["source"].lower() not in tokens:
            return False
    remote = params.get("remote")
    if remote is not None and case["remote"] is not (remote == "true"):
        return False
    return True


def _feed_page_response(params: dict) -> httpx.Response:
    """GET /v1/vacancies del fake: filtros + keyset opaco (nombre del caso),
    next_cursor solo si quedan filas mas alla de la pagina (contrato)."""
    matched = [n for n in FEED_ORDER if _feed_case_matches(n, params)]
    cursor = params.get("cursor")
    if cursor:
        idx = matched.index(cursor) + 1 if cursor in matched else len(matched)
        matched = matched[idx:]
    limit = int(params.get("limit", "20"))
    page, rest = matched[:limit], matched[limit:]
    return httpx.Response(
        200,
        json={
            "items": [_vacancy_dto(n) for n in page],
            "next_cursor": page[-1] if rest else None,
        },
    )


def fake_core_transport() -> httpx.MockTransport:
    """Core /v1 fake fiel al contrato: auth Bearer, 404 ErrorDTO, solo activas."""

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {TEST_CONSUMER_KEY}":
            return httpx.Response(
                401, json=_error_dto("unauthorized", "credencial ausente o invalida")
            )
        if request.url.path == "/v1/vacancies":
            return _feed_page_response(dict(request.url.params))
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


@pytest.fixture(autouse=True)
def _clean_catalog_feed_cache():
    """La cache de paginas por ETag es de modulo: se limpia por test."""
    clear_catalog_feed_cache()
    yield
    clear_catalog_feed_cache()


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
# search: equivalencia local vs core; stats/sources: cota del contrato /v1
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


@pytest.mark.parametrize("operation", ["stats", "sources"])
async def test_core_catalog_operations_not_in_v1_contract(catalogs, operation):
    """El /v1 del core no expone estadisticas/fuentes agregadas de catalogo:
    la costura lo declara como CatalogUnsupportedError, no lo simula."""
    core_catalog, _, _ = catalogs["core"]
    with pytest.raises(CatalogUnsupportedError):
        if operation == "stats":
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
    with pytest.raises(CoreUnavailableError):
        await catalog.search(CatalogSearchParams())


async def test_core_without_configured_credential_makes_no_request(monkeypatch):
    """Sin CORE_CONSUMER_KEY no se emite NI UNA peticion (factory default)."""
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    catalog = CoreCatalog()  # factory por defecto (produccion)
    with pytest.raises(CoreUnavailableError):
        await catalog.get(CASE_CORE_REFS["python_zurich"])
    with pytest.raises(CoreUnavailableError):
        await catalog.search(CatalogSearchParams())


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

    from services.catalog.core_client import (
        CatalogUnsupportedError,
        CoreUnavailableError,
    )
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


# ---------------------------------------------------------------------------
# Payloads invalidos en un 200 (P2 rev. externa): CoreUnavailableError
# ---------------------------------------------------------------------------


def _transport_returning(content: bytes, json_body=None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if json_body is not None:
            return httpx.Response(200, json=json_body)
        return httpx.Response(
            200, content=content, headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handler)


async def test_core_invalid_json_200_is_unavailable():
    """200 con JSON ilegible = tan inutilizable como el core caido — nunca un
    JSONDecodeError sin traducir (que reventaria el router con un 500)."""
    catalog = make_core_catalog(transport=_transport_returning(b"<html>oops"))
    with pytest.raises(CoreUnavailableError, match="payload invalido"):
        await catalog.get(CASE_CORE_REFS["python_zurich"])


@pytest.mark.parametrize(
    "body",
    [
        [1, 2, 3],  # lista donde va un objeto VacancyDTO (AttributeError)
        {"title": "sin id"},  # falta la clave id (KeyError)
        {"id": str(uuid.uuid4()), "tags": "no-lista"},  # tags rompe JobResponse
        {"id": str(uuid.uuid4()), "title": "x", "primary_listing": "no-dict"},
    ],
)
async def test_core_incompatible_schema_200_is_unavailable(body):
    """200 con esquema incompatible => CoreUnavailableError (fallback real en
    core_read), sea cual sea la forma del desvio."""
    catalog = make_core_catalog(transport=_transport_returning(b"", json_body=body))
    with pytest.raises(CoreUnavailableError, match="payload invalido"):
        await catalog.get(CASE_CORE_REFS["python_zurich"])


async def test_core_read_falls_back_to_local_on_invalid_payload(db_session, caplog):
    """core_read con payload invalido del core: FallbackCatalog cae al motor
    LOCAL (fallback REAL con su WARNING de canary) en vez de propagar un
    error de forma como 500."""
    import logging

    from services.catalog import LocalCatalog
    from services.catalog.seam import FallbackCatalog

    await seed_local_cases(db_session)
    seam = FallbackCatalog(
        make_core_catalog(transport=_transport_returning(b"not-json")),
        LocalCatalog(db_session),
    )
    with caplog.at_level(logging.WARNING, logger="services.catalog.seam"):
        # Ref UUID del core: el payload roto NO revienta la peticion...
        assert await seam.get(CASE_CORE_REFS["python_zurich"]) is None
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warns and "payload invalido" in warns[0].getMessage()
    # ...y el resto del catalogo local sigue sirviendose con normalidad.
    job = await seam.get(CASE_LOCAL_REFS["python_zurich"])
    assert job is not None and job.title == CASES["python_zurich"]["title"]


# ---------------------------------------------------------------------------
# search por el feed /v1 (cierre de cota C-API-R + filtros 5e8e849)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("impl", ["local", "core"])
async def test_search_shape_and_total(catalogs, impl):
    """FORMA: ambas implementaciones sirven JobSearchResponse con total
    exacto, limit/offset espejados y has_more coherente."""
    catalog, _, _ = catalogs[impl]
    result = await catalog.search(CatalogSearchParams())
    assert isinstance(result, JobSearchResponse)
    assert result.total == len(CASES)
    assert result.limit == 20 and result.offset == 0
    assert result.has_more is False
    assert {b.title for b in result.data} == {c["title"] for c in CASES.values()}


SEARCH_FILTER_CASES = {
    "q": (CatalogSearchParams(q="python"), {"python_zurich"}),
    "source_csv": (CatalogSearchParams(source="adzuna"), {"devops_remote"}),
    "remote_only": (CatalogSearchParams(remote_only=True), {"devops_remote"}),
}


@pytest.mark.parametrize("impl", ["local", "core"])
@pytest.mark.parametrize("filter_name", list(SEARCH_FILTER_CASES))
async def test_search_filters_equivalent_local_vs_core(catalogs, impl, filter_name):
    """SEMANTICA: los filtros expresables (q/source/remote) seleccionan el
    MISMO subconjunto de casos en ambos backends."""
    params, expected = SEARCH_FILTER_CASES[filter_name]
    catalog, _, _ = catalogs[impl]
    result = await catalog.search(params)
    assert {b.title for b in result.data} == {CASES[n]["title"] for n in expected}
    assert result.total == len(expected)


async def test_search_brief_semantics_equivalent(catalogs):
    """Campos del brief exigidos por el contrato identicos entre backends.
    La identidad difiere POR CONTRATO (MD5 legacy vs UUID de vacante core:
    la misma que sirve `get`, para que el detalle haga round-trip)."""
    local_catalog, local_refs, _ = catalogs["local"]
    core_catalog, core_refs, _ = catalogs["core"]
    local_page = await local_catalog.search(CatalogSearchParams())
    core_page = await core_catalog.search(CatalogSearchParams())
    local = {b.title: b for b in local_page.data}
    core = {b.title: b for b in core_page.data}
    assert set(local) == set(core)
    for title, brief in local.items():
        for field in (
            "company",
            "location",
            "remote",
            "tags",
            "url",
            "source",
            "first_seen_at",
            "is_active",
        ):
            assert getattr(brief, field) == getattr(core[title], field), (title, field)
    assert {b.hash for b in local.values()} == set(local_refs.values())
    assert {b.hash for b in core.values()} == set(core_refs.values())


UNSUPPORTED_SEARCH_PARAMS = {
    "canton": CatalogSearchParams(canton="ZH"),
    "language": CatalogSearchParams(language="de"),
    "seniority": CatalogSearchParams(seniority="senior"),
    "contract_type": CatalogSearchParams(contract_type="full_time"),
    "salary_min": CatalogSearchParams(salary_min=80000),
    "salary_max": CatalogSearchParams(salary_max=120000),
    "sort_oldest": CatalogSearchParams(sort="oldest"),
    "sort_salary": CatalogSearchParams(sort="salary"),
    "sort_relevance": CatalogSearchParams(sort="relevance", q="python"),
}


@pytest.mark.parametrize("case", list(UNSUPPORTED_SEARCH_PARAMS))
async def test_core_search_unsupported_params_make_no_request(case):
    """Filtros/orden sin equivalente en el /v1 (cota del core): la costura lo
    declara como CatalogUnsupportedError SIN emitir NI UNA peticion —
    fallback a local en core_read, 501 en core_primary."""

    def fail_on_any_request(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"busqueda no expresable emitio una peticion: {request.url}")

    catalog = make_core_catalog(transport=httpx.MockTransport(fail_on_any_request))
    with pytest.raises(CatalogUnsupportedError):
        await catalog.search(UNSUPPORTED_SEARCH_PARAMS[case])


async def test_core_search_source_filter_expands_legacy_prefix():
    """Cada fuente pedida viaja en sus DOS formas (original y `legacy:` del
    proyector sombra B-02) para casar core-nativas y proyectadas."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    catalog = make_core_catalog(transport=httpx.MockTransport(handler))
    await catalog.search(CatalogSearchParams(source="adzuna, jobroom"))
    assert seen["source"] == "adzuna,legacy:adzuna,jobroom,legacy:jobroom"


async def test_core_search_presents_original_source_for_shadow_listing():
    """El prefijo interno `legacy:` de la sombra NO se presenta (misma regla
    que el feed de matching) — ni en el brief ni en el detalle."""
    dto = _vacancy_dto("python_zurich")
    dto["primary_listing"]["source"] = "legacy:test_source"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/vacancies":
            return httpx.Response(200, json={"items": [dto], "next_cursor": None})
        return httpx.Response(200, json=dto)

    catalog = make_core_catalog(transport=httpx.MockTransport(handler))
    result = await catalog.search(CatalogSearchParams())
    assert result.data[0].source == "test_source"
    detail = await catalog.get(CASE_CORE_REFS["python_zurich"])
    assert detail.source == "test_source"


def _synthetic_feed(count: int) -> tuple[list[str], dict[str, dict]]:
    """Feed sintetico para ejercitar el recorrido por cursor keyset."""
    names = [f"synthetic_{i}" for i in range(count)]
    dtos = {}
    for name in names:
        dtos[name] = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, name)),
            "title": name,
            "company": "Corp",
            "description": None,
            "salary": None,
            "tags": [],
            "location": None,
            "remote": False,
            "primary_listing": {
                "source": "test_source",
                "url": f"https://example.com/{name}",
                "apply_url": None,
                "external_id": name,
                "first_seen_at": FIRST_SEEN.isoformat(),
                "last_seen_at": LAST_SEEN.isoformat(),
            },
            "listings": [],
            "translations": [],
        }
    return names, dtos


async def test_core_search_walks_cursor_pages_and_slices_offset():
    """PAGINACION/TOTAL: el cliente recorre el feed keyset COMPLETO (varias
    paginas), calcula el total exacto y pagina por offset localmente —
    mismo criterio que el cliente del feed de matching."""
    names, dtos = _synthetic_feed(5)
    page_size = 2  # el fake ignora `limit`: fuerza el recorrido por cursor
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        params = dict(request.url.params)
        cursor = params.get("cursor")
        start = names.index(cursor) + 1 if cursor in names else 0
        page = names[start : start + page_size]
        rest = names[start + page_size :]
        return httpx.Response(
            200,
            json={
                "items": [dtos[n] for n in page],
                "next_cursor": page[-1] if rest else None,
            },
        )

    catalog = make_core_catalog(transport=httpx.MockTransport(handler))
    result = await catalog.search(CatalogSearchParams(limit=2, offset=3))
    assert result.total == 5
    assert [b.title for b in result.data] == names[3:5]
    assert result.has_more is False
    assert len(requests) == 3  # 5 items en paginas de 2 => 3 peticiones


async def test_core_search_repeated_cursor_is_unavailable():
    """Un cursor repetido es un feed anomalo (bucle): CoreUnavailableError,
    nunca un recorrido infinito."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [], "next_cursor": "loop"})

    catalog = make_core_catalog(transport=httpx.MockTransport(handler))
    with pytest.raises(CoreUnavailableError, match="cursor repetido"):
        await catalog.search(CatalogSearchParams())


@pytest.mark.parametrize(
    "body",
    [
        [1, 2, 3],  # pagina no-objeto
        {"items": "no-lista"},  # items con tipo roto (ValidationError)
        {"items": [[1, 2]], "next_cursor": None},  # item no-dict
        {"items": [{"title": "sin id"}], "next_cursor": None},  # falta id
        {"items": [], "next_cursor": 42},  # next_cursor no-str
    ],
)
async def test_core_search_invalid_feed_payload_is_unavailable(body):
    """200 con pagina incompatible => CoreUnavailableError (fallback real en
    core_read), sea cual sea la forma del desvio."""
    catalog = make_core_catalog(transport=_transport_returning(b"", json_body=body))
    with pytest.raises(CoreUnavailableError):
        await catalog.search(CatalogSearchParams())


async def test_core_search_reuses_etag_cached_pages():
    """Refresco barato: la pagina cacheada por ETag se reutiliza con el 304
    del core (sin cuerpo)."""
    sent_inm: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inm = request.headers.get("if-none-match")
        sent_inm.append(inm)
        if inm == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            json={"items": [_vacancy_dto("python_zurich")], "next_cursor": None},
            headers={"ETag": '"v1"'},
        )

    catalog = make_core_catalog(transport=httpx.MockTransport(handler))
    first = await catalog.search(CatalogSearchParams())
    second = await catalog.search(CatalogSearchParams())
    assert first.total == second.total == 1
    assert second.data[0].title == CASES["python_zurich"]["title"]
    assert sent_inm == [None, '"v1"']

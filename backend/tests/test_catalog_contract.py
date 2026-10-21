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
- stats/fuentes: RETIRADAS (DT-148 A11). El /v1 no las expone, el cliente
  devolvia 501 incondicional y sus dos endpoints publicos no podian servir
  nada. Si el nucleo las publica, vuelven con su contrato.
"""

import hashlib
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from config import settings
from schemas.job import JobResponse, JobSearchResponse
from services.catalog import (
    CatalogSearchParams,
    CatalogUnsupportedError,
    CoreCatalog,
    CoreUnavailableError,
)
from services.catalog import core_client as catalog_core
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
    total = len(matched)
    matched = matched[int(params.get("offset", 0)) :]
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
            "total": total,
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


@pytest.fixture(autouse=True)
def _clean_catalog_feed_cache():
    """La cache de paginas por ETag es de modulo: se limpia por test."""
    clear_catalog_feed_cache()
    yield
    clear_catalog_feed_cache()


@pytest.fixture
async def catalogs():
    return {
        "core": (make_core_catalog(), CASE_CORE_REFS, MISSING_REFS["core"]),
    }


# ---------------------------------------------------------------------------
# get(): mismo juego de casos contra ambas implementaciones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("impl", ["core"])
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


@pytest.mark.parametrize("impl", ["core"])
async def test_get_missing_returns_none(catalogs, impl):
    catalog, _, missing_ref = catalogs[impl]
    assert await catalog.get(missing_ref) is None


# ---------------------------------------------------------------------------
# search: equivalencia local vs core; stats/sources: cota del contrato /v1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["stats", "sources"])
async def test_core_catalog_aggregates_no_longer_exist(catalogs, operation):
    """DT-148 A11: los agregados se RETIRARON, no son una cota.

    El /v1 no los expone, así que el cliente devolvia 501 incondicional y los
    dos endpoints publicos no podian servir nada: el desplegable de fuentes
    del buscador llevaba meses vacio y nadie lo veia. Cuando el nucleo los
    publique, vuelven con su contrato.
    """
    core_catalog, _, _ = catalogs["core"]
    assert not hasattr(core_catalog, operation)


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
    with pytest.raises(CoreUnavailableError):
        await catalog.recent(category="H", since=FIRST_SEEN)


async def test_core_down_raises_unavailable():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    catalog = make_core_catalog(transport=httpx.MockTransport(refuse))
    with pytest.raises(CoreUnavailableError):
        await catalog.get(CASE_CORE_REFS["python_zurich"])


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


# ---------------------------------------------------------------------------
# search por el feed /v1 (cierre de cota C-API-R + filtros 5e8e849)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("impl", ["core"])
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


def test_los_filtros_del_dto_son_los_que_el_v1_sirve():
    """DT-148 A18-01: el DTO modelaba nueve parametros que el /v1 rechaza.

    Aqui vivia `UNSUPPORTED_SEARCH_PARAMS` con sus nueve casos —canton,
    language, seniority, contract_type, salary_min/max y tres ordenes— y un
    test que comprobaba que la costura los declaraba imposibles SIN emitir
    peticion. Era correcto y no bastaba: el 501 llegaba igual al usuario,
    porque la interfaz seguia ofreciendolos. Se retiraron de punta a punta, y
    lo que se fija ahora es que el DTO no pueda volver a modelar lo que el
    catalogo no sirve. Si alguien le anade un campo, esto se pone rojo.
    """
    campos = set(CatalogSearchParams.__dataclass_fields__)
    assert campos == {"q", "source", "remote_only", "location", "limit", "offset"}


async def test_location_viaja_como_city_y_es_el_unico_filtro_de_ubicacion():
    """`location` sustituye al desplegable de cantones, que daba 501.

    El /v1 lo filtra como substring ci sobre el `location` de la canonica, que
    es el UNICO campo de ubicacion que el content lleva siempre (medido: 100 %
    de las vacantes activas, frente al 9,8 % de `canton`). Viaja con el nombre
    `city` porque asi se llama el parametro del /v1, no porque el dato sea una
    ciudad: es texto libre y acepta «Remote» igual que «Zurich».
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"items": [], "next_cursor": None, "total": 0})

    catalog = make_core_catalog(transport=httpx.MockTransport(handler))
    await catalog.search(CatalogSearchParams(location="Zurich"))
    assert seen["city"] == "Zurich"
    assert "canton" not in seen


async def test_una_busqueda_sin_ubicacion_no_envia_city():
    """Vacio significa «no filtrar», nunca `city=`: un filtro vacio que viaja
    es un filtro que el core tiene que evaluar para nada."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"items": [], "next_cursor": None, "total": 0})

    catalog = make_core_catalog(transport=httpx.MockTransport(handler))
    await catalog.search(CatalogSearchParams(location=""))
    assert "city" not in seen


async def test_un_csv_de_fuentes_demasiado_largo_no_emite_peticion():
    """El UNICO 501 que sobrevive a DT-148 A18-01, y se queda porque SI puede
    darse: el filtro de fuente viaja expandido (cada nombre mas su `legacy:`),
    asi que un CSV legitimo puede pasarse del `max_length=200` del Query del
    /v1. Enviarlo daria un 422 del core; declararlo imposible aqui evita la
    peticion. Antes este caso no tenia prueba propia: iba de polizon en la
    parametrizacion de los nueve filtros que ya no existen.
    """

    def fail_on_any_request(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"busqueda no expresable emitio una peticion: {request.url}")

    catalog = make_core_catalog(transport=httpx.MockTransport(fail_on_any_request))
    # 20 fuentes de 9 caracteres se expanden a mas de 200 con el prefijo.
    largo = ",".join(f"fuente{i:03d}" for i in range(20))
    with pytest.raises(CatalogUnsupportedError):
        await catalog.search(CatalogSearchParams(source=largo))


async def test_core_search_source_filter_expands_legacy_prefix():
    """Cada fuente pedida viaja en sus DOS formas (original y `legacy:` del
    proyector sombra B-02) para casar core-nativas y proyectadas."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"items": [], "next_cursor": None, "total": 0})

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
            return httpx.Response(
                200, json={"items": [dto], "next_cursor": None, "total": 1}
            )
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


async def test_core_search_sends_offset_and_preserves_total():
    """Mismo resultado público; una página interna en vez de todo el feed."""
    names, dtos = _synthetic_feed(5)
    calls = []

    def handler(request):
        params = dict(request.url.params)
        calls.append(params)
        start, size = int(params["offset"]), int(params["limit"])
        return httpx.Response(
            200,
            json={
                "items": [dtos[n] for n in names[start : start + size]],
                "next_cursor": None,
                "total": len(names),
            },
        )

    result = await make_core_catalog(transport=httpx.MockTransport(handler)).search(
        CatalogSearchParams(limit=2, offset=3),
    )
    assert result.total == 5 and not result.has_more
    assert [item.title for item in result.data] == names[3:5]
    assert calls == [{"limit": "2", "offset": "3"}]


async def test_core_recent_pages_by_cursor_until_the_feed_ends():
    """DT-148 A1: `recent` recorre por CURSOR y no exige `total`.

    El endpoint solo sirve `total` con offset; pedirlo aqui habria hecho
    fallar el aviso por un campo que el contrato no promete en este camino.
    """
    names, dtos = _synthetic_feed(3)
    calls = []

    def handler(request):
        params = dict(request.url.params)
        calls.append(params)
        if "cursor" not in params:
            return httpx.Response(
                200,
                json={"items": [dtos[names[0]], dtos[names[1]]], "next_cursor": "c1"},
            )
        return httpx.Response(
            200, json={"items": [dtos[names[2]]], "next_cursor": None}
        )

    offers = await make_core_catalog(transport=httpx.MockTransport(handler)).recent(
        category="H", since=FIRST_SEEN
    )

    assert [offer.title for offer in offers] == names
    assert all(isinstance(offer, JobResponse) for offer in offers)
    # La categoria y el `since` viajan como filtros del /v1, no se filtra local.
    assert calls[0]["category"] == "H"
    assert calls[0]["since"] == FIRST_SEEN.isoformat()
    assert calls[1]["cursor"] == "c1"


async def test_core_recent_stops_at_the_declared_limit():
    """El tope es una COTA declarada, no una pagina: deja de pedir al cubrirlo."""
    names, dtos = _synthetic_feed(4)
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(
            200, json={"items": [dtos[n] for n in names], "next_cursor": "mas"}
        )

    offers = await make_core_catalog(transport=httpx.MockTransport(handler)).recent(
        category="H", since=FIRST_SEEN, limit=2
    )

    assert len(offers) == 2 and len(calls) == 1


async def test_core_recent_refuses_a_cursor_that_does_not_advance():
    """Un cursor repetido es un core roto: falla cerrado en vez de dar vueltas."""
    names, dtos = _synthetic_feed(1)

    def handler(request):
        return httpx.Response(
            200, json={"items": [dtos[names[0]]], "next_cursor": "igual"}
        )

    with pytest.raises(CoreUnavailableError):
        await make_core_catalog(transport=httpx.MockTransport(handler)).recent(
            category="H", since=FIRST_SEEN
        )


@pytest.mark.parametrize("total", [None, -1, True, 1.5, "2", [], {}])
async def test_core_search_invalid_total_is_unavailable(total):
    """Core viejo/roto sin total exacto falla cerrado; nunca recuento parcial."""
    response = httpx.Response(
        200, json={"items": [], "next_cursor": None, "total": total}
    )
    catalog = make_core_catalog(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(CoreUnavailableError):
        await catalog.search(CatalogSearchParams())


@pytest.mark.parametrize(
    "body",
    [
        [1, 2, 3],  # pagina no-objeto
        {"items": "no-lista"},  # items con tipo roto (ValidationError)
        {"items": [[1, 2]], "next_cursor": None, "total": 1},  # item no-dict
        {"items": [{"title": "sin id"}], "next_cursor": None, "total": 1},  # falta id
        {"items": [], "next_cursor": 42, "total": 0},  # next_cursor no-str
    ],
)
async def test_core_search_invalid_feed_payload_is_unavailable(body):
    """200 con pagina incompatible => CoreUnavailableError (fallback real en
    core_read), sea cual sea la forma del desvio."""
    catalog = make_core_catalog(transport=_transport_returning(b"", json_body=body))
    with pytest.raises(CoreUnavailableError):
        await catalog.search(CatalogSearchParams())


async def test_core_search_large_total_cost_is_one_request():
    """20k results must not mean 200 HTTP calls or hydrate unrelated rows."""
    names, dtos = _synthetic_feed(2)
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "items": [dtos[n] for n in names],
                "total": 20000,
                "next_cursor": "opaque-next",
            },
        )

    result = await make_core_catalog(transport=httpx.MockTransport(handler)).search(
        CatalogSearchParams(limit=2, offset=12500),
    )
    assert result.total == 20000 and result.has_more
    assert [b.title for b in result.data] == names
    assert calls == [{"limit": "2", "offset": "12500"}]


async def test_core_search_cache_is_scoped_to_offset_and_limit():
    names, dtos = _synthetic_feed(2)
    calls = []

    def handler(request):
        params = dict(request.url.params)
        calls.append((params, request.headers.get("if-none-match")))
        offset = int(params["offset"])
        return httpx.Response(
            200,
            json={
                "items": [dtos[names[offset]]],
                "total": 2,
                "next_cursor": None,
            },
            headers={"ETag": f'"offset-{offset}"'},
        )

    catalog = make_core_catalog(transport=httpx.MockTransport(handler))
    first = await catalog.search(CatalogSearchParams(limit=1, offset=0))
    second = await catalog.search(CatalogSearchParams(limit=1, offset=1))
    assert first.data[0].title != second.data[0].title
    assert [inm for _, inm in calls] == [None, None]


async def test_core_search_reuses_fresh_page_then_revalidates_etag(monkeypatch):
    """La pagina vigente no toca Core; al caducar se revalida por ETag."""
    sent_inm: list[str | None] = []
    now = 1000.0
    monkeypatch.setattr(catalog_core.time, "monotonic", lambda: now)

    def handler(request: httpx.Request) -> httpx.Response:
        inm = request.headers.get("if-none-match")
        sent_inm.append(inm)
        if inm == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            json={
                "items": [_vacancy_dto("python_zurich")],
                "next_cursor": None,
                "total": 1,
            },
            headers={"ETag": '"v1"'},
        )

    catalog = make_core_catalog(transport=httpx.MockTransport(handler))
    first = await catalog.search(CatalogSearchParams())
    second = await catalog.search(CatalogSearchParams())
    now += catalog_core._PAGE_FRESH_SECONDS + 1
    third = await catalog.search(CatalogSearchParams())
    assert first.total == second.total == 1
    assert third.total == 1
    assert second.data[0].title == CASES["python_zurich"]["title"]
    assert sent_inm == [None, '"v1"']

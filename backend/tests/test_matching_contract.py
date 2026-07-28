"""CONTRACT TESTS de la capacidad MATCHING — A.SEAM (plan §15bis).

El MISMO juego de casos (CASES) se ejecuta contra las DOS implementaciones
del puerto MatchingPort:

- `local`: MatchResultService sobre `match_results` + `jobs` (BD de test).
- `core`: cliente del feed contra un /v1 FAKE cuya forma replica el contrato
  REAL del core (jobhunt_core/api/v1.py + api/schemas.py: MatchesPageDTO
  con items {vacancy, evaluation, state}, keyset opaco, ETag de pagina con
  If-None-Match -> 304, auth Bearer key_id.secret, 404 ErrorDTO). El backend
  legacy no importa jobhunt_core (frontera estricta, plan §21), por eso el
  DTO se replica aqui como dict.

Se afirma forma y semantica equivalentes DONDE EL CONTRATO LO EXIGE
(criterio unificador — docstring de services/matching/seam.py: mientras el
escritor sea LOCAL, nada visible es no-accionable y ningun estado local es
inaccesible):
- feed (results): mismos job_hash (identidad legacy via external_id del
  primary_listing `legacy:*` — el mapeo job_ref de la sombra), mismo orden
  (score_final DESC), mismo total, mismo contenido de la oferta y MISMO
  estado del escritor local (feedback/status/borrador superpuestos; el
  feedback negativo local EXCLUYE el item tambien en el core).
- items sin respaldo local accionable (core-nativos o sin Job local): se
  EXCLUYEN del feed servido por el core en esta etapa (su feedback local
  seria 404). Cota fijada aqui: reapareceran en Fase C con el flip de
  escritor + idempotency key.
- saved: proyeccion PURA del estado del escritor local — se sirve de LOCAL
  en TODOS los modos (equivalencia exigida tambien contra CoreMatching).
- La ESCALA de scores NO se compara (Fase A core = coseno; legacy =
  multifactor 0-100): el contrato solo exige la forma del breakdown.
"""

import hashlib
import json
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.job import Job
from models.match_result import MatchResult
from models.user import User
from routers.match import _to_match_response
from schemas.match import MatchResultResponse
from services.matching import (
    CoreMatching,
    CoreUnavailableError,
    FallbackMatching,
    LocalMatching,
    MatchingUnsupportedError,
    clear_feed_cache,
    core_client,
    resolve_matching,
    set_profile_link,
)
from services.matching.core_client import legacy_job_ref
from services.routing import (
    CAPABILITY_MATCHING,
    invalidate_routing_cache,
    set_routing,
)

# ---------------------------------------------------------------------------
# Juego de casos compartido
# ---------------------------------------------------------------------------

# Orden del feed: score_final DESC — la clave del dict NO define el orden.
CASES = {
    "python_zurich": {
        "title": "Python Developer",
        "company": "Acme Corp",
        "url": "https://example.com/job/python",
        "tags": ["python", "fastapi"],
        "description": "Build Python APIs with FastAPI and PostgreSQL",
        "score_final": 92.5,
        "similarity": 0.925,
        # Estado del ESCRITOR LOCAL que el core debe superponer:
        "feedback": "thumbs_up",
        "application_status": "drafted",
        "urgency_score": 3.5,
        "draft_letter": "Dear Hiring Team, ...",
    },
    "devops_remote": {
        "title": "DevOps Engineer",
        "company": "Beta AG",
        "url": "https://example.com/job/devops",
        "tags": [],
        "description": None,
        "score_final": 55.25,
        "similarity": 0.5525,
        "feedback": None,
        "application_status": "detected",
        "urgency_score": 0.0,
        "draft_letter": None,
    },
    # Feedback negativo LOCAL: debe desaparecer del feed en AMBOS backends
    # (en el core, via overlay del escritor local — la sombra no proyecta
    # match_results, el core no sabe nada de este thumbs_down).
    "rejected_one": {
        "title": "Sales Manager",
        "company": "Gamma SA",
        "url": "https://example.com/job/sales",
        "tags": ["sales"],
        "description": "Sell things",
        "score_final": 70.0,
        "similarity": 0.7,
        "feedback": "thumbs_down",
        "application_status": "detected",
        "urgency_score": 0.0,
        "draft_letter": None,
    },
}

# Visibles en el feed (sin feedback negativo), en orden score DESC.
VISIBLE_ORDER = ["python_zurich", "devops_remote"]

# Identidades: MD5 legacy compartido por AMBOS backends (el feed core viaja
# con external_id legacy en el primary_listing `legacy:*`); UUID de vacante
# solo interno al core.
CASE_HASHES = {n: hashlib.md5(n.encode()).hexdigest() for n in CASES}
CASE_VACANCY_IDS = {n: str(uuid.uuid5(uuid.NAMESPACE_URL, f"vac:{n}")) for n in CASES}

LEGACY_SOURCE = "test_source"
TEST_CONSUMER_KEY = "testkid.testsecret"
FAKE_PAGE_SIZE = 2  # fuerza paginacion keyset real en el fake

# Campos de la oferta cuya equivalencia EXIGE el contrato entre backends.
JOB_EQUIVALENT_FIELDS = (
    "job_title",
    "job_company",
    "job_url",
    "job_tags",
    "job_source",
)
# Estado del escritor local que el core debe superponer identico.
STATE_EQUIVALENT_FIELDS = (
    "feedback",
    "application_status",
    "urgency_score",
    "has_draft",
)


@pytest.fixture(autouse=True)
def _clean_seam_caches():
    """Las caches de routing y de paginas del feed son de proceso: sin esto
    un test podria leer el modo o la pagina de otro test."""
    invalidate_routing_cache()
    clear_feed_cache()
    yield
    invalidate_routing_cache()
    clear_feed_cache()


# ---------------------------------------------------------------------------
# Semilla local (usuario + jobs + match_results)
# ---------------------------------------------------------------------------


async def seed_local(db: AsyncSession) -> uuid.UUID:
    user = User(
        email=f"contract-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    for name, case in CASES.items():
        db.add(
            Job(
                hash=CASE_HASHES[name],
                source=LEGACY_SOURCE,
                title=case["title"],
                company=case["company"],
                url=case["url"],
                description=case["description"],
                description_snippet=(
                    case["description"][:500] if case["description"] else None
                ),
                tags=case["tags"],
                is_active=True,
            )
        )
        db.add(
            MatchResult(
                user_id=user.id,
                job_hash=CASE_HASHES[name],
                score_embedding=case["similarity"],
                score_salary=0.0,
                score_location=0.0,
                score_recency=0.0,
                score_llm=0.0,
                score_final=case["score_final"],
                matching_skills=[],
                missing_skills=[],
                feedback=case["feedback"],
                application_status=case["application_status"],
                urgency_score=case["urgency_score"],
                draft_letter=case["draft_letter"],
            )
        )
    await db.commit()
    return user.id


# ---------------------------------------------------------------------------
# /v1 fake fiel al contrato del feed (MatchesPageDTO + keyset + ETag + auth)
# ---------------------------------------------------------------------------


def _vacancy_dto(name: str, legacy: bool = True) -> dict:
    case = CASES[name]
    primary = {
        "source": f"legacy:{LEGACY_SOURCE}" if legacy else "partner_feed",
        "external_id": CASE_HASHES[name] if legacy else f"ext-{name}",
        "url": case["url"],
        "apply_url": None,
        "first_seen_at": "2026-07-01T08:00:00Z",
        "last_seen_at": "2026-07-20T08:00:00Z",
    }
    return {
        "id": CASE_VACANCY_IDS[name],
        "title": case["title"],
        "company": case["company"],
        "description": case["description"],
        "salary": None,
        "tags": case["tags"],
        "location": None,
        "remote": None,
        "primary_listing": primary,
        "listings": [
            {"source": primary["source"], "url": case["url"], "apply_url": None}
        ],
        "translations": [],
    }


def _match_dto(name: str, legacy: bool = True) -> dict:
    case = CASES[name]
    return {
        "vacancy": _vacancy_dto(name, legacy=legacy),
        "evaluation": {
            "eval_key": f"eval-{name}",
            "model": {"name": "e5-small-v2", "version": "1"},
            "policy": {"name": "cosine", "prompt_version": "0"},
            "score_final": case["score_final"],
            "scores": {"similarity": case["similarity"]},
            "explanation": None,
            "matching_skills": None,
            "missing_skills": None,
        },
        # Estado del CORE: sin escritor en esta etapa (la sombra no proyecta
        # match_results) — el overlay local es quien aporta feedback/status.
        "state": {"saved": False, "dismissed": False, "feedback": None, "notes": None},
    }


class FakeFeed:
    """Servidor /v1 fake del feed: keyset por indice, ETag por pagina."""

    def __init__(self, profile_id: uuid.UUID, items: list[dict]):
        self.profile_id = profile_id
        self.items = items
        self.requests: list[httpx.Request] = []
        self.hits_304 = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {TEST_CONSUMER_KEY}":
            return httpx.Response(
                401,
                json={
                    "code": "unauthorized",
                    "message": "credencial invalida",
                    "details": {},
                },
            )
        expected = f"/v1/profiles/{self.profile_id}/matches"
        if request.url.path != expected:
            return httpx.Response(
                404,
                json={
                    "code": "not_found",
                    "message": "perfil no encontrado",
                    "details": {},
                },
            )
        start = int(request.url.params.get("cursor", "0"))
        page = self.items[start : start + FAKE_PAGE_SIZE]
        next_cursor = (
            str(start + FAKE_PAGE_SIZE)
            if start + FAKE_PAGE_SIZE < len(self.items)
            else None
        )
        body = {"items": page, "next_cursor": next_cursor}
        etag = (
            '"'
            + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]
            + '"'
        )
        if request.headers.get("if-none-match") == etag:
            self.hits_304 += 1
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(200, json=body, headers={"ETag": etag})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def make_core_matching(
    db: AsyncSession,
    transport: httpx.MockTransport,
    bearer: str = TEST_CONSUMER_KEY,
) -> CoreMatching:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=1.0,
            transport=transport,
        )

    return CoreMatching(db, client_factory=factory)


@pytest.fixture
async def seeded(db_session):
    """Usuario + casos locales + vinculo de identidad + feed fake del core.

    Devuelve (user_id, matchings, fake) con `matchings` = dict impl -> puerto.
    """
    user_id = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, core_profile_id, updated_by="tests")
    ordered = sorted(CASES, key=lambda n: CASES[n]["score_final"], reverse=True)
    fake = FakeFeed(core_profile_id, [_match_dto(n) for n in ordered])
    matchings = {
        "local": LocalMatching(db_session),
        "core": make_core_matching(db_session, fake.transport()),
    }
    return user_id, matchings, fake


# ---------------------------------------------------------------------------
# results(): mismo juego de casos contra ambas implementaciones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("impl", ["local", "core"])
async def test_results_shape_maps_to_match_result_response(seeded, impl):
    """FORMA: los items de ambos backends pasan por el mapeo REAL del router
    (_to_match_response) y validan como MatchResultResponse."""
    user_id, matchings, _ = seeded
    items, total = await matchings[impl].results(user_id)
    assert total == len(VISIBLE_ORDER)
    for item in items:
        resp = _to_match_response(item, translations={})
        assert isinstance(resp, MatchResultResponse)
        assert resp.job_hash in CASE_HASHES.values()


async def test_results_semantics_equivalent_local_vs_core(seeded):
    """SEMANTICA: identidad (job_hash legacy via mapeo de la sombra), orden,
    total, contenido de la oferta y estado del escritor local IDENTICOS."""
    user_id, matchings, _ = seeded
    local_items, local_total = await matchings["local"].results(user_id)
    core_items, core_total = await matchings["core"].results(user_id)

    assert local_total == core_total == len(VISIBLE_ORDER)
    assert [i["match"].job_hash for i in local_items] == [
        CASE_HASHES[n] for n in VISIBLE_ORDER
    ]
    assert [i["match"].job_hash for i in core_items] == [
        CASE_HASHES[n] for n in VISIBLE_ORDER
    ]

    for local_item, core_item in zip(local_items, core_items):
        local_resp = _to_match_response(local_item, translations={})
        core_resp = _to_match_response(core_item, translations={})
        for field in JOB_EQUIVALENT_FIELDS + STATE_EQUIVALENT_FIELDS:
            assert getattr(local_resp, field) == getattr(core_resp, field), field
        assert local_resp.score_final == core_resp.score_final
        # id estable del escritor local en ambos (overlay del core).
        assert local_resp.id == core_resp.id


@pytest.mark.parametrize("impl", ["local", "core"])
async def test_results_exclude_local_negative_feedback(seeded, impl):
    """El thumbs_down escrito por el ESCRITOR LOCAL excluye el item tambien
    cuando el feed lo sirve el core (que no sabe nada de ese feedback)."""
    user_id, matchings, _ = seeded
    items, _ = await matchings[impl].results(user_id)
    hashes = {i["match"].job_hash for i in items}
    assert CASE_HASHES["rejected_one"] not in hashes


@pytest.mark.parametrize("impl", ["local", "core"])
async def test_results_pagination_slice(seeded, impl):
    """limit/offset con total EXACTO en ambos backends."""
    user_id, matchings, _ = seeded
    items, total = await matchings[impl].results(user_id, limit=1, offset=1)
    assert total == len(VISIBLE_ORDER)
    assert [i["match"].job_hash for i in items] == [CASE_HASHES[VISIBLE_ORDER[1]]]


async def test_core_walks_keyset_pages_and_preserves_order(seeded):
    """El cliente sigue next_cursor hasta agotar el feed (fake pagina de a
    FAKE_PAGE_SIZE aunque se pida mas) y conserva el orden score DESC."""
    user_id, matchings, fake = seeded
    items, _ = await matchings["core"].results(user_id)
    assert len(fake.requests) == 2  # ceil(3 casos / 2 por pagina)
    scores = [i["match"].score_final for i in items]
    assert scores == sorted(scores, reverse=True)


async def test_core_etag_second_pass_uses_304(seeded, db_session):
    """Paginacion con ETag REAL: la segunda pasada manda If-None-Match, el
    fake responde 304 sin cuerpo y el resultado es identico."""
    user_id, matchings, fake = seeded
    first, first_total = await matchings["core"].results(user_id)
    # Nueva instancia (nueva peticion HTTP): la cache de paginas es global.
    again = make_core_matching(db_session, fake.transport())
    second, second_total = await again.results(user_id)
    assert fake.hits_304 == 2  # ambas paginas revalidadas sin cuerpo
    assert first_total == second_total
    assert [i["match"].job_hash for i in first] == [i["match"].job_hash for i in second]


def _orphan_dto(orphan_hash: str, eval_key: str) -> dict:
    """Item del feed core con identidad legacy AJENA a todo match_result."""
    extra = _match_dto("devops_remote")
    extra["vacancy"]["id"] = str(uuid.uuid4())
    extra["vacancy"]["primary_listing"]["external_id"] = orphan_hash
    extra["evaluation"]["eval_key"] = eval_key
    return extra


def _add_job(db_session, job_hash: str) -> None:
    """Job local minimo (respaldo accionable) para una identidad huerfana."""
    db_session.add(
        Job(
            hash=job_hash,
            source=LEGACY_SOURCE,
            title=f"Orphan {job_hash[:8]}",
            company="Orphan Co",
            url=f"https://example.com/job/{job_hash}",
            is_active=True,
        )
    )


async def test_core_item_without_local_row_uses_model_defaults(db_session):
    """Item del feed core con Job local pero SIN fila match_result: se sirve
    con los defaults del modelo legacy (feedback None, status 'detected') e
    id deterministico."""
    user_id = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, core_profile_id)
    # identidad legacy DISTINTA de todo match_result local, con Job local
    orphan_hash = "a" * 32
    _add_job(db_session, orphan_hash)
    await db_session.commit()
    fake = FakeFeed(core_profile_id, [_orphan_dto(orphan_hash, "eval-orphan")])
    core = make_core_matching(db_session, fake.transport())
    items, total = await core.results(user_id)
    assert total == 1
    match = items[0]["match"]
    assert match.job_hash == orphan_hash
    assert match.feedback is None
    assert match.application_status == "detected"
    assert match.id == uuid.uuid5(uuid.NAMESPACE_URL, "jobhunt-core-eval:eval-orphan")


async def test_core_native_vacancy_excluded_until_phase_c(db_session):
    """Vacante SIN listing legacy (core-nativa): sin respaldo local
    accionable — su feedback local seria 404 — se EXCLUYE del feed (criterio
    unificador). Cota: reaparece en Fase C con el flip de escritor +
    idempotency key. La identidad que presentaria sigue fijada por
    legacy_job_ref: UUID CANONICO (leccion del MD5: jamas una forma ambigua
    de 32 hex)."""
    user_id = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, core_profile_id)
    dto = _match_dto("devops_remote", legacy=False)
    ref, is_legacy = legacy_job_ref(dto["vacancy"])
    assert not is_legacy
    assert ref == CASE_VACANCY_IDS["devops_remote"]
    assert str(uuid.UUID(ref)) == ref  # canonica, con guiones
    fake = FakeFeed(core_profile_id, [dto])
    core = make_core_matching(db_session, fake.transport())
    items, total = await core.results(user_id)
    assert items == [] and total == 0


async def test_core_legacy_item_without_local_job_excluded(db_session):
    """Item con identidad legacy pero SIN Job local: el upsert de feedback
    exige el Job (FK) => sin respaldo accionable, se excluye igual que el
    core-nativo (misma cota de Fase C)."""
    user_id = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, core_profile_id)
    fake = FakeFeed(core_profile_id, [_orphan_dto("b" * 32, "eval-no-job")])
    core = make_core_matching(db_session, fake.transport())
    items, total = await core.results(user_id)
    assert items == [] and total == 0


async def test_core_strips_shadow_source_prefix(seeded):
    """La fuente sombra `legacy:<source>` se presenta como la ORIGINAL."""
    user_id, matchings, _ = seeded
    items, _ = await matchings["core"].results(user_id)
    assert {i["job"].source for i in items} == {LEGACY_SOURCE}


# ---------------------------------------------------------------------------
# saved(): proyeccion del escritor LOCAL — se sirve de local en TODOS los modos
# ---------------------------------------------------------------------------


async def test_saved_local_serves_positive_feedback(seeded):
    user_id, matchings, _ = seeded
    items, total = await matchings["local"].saved(user_id)
    assert total == 1
    assert items[0]["match"].job_hash == CASE_HASHES["python_zurich"]


async def test_saved_served_from_local_writer_in_all_modes(seeded):
    """'saved' es proyeccion PURA del estado del escritor LOCAL (feedback
    positivo): mientras el escritor sea local (hasta Fase C) se sirve de
    local tambien detras de CoreMatching — criterio unificador: ningun
    estado local puede ser inaccesible por el routing."""
    user_id, matchings, _ = seeded
    local_items, local_total = await matchings["local"].saved(user_id)
    core_items, core_total = await matchings["core"].saved(user_id)
    assert core_total == local_total == 1
    assert [i["match"].job_hash for i in core_items] == [
        i["match"].job_hash for i in local_items
    ]
    assert core_items[0]["match"].job_hash == CASE_HASHES["python_zurich"]


# ---------------------------------------------------------------------------
# Contrato de identidad, auth y fallo del cliente core
# ---------------------------------------------------------------------------


async def test_core_unlinked_user_makes_no_request(db_session):
    """Sin vinculo en jobhunt_profile_map: CoreUnavailableError y CERO
    peticiones (el transporte falla el test si recibe alguna)."""

    def fail_on_any_request(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"peticion emitida sin vinculo de identidad: {request.url}")

    user_id = await seed_local(db_session)
    core = make_core_matching(db_session, httpx.MockTransport(fail_on_any_request))
    with pytest.raises(CoreUnavailableError):
        await core.results(user_id)


async def test_core_without_configured_credential_makes_no_request(
    db_session, monkeypatch
):
    """Con vinculo pero sin CORE_CONSUMER_KEY: ni una peticion (factory
    por defecto de produccion)."""
    user_id = await seed_local(db_session)
    await set_profile_link(db_session, user_id, uuid.uuid4())
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    core = CoreMatching(db_session)  # factory por defecto (produccion)
    with pytest.raises(CoreUnavailableError):
        await core.results(user_id)


async def test_core_rejects_bad_credential_as_unavailable(seeded, db_session):
    user_id, _, fake = seeded
    core = make_core_matching(
        db_session, fake.transport(), bearer="wrongkid.wrongsecret"
    )
    with pytest.raises(CoreUnavailableError):
        await core.results(user_id)


async def test_core_stale_profile_link_404_is_unavailable(db_session):
    """Vinculo apuntando a un perfil que el core no reconoce para este
    consumer (404 indistinguible del contrato): configuracion, no datos."""
    user_id = await seed_local(db_session)
    await set_profile_link(db_session, user_id, uuid.uuid4())
    # El fake solo conoce OTRO perfil => responde 404 para el vinculado.
    fake = FakeFeed(uuid.uuid4(), [])
    core = make_core_matching(db_session, fake.transport())
    with pytest.raises(CoreUnavailableError):
        await core.results(user_id)


async def test_core_down_raises_unavailable(db_session):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    user_id = await seed_local(db_session)
    await set_profile_link(db_session, user_id, uuid.uuid4())
    core = make_core_matching(db_session, httpx.MockTransport(refuse))
    with pytest.raises(CoreUnavailableError):
        await core.results(user_id)


async def test_core_repeated_cursor_hits_loop_guard(db_session):
    """Un feed anomalo que repite cursor no cuelga el BFF: cota anti-bucle."""
    user_id = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, core_profile_id)

    def looping(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [], "next_cursor": "LOOP"})

    core = make_core_matching(db_session, httpx.MockTransport(looping))
    with pytest.raises(CoreUnavailableError, match="cursor repetido"):
        await core.results(user_id)


# ---------------------------------------------------------------------------
# Routing: la costura resuelve la implementacion por perfil
# ---------------------------------------------------------------------------


async def test_resolve_matching_default_is_local(db_session):
    port = await resolve_matching(db_session, uuid.uuid4())
    assert isinstance(port, LocalMatching)


async def test_resolve_matching_core_read_is_fallback(db_session):
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_MATCHING, "core_read", profile_id=user_id)
    port = await resolve_matching(db_session, user_id)
    assert isinstance(port, FallbackMatching)


async def test_resolve_matching_core_primary_has_no_fallback(db_session):
    user_id = uuid.uuid4()
    await set_routing(
        db_session, CAPABILITY_MATCHING, "core_primary", profile_id=user_id
    )
    port = await resolve_matching(db_session, user_id)
    assert isinstance(port, CoreMatching)


async def test_resolve_matching_profile_row_beats_wildcard(db_session):
    """Canary POR PERFIL: la fila exacta gana al comodin del consumer."""
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_MATCHING, "core_primary")  # comodin
    await set_routing(db_session, CAPABILITY_MATCHING, "local", profile_id=user_id)
    assert isinstance(await resolve_matching(db_session, user_id), LocalMatching)
    assert isinstance(await resolve_matching(db_session, uuid.uuid4()), CoreMatching)


# ---------------------------------------------------------------------------
# Canary core_read: fallback funcional + severidades separadas
# ---------------------------------------------------------------------------


async def test_fallback_serves_local_when_core_down(seeded, db_session):
    user_id, matchings, _ = seeded

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    seam = FallbackMatching(
        make_core_matching(db_session, httpx.MockTransport(refuse)),
        matchings["local"],
    )
    items, total = await seam.results(user_id)
    assert total == len(VISIBLE_ORDER)
    assert [i["match"].job_hash for i in items] == [
        CASE_HASHES[n] for n in VISIBLE_ORDER
    ]
    # saved se sirve del escritor LOCAL en todos los modos (sin red): no
    # necesita fallback aunque el core este caido.
    items, total = await seam.saved(user_id)
    assert total == 1


async def test_canary_warn_levels_separate_expected_from_actionable(caplog):
    """Regla de la 2ª rev. A.SEAM catalogo, aplicada a matching: Unsupported
    (cota del escritor, esperado) va a DEBUG; el core CAIDO
    (CoreUnavailableError) es el UNICO WARNING del canary."""
    import logging

    class _Primary:
        async def saved(self, user_id, limit=100, offset=0):
            raise MatchingUnsupportedError("cota escritor")

        async def results(self, user_id, limit=20, offset=0):
            raise CoreUnavailableError("core caido")

    class _Fallback:
        async def saved(self, user_id, limit=100, offset=0):
            return [], 0

        async def results(self, user_id, limit=20, offset=0):
            return [], 0

    seam = FallbackMatching(_Primary(), _Fallback())
    with caplog.at_level(logging.DEBUG, logger="services.matching.seam"):
        await seam.saved(uuid.uuid4())
        await seam.results(uuid.uuid4())
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warns) == 1 and "core caido" in warns[0].getMessage()
    assert len(debugs) == 1 and "cota escritor" in debugs[0].getMessage()


# ---------------------------------------------------------------------------
# Router: la costura llega a HTTP con las semanticas de la matriz de escritor
# ---------------------------------------------------------------------------


async def _register(client) -> tuple[uuid.UUID, dict]:
    email = f"seam-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "TestPass123!", "gdpr_consent": True},
    )
    assert resp.status_code == 201
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    return uuid.UUID(me.json()["id"]), headers


async def test_router_core_primary_without_core_feed_503_saved_local_200(
    client, db_session, monkeypatch
):
    """core_primary SIN fallback silencioso: core inaccesible => 503 en el
    feed; 'saved' (proyeccion del escritor LOCAL, sin red) se sirve local
    => 200 en todos los modos — criterio unificador: ningun estado local
    puede ser inaccesible por el routing (antes aqui se fijaba un 501)."""
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    user_id, headers = await _register(client)
    await set_routing(
        db_session, CAPABILITY_MATCHING, "core_primary", profile_id=user_id
    )
    resp = await client.get("/api/v1/match/results", headers=headers)
    assert resp.status_code == 503
    resp = await client.get("/api/v1/match/history", headers=headers)
    assert resp.status_code == 503
    resp = await client.get("/api/v1/match/saved", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"] == [] and resp.json()["total"] == 0


async def test_router_orphan_feedback_discards_from_core_feed(
    client, db_session, monkeypatch
):
    """Huerfano legacy (Job local SIN fila MatchResult) servido por el core:
    thumbs_down => 200 (upsert de fila minima del escritor local) y el item
    DESAPARECE del feed en el siguiente GET — la garantia "not-for-me
    desaparece" se mantiene tambien para huerfanos (criterio unificador)."""
    user_id, headers = await _register(client)
    orphan_hash = "c" * 32
    _add_job(db_session, orphan_hash)
    await db_session.commit()
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, core_profile_id)
    fake = FakeFeed(core_profile_id, [_orphan_dto(orphan_hash, "eval-router-orph")])

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            headers={"Authorization": f"Bearer {TEST_CONSUMER_KEY}"},
            timeout=1.0,
            transport=fake.transport(),
        )

    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", TEST_CONSUMER_KEY)
    monkeypatch.setattr(core_client, "default_client_factory", factory)
    await set_routing(
        db_session, CAPABILITY_MATCHING, "core_primary", profile_id=user_id
    )

    resp = await client.get(
        "/api/v1/match/results", headers=headers, params={"translate": "false"}
    )
    assert resp.status_code == 200
    assert [r["job_hash"] for r in resp.json()["data"]] == [orphan_hash]

    resp = await client.post(
        f"/api/v1/match/{orphan_hash}/feedback",
        headers=headers,
        json={"feedback": "thumbs_down"},
    )
    assert resp.status_code == 200

    resp = await client.get(
        "/api/v1/match/results", headers=headers, params={"translate": "false"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == [] and resp.json()["total"] == 0


async def test_router_core_read_falls_back_to_local(client, db_session, monkeypatch):
    """core_read = canary: con el core inaccesible el feed se sirve LOCAL."""
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    user_id, headers = await _register(client)
    await set_routing(db_session, CAPABILITY_MATCHING, "core_read", profile_id=user_id)
    resp = await client.get(
        "/api/v1/match/results", headers=headers, params={"translate": "false"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == [] and resp.json()["total"] == 0
    resp = await client.get("/api/v1/match/saved", headers=headers)
    assert resp.status_code == 200


async def test_router_default_local_untouched(client, db_session):
    """Sin filas de routing todo sigue local (default seguro del plan)."""
    _, headers = await _register(client)
    resp = await client.get(
        "/api/v1/match/results", headers=headers, params={"translate": "false"}
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

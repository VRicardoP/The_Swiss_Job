"""CONTRACT TESTS de la capacidad CANDIDATURAS — A.SEAM (plan §15bis).

Variante LIGERA de la costura: el /v1 del core (jobhunt_core/api/v1.py) NO
expone candidaturas en Fase A (solo vacancies/profiles/matches; las tablas
existen en su esquema, sin API) — la cota contractual es
ApplicationsUnsupportedError en TODAS las operaciones del puerto, fijada
aqui (patron search/stats de catalogo): cuando el core publique endpoints de
candidatura estos tests pasan a exigir equivalencia.

CRITERIO UNIFICADOR (heredado de A.SEAM matching): el UNICO escritor del
estado (job_applications + state machine sobre match_results) es LOCAL hasta
Fase C => el estado es accesible en TODOS los modos de routing, incluida
core_primary — nunca 501/503 por routing. Fijado aqui a nivel de resolver y
de HTTP.
"""

import logging
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models.enums import ApplicationStatus
from models.job import Job
from models.match_result import MatchResult
from models.user import User
from schemas.applications import ApplicationResponse, ApplicationUpdate
from services.applications import (
    ApplicationJobNotFoundError,
    ApplicationsUnsupportedError,
    CoreApplications,
    CoreUnavailableError,
    DuplicateApplicationError,
    FallbackApplications,
    LocalApplications,
    resolve_applications,
)
from services.routing import (
    CAPABILITY_APPLICATIONS,
    invalidate_routing_cache,
    set_routing,
)

JOB_HASH = "a" * 32
OTHER_HASH = "b" * 32


@pytest.fixture(autouse=True)
def _fresh_routing_cache():
    """La cache del routing es por proceso: sin esto un test podria leer el
    modo de otro test."""
    invalidate_routing_cache()
    yield
    invalidate_routing_cache()


# ---------------------------------------------------------------------------
# Semilla local (usuario + job + match)
# ---------------------------------------------------------------------------


async def seed_user(db: AsyncSession) -> uuid.UUID:
    user = User(
        email=f"contract-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user.id


async def seed_job(db: AsyncSession, job_hash: str = JOB_HASH) -> str:
    db.add(
        Job(
            hash=job_hash,
            source="test_source",
            title="Primary Teacher",
            company="Zurich Intl School",
            url=f"https://example.com/job/{job_hash[:6]}",
            location="Zurich, ZH",
            is_active=True,
        )
    )
    return job_hash


async def seed_match(
    db: AsyncSession,
    user_id: uuid.UUID,
    job_hash: str = JOB_HASH,
    application_status: str = "detected",
    draft_letter: str | None = None,
) -> None:
    db.add(
        MatchResult(
            user_id=user_id,
            job_hash=job_hash,
            score_embedding=0.9,
            score_salary=0.0,
            score_location=0.0,
            score_recency=0.0,
            score_llm=0.0,
            score_final=0.9,
            matching_skills=[],
            missing_skills=[],
            application_status=application_status,
            draft_letter=draft_letter,
        )
    )


# ---------------------------------------------------------------------------
# Cota /v1: el core NO expone candidaturas — Unsupported en TODA operacion
# ---------------------------------------------------------------------------

# Una entrada POR OPERACION del puerto: la cota es TOTAL y queda fijada aqui.
CORE_OPS = {
    "list": lambda c, u: c.list(u),
    "create": lambda c, u: c.create(u, JOB_HASH),
    "stats": lambda c, u: c.stats(u),
    "update": lambda c, u: c.update(u, uuid.uuid4(), ApplicationUpdate()),
    "delete": lambda c, u: c.delete(u, uuid.uuid4()),
    "set_match_status": lambda c, u: c.set_match_status(u, JOB_HASH, "reviewed"),
    "get_match": lambda c, u: c.get_match(u, JOB_HASH),
    "save_draft": lambda c, u: c.save_draft(u, JOB_HASH, "draft"),
    "get_draft": lambda c, u: c.get_draft(u, JOB_HASH),
}


@pytest.mark.parametrize("op", list(CORE_OPS))
async def test_core_applications_operations_not_in_v1_contract(op):
    """El /v1 del core no expone candidaturas (Fase A): la costura lo declara
    como ApplicationsUnsupportedError, no lo simula. CoreApplications no
    necesita credencial ni abre cliente HTTP: cero peticiones por
    construccion."""
    core = CoreApplications()
    with pytest.raises(ApplicationsUnsupportedError):
        await CORE_OPS[op](core, uuid.uuid4())


def test_core_covers_entire_port_surface():
    """La cota es TOTAL: cada operacion publica del puerto local existe en el
    cliente core (con Unsupported) y esta fijada en CORE_OPS — anadir una
    operacion al puerto obliga a decidir su cota aqui."""
    local_ops = {n for n in vars(LocalApplications) if not n.startswith("_")}
    core_ops = {n for n in vars(CoreApplications) if not n.startswith("_")}
    assert local_ops == core_ops == set(CORE_OPS)


# ---------------------------------------------------------------------------
# LOCAL: evidencia a nivel de puerto (la HTTP la dan los tests preexistentes)
# ---------------------------------------------------------------------------


async def test_local_create_list_stats_roundtrip(db_session):
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await db_session.commit()
    port = LocalApplications(db_session)

    created = await port.create(user_id, JOB_HASH, notes="hola")
    assert isinstance(created, ApplicationResponse)
    assert created.status == ApplicationStatus.saved
    assert created.job_title == "Primary Teacher"
    assert created.notes == "hola"

    listed = await port.list(user_id)
    assert listed.total == 1
    assert listed.by_status == {"saved": 1}
    assert listed.data[0].id == created.id

    stats = await port.stats(user_id)
    assert stats.by_status == {"saved": 1}
    assert stats.by_source == {"test_source": 1}
    assert stats.conversion_rates["saved_to_applied"] == 0.0


async def test_local_create_errors_are_domain_bounds(db_session):
    """Oferta inexistente y duplicado son errores del DOMINIO del puerto
    (el router los traduce a 404/409)."""
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await db_session.commit()
    port = LocalApplications(db_session)

    with pytest.raises(ApplicationJobNotFoundError):
        await port.create(user_id, "f" * 32)

    await port.create(user_id, JOB_HASH)
    with pytest.raises(DuplicateApplicationError):
        await port.create(user_id, JOB_HASH)


async def test_local_update_delete_semantics(db_session):
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await db_session.commit()
    port = LocalApplications(db_session)
    created = await port.create(user_id, JOB_HASH)

    # Desconocidos: None / False (el router los hace 404).
    assert await port.update(user_id, uuid.uuid4(), ApplicationUpdate()) is None
    assert await port.delete(user_id, uuid.uuid4()) is False

    # Auto-transicion verbatim: status=applied fija applied_at si faltaba.
    updated = await port.update(
        user_id, created.id, ApplicationUpdate(status=ApplicationStatus.applied)
    )
    assert updated.status == ApplicationStatus.applied
    assert updated.applied_at is not None

    assert await port.delete(user_id, created.id) is True
    assert (await port.list(user_id)).total == 0


async def test_local_state_machine_roundtrip(db_session):
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await seed_match(db_session, user_id, application_status="reviewed")
    await db_session.commit()
    port = LocalApplications(db_session)

    # Sin match: False / None (el router los hace 404).
    assert await port.set_match_status(user_id, OTHER_HASH, "sent") is False
    assert await port.get_match(user_id, OTHER_HASH) is None
    assert await port.save_draft(user_id, OTHER_HASH, "x") is False
    assert await port.get_draft(user_id, OTHER_HASH) is None

    assert await port.set_match_status(user_id, JOB_HASH, "sent") is True
    match, job = await port.get_match(user_id, JOB_HASH)
    assert match.application_status == "sent"
    assert job.hash == JOB_HASH

    # save_draft verbatim: NO avanza desde 'sent' (solo detected/reviewed).
    assert await port.save_draft(user_id, JOB_HASH, "Dear school") is True
    assert await port.get_draft(user_id, JOB_HASH) == "Dear school"
    match, _ = await port.get_match(user_id, JOB_HASH)
    assert match.application_status == "sent"


async def test_local_save_draft_advances_from_reviewed(db_session):
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await seed_match(db_session, user_id, application_status="reviewed")
    await db_session.commit()
    port = LocalApplications(db_session)

    assert await port.save_draft(user_id, JOB_HASH, "Draft v1") is True
    match, _ = await port.get_match(user_id, JOB_HASH)
    assert match.application_status == "drafted"
    assert match.draft_letter == "Draft v1"


# ---------------------------------------------------------------------------
# Resolucion por jobhunt_routing (criterio unificador incluido)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode", [None, "local", "shadow", "core_primary", "rollback_pending"]
)
async def test_resolve_applications_serves_local_writer(db_session, mode):
    """Todo modo salvo core_read resuelve a LOCAL — incluida core_primary:
    criterio unificador, el unico escritor del estado es local y el /v1 no
    expone la capacidad => nunca 501/503 por routing (None = sin fila,
    default del plan)."""
    user_id = uuid.uuid4()
    if mode is not None:
        await set_routing(db_session, CAPABILITY_APPLICATIONS, mode, profile_id=user_id)
    port = await resolve_applications(db_session, user_id)
    assert isinstance(port, LocalApplications)


async def test_resolve_applications_core_read_is_fallback(db_session):
    user_id = uuid.uuid4()
    await set_routing(
        db_session, CAPABILITY_APPLICATIONS, "core_read", profile_id=user_id
    )
    port = await resolve_applications(db_session, user_id)
    assert isinstance(port, FallbackApplications)


async def test_resolve_applications_profile_row_beats_wildcard(db_session):
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_APPLICATIONS, "core_read")  # comodin
    await set_routing(db_session, CAPABILITY_APPLICATIONS, "local", profile_id=user_id)
    assert isinstance(
        await resolve_applications(db_session, user_id), LocalApplications
    )
    assert isinstance(
        await resolve_applications(db_session, uuid.uuid4()), FallbackApplications
    )


# ---------------------------------------------------------------------------
# Canary core_read: cae a local con severidades heredadas
# ---------------------------------------------------------------------------


async def test_core_read_falls_back_to_local_for_v1_bound(db_session):
    """En core_read la cota Unsupported del /v1 cae a local: el estado del
    escritor local sigue sirviendose (aqui, la lectura del feed de
    candidaturas)."""
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await db_session.commit()
    seam = FallbackApplications(CoreApplications(), LocalApplications(db_session))
    created = await seam.create(user_id, JOB_HASH)
    listed = await seam.list(user_id)
    assert listed.total == 1
    assert listed.data[0].id == created.id


async def test_canary_warn_levels_separate_expected_from_actionable(caplog):
    """Severidades heredadas (2ª rev. A.SEAM catalogo): Unsupported (cota
    /v1, esperado) va a DEBUG; el core CAIDO (CoreUnavailableError) es el
    UNICO WARNING — la senal del canary no puede ahogarse en ruido esperado
    por contrato."""

    class _Unsupported:
        async def list(self, user_id, status=None, limit=50, offset=0):
            raise ApplicationsUnsupportedError("cota contrato")

    class _Down:
        async def list(self, user_id, status=None, limit=50, offset=0):
            raise CoreUnavailableError("core caido")

    class _Fallback:
        async def list(self, user_id, status=None, limit=50, offset=0):
            return None

    with caplog.at_level(logging.DEBUG, logger="services.applications.seam"):
        await FallbackApplications(_Unsupported(), _Fallback()).list(uuid.uuid4())
        await FallbackApplications(_Down(), _Fallback()).list(uuid.uuid4())
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warns) == 1 and "core caido" in warns[0].getMessage()
    assert len(debugs) == 1 and "cota contrato" in debugs[0].getMessage()


# ---------------------------------------------------------------------------
# Router: el estado local es accesible en TODOS los modos (nunca 501/503)
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


@pytest.mark.parametrize(
    "mode", ["local", "shadow", "core_read", "core_primary", "rollback_pending"]
)
async def test_router_local_state_accessible_in_all_modes(client, db_session, mode):
    """Escrituras Y lecturas de candidatura sirven en los 5 modos de routing
    — incluida core_primary (criterio unificador): jamas un 501/503 para
    estado cuyo unico escritor es local."""
    user_id, headers = await _register(client)
    await seed_job(db_session)
    await seed_match(db_session, user_id, draft_letter="Saved draft")
    await db_session.commit()
    await set_routing(db_session, CAPABILITY_APPLICATIONS, mode, profile_id=user_id)

    # Escritura CRUD + lecturas
    resp = await client.post(
        "/api/v1/applications", headers=headers, json={"job_hash": JOB_HASH}
    )
    assert resp.status_code == 201
    resp = await client.get("/api/v1/applications", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    resp = await client.get("/api/v1/applications/stats", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["by_status"] == {"saved": 1}

    # State machine watchlist (escritura) + borrador (lectura)
    resp = await client.post(
        f"/api/v1/watchlist/match/{JOB_HASH}/status",
        headers=headers,
        json={"application_status": "reviewed"},
    )
    assert resp.status_code == 200
    resp = await client.get(
        f"/api/v1/watchlist/match/{JOB_HASH}/draft", headers=headers
    )
    assert resp.status_code == 200
    assert resp.text == "Saved draft"


async def test_router_default_local_untouched(client, db_session):
    """Sin filas de routing todo sigue local (default seguro del plan)."""
    _, headers = await _register(client)
    await seed_job(db_session)
    await db_session.commit()
    resp = await client.post(
        "/api/v1/applications", headers=headers, json={"job_hash": JOB_HASH}
    )
    assert resp.status_code == 201

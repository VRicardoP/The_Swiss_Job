"""CONTRACT TESTS de la capacidad CANDIDATURAS — escrituras C-4 (plan §15bis).

El /v1 del core expone el CRUD de candidaturas (jobhunt_core/api/
v1_applications.py, DISENO_C4_ESCRITURAS_V2_1) y `CoreApplications` es su
cliente HTTP real. Estos tests fijan:

- EQUIVALENCIA local-vs-core de las operaciones CRUD+stats servidas por ambos
  puertos (mismo contrato de respuesta para datos escritos por este BFF),
  contra un fake in-memory del /v1 que respeta el contrato C-4 (composite
  feed con kind, 409 application_exists, PATCH parcial, 404 de recurso).
- IDENTIDAD multi-perfil: el vinculo usuario→perfil core es POR USUARIO via
  jobhunt_profile_map (NO comodin) — sin vinculo o sin credencial, CERO
  peticiones.
- IDENTIDAD de vacante: round-trip del job_hash por la url del Job local;
  fallback determinista compute_hash(title|company|url) del snapshot.
- COTAS honestas: state machine de match_results y applied_url →
  ApplicationsUnsupportedError; bookmarks puros excluidos; follow_up_date
  date-vs-datetime; applied_at no representable (None).
- RESOLUCION por routing: escritor core SOLO en core_primary/rollback_pending
  (CoreWriterApplications, sin fallback silencioso; state machine siempre
  local); core_read declarado equivalente a local (durable de escritor local).
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
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
    CoreWriterApplications,
    DuplicateApplicationError,
    LocalApplications,
    resolve_applications,
)
from services.job_service import BaseJobProvider
from services.matching.identity import set_profile_link
from services.routing import (
    CAPABILITY_APPLICATIONS,
    invalidate_routing_cache,
    set_routing,
)

logger = logging.getLogger(__name__)

JOB_HASH = "a" * 32
OTHER_HASH = "b" * 32
TEST_CONSUMER_KEY = "key_test.secret_test"

# Los 8 estados del contrato C-4 == los 8 del enum legacy (identidad).
CORE_STATUSES = (
    "saved",
    "applied",
    "phone_screen",
    "technical",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
)


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
# Fake in-memory del /v1 C-4 (contrato de jobhunt_core/api/v1_applications.py)
# ---------------------------------------------------------------------------


class FakeCoreV1:
    """Doble del /v1 de candidaturas: feed compuesto con `kind`, candado 409
    por (perfil, url), PATCH parcial de status/notes/follow_up_date, 404 de
    recurso, paginacion por cursor opcional. Registra las cabeceras de
    escritura para fijar la Idempotency-Key del contrato."""

    def __init__(self, core_profile_id: uuid.UUID, page_size: int | None = None):
        self.profile_id = str(core_profile_id)
        self.items: dict[str, dict] = {}  # id -> item DTO (dict serializable)
        self.write_headers: list[dict] = []
        self.page_size = page_size
        self._clock = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # reloj determinista y monotono para created_at/updated_at
    def _tick(self) -> str:
        self._clock += timedelta(minutes=1)
        return self._clock.isoformat()

    def add_item(self, *, kind: str = "application", **overrides) -> dict:
        """Siembra directa de un item del feed (p.ej. un bookmark puro o un
        item no escrito por este BFF)."""
        ts = self._tick()
        item = {
            "id": str(uuid.uuid4()),
            "profile_id": self.profile_id,
            "vacancy_id": str(uuid.uuid4()),
            "kind": kind,
            "status": "saved",
            "notes": None,
            "follow_up_date": None,
            "created_at": ts,
            "updated_at": ts,
            "title": "Seeded",
            "company": "Seeded Co",
            "url": None,
            "source": None,
            "description": None,
        }
        item.update(overrides)
        self.items[item["id"]] = item
        return item

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == f"Bearer {TEST_CONSUMER_KEY}"
        if request.method in ("POST", "PATCH", "DELETE"):
            self.write_headers.append(dict(request.headers))
        path = request.url.path
        if request.method == "GET" and path == "/v1/applications":
            return self._list(request)
        if request.method == "POST" and path == "/v1/applications":
            return self._create(request)
        item_id = path.rsplit("/", 1)[-1]
        if request.method == "PATCH":
            return self._patch(item_id, request)
        if request.method == "DELETE":
            return self._delete(item_id)
        return httpx.Response(404, json={"code": "not_found", "message": "?"})

    def _list(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("profile") != self.profile_id:
            return httpx.Response(404, json={"code": "not_found", "message": "perfil"})
        ordered = sorted(
            self.items.values(), key=lambda i: (i["created_at"], i["id"]), reverse=True
        )
        if self.page_size is None:
            return httpx.Response(200, json={"items": ordered, "next_cursor": None})
        start = int(params.get("cursor") or 0)
        page = ordered[start : start + self.page_size]
        nxt = start + self.page_size
        cursor = str(nxt) if nxt < len(ordered) else None
        return httpx.Response(200, json={"items": page, "next_cursor": cursor})

    def _create(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("profile_id") != self.profile_id:
            return httpx.Response(404, json={"code": "not_found", "message": "perfil"})
        url = body.get("url")
        if url and any(
            i["url"] == url and i["kind"] == "application" for i in self.items.values()
        ):
            return httpx.Response(
                409, json={"code": "application_exists", "message": "dup"}
            )
        item = self.add_item(
            kind="application",
            status=body.get("status") or "saved",
            notes=body.get("notes"),
            follow_up_date=body.get("follow_up_date"),
            title=body.get("title"),
            company=body.get("company"),
            url=url,
            source=body.get("source"),
            description=body.get("description"),
        )
        return httpx.Response(201, json=item)

    def _patch(self, item_id: str, request: httpx.Request) -> httpx.Response:
        item = self.items.get(item_id)
        if item is None:
            return httpx.Response(404, json={"code": "not_found", "message": "?"})
        body = json.loads(request.content)
        for key in ("status", "notes", "follow_up_date"):
            if key in body:
                item[key] = body[key]
        item["updated_at"] = self._tick()
        return httpx.Response(200, json=item)

    def _delete(self, item_id: str) -> httpx.Response:
        if self.items.pop(item_id, None) is None:
            return httpx.Response(404, json={"code": "not_found", "message": "?"})
        return httpx.Response(204)


def make_core(
    db: AsyncSession,
    transport: httpx.MockTransport,
    bearer: str = TEST_CONSUMER_KEY,
) -> CoreApplications:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=1.0,
            transport=transport,
        )

    return CoreApplications(db, client_factory=factory)


def fail_on_any_request(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"peticion inesperada al core: {request.url}")


@pytest.fixture
async def seeded(db_session):
    """Usuario enrolado (vinculo POR USUARIO en jobhunt_profile_map) + jobs
    locales + fake del /v1. Devuelve (user_id, fake, core)."""
    user_id = await seed_user(db_session)
    await seed_job(db_session, JOB_HASH)
    await seed_job(db_session, OTHER_HASH)
    await db_session.commit()
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, core_profile_id, updated_by="tests")
    fake = FakeCoreV1(core_profile_id)
    core = make_core(db_session, httpx.MockTransport(fake.handler))
    return user_id, fake, core


# ---------------------------------------------------------------------------
# Superficie del puerto: cada operacion decidida en ambas implementaciones
# ---------------------------------------------------------------------------

PORT_OPS = {
    "list",
    "create",
    "stats",
    "update",
    "delete",
    "set_match_status",
    "get_match",
    "save_draft",
    "get_draft",
}


def test_all_impls_cover_entire_port_surface():
    """Anadir una operacion al puerto obliga a decidirla en el cliente core y
    en el compuesto escritor-core (cota o implementacion, nunca omision)."""
    for impl in (LocalApplications, CoreApplications, CoreWriterApplications):
        ops = {
            n for n, v in vars(impl).items() if not n.startswith("_") and callable(v)
        }
        assert PORT_OPS <= ops, impl.__name__


# ---------------------------------------------------------------------------
# EQUIVALENCIA local-vs-core (CRUD + stats)
# ---------------------------------------------------------------------------


def _projection(resp: ApplicationResponse) -> dict:
    """Campos cuyo contrato exige equivalencia local-vs-core. Fuera quedan las
    cotas documentadas (applied_at/applied_url→None en core; timestamps del
    escritor activo) y el id (identidad del escritor activo)."""
    return {
        "job_hash": resp.job_hash,
        "status": resp.status,
        "notes": resp.notes,
        "job_title": resp.job_title,
        "job_company": resp.job_company,
        "job_location": resp.job_location,
        "job_source": resp.job_source,
    }


async def test_create_list_stats_equivalence(db_session, seeded):
    """El mismo guion (alta + listado + stats) produce la misma proyeccion de
    contrato en el motor local y en el cliente C-4."""
    user_id, _fake, core = seeded
    local = LocalApplications(db_session)

    results = {}
    for name, port in (("local", local), ("core", core)):
        created = await port.create(user_id, JOB_HASH, notes="hola")
        listed = await port.list(user_id)
        stats = await port.stats(user_id)
        results[name] = (created, listed, stats)
        # limpiar el estado LOCAL para que el segundo puerto parta igual
        if name == "local":
            assert await local.delete(user_id, created.id) is True

    for created, listed, stats in results.values():
        assert _projection(created) == {
            "job_hash": JOB_HASH,
            "status": ApplicationStatus.saved,
            "notes": "hola",
            "job_title": "Primary Teacher",
            "job_company": "Zurich Intl School",
            "job_location": "Zurich, ZH",
            "job_source": "test_source",
        }
        assert listed.total == 1
        assert listed.by_status == {"saved": 1}
        assert _projection(listed.data[0]) == _projection(created)
        assert stats.by_status == {"saved": 1}
        assert stats.by_source == {"test_source": 1}
        assert stats.conversion_rates["saved_to_applied"] == 0.0


async def test_core_update_delete_semantics(db_session, seeded):
    """PATCH/DELETE contra el /v1: parciales, 404 de recurso → None/False
    (mismo contrato del puerto que local) y cota follow_up_date (date en el
    core → medianoche UTC en lectura)."""
    user_id, _fake, core = seeded

    # Desconocidos: None / False (el router los hace 404), via 404 del /v1.
    assert await core.update(user_id, uuid.uuid4(), ApplicationUpdate()) is None
    assert await core.delete(user_id, uuid.uuid4()) is False

    created = await core.create(user_id, JOB_HASH)
    updated = await core.update(
        user_id,
        created.id,
        ApplicationUpdate(
            status=ApplicationStatus.applied,
            notes="enviada",
            follow_up_date=datetime(2026, 9, 1, 15, 30, tzinfo=timezone.utc),
        ),
    )
    assert updated.status == ApplicationStatus.applied
    assert updated.notes == "enviada"
    # Cota date-vs-datetime: truncado al dia, servido a medianoche UTC.
    assert updated.follow_up_date == datetime(2026, 9, 1, tzinfo=timezone.utc)
    # Cota documentada: applied_at no es representable en el contrato C-4.
    assert updated.applied_at is None
    assert updated.job_hash == JOB_HASH

    assert await core.delete(user_id, created.id) is True
    assert (await core.list(user_id)).total == 0


@pytest.mark.parametrize("status_value", CORE_STATUSES)
async def test_status_enum_identity_roundtrip(db_session, seeded, status_value):
    """Los 8 estados del core son EXACTAMENTE los 8 del enum legacy: identidad
    en escritura y en lectura, sin degradacion (a diferencia del portfolio)."""
    user_id, _fake, core = seeded
    created = await core.create(user_id, JOB_HASH)
    updated = await core.update(
        user_id, created.id, ApplicationUpdate(status=ApplicationStatus(status_value))
    )
    assert updated.status == ApplicationStatus(status_value)


async def test_core_list_orders_by_updated_at_and_filters(db_session, seeded):
    """Contrato del listado local reproducido: orden updated_at DESC (el feed
    core ordena por created_at — se reordena), filtro de status en cliente y
    by_status sobre TODAS las candidaturas."""
    user_id, _fake, core = seeded
    first = await core.create(user_id, JOB_HASH)
    await core.create(user_id, OTHER_HASH)
    # tocar la PRIMERA: su updated_at pasa a ser el mas reciente
    await core.update(
        user_id, first.id, ApplicationUpdate(status=ApplicationStatus.applied)
    )

    listed = await core.list(user_id)
    assert listed.total == 2
    assert [r.job_hash for r in listed.data] == [JOB_HASH, OTHER_HASH]
    assert listed.by_status == {"applied": 1, "saved": 1}

    only_saved = await core.list(user_id, status=ApplicationStatus.saved)
    assert only_saved.total == 1
    assert only_saved.data[0].job_hash == OTHER_HASH
    # by_status NO se ve afectado por el filtro (semantica del motor local)
    assert only_saved.by_status == {"applied": 1, "saved": 1}

    paged = await core.list(user_id, limit=1, offset=1)
    assert paged.total == 2
    assert [r.job_hash for r in paged.data] == [OTHER_HASH]


async def test_core_drains_paginated_feed(db_session, seeded):
    """El feed compuesto se drena COMPLETO por keyset (paginas de 100; aqui
    page_size=1 para forzar cursores)."""
    user_id, fake, core = seeded
    fake.page_size = 1
    await core.create(user_id, JOB_HASH)
    await core.create(user_id, OTHER_HASH)
    listed = await core.list(user_id)
    assert listed.total == 2


async def test_core_repeated_cursor_is_unavailable(db_session, seeded):
    """Cursor en bucle = feed anomalo → CoreUnavailableError (cota anti-bucle
    heredada de matching), nunca un cuelgue."""
    user_id, _fake, _core = seeded

    def looping(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [], "next_cursor": "same"})

    core = make_core(db_session, httpx.MockTransport(looping))
    with pytest.raises(CoreUnavailableError):
        await core.list(user_id)


async def test_core_excludes_pure_bookmarks(db_session, seeded):
    """COTA documentada: los bookmarks puros del feed compuesto (kind=bookmark)
    NO son candidaturas en el modelo SwissJob ('saved' es estado de matching,
    escritor local) — fuera de list y de stats."""
    user_id, fake, core = seeded
    await core.create(user_id, JOB_HASH, notes="real")
    fake.add_item(kind="bookmark", title="Solo bookmark", url="https://x.invalid/b")

    listed = await core.list(user_id)
    assert listed.total == 1
    assert listed.data[0].notes == "real"
    stats = await core.stats(user_id)
    assert sum(stats.by_status.values()) == 1


async def test_core_create_conflict_and_missing_job(db_session, seeded):
    """409 application_exists del core → DuplicateApplicationError; oferta
    inexistente en LOCAL → ApplicationJobNotFoundError SIN red (la misma
    precedencia que el motor local)."""
    user_id, _fake, core = seeded
    await core.create(user_id, JOB_HASH)
    with pytest.raises(DuplicateApplicationError):
        await core.create(user_id, JOB_HASH)

    silent = make_core(db_session, httpx.MockTransport(fail_on_any_request))
    with pytest.raises(ApplicationJobNotFoundError):
        await silent.create(user_id, "f" * 32)


async def test_core_writes_carry_idempotency_key(db_session, seeded):
    """Toda escritura viaja con Idempotency-Key nueva (uuid4) — el candado de
    reintento del contrato C-4 (Decision 1)."""
    user_id, fake, core = seeded
    created = await core.create(user_id, JOB_HASH)
    await core.update(
        user_id, created.id, ApplicationUpdate(status=ApplicationStatus.applied)
    )
    await core.delete(user_id, created.id)
    keys = [h.get("idempotency-key") for h in fake.write_headers]
    assert len(keys) == 3 and all(keys)
    assert len(set(keys)) == 3  # una key NUEVA por escritura


async def test_job_hash_fallback_recomputes_snapshot_identity(db_session, seeded):
    """Item sin Job local de respaldo (podado o ajeno): job_hash determinista
    con compute_hash(title|company|url) — la funcion de identidad del pipeline
    de ingesta — y job_* del snapshot (location None, cota del DTO)."""
    user_id, fake, core = seeded
    fake.add_item(
        kind="application",
        title="Orphan Job",
        company="Ghost AG",
        url="https://gone.example.com/job/1",
        source="ghost_source",
    )
    listed = await core.list(user_id)
    assert listed.total == 1
    item = listed.data[0]
    assert item.job_hash == BaseJobProvider.compute_hash(
        "Orphan Job", "Ghost AG", "https://gone.example.com/job/1"
    )
    assert item.job_title == "Orphan Job"
    assert item.job_location is None
    assert item.job_source == "ghost_source"


# ---------------------------------------------------------------------------
# COTAS: state machine y applied_url → Unsupported (sin red)
# ---------------------------------------------------------------------------


async def test_core_state_machine_is_unsupported(db_session, seeded):
    """El state machine sobre match_results NO tiene superficie C-4: cota
    Unsupported en el cliente (la costura lo sirve de local)."""
    user_id, _fake, _core = seeded
    core = make_core(db_session, httpx.MockTransport(fail_on_any_request))
    for op in (
        core.set_match_status(user_id, JOB_HASH, "reviewed"),
        core.get_match(user_id, JOB_HASH),
        core.save_draft(user_id, JOB_HASH, "draft"),
        core.get_draft(user_id, JOB_HASH),
    ):
        with pytest.raises(ApplicationsUnsupportedError):
            await op


async def test_core_update_applied_url_is_unsupported(db_session, seeded):
    """`applied_url` no existe en el contrato C-4 (el PATCH /v1 solo muta
    status/notes/follow_up_date): Unsupported ANTES de emitir red."""
    user_id, _fake, _core = seeded
    core = make_core(db_session, httpx.MockTransport(fail_on_any_request))
    with pytest.raises(ApplicationsUnsupportedError):
        await core.update(
            user_id, uuid.uuid4(), ApplicationUpdate(applied_url="https://x.invalid")
        )


# ---------------------------------------------------------------------------
# Identidad y resiliencia: sin vinculo/credencial → CERO peticiones; fallos
# de transporte/payload/status → CoreUnavailableError
# ---------------------------------------------------------------------------


async def test_core_without_profile_link_makes_no_request(db_session):
    """Usuario NO enrolado en jobhunt_profile_map: indisponibilidad operativa
    SIN red (identidad POR USUARIO, no comodin)."""
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await db_session.commit()
    core = make_core(db_session, httpx.MockTransport(fail_on_any_request))
    with pytest.raises(CoreUnavailableError):
        await core.list(user_id)
    with pytest.raises(CoreUnavailableError):
        await core.create(user_id, JOB_HASH)


async def test_core_without_credential_makes_no_request(db_session, monkeypatch):
    """Con vinculo pero sin CORE_CONSUMER_KEY (factory de produccion): ni una
    peticion — mismo trato que el core caido."""
    user_id = await seed_user(db_session)
    await db_session.commit()
    await set_profile_link(db_session, user_id, uuid.uuid4())
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    core = CoreApplications(db_session)  # factory por defecto (produccion)
    with pytest.raises(CoreUnavailableError):
        await core.list(user_id)


@pytest.mark.parametrize(
    "responder",
    [
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("boom", request=req)),
        lambda req: httpx.Response(200, content=b"not json"),
        lambda req: httpx.Response(200, json={"items": [{"id": "no-uuid"}]}),
        lambda req: httpx.Response(500, json={"code": "internal", "message": "x"}),
        lambda req: httpx.Response(404, json={"code": "not_found", "message": "p"}),
    ],
    ids=["transporte", "json-ilegible", "fuera-de-contrato", "500", "404-perfil"],
)
async def test_core_failures_map_to_unavailable(db_session, seeded, responder):
    """Transporte roto, 2xx fuera de contrato, status inesperado o 404 de
    PERFIL: CoreUnavailableError (fallo cerrado y honesto, nunca un 500 del
    router ni una escritura silenciosamente perdida)."""
    user_id, _fake, _core = seeded
    core = make_core(db_session, httpx.MockTransport(responder))
    with pytest.raises(CoreUnavailableError):
        await core.list(user_id)
    with pytest.raises(CoreUnavailableError):
        await core.create(user_id, JOB_HASH)


# ---------------------------------------------------------------------------
# Resolucion por jobhunt_routing (matriz de escritor C-4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [None, "local", "shadow", "core_read"])
async def test_resolve_local_writer_modes(db_session, mode):
    """local/shadow/core_read (y sin fila = default) resuelven a LOCAL —
    core_read DECLARADO EQUIVALENTE: para un durable de escritor local un
    canary romperia read-your-writes o crearia un segundo escritor."""
    user_id = uuid.uuid4()
    if mode is not None:
        await set_routing(db_session, CAPABILITY_APPLICATIONS, mode, profile_id=user_id)
    port = await resolve_applications(db_session, user_id)
    assert isinstance(port, LocalApplications)


@pytest.mark.parametrize("mode", ["core_primary", "rollback_pending"])
async def test_resolve_core_writer_modes(db_session, mode):
    """core_primary/rollback_pending: el core es el escritor del CRUD (sin
    fallback silencioso), compuesto con state machine local."""
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_APPLICATIONS, mode, profile_id=user_id)
    port = await resolve_applications(db_session, user_id)
    assert isinstance(port, CoreWriterApplications)


async def test_resolve_profile_row_beats_wildcard(db_session):
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_APPLICATIONS, "core_primary")  # comodin
    await set_routing(db_session, CAPABILITY_APPLICATIONS, "local", profile_id=user_id)
    assert isinstance(
        await resolve_applications(db_session, user_id), LocalApplications
    )
    assert isinstance(
        await resolve_applications(db_session, uuid.uuid4()), CoreWriterApplications
    )


async def test_core_writer_serves_state_machine_from_local(db_session, seeded):
    """Criterio unificador en modo escritor-core: el state machine de
    match_results (escritor local, sin superficie C-4) sigue sirviendose de
    local — CERO peticiones al core para esas operaciones."""
    user_id, _fake, _core = seeded
    await seed_match(db_session, user_id, application_status="reviewed")
    await db_session.commit()
    failing_core = make_core(db_session, httpx.MockTransport(fail_on_any_request))
    seam = CoreWriterApplications(failing_core, LocalApplications(db_session))

    assert await seam.set_match_status(user_id, JOB_HASH, "sent") is True
    match, job = await seam.get_match(user_id, JOB_HASH)
    assert match.application_status == "sent"
    assert job.hash == JOB_HASH
    assert await seam.save_draft(user_id, JOB_HASH, "Dear school") is True
    assert await seam.get_draft(user_id, JOB_HASH) == "Dear school"


async def test_core_writer_routes_crud_to_core(db_session, seeded):
    """En modo escritor-core el CRUD va al cliente C-4 (aqui, al fake) y NO
    escribe en job_applications local."""
    user_id, fake, core = seeded
    seam = CoreWriterApplications(core, LocalApplications(db_session))
    created = await seam.create(user_id, JOB_HASH)
    assert (await seam.list(user_id)).total == 1
    assert len(fake.items) == 1
    # el motor local NO vio la escritura (el core es el unico escritor)
    assert (await LocalApplications(db_session).list(user_id)).total == 0
    assert await seam.delete(user_id, created.id) is True


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
    # (COTA documentada: applied_at NO es representable en el contrato C-4 —
    # el cliente core lo sirve a None.)
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
# Router: escritor local accesible en sus modos; escritor core FALLA CERRADO
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


@pytest.mark.parametrize("mode", ["local", "shadow", "core_read"])
async def test_router_local_writer_modes_serve_everything(client, db_session, mode):
    """En los modos de escritor local (core_read declarado equivalente) las
    escrituras Y lecturas de candidatura + state machine sirven completas."""
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


@pytest.mark.parametrize("mode", ["core_primary", "rollback_pending"])
async def test_router_core_writer_fails_closed_without_core(client, db_session, mode):
    """Escritor core sin identidad/credencial: el CRUD responde 503 HONESTO —
    jamas una escritura local silenciosa que bifurque el estado — mientras el
    state machine (escritor local, criterio unificador) sigue sirviendo."""
    user_id, headers = await _register(client)
    await seed_job(db_session)
    await seed_match(db_session, user_id, draft_letter="Saved draft")
    await db_session.commit()
    await set_routing(db_session, CAPABILITY_APPLICATIONS, mode, profile_id=user_id)

    # CRUD → 503 (usuario sin vinculo en jobhunt_profile_map; falla cerrado)
    resp = await client.post(
        "/api/v1/applications", headers=headers, json={"job_hash": JOB_HASH}
    )
    assert resp.status_code == 503
    resp = await client.get("/api/v1/applications", headers=headers)
    assert resp.status_code == 503
    # ...y NO hubo escritura local (sin split-brain)
    assert (await LocalApplications(db_session).list(user_id)).total == 0

    # State machine: siempre accesible (local)
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

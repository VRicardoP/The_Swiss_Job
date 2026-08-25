"""Tests del routing por perfil+capacidad y su costura — A.SEAM (plan §15bis).

Cubren el contrato operativo del routing:
- default 'local' (sin filas => todo sirve local, sin contactar al core);
- cambio por perfil (fila exacta > comodin > default) con auditoria/revision;
- cache corta con invalidacion TRANSACCIONAL (solo tras commit);
- core caido => los perfiles en 'local' siguen sirviendo, 'core_read' cae a
  local (canary de lecturas) y 'core_primary' responde 503 (sin mentir con
  datos locales desactualizados).
"""

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from config import settings
from models.jobhunt_routing import CONSUMER_SWISSJOB, PROFILE_WILDCARD
from services import routing
from services.catalog import (
    CoreCatalog,
    FallbackCatalog,
    LocalCatalog,
    core_client,
    resolve_catalog,
)
from tests.test_catalog_contract import (
    CASE_CORE_REFS,
    CASE_LOCAL_REFS,
    CASES,
    TEST_CONSUMER_KEY,
    fake_core_transport,
    seed_local_cases,
)

CAP = routing.CAPABILITY_CATALOG


@pytest.fixture(autouse=True)
def _fresh_routing_cache():
    routing.invalidate_routing_cache()
    yield
    routing.invalidate_routing_cache()


def _factory_with(transport: httpx.MockTransport):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            headers={"Authorization": f"Bearer {TEST_CONSUMER_KEY}"},
            timeout=1.0,
            transport=transport,
        )

    return factory


def _core_down_factory():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return _factory_with(httpx.MockTransport(refuse))


def _patch_core(monkeypatch, factory) -> None:
    """Sustituye el cliente core por uno de test (y configura credencial)."""
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", TEST_CONSUMER_KEY)
    monkeypatch.setattr(core_client, "default_client_factory", factory)


# ---------------------------------------------------------------------------
# Resolucion: defaults, por perfil, precedencia
# ---------------------------------------------------------------------------


async def test_default_mode_is_local(db_session):
    """Sin filas, TODO enruta a local (arranque seguro del plan)."""
    assert await routing.resolve_mode(db_session, CAP) == routing.MODE_LOCAL
    assert (
        await routing.resolve_mode(db_session, CAP, uuid.uuid4()) == routing.MODE_LOCAL
    )


async def test_table_defaults_mode_local_revision_1(db_session):
    """La TABLA tambien tiene default 'local' (server_default del plan)."""
    await db_session.execute(
        text(
            "INSERT INTO jobhunt_routing (consumer_id, profile_id, capability) "
            "VALUES (:cid, :pid, :cap)"
        ),
        {"cid": CONSUMER_SWISSJOB, "pid": PROFILE_WILDCARD, "cap": CAP},
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text(
                "SELECT mode, revision, updated_at FROM jobhunt_routing "
                "WHERE capability = :cap"
            ),
            {"cap": CAP},
        )
    ).one()
    assert row.mode == "local"
    assert row.revision == 1
    assert row.updated_at is not None


async def test_check_constraint_rejects_unknown_mode(db_session):
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO jobhunt_routing "
                "(consumer_id, profile_id, capability, mode) "
                "VALUES (:cid, :pid, :cap, 'banana')"
            ),
            {"cid": CONSUMER_SWISSJOB, "pid": PROFILE_WILDCARD, "cap": CAP},
        )
    await db_session.rollback()


async def test_set_routing_validates_mode(db_session):
    with pytest.raises(ValueError):
        await routing.set_routing(db_session, CAP, "banana")


async def test_per_profile_override_isolated_from_other_profiles(db_session):
    profile_a, profile_b = uuid.uuid4(), uuid.uuid4()
    await routing.set_routing(
        db_session,
        CAP,
        routing.MODE_CORE_READ,
        profile_id=profile_a,
        updated_by="test",
    )
    assert (
        await routing.resolve_mode(db_session, CAP, profile_a) == routing.MODE_CORE_READ
    )
    # El canary de A no arrastra a B ni al trafico sin perfil.
    assert await routing.resolve_mode(db_session, CAP, profile_b) == routing.MODE_LOCAL
    assert await routing.resolve_mode(db_session, CAP) == routing.MODE_LOCAL


async def test_exact_profile_row_beats_wildcard(db_session):
    profile_a = uuid.uuid4()
    await routing.set_routing(db_session, CAP, routing.MODE_CORE_PRIMARY)  # comodin
    await routing.set_routing(db_session, CAP, routing.MODE_LOCAL, profile_id=profile_a)
    assert await routing.resolve_mode(db_session, CAP, profile_a) == routing.MODE_LOCAL
    # Sin fila propia, aplica el comodin del consumer.
    assert (
        await routing.resolve_mode(db_session, CAP, uuid.uuid4())
        == routing.MODE_CORE_PRIMARY
    )


async def test_set_routing_bumps_revision_and_records_author(db_session):
    await routing.set_routing(
        db_session, CAP, routing.MODE_SHADOW, updated_by="runbook"
    )
    await routing.set_routing(
        db_session, CAP, routing.MODE_CORE_READ, updated_by="runbook-2"
    )
    row = (
        await db_session.execute(
            text(
                "SELECT mode, revision, updated_by FROM jobhunt_routing "
                "WHERE capability = :cap AND profile_id = :pid"
            ),
            {"cap": CAP, "pid": PROFILE_WILDCARD},
        )
    ).one()
    assert row.mode == "core_read"
    assert row.revision == 2  # auditoria: cada cambio incrementa
    assert row.updated_by == "runbook-2"


# ---------------------------------------------------------------------------
# Resolucion MASIVA (resolve_modes) y predicado legacy_owns — D.1
# ---------------------------------------------------------------------------


class _CountingSession:
    """Envuelve la sesion real y cuenta las ejecuciones de SQL (gate de D.1:
    N perfiles deben resolverse con UNA consulta, no una por perfil)."""

    def __init__(self, inner):
        self._inner = inner
        self.executes = 0

    async def execute(self, *args, **kwargs):
        self.executes += 1
        return await self._inner.execute(*args, **kwargs)


async def test_resolve_modes_empty_list_never_touches_db():
    session = _CountingSession(None)  # cualquier execute reventaria (inner None)
    assert await routing.resolve_modes(session, CAP, []) == {}
    assert session.executes == 0


async def test_resolve_modes_precedence_in_one_query(db_session):
    """Exacta > comodin > local para N perfiles, con UNA sola consulta."""
    profile_a, profile_b, profile_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await routing.set_routing(db_session, CAP, routing.MODE_CORE_PRIMARY)  # comodin
    await routing.set_routing(
        db_session, CAP, routing.MODE_CORE_READ, profile_id=profile_a
    )
    session = _CountingSession(db_session)
    modes = await routing.resolve_modes(
        db=session,
        capability=CAP,
        profile_ids=[profile_a, profile_b, profile_c, profile_a],  # con duplicado
    )
    assert modes == {
        profile_a: routing.MODE_CORE_READ,  # fila exacta gana
        profile_b: routing.MODE_CORE_PRIMARY,  # comodin del consumer
        profile_c: routing.MODE_CORE_PRIMARY,
    }
    assert session.executes == 1


async def test_resolve_modes_defaults_local_without_rows(db_session):
    profile_a = uuid.uuid4()
    session = _CountingSession(db_session)
    assert await routing.resolve_modes(session, CAP, [profile_a]) == {
        profile_a: routing.MODE_LOCAL
    }
    assert session.executes == 1


async def test_resolve_modes_shares_cache_with_resolve_mode(db_session):
    """MISMA cache en ambos sentidos: lo que resuelve uno lo reutiliza el otro
    sin tocar la BD (dentro de la TTL)."""
    profile_a, profile_b = uuid.uuid4(), uuid.uuid4()
    # resolve_modes puebla la cache -> resolve_mode no consulta.
    session = _CountingSession(db_session)
    await routing.resolve_modes(session, CAP, [profile_a])
    assert await routing.resolve_mode(session, CAP, profile_a) == routing.MODE_LOCAL
    assert session.executes == 1
    # resolve_mode puebla la cache -> resolve_modes con todo cacheado no consulta.
    await routing.resolve_mode(session, CAP, profile_b)
    assert session.executes == 2
    assert await routing.resolve_modes(session, CAP, [profile_a, profile_b]) == {
        profile_a: routing.MODE_LOCAL,
        profile_b: routing.MODE_LOCAL,
    }
    assert session.executes == 2  # cero consultas nuevas


async def test_resolve_modes_respects_transactional_invalidation(db_session):
    """Tras un set_routing (commit + invalidacion), resolve_modes ve el cambio."""
    profile_a = uuid.uuid4()
    assert (await routing.resolve_modes(db_session, CAP, [profile_a]))[
        profile_a
    ] == routing.MODE_LOCAL
    await routing.set_routing(
        db_session, CAP, routing.MODE_CORE_READ, profile_id=profile_a
    )
    assert (await routing.resolve_modes(db_session, CAP, [profile_a]))[
        profile_a
    ] == routing.MODE_CORE_READ


def test_legacy_owns_matrix():
    """Matriz de escritor del §15bis: legacy actua SOLO en local y shadow; en
    rollback_pending el core sigue siendo autoritativo hasta el replay final."""
    assert routing.legacy_owns(routing.MODE_LOCAL)
    assert routing.legacy_owns(routing.MODE_SHADOW)
    assert not routing.legacy_owns(routing.MODE_CORE_READ)
    assert not routing.legacy_owns(routing.MODE_CORE_PRIMARY)
    assert not routing.legacy_owns(routing.MODE_ROLLBACK_PENDING)


# ---------------------------------------------------------------------------
# Cache corta con invalidacion transaccional
# ---------------------------------------------------------------------------


async def test_cache_invalidated_after_committed_change(db_session):
    assert await routing.resolve_mode(db_session, CAP) == routing.MODE_LOCAL  # cachea
    await routing.set_routing(db_session, CAP, routing.MODE_CORE_READ)
    # Sin esperar TTL: la invalidacion ocurre tras el commit del cambio.
    assert await routing.resolve_mode(db_session, CAP) == routing.MODE_CORE_READ


async def test_cache_serves_until_invalidation_or_ttl(db_session):
    """Escribir SIN pasar por set_routing no invalida: la cache sirve el valor
    vigente hasta invalidacion explicita (o expiracion de la TTL corta)."""
    assert await routing.resolve_mode(db_session, CAP) == routing.MODE_LOCAL
    await db_session.execute(
        text(
            "INSERT INTO jobhunt_routing (consumer_id, profile_id, capability, mode) "
            "VALUES (:cid, :pid, :cap, 'core_read')"
        ),
        {"cid": CONSUMER_SWISSJOB, "pid": PROFILE_WILDCARD, "cap": CAP},
    )
    await db_session.commit()
    assert await routing.resolve_mode(db_session, CAP) == routing.MODE_LOCAL
    routing.invalidate_routing_cache()
    assert await routing.resolve_mode(db_session, CAP) == routing.MODE_CORE_READ


# ---------------------------------------------------------------------------
# Mapeo modo -> implementacion en la costura
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected_type"),
    [
        (routing.MODE_LOCAL, LocalCatalog),
        (routing.MODE_SHADOW, LocalCatalog),  # legacy sigue de lector (§15bis)
        (routing.MODE_CORE_READ, FallbackCatalog),  # canary con fallback
        (routing.MODE_CORE_PRIMARY, CoreCatalog),  # core manda, sin fallback
        (routing.MODE_ROLLBACK_PENDING, CoreCatalog),  # core hasta replay final
    ],
)
async def test_resolve_catalog_mode_mapping(db_session, mode, expected_type):
    await routing.set_routing(db_session, CAP, mode)
    catalog = await resolve_catalog(db_session)
    assert type(catalog) is expected_type


# ---------------------------------------------------------------------------
# Core caido: el modo local sigue sirviendo (routing local al BFF)
# ---------------------------------------------------------------------------


async def test_local_mode_serves_with_core_down(client, db_session, monkeypatch):
    """Default local: el catalogo sirve sin contactar SIQUIERA al core."""
    _patch_core(monkeypatch, _core_down_factory())
    await seed_local_cases(db_session)
    resp = await client.get("/api/v1/jobs/search")
    assert resp.status_code == 200
    assert resp.json()["total"] == len(CASES)
    detail = await client.get(f"/api/v1/jobs/{CASE_LOCAL_REFS['python_zurich']}")
    assert detail.status_code == 200
    assert detail.json()["title"] == CASES["python_zurich"]["title"]


async def test_core_read_falls_back_to_local_when_core_down(
    client, db_session, monkeypatch
):
    """Canary de lecturas: con el core caido, el legacy (aun escritor con la
    copia completa) sigue sirviendo — continuidad de lectura del plan."""
    _patch_core(monkeypatch, _core_down_factory())
    await seed_local_cases(db_session)
    await routing.set_routing(db_session, CAP, routing.MODE_CORE_READ)
    resp = await client.get("/api/v1/jobs/search")
    assert resp.status_code == 200
    assert resp.json()["total"] == len(CASES)
    detail = await client.get(f"/api/v1/jobs/{CASE_LOCAL_REFS['python_zurich']}")
    assert detail.status_code == 200


async def test_core_read_serves_detail_from_core_when_up(
    client, db_session, monkeypatch
):
    """Con el core arriba, core_read sirve el detalle DESDE el core.

    G1/P2-17: la identidad presentada es el MD5 legacy accionable cuando hay
    Job local de respaldo (antes se presentaba el UUID del core y toda
    escritura desde el feed moria con 422/404)."""
    _patch_core(monkeypatch, _factory_with(fake_core_transport()))
    await seed_local_cases(db_session)
    await routing.set_routing(db_session, CAP, routing.MODE_CORE_READ)
    core_ref = CASE_CORE_REFS["python_zurich"]
    detail = await client.get(f"/api/v1/jobs/{core_ref}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["hash"] == CASE_LOCAL_REFS["python_zurich"]  # MD5 accionable
    assert body["title"] == CASES["python_zurich"]["title"]


async def test_core_primary_with_core_down_returns_503(client, db_session, monkeypatch):
    """core_primary: sin fallback silencioso — el fallo se ve (503)."""
    _patch_core(monkeypatch, _core_down_factory())
    await seed_local_cases(db_session)
    await routing.set_routing(db_session, CAP, routing.MODE_CORE_PRIMARY)
    detail = await client.get(f"/api/v1/jobs/{CASE_CORE_REFS['python_zurich']}")
    assert detail.status_code == 503
    # La busqueda EXPRESABLE viaja al core: caido => 503 (sin fallback).
    resp = await client.get("/api/v1/jobs/search")
    assert resp.status_code == 503
    # Filtros/orden fuera del contrato /v1: cota => 501 SIN peticion de red
    # (el factory caido no llega a usarse: la cota corta antes).
    resp = await client.get("/api/v1/jobs/search", params={"sort": "salary"})
    assert resp.status_code == 501

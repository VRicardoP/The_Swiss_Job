"""CONTRACT TESTS de la capacidad COLEGIOS — A.SEAM (plan §15bis).

Variante LIGERA de la costura: el /v1 del core (jobhunt_core/api/v1.py) NO
expone colegios vigilados en Fase A — la cota contractual es
SchoolsUnsupportedError, fijada aqui (patron search/stats de catalogo).

CRITERIO UNIFICADOR (heredado de A.SEAM matching): el escritor del estado
(la config estatica `SCHOOLS` del propio BFF) es LOCAL => el listado es
accesible en TODOS los modos de routing, incluida core_primary — nunca
501/503 por routing. Fijado aqui a nivel de resolver y de HTTP.
"""

import logging
import uuid

import pytest

from scrapers.swiss_schools_config import SCHOOLS
from services.routing import (
    CAPABILITY_SCHOOLS,
    invalidate_routing_cache,
    set_routing,
)
from services.schools import (
    CoreSchools,
    CoreUnavailableError,
    FallbackSchools,
    LocalSchools,
    SchoolsUnsupportedError,
    resolve_schools,
)


@pytest.fixture(autouse=True)
def _fresh_routing_cache():
    """La cache del routing es por proceso: sin esto un test podria leer el
    modo de otro test."""
    invalidate_routing_cache()
    yield
    invalidate_routing_cache()


# ---------------------------------------------------------------------------
# Cota /v1: el core NO expone colegios — Unsupported en TODA operacion
# ---------------------------------------------------------------------------


async def test_core_schools_list_not_in_v1_contract():
    """El /v1 del core no expone colegios (Fase A): la costura lo declara
    como SchoolsUnsupportedError, no lo simula. CoreSchools no necesita
    credencial ni abre cliente HTTP: cero peticiones por construccion."""
    with pytest.raises(SchoolsUnsupportedError):
        await CoreSchools().list()


def test_core_covers_entire_port_surface():
    """La cota es TOTAL: cada operacion publica del puerto local existe en
    el cliente core (con Unsupported)."""
    local_ops = {n for n in vars(LocalSchools) if not n.startswith("_")}
    core_ops = {n for n in vars(CoreSchools) if not n.startswith("_")}
    assert local_ops == core_ops == {"list"}


# ---------------------------------------------------------------------------
# LOCAL: el listado es la metadata publica de la config, verbatim
# ---------------------------------------------------------------------------


async def test_local_list_serves_config_metadata():
    payload = await LocalSchools().list()
    assert set(payload) == {"schools"}
    assert [s["id"] for s in payload["schools"]] == [s.id for s in SCHOOLS]
    # Campos de la representacion publica (verbatim del router previo).
    expected_keys = {
        "id",
        "name",
        "city",
        "group_tier",
        "policy",
        "contact_email",
        "contact_name",
        "template_id",
        "application_url",
        "careers_url",
        "notes",
    }
    assert all(set(s) == expected_keys for s in payload["schools"])


# ---------------------------------------------------------------------------
# Resolucion por jobhunt_routing (criterio unificador incluido)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode", [None, "local", "shadow", "core_primary", "rollback_pending"]
)
async def test_resolve_schools_serves_local_writer(db_session, mode):
    """Todo modo salvo core_read resuelve a LOCAL — incluida core_primary:
    criterio unificador, el escritor del estado es local (config del BFF) y
    el /v1 no expone la capacidad => nunca 501/503 por routing."""
    user_id = uuid.uuid4()
    if mode is not None:
        await set_routing(db_session, CAPABILITY_SCHOOLS, mode, profile_id=user_id)
    port = await resolve_schools(db_session, user_id)
    assert isinstance(port, LocalSchools)


async def test_resolve_schools_core_read_is_fallback(db_session):
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_SCHOOLS, "core_read", profile_id=user_id)
    port = await resolve_schools(db_session, user_id)
    assert isinstance(port, FallbackSchools)


async def test_resolve_schools_profile_row_beats_wildcard(db_session):
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_SCHOOLS, "core_read")  # comodin
    await set_routing(db_session, CAPABILITY_SCHOOLS, "local", profile_id=user_id)
    assert isinstance(await resolve_schools(db_session, user_id), LocalSchools)
    assert isinstance(await resolve_schools(db_session, uuid.uuid4()), FallbackSchools)


# ---------------------------------------------------------------------------
# Canary core_read: cae a local con severidades heredadas
# ---------------------------------------------------------------------------


async def test_core_read_falls_back_to_local_for_v1_bound():
    seam = FallbackSchools(CoreSchools(), LocalSchools())
    payload = await seam.list()
    assert [s["id"] for s in payload["schools"]] == [s.id for s in SCHOOLS]


async def test_canary_warn_levels_separate_expected_from_actionable(caplog):
    """Severidades heredadas (2ª rev. A.SEAM catalogo): Unsupported (cota
    /v1, esperado) va a DEBUG; el core CAIDO (CoreUnavailableError) es el
    UNICO WARNING."""

    class _Unsupported:
        async def list(self):
            raise SchoolsUnsupportedError("cota contrato")

    class _Down:
        async def list(self):
            raise CoreUnavailableError("core caido")

    class _Fallback:
        async def list(self):
            return None

    with caplog.at_level(logging.DEBUG, logger="services.schools.seam"):
        await FallbackSchools(_Unsupported(), _Fallback()).list()
        await FallbackSchools(_Down(), _Fallback()).list()
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warns) == 1 and "core caido" in warns[0].getMessage()
    assert len(debugs) == 1 and "cota contrato" in debugs[0].getMessage()


# ---------------------------------------------------------------------------
# Router: el listado es accesible en TODOS los modos (nunca 501/503)
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
async def test_router_schools_listing_in_all_modes(client, db_session, mode):
    """GET /watchlist/schools sirve la config local en los 5 modos de
    routing — incluida core_primary (criterio unificador): jamas un 501/503
    para estado cuyo unico escritor es local."""
    user_id, headers = await _register(client)
    await set_routing(db_session, CAPABILITY_SCHOOLS, mode, profile_id=user_id)

    resp = await client.get("/api/v1/watchlist/schools", headers=headers)
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["schools"]] == [s.id for s in SCHOOLS]

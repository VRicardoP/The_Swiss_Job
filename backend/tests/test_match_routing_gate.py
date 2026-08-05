"""Tests del gate anti-doble-motor D.2 en la via interactiva (plan §15bis).

D.1 cerro el gate en los schedulers; D.2 cierra la otra mitad: el disparo
interactivo del pipeline (`POST /api/v1/match/analyze`). Con el matching del
perfil gobernado por el core (core_read/core_primary/rollback_pending) el
endpoint responde 409 SIN instanciar servicios LLM ni invocar el motor local;
en local/shadow el comportamiento queda intacto. Cubre ademas la precedencia
fila exacta > comodin: un perfil fijado a 'local' sigue analizando aunque el
comodin del consumer este migrado.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from services import routing
from tests.conftest import random_email

_TEST_PASSWORD = "TestPass123!"

# Modos en los que el core es autoritativo => /analyze debe rechazar.
_CORE_OWNED = (
    routing.MODE_CORE_READ,
    routing.MODE_CORE_PRIMARY,
    routing.MODE_ROLLBACK_PENDING,
)


@pytest.fixture(autouse=True)
def _fresh_routing_cache():
    routing.invalidate_routing_cache()
    yield
    routing.invalidate_routing_cache()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient) -> tuple[str, uuid.UUID]:
    """Registra un usuario y devuelve (token, user_id)."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": random_email(),
            "password": _TEST_PASSWORD,
            "gdpr_consent": True,
        },
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers=_auth(token))
    return token, uuid.UUID(me.json()["id"])


def _engine_double() -> MagicMock:
    """Doble del motor (MatchService): registra instanciacion y llamadas.

    El comportamiento REAL del pipeline con routing 'local' ya lo cubre
    tests/test_match.py; aqui el doble prueba exactamente si el gate deja
    pasar (run_matching invocado) o corta (cero llamadas).
    """
    service = MagicMock()
    service.run_matching = AsyncMock(
        return_value={"status": "success", "total_candidates": 3, "results_count": 2}
    )
    return service


# ---------------------------------------------------------------------------
# Modos legacy-owned: el motor se ejecuta (comportamiento intacto)
# ---------------------------------------------------------------------------


async def test_analyze_runs_engine_in_legacy_owned_modes(client, db_session):
    """'local' (default sin fila de routing) y 'shadow' (el core solo observa
    via CDC): /analyze invoca el motor para ambos perfiles."""
    token_local, uid_local = await _register(client)
    token_shadow, uid_shadow = await _register(client)
    await routing.set_routing(
        db_session,
        routing.CAPABILITY_MATCHING,
        routing.MODE_SHADOW,
        profile_id=uid_shadow,
    )

    engine = _engine_double()
    with patch("routers.match.MatchService", return_value=engine) as service_cls:
        for token in (token_local, token_shadow):
            resp = await client.post("/api/v1/match/analyze", headers=_auth(token))
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"

    assert service_cls.call_count == 2
    analyzed = {c.kwargs["user_id"] for c in engine.run_matching.await_args_list}
    assert analyzed == {uid_local, uid_shadow}


# ---------------------------------------------------------------------------
# Modos core-owned: 409 y cero trabajo caro
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", _CORE_OWNED)
async def test_analyze_conflict_when_core_owns_matching(client, db_session, mode):
    """core_read/core_primary/rollback_pending: 409 Conflict y CERO trabajo
    caro — ni el motor (MatchService) ni los servicios LLM (Groq/Gemini)
    llegan a instanciarse (prioridad del ticket: descarte antes de todo)."""
    token, uid = await _register(client)
    await routing.set_routing(
        db_session, routing.CAPABILITY_MATCHING, mode, profile_id=uid
    )

    with (
        patch("routers.match.MatchService") as service_cls,
        patch("routers.match.GeminiService") as gemini_cls,
        patch("routers.match._get_groq") as get_groq,
    ):
        resp = await client.post("/api/v1/match/analyze", headers=_auth(token))

    assert resp.status_code == 409
    assert "core engine" in resp.json()["detail"]
    service_cls.assert_not_called()  # el motor local NO se toca
    assert service_cls.return_value.run_matching.call_count == 0
    gemini_cls.assert_not_called()  # cero coste LLM
    get_groq.assert_not_called()


# ---------------------------------------------------------------------------
# Precedencia: fila exacta 'local' gana al comodin migrado
# ---------------------------------------------------------------------------


async def test_analyze_exact_local_row_beats_migrated_wildcard(client, db_session):
    """Con el comodin del consumer en core_primary, un perfil con fila exacta
    'local' SIGUE analizando; otro perfil (que solo hereda el comodin) recibe
    409 — misma precedencia exacta > comodin > 'local' que en D.1."""
    token_pinned, uid_pinned = await _register(client)
    token_wildcard, _uid_wildcard = await _register(client)
    await routing.set_routing(
        db_session, routing.CAPABILITY_MATCHING, routing.MODE_CORE_PRIMARY
    )  # comodin: consumer migrado
    await routing.set_routing(
        db_session,
        routing.CAPABILITY_MATCHING,
        routing.MODE_LOCAL,
        profile_id=uid_pinned,
    )

    engine = _engine_double()
    with patch("routers.match.MatchService", return_value=engine):
        resp_pinned = await client.post(
            "/api/v1/match/analyze", headers=_auth(token_pinned)
        )
        resp_wildcard = await client.post(
            "/api/v1/match/analyze", headers=_auth(token_wildcard)
        )

    assert resp_pinned.status_code == 200
    assert resp_wildcard.status_code == 409
    analyzed = {c.kwargs["user_id"] for c in engine.run_matching.await_args_list}
    assert analyzed == {uid_pinned}

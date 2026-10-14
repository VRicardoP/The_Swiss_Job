"""Tres bloqueos seguidos del portal y Core deja de pedirle."""

import asyncio

import httpx
import sqlalchemy as sa

from jobhunt_core.harvest.provider import BaseProvider, ProviderResponseError
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.types import FetchResult, RawListing
from jobhunt_core.tests.test_integration_harvest import (  # noqa: F401
    CollectSink,
    _seed_scopes,
    db,
    pytestmark,
)


class _Portal(BaseProvider):
    """Devuelve lo que se le diga: un bloqueo, un fallo cualquiera o una cosecha."""

    name = "arbeitnow"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def fetch_new(self, params, cursor, http):
        self.calls += 1
        verdict = self.script.pop(0) if self.script else "ok"
        if verdict == "ok":
            return FetchResult(
                listings=(RawListing("n1", "https://x/n1", {"t": 1}),),
                next_cursor={"watermark": 1},
                pages_fetched=1,
            )
        if verdict == "fail":
            raise ProviderResponseError("arbeitnow: unreadable envelope")
        request = httpx.Request("GET", "https://portal.test/jobs")
        try:
            raise httpx.HTTPStatusError(
                "blocked",
                request=request,
                response=httpx.Response(int(verdict), request=request),
            )
        except httpx.HTTPStatusError:
            # Como lo envuelven los adaptadores reales.
            raise ProviderResponseError("arbeitnow: request failed") from None


def _state(factory, scope_id):
    async def go():
        async with factory() as session:
            return (
                await session.execute(
                    sa.text(
                        "SELECT sss.consecutive_failures, sss.consecutive_blocks, "
                        "  sss.last_blocked_at, sss.disabled_by_blocks_at, hs.enabled "
                        "FROM harvest_scopes hs "
                        "LEFT JOIN source_scope_state sss ON sss.scope_id = hs.id "
                        "WHERE hs.id = :sid"
                    ),
                    {"sid": scope_id},
                )
            ).one()

    return asyncio.run(go())


def _run(factory, scope_id, provider, times=1):
    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, text="x"))
        ) as client:
            return [
                await run_scope(scope_id, provider, CollectSink(), client, factory)
                for _ in range(times)
            ]

    return asyncio.run(go())


def test_tres_bloqueos_seguidos_apagan_el_scope(db, caplog):  # noqa: F811
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    provider = _Portal(["429", "403", "503"])

    with caplog.at_level("ERROR"):
        results = _run(factory, scope_id, provider, times=3)

    assert [r.status for r in results] == ["error", "error", "error"]
    row = _state(factory, scope_id)
    assert row.consecutive_blocks == 3
    assert row.consecutive_failures == 3
    assert row.last_blocked_at is not None
    # El scope queda APAGADO y con sello: la vigilancia sólo mira habilitados.
    assert row.enabled is False
    assert row.disabled_by_blocks_at is not None
    assert any("harvest_block_killswitch" in m for m in caplog.messages)

    # Y deja de pedir: el cuarto run no llama al provider.
    before = provider.calls
    assert [r.status for r in _run(factory, scope_id, provider)] == ["skipped"]
    assert provider.calls == before


def test_un_fallo_que_no_es_bloqueo_no_apaga_nada(db):  # noqa: F811
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)

    _run(factory, scope_id, _Portal(["fail", "fail", "fail", "fail"]), times=4)

    row = _state(factory, scope_id)
    assert row.consecutive_failures == 4
    # Una caída de red o un sobre ilegible no son un portal bloqueándonos.
    assert row.consecutive_blocks == 0
    assert row.enabled is True and row.disabled_by_blocks_at is None


def test_una_cosecha_completa_borra_el_contador_de_bloqueos(db):  # noqa: F811
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    provider = _Portal(["429", "403", "ok", "429"])

    _run(factory, scope_id, provider, times=4)

    row = _state(factory, scope_id)
    # El éxito probó que el portal ya no bloquea: el tercer bloqueo vuelve a 1.
    assert row.consecutive_blocks == 1
    assert row.enabled is True and row.disabled_by_blocks_at is None


def test_el_censo_de_salud_canta_los_scopes_apagados_por_bloqueo(db, caplog):  # noqa: F811
    from jobhunt_core.harvest.health import check_harvest_health

    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    _run(factory, scope_id, _Portal(["429", "429", "429"]), times=3)

    async def go():
        async with factory() as session:
            return await check_harvest_health(session)

    with caplog.at_level("ERROR"):
        report = asyncio.run(go())

    assert report["censo"]["apagados_por_bloqueo"] >= 1
    assert any(
        "apagados_por_bloqueo" in str(m) or "DESHABILITADOS por bloqueo" in str(m)
        for m in caplog.messages
    )

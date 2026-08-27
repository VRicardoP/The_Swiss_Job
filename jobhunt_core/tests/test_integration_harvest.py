"""Runner de ingesta (A-03) contra Postgres real: la DISCIPLINA del cursor.

DoD: 2 scopes con cursor POR SCOPE; el cursor se commitea AL FINAL (misma
transacción que la persistencia); un fallo del sink deja el cursor intacto.
Ejecutar vía core-migrate (los datos de prueba se limpian al final).
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core.config import settings
from jobhunt_core.harvest.provider import BaseProvider, ProviderConfigError
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.types import FetchResult, RawListing
from jobhunt_core.tests import dbcleanup

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


class FakeProvider(BaseProvider):
    """Determinista: 2 nuevas si no hay cursor; 1 nueva si watermark=100."""

    name = "arbeitnow"  # debe casar con sources.name del scope

    async def fetch_new(self, params, cursor, http) -> FetchResult:
        watermark = int((cursor or {}).get("watermark", 0))
        all_items = [
            (200, RawListing(external_id="n2", url="https://x/n2", payload={"t": 2})),
            (100, RawListing(external_id="n1", url="https://x/n1", payload={"t": 1})),
        ]
        new = tuple(listing for ts, listing in all_items if ts > watermark)
        return FetchResult(listings=new, next_cursor={"watermark": 200}, pages_fetched=1)


class CollectSink:
    def __init__(self):
        self.batches: list[tuple[str, tuple[RawListing, ...]]] = []

    async def handle(self, session, scope_id, listings):
        self.batches.append((scope_id, listings))


class FailingSink:
    async def handle(self, session, scope_id, listings):
        raise RuntimeError("persistencia rota (simulada)")


@pytest.fixture()
def db():
    """Sesiones async contra la BD del core + limpieza de los datos de prueba."""
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"source": None, "scopes": [], "extra_sources": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            await dbcleanup.purge_source_graph(
                s, created["extra_sources"] + [created["source"]], created["scopes"]
            )
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _seed_scopes(factory, created, n=2) -> list[str]:
    async def go():
        async with factory() as s:
            source_id = uuid.uuid4()
            created["source"] = source_id
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, 'arbeitnow', 0)"),
                {"id": source_id},
            )
            for _ in range(n):
                sid = uuid.uuid4()
                created["scopes"].append(sid)
                await s.execute(
                    sa.text(
                        "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                        "VALUES (:id, :src, '{}'::jsonb, 0)"
                    ),
                    {"id": sid, "src": source_id},
                )
            await s.commit()
        return [str(x) for x in created["scopes"]]

    return asyncio.run(go())


def _state(factory, scope_id):
    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT cursor, last_complete_at, consecutive_failures "
                        "FROM source_scope_state WHERE scope_id = :i"
                    ),
                    {"i": scope_id},
                )
            ).one_or_none()

    return asyncio.run(go())


def _provider_cursor_of(state) -> dict | None:
    """El cursor sin la clave interna de fingerprint (para asserts limpios)."""
    if state is None or state.cursor is None:
        return None
    c = dict(state.cursor)
    c.pop("_params_fp", None)
    return c


def _run(factory, scope_id, sink):
    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ) as http:  # el FakeProvider no usa HTTP; el transport 500 vigila que así sea
            return await run_scope(scope_id, FakeProvider(), sink, http, session_factory=factory)

    return asyncio.run(go())


def test_two_scopes_commit_cursor_each(db):
    factory, created = db
    s1, s2 = _seed_scopes(factory, created, n=2)
    sink = CollectSink()
    r1, r2 = _run(factory, s1, sink), _run(factory, s2, sink)
    assert (r1.status, r2.status) == ("ok", "ok")
    assert r1.listings == r2.listings == 2
    for sid in (s1, s2):  # cursor POR SCOPE, commiteado, sin fallos
        st = _state(factory, sid)
        assert _provider_cursor_of(st) == {"watermark": 200}
        assert st.last_complete_at is not None and st.consecutive_failures == 0
    assert len(sink.batches) == 2


def test_failing_sink_leaves_cursor_untouched(db):
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    r = _run(factory, s1, FailingSink())
    assert r.status == "error"
    st = _state(factory, s1)
    assert st.cursor is None  # el cursor NO avanzó: nada se pierde para A-04
    assert st.consecutive_failures == 1


def test_resume_from_cursor_fetches_only_new(db):
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    first = CollectSink()
    assert _run(factory, s1, first).listings == 2
    # Simular avance parcial: watermark=100 → solo n2 es nueva.
    async def set_wm():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE source_scope_state SET cursor = '{\"watermark\": 100}'::jsonb "
                    "WHERE scope_id = :i"
                ),
                {"i": s1},
            )
            await s.commit()

    asyncio.run(set_wm())
    second = CollectSink()
    r = _run(factory, s1, second)
    assert r.listings == 1
    assert [x.external_id for x in second.batches[0][1]] == ["n2"]


class MarkerSink:
    """Escribe una fila REAL a través de la session del runner (prueba la
    atomicidad sink+cursor en la MISMA transacción, auditoría #10)."""

    def __init__(self, marker_name: str, and_break_cursor: bool = False):
        self.marker_name = marker_name
        self.and_break_cursor = and_break_cursor

    async def handle(self, session, scope_id, listings):
        await session.execute(
            sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, :n, 9)"),
            {"id": uuid.uuid4(), "n": self.marker_name},
        )
        if self.and_break_cursor:
            # Borra el scope → el upsert del cursor violará su FK DESPUÉS del
            # sink: si el marker sobreviviera, sink y cursor no compartirían tx.
            await session.execute(
                sa.text("DELETE FROM harvest_scopes WHERE id = :i"), {"i": scope_id}
            )


class RaisingProvider(FakeProvider):
    async def fetch_new(self, params, cursor, http):
        raise RuntimeError("fuente caída (simulada)")


def _marker_exists(factory, name) -> bool:
    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text("SELECT count(*) FROM sources WHERE name = :n"), {"n": name}
                )
            ).scalar() == 1

    return asyncio.run(go())


def _delete_marker(factory, name):
    async def go():
        async with factory() as s:
            await s.execute(sa.text("DELETE FROM sources WHERE name = :n"), {"n": name})
            await s.commit()

    asyncio.run(go())


def test_sink_and_cursor_commit_in_same_tx(db):
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    marker = f"marker-{uuid.uuid4().hex[:8]}"
    r = _run(factory, s1, MarkerSink(marker))
    assert r.status == "ok"
    assert _marker_exists(factory, marker)  # lo del sink quedó COMMITEADO con el cursor
    assert _provider_cursor_of(_state(factory, s1)) == {"watermark": 200}
    _delete_marker(factory, marker)


def test_cursor_failure_rolls_back_sink_writes(db):
    """Si el commit del cursor falla DESPUÉS del sink, lo del sink se revierte
    también: misma transacción de verdad, no dos."""
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    marker = f"marker-{uuid.uuid4().hex[:8]}"
    r = _run(factory, s1, MarkerSink(marker, and_break_cursor=True))
    assert r.status == "error"
    assert not _marker_exists(factory, marker)  # rollback conjunto sink+cursor
    # El rollback también restaura el scope borrado por el sink (misma tx), así
    # que el contador de fallo SÍ se registra después; el cursor queda intacto.
    st = _state(factory, s1)
    assert st.cursor is None and st.last_complete_at is None
    assert st.consecutive_failures == 1


def test_provider_failure_records_and_leaves_cursor(db):
    """Auditoría #11: fallo del PROVIDER → error, contador+1, sin cursor."""
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ) as http:
            return await run_scope(s1, RaisingProvider(), CollectSink(), http, session_factory=factory)

    r = asyncio.run(go())
    assert r.status == "error" and "simulada" in r.error
    st = _state(factory, s1)
    assert st.cursor is None and st.last_complete_at is None
    assert st.consecutive_failures == 1


class RecordingProvider(FakeProvider):
    """FakeProvider sensible a la keyword (fingerprint) que graba los cursores."""

    SEMANTIC_PARAMS = ("keyword",)

    def __init__(self):
        self.received_cursors: list = []

    async def fetch_new(self, params, cursor, http):
        self.received_cursors.append(cursor)
        return await super().fetch_new(params, cursor, http)


def test_semantic_param_change_resets_cursor(db):
    """Revisión #3: cambiar la keyword del scope con cursor existente reinicia
    el cursor (un watermark heredado enterraría ofertas del filtro nuevo)."""
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)

    async def set_keyword(kw):
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE harvest_scopes SET params = CAST(:p AS jsonb) WHERE id = :i"
                ),
                {"p": f'{{"keyword": "{kw}"}}', "i": s1},
            )
            await s.commit()

    asyncio.run(set_keyword("python"))
    provider = RecordingProvider()

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ) as http:
            return await run_scope(s1, provider, CollectSink(), http, session_factory=factory)

    assert asyncio.run(go()).status == "ok"
    assert provider.received_cursors == [None]  # primer run: sin cursor

    asyncio.run(set_keyword("java"))  # cambia el parámetro SEMÁNTICO
    assert asyncio.run(go()).status == "ok"
    # Pese a existir estado, el provider recibe cursor=None (reinicio).
    assert provider.received_cursors == [None, None]

    asyncio.run(set_keyword("java"))  # sin cambio → el cursor SÍ se conserva
    assert asyncio.run(go()).status == "ok"
    assert provider.received_cursors[2] == {"watermark": 200}


class InterleavedProvider(FakeProvider):
    """Simula el run LENTO de la carrera del revisor: durante su fetch, OTRO run
    completo del mismo scope avanza el cursor y commitea."""

    def __init__(self, factory, scope_id):
        self.factory = factory
        self.scope_id = scope_id

    async def fetch_new(self, params, cursor, http):
        inner = await run_scope(
            self.scope_id, FakeProvider(), CollectSink(), http, session_factory=self.factory
        )
        assert inner.status == "ok"  # el run rápido gana y commitea watermark=200
        # El lento devuelve un cursor VIEJO (calculado con su snapshot previo).
        return FetchResult(listings=(), next_cursor={"watermark": 1}, pages_fetched=1)


def test_concurrent_run_aborts_stale_instead_of_clobbering(db):
    """Revisión #2 (lost-update): el run lento detecta el cursor avanzado bajo
    FOR UPDATE y aborta como 'stale' sin pisar el estado del rápido."""
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ) as http:
            return await run_scope(
                s1, InterleavedProvider(factory, s1), CollectSink(), http, session_factory=factory
            )

    r = asyncio.run(go())
    assert r.status == "stale"
    st = _state(factory, s1)
    assert _provider_cursor_of(st) == {"watermark": 200}  # gana el rápido, no el lento
    assert st.consecutive_failures == 0  # stale NO cuenta como fallo


class DisablingProvider(FakeProvider):
    """Simula que el scope se DESHABILITA durante el fetch (rev. 2ª #3)."""

    def __init__(self, factory, scope_id):
        self.factory = factory
        self.scope_id = scope_id

    async def fetch_new(self, params, cursor, http):
        async with self.factory() as s:
            await s.execute(
                sa.text("UPDATE harvest_scopes SET enabled = false WHERE id = :i"),
                {"i": self.scope_id},
            )
            await s.commit()
        return await FakeProvider.fetch_new(self, params, cursor, http)


class ParamChangingProvider(FakeProvider):
    """Simula que la keyword cambia durante el fetch (rev. 2ª #3)."""

    SEMANTIC_PARAMS = ("keyword",)

    def __init__(self, factory, scope_id):
        self.factory = factory
        self.scope_id = scope_id

    async def fetch_new(self, params, cursor, http):
        async with self.factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE harvest_scopes SET params = CAST(:p AS jsonb) WHERE id = :i"
                ),
                {"p": '{"keyword": "otra"}', "i": self.scope_id},
            )
            await s.commit()
        return await FakeProvider.fetch_new(self, params, cursor, http)


class SourceRepointingProvider(FakeProvider):
    """Simula que el scope se RE-APUNTA a otra fuente durante el fetch."""

    def __init__(self, factory, scope_id, created):
        self.factory = factory
        self.scope_id = scope_id
        self.created = created

    async def fetch_new(self, params, cursor, http):
        async with self.factory() as s:
            other = uuid.uuid4()
            self.created["extra_sources"].append(other)
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, :n, 0)"),
                {"id": other, "n": f"otra-fuente-{other.hex[:8]}"},
            )
            await s.execute(
                sa.text("UPDATE harvest_scopes SET source_id = :src WHERE id = :i"),
                {"src": other, "i": self.scope_id},
            )
            await s.commit()
        return await FakeProvider.fetch_new(self, params, cursor, http)


def _run_with(factory, scope_id, provider, sink):
    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ) as http:
            return await run_scope(scope_id, provider, sink, http, session_factory=factory)

    return asyncio.run(go())


def test_disable_during_fetch_aborts_without_persisting(db):
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    sink = CollectSink()
    r = _run_with(factory, s1, DisablingProvider(factory, s1), sink)
    assert r.status == "skipped"  # NO 'ok': la re-validación bajo el lock lo cazó
    assert sink.batches == []  # el sink JAMÁS llegó a invocarse (rev. 3ª #3)
    assert _state(factory, s1) is None  # nada persistido


def test_param_change_during_fetch_aborts_stale(db):
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    sink = CollectSink()
    r = _run_with(factory, s1, ParamChangingProvider(factory, s1), sink)
    assert r.status == "stale"  # el lote viejo no se persiste bajo config nueva
    assert sink.batches == []
    assert _state(factory, s1) is None


def test_source_repoint_during_fetch_aborts_stale(db):
    """Rev. 3ª #3: re-apuntar el scope a OTRA fuente durante el fetch → stale."""
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    sink = CollectSink()
    r = _run_with(factory, s1, SourceRepointingProvider(factory, s1, created), sink)
    assert r.status == "stale"
    assert sink.batches == []
    assert _state(factory, s1) is None


class DeletingProvider(FakeProvider):
    """Simula que el scope se ELIMINA durante el fetch (rev. A-04 2ª #3)."""

    def __init__(self, factory, scope_id):
        self.factory = factory
        self.scope_id = scope_id

    async def fetch_new(self, params, cursor, http):
        async with self.factory() as s:
            await s.execute(
                sa.text("DELETE FROM source_scope_state WHERE scope_id = :i"),
                {"i": self.scope_id},
            )
            await s.execute(
                sa.text("DELETE FROM harvest_scopes WHERE id = :i"), {"i": self.scope_id}
            )
            await s.commit()
        return await FakeProvider.fetch_new(self, params, cursor, http)


def test_scope_deleted_during_fetch_returns_not_found(db):
    """Rev. A-04 2ª #3: scope borrado DURANTE el fetch → not_found (permanente
    y normal, sin retry), sin persistir nada y sin excepción."""
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    sink = CollectSink()
    r = _run_with(factory, s1, DeletingProvider(factory, s1), sink)
    assert r.status == "not_found"
    assert sink.batches == []
    assert _state(factory, s1) is None


def test_missing_scope_returns_not_found(db):
    """Rev. A-04 2ª #3: scope inexistente al arrancar → not_found, no error."""
    factory, created = db
    r = _run(factory, str(uuid.uuid4()), CollectSink())
    assert r.status == "not_found"


class ConfigErrorProvider(FakeProvider):
    async def fetch_new(self, params, cursor, http):
        raise ProviderConfigError("hard_max_pages inválido (simulado)")


def test_provider_config_error_propagates_without_failure_count(db):
    """Rev. A-04 2ª #3: config PERMANENTE sube a la tarea (que falla sin
    retry) y NO cuenta como fallo de fuente (sin backoff)."""
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    with pytest.raises(ProviderConfigError):
        _run_with(factory, s1, ConfigErrorProvider(), CollectSink())
    assert _state(factory, s1) is None  # sin contador de fallo


class PartialProvider(FakeProvider):
    async def fetch_new(self, params, cursor, http):
        return FetchResult(
            listings=(RawListing(external_id="p1", url="https://x/p1", payload={}),),
            next_cursor={"last_top_seen": 5, "page_target": 8},
            pages_fetched=2,
            complete=False,
        )


def test_partial_sweep_persists_but_not_complete(db):
    """Rev. 4ª #1: un barrido incompleto persiste sink+cursor pero NO marca
    last_complete_at ni resetea fallos, y reporta 'partial'."""
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    sink = CollectSink()
    r = _run_with(factory, s1, PartialProvider(), sink)
    assert r.status == "partial"
    assert len(sink.batches) == 1  # los listings vistos SÍ llegan al sink
    st = _state(factory, s1)
    assert _provider_cursor_of(st) == {"last_top_seen": 5, "page_target": 8}
    assert st.last_complete_at is None  # jamás se marcó cosecha completa
    # Un run COMPLETO posterior sí consolida.
    r2 = _run_with(factory, s1, FakeProvider(), CollectSink())
    assert r2.status == "ok"
    assert _state(factory, s1).last_complete_at is not None


def _run_arbeitnow(factory, scope_id, sink, body):
    """Corre el runner con el provider REAL de arbeitnow y un feed mockeado.

    Sin FakeProvider: la propiedad auditada es justamente cómo el provider real
    clasifica la respuesta y qué escribe el runner con esa clasificación.
    """
    from jobhunt_core.harvest.providers.arbeitnow import ArbeitnowProvider

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, text=json.dumps(body.get(page, {"data": [], "links": {}})))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await run_scope(
                scope_id, ArbeitnowProvider(), sink, http, session_factory=factory
            )

    return asyncio.run(go())


_FEED_OK = {
    1: {
        "data": [{"slug": "a", "url": "https://x/a", "title": "T", "created_at": 300, "tags": []}],
        "links": {},
    }
}


def test_invalid_provider_response_does_not_confirm_a_complete_harvest(db):
    """REGRESIÓN auditoría externa 2026-08-27 P1-1 (#3), provider REAL → runner → BD.

    Un HTTP 200 con `data` que no es lista NO puede quedar registrado como cosecha completa.
    Antes del arreglo el provider devolvía `items=[]`/`complete=True` y el runner refrescaba
    `last_complete_at` y ponía `consecutive_failures=0`: el corpus dejaba de refrescarse mientras
    la salud del scope decía "todo bien" y, pasado CORE_CORPUS_STALE_DAYS, el archivado cerraba
    encarnaciones de vacantes todavía publicadas.
    """
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    # 1) Cosecha buena: deja cursor, last_complete_at y contador a cero.
    assert _run_arbeitnow(factory, s1, CollectSink(), _FEED_OK).status == "ok"
    previo = _state(factory, s1)
    assert previo.last_complete_at is not None and previo.consecutive_failures == 0

    # 2) La fuente cambia de forma (200 con sobre no conforme).
    sink = CollectSink()
    r = _run_arbeitnow(factory, s1, sink, {1: {"data": "no-soy-una-lista", "links": {}}})

    assert r.status == "error"
    assert sink.batches == []  # nada llegó al sink: el fetch murió antes
    ahora = _state(factory, s1)
    assert ahora.cursor == previo.cursor  # cursor INTACTO
    assert ahora.last_complete_at == previo.last_complete_at  # NO se confirmó cosecha
    assert ahora.consecutive_failures == previo.consecutive_failures + 1


def test_disabled_scope_is_skipped(db):
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)

    async def disable():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE harvest_scopes SET enabled = false WHERE id = :i"), {"i": s1}
            )
            await s.commit()

    asyncio.run(disable())
    r = _run(factory, s1, CollectSink())
    assert r.status == "skipped"
    assert _state(factory, s1) is None  # ni cursor ni fallos: no se tocó


def test_envelope_without_links_does_not_confirm_a_complete_harvest(db):
    """REGRESIÓN auditoría G9 P1-A, provider REAL → runner → BD.

    Hermano exacto del caso #3 por la OTRA clave del sobre: un HTTP 200 sin `links` se leía
    como final contractual del feed y el runner registraba cosecha completa (last_complete_at
    refrescado, consecutive_failures=0) sobre un barrido cortado en la página 1.
    """
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    assert _run_arbeitnow(factory, s1, CollectSink(), _FEED_OK).status == "ok"
    previo = _state(factory, s1)

    sink = CollectSink()
    sin_links = {1: {"data": [{"slug": "z", "url": "https://x/z", "title": "T", "tags": []}]}}
    r = _run_arbeitnow(factory, s1, sink, sin_links)

    assert r.status == "error"
    assert sink.batches == []
    ahora = _state(factory, s1)
    assert ahora.cursor == previo.cursor
    assert ahora.last_complete_at == previo.last_complete_at
    assert ahora.consecutive_failures == previo.consecutive_failures + 1


def test_a_harvest_that_ingests_nothing_is_never_confirmed_complete(db):
    """REGRESIÓN auditoría G10 P1-1 y P2-1, provider REAL → runner → BD.

    Las dos maneras de fingir una cosecha con el sobre INTACTO, que es lo que las hacía
    invisibles: (a) `data: []` con `links.next` presente —el feed se contradice y el
    barrido se declaraba agotado emitiendo cero ofertas—; (b) `data` con items que ya no
    se reconocen como ofertas (subida de versión que renombra `url`→`job_url`). Las dos
    dejaban `last_complete_at` fresco y `consecutive_failures = 0`, que son EXACTAMENTE
    las dos señales que mira la vigilancia de `harvest/health.py`: el corpus se congelaba
    en verde y el archivado ADR-07 acabaría retirando vacantes todavía publicadas.
    """
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    assert _run_arbeitnow(factory, s1, CollectSink(), _FEED_OK).status == "ok"
    previo = _state(factory, s1)
    assert previo.last_complete_at is not None and previo.consecutive_failures == 0

    for feed in (
        {1: {"data": [], "links": {"next": "?page=2"}}},                      # P1-1
        {1: {"data": [{"id": 1, "job_url": "https://x/a", "name": "T"}],      # P2-1
             "links": {"next": None}}},
    ):
        sink = CollectSink()
        r = _run_arbeitnow(factory, s1, sink, feed)
        assert r.status == "error"
        assert sink.batches == []                       # nada que ingerir, nada ingerido
        ahora = _state(factory, s1)
        assert ahora.cursor == previo.cursor            # cursor INTACTO
        assert ahora.last_complete_at == previo.last_complete_at   # NO se confirmó nada
        assert ahora.consecutive_failures == previo.consecutive_failures + 1  # y se VE
        previo = ahora


def test_broken_page_mid_sweep_persists_the_good_pages_and_counts_the_failure(db):
    """REGRESIÓN auditoría G9 P2-C, provider REAL → runner → BD: las dos mitades.

    (a) Lo cosechado ANTES del sobre roto se persiste — el todo-o-nada dejaba una fuente con
    forma inválida persistente en la página 2 sin ingerir una sola oferta.
    (b) El barrido NO se confirma completo y el fallo SÍ se contabiliza: un 'partial' que
    dejara `consecutive_failures` quieto convertiría el rojo mudo en un silencio total.
    """
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    feed = {
        1: {
            "data": [{"slug": "a", "url": "https://x/a", "title": "T", "created_at": 300,
                      "tags": []}],
            "links": {"next": "?page=2"},
        },
        2: {"data": {"no": "soy una lista"}, "links": {}},
    }
    sink = CollectSink()
    r = _run_arbeitnow(factory, s1, sink, feed)

    assert r.status == "partial"
    assert [x.external_id for _, batch in sink.batches for x in batch] == ["a"]
    st = _state(factory, s1)
    assert st.cursor is not None                 # cursor del barrido parcial persistido
    assert st.last_complete_at is None           # NUNCA cosecha completa
    assert st.consecutive_failures == 1          # el fallo es VISIBLE, no un silencio


def _set_state(factory, scope_id, *, failures=0, last_complete_at=None):
    """Fija el estado del scope (la avería que la vigilancia debe VER)."""
    async def go():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO source_scope_state "
                    "(scope_id, consecutive_failures, last_complete_at) "
                    "VALUES (:i, :f, :t) ON CONFLICT (scope_id) DO UPDATE SET "
                    "consecutive_failures = EXCLUDED.consecutive_failures, "
                    "last_complete_at = EXCLUDED.last_complete_at"
                ),
                {"i": scope_id, "f": failures, "t": last_complete_at},
            )
            await s.commit()

    asyncio.run(go())


def _health(factory, **kw):
    from jobhunt_core.harvest.health import check_harvest_health

    async def go():
        async with factory() as s:
            return await check_harvest_health(s, **kw)

    return asyncio.run(go())


def test_harvest_health_alerts_on_failing_and_stale_scopes(db):
    """REGRESIÓN auditoría G9 P2-C (2ª mitad): el rojo tenía que dejar de ser MUDO.

    `consecutive_failures` y `last_complete_at` existían desde A-03 y no los leía ninguna
    métrica, gate ni alerta: una fuente que dejaba de cosechar no producía señal hasta que,
    120 d después, el archivado ADR-07 empezaba a retirar vacantes todavía publicadas.
    """
    factory, created = db
    fallando, rancio, sano = _seed_scopes(factory, created, n=3)
    ahora = datetime.now(timezone.utc)
    _set_state(factory, fallando, failures=3, last_complete_at=ahora)
    _set_state(factory, rancio, failures=0, last_complete_at=ahora - timedelta(days=30))
    _set_state(factory, sano, failures=1, last_complete_at=ahora - timedelta(hours=2))

    out = _health(factory, now=ahora)
    por_scope = {(a["scope_id"], a["code"]) for a in out["alertas"]}

    assert (fallando, "cosecha_fallando") in por_scope
    assert (rancio, "cosecha_sin_completar") in por_scope
    assert not [a for a in out["alertas"] if a["scope_id"] == sano]  # 1 fallo y fresco: sin ruido
    assert out["scopes"] == 3


def test_harvest_health_alerts_when_a_scope_never_completed_a_sweep(db):
    """El caso EXACTO del barrido parcial perpetuo (G9 P2-C): el scope se ejecuta, persiste
    listings y nunca confirma una cosecha completa. `last_complete_at IS NULL` es la señal —
    sin ella el corpus se congela con el contador de fallos en un valor cualquiera."""
    factory, created = db
    (s1,) = _seed_scopes(factory, created, n=1)
    _set_state(factory, s1, failures=0, last_complete_at=None)

    alertas = _health(factory)["alertas"]

    assert [a["code"] for a in alertas] == ["cosecha_sin_completar"]
    assert alertas[0]["dias_sin_cosecha_completa"] is None
    assert "NUNCA" in alertas[0]["msg"]


def test_harvest_health_is_silent_for_never_run_and_disabled_scopes(db):
    """Silencio DELIBERADO: sin fila de estado el scope no se ha ejecutado nunca (la cosecha
    del core se lanza a mano, sin beat propio) y un scope deshabilitado no debe cosechar —
    ninguno de los dos es una avería, y una alerta ahí sería ruido que entrena a ignorarlas."""
    factory, created = db
    nunca, apagado = _seed_scopes(factory, created, n=2)
    _set_state(factory, apagado, failures=9, last_complete_at=None)

    async def disable():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE harvest_scopes SET enabled = false WHERE id = :i"),
                {"i": apagado},
            )
            await s.commit()

    asyncio.run(disable())
    out = _health(factory)
    assert out["alertas"] == [] and out["scopes"] == 0


def test_harvest_health_task_is_wired_to_the_beat_on_the_light_queue():
    """La vigilancia solo sirve si CORRE: va en el beat del core-worker y se rutea a
    core.default — el comodín `jobhunt.harvest.*` la habría puesto en core.harvest, detrás
    de los lotes de cosecha (misma decisión que la vigilancia del slot de la sombra)."""
    from jobhunt_core.celery_app import celery_app
    from jobhunt_core.tasks import harvest as harvest_tasks  # registra las tareas

    assert harvest_tasks.check_harvest_health_task.name == "jobhunt.harvest.check_health"
    assert "jobhunt.harvest.check_health" in celery_app.tasks
    assert celery_app.conf.task_routes["jobhunt.harvest.check_health"] == {
        "queue": "core.default"
    }
    beat_tasks = {e["task"] for e in celery_app.conf.beat_schedule.values()}
    assert "jobhunt.harvest.check_health" in beat_tasks

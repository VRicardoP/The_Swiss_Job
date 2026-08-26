"""RawListingSink (A-04) contra Postgres real: slot + incarnación + revisión.

DoD: revisión cuelga de la incarnación; `last_seen_at` en CADA cosecha; raw
antes de normalizar; idempotencia por content_hash. Ejecutar vía core-migrate.
"""

import asyncio
import logging
import os
import uuid

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core.config import settings
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing
from jobhunt_core.tests import dbcleanup
from jobhunt_core.tests.test_integration_harvest import FakeProvider

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"source": None, "scopes": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            # Borra TODO el grafo creado por el sink para esta fuente, en orden
            # FK-seguro (el puntero primario se nulifica por SET NULL de columna).
            await dbcleanup.purge_source_graph(
                s, [created["source"]], created["scopes"]
            )
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _seed_scope(factory, created) -> str:
    async def go():
        async with factory() as s:
            source_id, scope_id = uuid.uuid4(), uuid.uuid4()
            created["source"] = source_id
            created["scopes"].append(scope_id)
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, 'arbeitnow', 0)"),
                {"id": source_id},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                    "VALUES (:id, :src, '{}'::jsonb, 0)"
                ),
                {"id": scope_id, "src": source_id},
            )
            await s.commit()
            return str(scope_id)

    return asyncio.run(go())


def _sink_batch(factory, scope_id, listings) -> None:
    async def go():
        async with factory() as s:
            await RawListingSink().handle(s, scope_id, tuple(listings))
            await s.commit()

    asyncio.run(go())


def _counts(factory, source_id):
    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT "
                        "(SELECT count(*) FROM source_listings WHERE source_id=:src) AS slots, "
                        "(SELECT count(*) FROM source_listing_incarnations i "
                        " JOIN source_listings l ON l.id=i.source_listing_id "
                        " WHERE l.source_id=:src) AS incs, "
                        "(SELECT count(*) FROM source_listing_revisions r "
                        " JOIN source_listing_incarnations i ON i.id=r.incarnation_id "
                        " JOIN source_listings l ON l.id=i.source_listing_id "
                        " WHERE l.source_id=:src) AS revs"
                    ),
                    {"src": source_id},
                )
            ).one()

    return asyncio.run(go())


def _listing(ext, payload=None, url=None):
    return RawListing(
        external_id=ext,
        url=url or f"https://x/{ext}",
        payload=payload if payload is not None else {"title": ext, "v": 1},
    )


def test_first_batch_creates_full_graph(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _sink_batch(factory, scope, [_listing("j1"), _listing("j2")])
    c = _counts(factory, created["source"])
    assert (c.slots, c.incs, c.revs) == (2, 2, 2)

    async def check_graph():
        async with factory() as s:
            row = (
                await s.execute(
                    sa.text(
                        "SELECT i.seq, i.vacancy_id, v.primary_incarnation_id, i.id "
                        "FROM source_listing_incarnations i "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "JOIN vacancies v ON v.id = i.vacancy_id "
                        "WHERE l.external_id = 'j1'"
                    )
                )
            ).one()
            assert row.seq == 1
            assert row.primary_incarnation_id == row.id  # puntero primario fijado

    asyncio.run(check_graph())


def test_unchanged_content_refreshes_last_seen_without_new_revision(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _sink_batch(factory, scope, [_listing("j1")])

    async def seen():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT i.last_seen_at FROM source_listing_incarnations i "
                        "JOIN source_listings l ON l.id=i.source_listing_id "
                        "WHERE l.external_id='j1'"
                    )
                )
            ).scalar_one()

    t1 = asyncio.run(seen())
    _sink_batch(factory, scope, [_listing("j1")])  # MISMO contenido
    t2 = asyncio.run(seen())
    assert t2 > t1  # last_seen_at refrescado en CADA cosecha (contrato §1)
    assert _counts(factory, created["source"]).revs == 1  # sin revisión nueva


def test_changed_content_creates_new_revision_same_incarnation(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _sink_batch(factory, scope, [_listing("j1", payload={"title": "j1", "v": 1})])
    _sink_batch(factory, scope, [_listing("j1", payload={"title": "j1", "v": 2})])
    c = _counts(factory, created["source"])
    assert (c.slots, c.incs, c.revs) == (1, 1, 2)  # misma incarnación, 2 revisiones


def test_url_collision_skipped_with_log(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _sink_batch(factory, scope, [_listing("j1", url="https://x/misma")])
    # Otro external_id con la MISMA URL normalizada → frontera: se salta.
    _sink_batch(factory, scope, [_listing("j2", url="https://x/misma/")])
    c = _counts(factory, created["source"])
    assert (c.slots, c.incs, c.revs) == (1, 1, 1)


def _seed_second_scope(factory, created) -> str:
    async def go():
        async with factory() as s:
            scope_id = uuid.uuid4()
            created["scopes"].append(scope_id)
            await s.execute(
                sa.text(
                    "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                    "VALUES (:id, :src, '{}'::jsonb, 0)"
                ),
                {"id": scope_id, "src": created["source"]},
            )
            await s.commit()
            return str(scope_id)

    return asyncio.run(go())


def test_concurrent_scopes_same_source_no_duplicates(db):
    """Auditoría A-04 #1/#2: DOS scopes de la MISMA fuente, sinks CONCURRENTES
    sobre external_ids solapados (slot nuevo + slot reciclado) → exactamente
    1 slot / 1 incarnación activa / 1 vacante por external_id; sin
    unique_violation ni deadlock sin gestionar; sin vacantes huérfanas."""
    factory, created = db
    scope_a = _seed_scope(factory, created)
    scope_b = _seed_second_scope(factory, created)

    # Slot 'recycled' pre-existente con su incarnación CERRADA (rama seq>1).
    _sink_batch(factory, scope_a, [_listing("recycled")])

    async def close_incarnation():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE source_listing_incarnations SET ended_at = now() "
                    "WHERE source_listing_id IN "
                    "(SELECT id FROM source_listings WHERE external_id = 'recycled')"
                )
            )
            await s.commit()

    asyncio.run(close_incarnation())

    batch = [_listing("nuevo"), _listing("recycled"), _listing("compartido")]

    async def worker(scope_id, listings):
        async with factory() as s:
            await RawListingSink().handle(s, scope_id, tuple(listings))
            await s.commit()

    async def race():
        # Órdenes de lote INVERSOS entre scopes: el peor caso de locks.
        await asyncio.gather(
            worker(scope_a, batch), worker(scope_b, list(reversed(batch)))
        )

    asyncio.run(race())  # sin excepciones no gestionadas

    async def invariants():
        async with factory() as s:
            rows = (
                await s.execute(
                    sa.text(
                        "SELECT l.external_id, "
                        "count(*) FILTER (WHERE i.ended_at IS NULL) AS activas, "
                        "count(DISTINCT l.id) AS slots "
                        "FROM source_listings l "
                        "LEFT JOIN source_listing_incarnations i ON i.source_listing_id = l.id "
                        "WHERE l.source_id = :src GROUP BY l.external_id"
                    ),
                    {"src": created["source"]},
                )
            ).all()
            for r in rows:
                assert (r.slots, r.activas) == (1, 1), r
            # Sin vacantes huérfanas: cada vacante creada tiene su incarnación.
            orphans = (
                await s.execute(
                    sa.text(
                        "SELECT count(*) FROM vacancies v "
                        "WHERE v.id NOT IN (SELECT vacancy_id FROM source_listing_incarnations) "
                        "AND v.primary_incarnation_id IS NULL AND v.created_at > now() - interval '5 minutes'"
                    )
                )
            ).scalar()
            assert orphans == 0
            # El slot reciclado reabrió con seq=2.
            seq = (
                await s.execute(
                    sa.text(
                        "SELECT i.seq FROM source_listing_incarnations i "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.external_id = 'recycled' AND i.ended_at IS NULL"
                    )
                )
            ).scalar_one()
            assert seq == 2

    asyncio.run(invariants())


def test_concurrent_cross_key_lock_order_no_deadlock(db):
    """Rev. A-04 #1 (repro de la revisión): claves UNIQUE CRUZADAS entre lotes
    — T1 [a→urlZ, b→urlA] vs T2 [c→urlA, d→urlZ]. Ordenar por external_id deja
    los locks de url_normalized en orden INVERSO → deadlock (2/5 en la repro
    original). La serialización por fuente (pg_advisory_xact_lock) lo elimina
    manteniendo paralelismo entre fuentes. 5 rondas con slots frescos."""
    factory, created = db
    scope_a = _seed_scope(factory, created)
    scope_b = _seed_second_scope(factory, created)

    async def worker(scope_id, listings):
        async with factory() as s:
            await RawListingSink().handle(s, scope_id, tuple(listings))
            await s.commit()

    async def race(i):
        t1 = [_listing(f"a{i}", url=f"https://x/z{i}"), _listing(f"b{i}", url=f"https://x/a{i}")]
        t2 = [_listing(f"c{i}", url=f"https://x/a{i}"), _listing(f"d{i}", url=f"https://x/z{i}")]
        await asyncio.gather(worker(scope_a, t1), worker(scope_b, t2))

    for i in range(5):
        asyncio.run(race(i))  # sin DeadlockDetectedError

    async def invariants():
        async with factory() as s:
            dup_urls = (
                await s.execute(
                    sa.text(
                        "SELECT count(*) FROM (SELECT url_normalized "
                        "FROM source_listings WHERE source_id = :src "
                        "GROUP BY url_normalized HAVING count(*) > 1) d"
                    ),
                    {"src": created["source"]},
                )
            ).scalar()
            assert dup_urls == 0  # la UNIQUE cruzada decide UN ganador por URL
            c = (
                await s.execute(
                    sa.text(
                        "SELECT "
                        "(SELECT count(*) FROM source_listings WHERE source_id=:src) AS slots, "
                        "(SELECT count(*) FROM source_listing_incarnations i "
                        " JOIN source_listings l ON l.id=i.source_listing_id "
                        " WHERE l.source_id=:src AND i.ended_at IS NULL) AS activas"
                    ),
                    {"src": created["source"]},
                )
            ).one()
            # 2 URLs por ronda x 5 rondas; cada slot con SU incarnación activa.
            assert (c.slots, c.activas) == (10, 10)
            orphans = (
                await s.execute(
                    sa.text(
                        "SELECT count(*) FROM vacancies v "
                        "WHERE v.id NOT IN (SELECT vacancy_id FROM source_listing_incarnations) "
                        "AND v.primary_incarnation_id IS NULL "
                        "AND v.created_at > now() - interval '5 minutes'"
                    )
                )
            ).scalar()
            assert orphans == 0

    asyncio.run(invariants())


def test_mixed_batch_isolates_invalid_listings(db, caplog):
    """Rev. A-04 #2 (repro): un external_id de 201 chars (o url > 2048 — contrato core0028 —, o NUL)
    NO revienta el lote con StringDataRightTruncationError — se cuarentena con
    log y los válidos persisten. Con la emisión total de A-03, sin esto el dato
    tóxico reaparecería en cada cosecha y bloquearía el scope para siempre."""
    factory, created = db
    scope = _seed_scope(factory, created)
    poison = [
        _listing("x" * 201),  # external_id > 200
        _listing("url-larga", url="https://x/" + "u" * 2100),  # url > 2048
        _listing("nul", payload={"title": "a\x00b"}),  # NUL (jsonb lo rechaza)
        _listing("surrogate", payload={"t": "\ud800"}),  # no codificable UTF-8
        _listing("nan", payload={"n": float("nan")}),  # 2ª: jsonb rechaza NaN
        _listing("ipv6", url="https://[invalid"),  # 2ª: urlsplit ValueError
        _listing("s\ud800id"),  # 2ª: surrogate en el propio external_id
    ]
    # Regresión de FALSO POSITIVO (2ª #2): texto legítimo con '\u0000' LITERAL
    # (seis caracteres, sin NUL real) debe persistir con normalidad.
    lit = _listing("lit", payload={"t": "\\u0000"})
    with caplog.at_level(logging.WARNING, logger="jobhunt_core.harvest.sink"):
        _sink_batch(factory, scope, [_listing("ok1"), *poison, lit, _listing("ok2")])
    c = _counts(factory, created["source"])
    assert (c.slots, c.incs, c.revs) == (3, 3, 3)  # ok1, lit, ok2
    assert sum("CUARENTENA" in r.getMessage() for r in caplog.records) == 7


def test_empty_batch_is_noop(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _sink_batch(factory, scope, [])
    assert tuple(_counts(factory, created["source"])) == (0, 0, 0)


def test_intra_batch_duplicate_external_last_wins(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _sink_batch(
        factory, scope,
        [_listing("j1", payload={"v": 1}), _listing("j1", payload={"v": 2})],
    )
    c = _counts(factory, created["source"])
    assert (c.slots, c.incs, c.revs) == (1, 1, 1)

    async def payload():
        async with factory() as s:
            return (
                await s.execute(sa.text("SELECT raw FROM source_listing_revisions"))
            ).scalar_one()

    assert asyncio.run(payload())["v"] == 2  # la última aparición gana


def test_same_normalized_url_within_one_batch(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _sink_batch(
        factory, scope,
        [_listing("j1", url="https://x/misma"), _listing("j2", url="https://x/misma/")],
    )
    c = _counts(factory, created["source"])
    assert (c.slots, c.incs, c.revs) == (1, 1, 1)  # el segundo se salta con log


def test_task_two_consecutive_runs_same_process(db):
    """Rev. 2ª #1 (repro): dos tareas Celery REALES consecutivas en el MISMO
    proceso worker. Con el engine global compartido, la 2ª muere con 'Future
    attached to a different loop' + InterfaceError (cada asyncio.run crea un
    loop nuevo y el pool asyncpg queda ligado al primero). El engine
    desechable por invocación (task_session_factory) lo elimina."""
    from jobhunt_core.tasks.harvest import run_scope_task

    factory, created = db
    scope_a = _seed_scope(factory, created)
    scope_b = _seed_second_scope(factory, created)

    async def disable():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE harvest_scopes SET enabled = false WHERE id = ANY(:ids)"),
                {"ids": [uuid.UUID(scope_a), uuid.UUID(scope_b)]},
            )
            await s.commit()

    asyncio.run(disable())

    r1 = run_scope_task.apply(args=[scope_a])
    r2 = run_scope_task.apply(args=[scope_b])  # antes: InterfaceError aquí
    assert r1.successful() and r1.result["status"] == "skipped"
    assert r2.successful() and r2.result["status"] == "skipped"


def test_reparacion_da_de_alta_el_handler_sombra_en_vez_de_anular_la_canonica(db):
    """Regresión G3-A-P3-2: `_rebuild_canonical_after_repair` trataba el None de
    `normalize_offer` como «contenido no normalizable», pero esa función
    devuelve None TAMBIÉN cuando el proceso no tiene registrado el normalizador
    de esa fuente — y el registry es memoria POR PROCESO: los `legacy:*` los
    registra el proyector, que comparte la cola `core.harvest` con la cosecha.
    La vacante VIVA perdía su canónica (fuera de /v1, del corpus de matching y
    del de dedup, con re-evaluación completa por bump_corpus_generation). Ahora
    esa causa se ELIMINA dando de alta el handler sombra en caliente.

    El invariante de A-06 no se toca: una fuente AJENA sin handler sigue
    anulando el puntero (test_recycled_shared_primary_without_normalizer_nulls_pointer),
    porque servir el contenido del primary ANTERIOR —otra empresa— es peor."""
    from jobhunt_core.harvest import normalize
    from jobhunt_core.harvest.providers import arbeitnow, legacy_shadow  # noqa: F401

    factory, created = db
    scope = _seed_scope(factory, created)
    ext = f"g3norm-{uuid.uuid4().hex[:8]}"
    _sink_batch(factory, scope, [
        RawListing(external_id=ext, url=f"https://x/{ext}",
                   payload={"title": "SRE Engineer", "company_name": "ACME AG"}),
    ])

    def _target():
        async def go():
            async with factory() as s:
                return (
                    await s.execute(
                        sa.text(
                            "SELECT v.id AS vid, i.id AS iid, "
                            "v.current_offer_revision_id AS ptr "
                            "FROM vacancies v "
                            "JOIN source_listing_incarnations i ON i.vacancy_id = v.id "
                            "JOIN source_listings l ON l.id = i.source_listing_id "
                            "WHERE l.external_id = :e"
                        ),
                        {"e": ext},
                    )
                ).one()

        return asyncio.run(go())

    antes = _target()
    assert antes.ptr is not None  # canónica VIVA

    # Fuente SOMBRA cuyo handler no está registrado en ESTE proceso.
    sombra = f"legacy:g3-sin-registrar-{uuid.uuid4().hex[:6]}"
    assert normalize.has_normalizer(sombra) is False

    async def rebuild():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE sources SET name = :n WHERE id = :i"),
                {"n": sombra, "i": created["source"]},
            )
            await RawListingSink()._rebuild_canonical_after_repair(
                s, [(antes.vid, antes.iid)]
            )
            await s.commit()

    try:
        asyncio.run(rebuild())
        assert _target().ptr is not None  # antes: None (canónica viva anulada)
        assert normalize.has_normalizer(sombra) is True  # alta en caliente
    finally:
        normalize._NORMALIZERS.pop(sombra, None)
        legacy_shadow._registered.discard(sombra)


def _exec_sql(factory, stmt, **params):
    async def go():
        async with factory() as s:
            r = await s.execute(sa.text(stmt), params)
            await s.commit()
            return r

    return asyncio.run(go())


def test_g4_la_marca_del_raw_re_visto_es_monotona(db):
    """G4-N-2: `now()` es `transaction_timestamp()`, no la hora de escritura, y
    el `DO UPDATE` ASIGNABA en vez de maximizar — una tx que selló su reloj
    antes pero escribe después (espera del advisory lock por fuente +
    preprocesado del lote) hacía RETROCEDER el `fetched_at` de la revisión
    vigente y reabría G3-A-P3-3: los dos call-sites del raw vigente leen por
    `fetched_at DESC` y devolverían la revisión SUPERADA."""
    from jobhunt_core.harvest.providers import arbeitnow  # noqa: F401

    factory, created = db
    scope = _seed_scope(factory, created)
    ext = f"g4mono-{uuid.uuid4().hex[:8]}"
    payload = {"title": "SRE", "company_name": "ACME AG"}
    _sink_batch(factory, scope, [
        RawListing(external_id=ext, url=f"https://x/{ext}", payload=payload),
    ])

    def _fetched():
        return _exec_sql(
            factory,
            "SELECT r.fetched_at FROM source_listing_revisions r "
            "JOIN source_listing_incarnations i ON i.id = r.incarnation_id "
            "JOIN source_listings l ON l.id = i.source_listing_id "
            "WHERE l.external_id = :e", e=ext,
        ).scalar_one()

    # Otra transacción dejó una marca POSTERIOR a la que traerá la siguiente
    # cosecha (el interleaving del que habla la nota, sin construirlo).
    _exec_sql(
        factory,
        "UPDATE source_listing_revisions SET fetched_at = now() + interval '1 hour' "
        "WHERE incarnation_id IN (SELECT i.id FROM source_listing_incarnations i "
        " JOIN source_listings l ON l.id = i.source_listing_id "
        " WHERE l.external_id = :e)", e=ext,
    )
    futuro = _fetched()
    _sink_batch(factory, scope, [  # re-cosecha del MISMO contenido
        RawListing(external_id=ext, url=f"https://x/{ext}", payload=payload),
    ])
    assert _fetched() == futuro  # antes: retrocedía a now() de esta tx


def test_g4_reparacion_da_de_alta_el_handler_de_portfolio_import(db):
    """Regresión G4-P2-4: el alta en caliente de G3-A-P3-2 solo cubría
    `legacy:*` y volvía —logueando— para cualquier otra fuente. El registro es
    memoria POR PROCESO y a `portfolio-import` no la importa NINGÚN módulo del
    `celery_app.conf.include`, así que el `core-worker` que repara el primary
    de una vacante COMPARTIDA (attach cross-source por la misma
    `url_normalized`) le anulaba la canónica estando VIVA: fuera de /v1, del
    corpus de matching y del de dedup, y con re-evaluación completa por
    `bump_corpus_generation`. Y son justo las vacantes a las que el usuario se
    apuntó o marcó — que nadie vuelve a cosechar (su scope nace deshabilitado),
    así que no se auto-reparaba jamás.

    El alta no depende ya del PREFIJO: cada namespace conocido tiene su alta
    idempotente (`harvest/registry.py`)."""
    from jobhunt_core.harvest import normalize
    from jobhunt_core.harvest.providers import arbeitnow  # noqa: F401
    from jobhunt_core.import_portfolio import PORTFOLIO_IMPORT_SOURCE

    factory, created = db
    scope = _seed_scope(factory, created)  # fuente 'arbeitnow'
    ext = f"g4pf-{uuid.uuid4().hex[:8]}"
    _sink_batch(factory, scope, [
        RawListing(external_id=ext, url=f"https://x/{ext}",
                   payload={"title": "SRE Engineer", "company_name": "ACME AG"}),
    ])

    def _target():
        async def go():
            async with factory() as s:
                return (
                    await s.execute(
                        sa.text(
                            "SELECT v.id AS vid, i.id AS iid, "
                            "v.current_offer_revision_id AS ptr "
                            "FROM vacancies v "
                            "JOIN source_listing_incarnations i ON i.vacancy_id = v.id "
                            "JOIN source_listings l ON l.id = i.source_listing_id "
                            "WHERE l.external_id = :e"
                        ),
                        {"e": ext},
                    )
                ).one()

        return asyncio.run(go())

    antes = _target()
    assert antes.ptr is not None  # canónica VIVA

    # El slot pasa a colgar de la fuente REAL `portfolio-import` (alta
    # idempotente: puede existir ya de otro test) y se devuelve al terminar,
    # para que el teardown por fuente siga limpiando el grafo.
    mia = uuid.uuid4()
    _exec_sql(
        factory,
        "INSERT INTO sources (id, name, tier) VALUES (:i, :n, 0) "
        "ON CONFLICT (name) DO NOTHING",
        i=mia, n=PORTFOLIO_IMPORT_SOURCE,
    )
    src_pf = _exec_sql(
        factory, "SELECT id FROM sources WHERE name = :n", n=PORTFOLIO_IMPORT_SOURCE
    ).scalar_one()
    _exec_sql(
        factory,
        "UPDATE source_listings SET source_id = :s WHERE external_id = :e",
        s=src_pf, e=ext,
    )
    # Proceso que NUNCA importó `import_portfolio` (el core-worker real).
    fn = normalize._NORMALIZERS.pop(PORTFOLIO_IMPORT_SOURCE)
    assert normalize.has_normalizer(PORTFOLIO_IMPORT_SOURCE) is False

    async def rebuild():
        async with factory() as s:
            await RawListingSink()._rebuild_canonical_after_repair(
                s, [(antes.vid, antes.iid)]
            )
            await s.commit()

    try:
        asyncio.run(rebuild())
        assert _target().ptr is not None  # antes: None (canónica viva anulada)
        assert normalize.has_normalizer(PORTFOLIO_IMPORT_SOURCE) is True
    finally:
        normalize._NORMALIZERS.setdefault(PORTFOLIO_IMPORT_SOURCE, fn)
        _exec_sql(
            factory,
            "UPDATE source_listings SET source_id = :s WHERE external_id = :e",
            s=created["source"], e=ext,
        )
        _exec_sql(factory, "DELETE FROM sources WHERE id = :i", i=mia)


def test_g5_un_alta_que_revienta_no_tumba_la_cosecha(monkeypatch):
    """G5-N-3: `_altas_exact_match()` se evalúa para CUALQUIER fuente no
    `legacy:*`, y el alta importa un módulo DENTRO de la transacción de
    reparación del sink. Donde el camino anterior solo LOGUEABA, un
    ImportError o un efecto de import abortaría la cosecha entera. Fail-soft:
    se devuelve False (canónica NULL, el contrato A-06 ya escrito) y se
    registra, en vez de propagar."""
    from jobhunt_core.harvest import registry

    def _alta_rota():
        raise ImportError("ciclo de import simulado")

    monkeypatch.setattr(
        registry, "_altas_exact_match", lambda: {"fuente-rota": _alta_rota}
    )
    assert registry.ensure_handler("fuente-rota") is False


def test_g4_registry_solo_da_de_alta_los_namespaces_conocidos():
    """El alta por namespace no adivina: `legacy:*` va al handler genérico de
    la sombra, las exact-match a la suya, y una fuente AJENA sigue sin
    normalizador (A-06: antes que servir el contenido del primary ANTERIOR
    —que puede ser otra empresa— se anula el puntero)."""
    from jobhunt_core.harvest import normalize, registry
    from jobhunt_core.harvest.providers import legacy_shadow

    assert registry.ensure_handler("arbeitnow") is True
    assert registry.ensure_handler("portfolio-import") is True
    assert registry.ensure_handler("fuente-ajena-inexistente") is False

    sombra = f"legacy:g4-registry-{uuid.uuid4().hex[:6]}"
    try:
        assert registry.ensure_handler(sombra) is True
        assert normalize.has_normalizer(sombra) is True
    finally:
        normalize._NORMALIZERS.pop(sombra, None)
        legacy_shadow._registered.discard(sombra)


def test_contenido_que_revierte_refresca_la_marca_del_raw_vigente(db):
    """Regresión G3-A-P3-3: «última revisión» se lee por `fetched_at DESC` en
    los dos sitios que necesitan el raw VIGENTE (el guard de reciclado y
    `_rebuild_canonical_after_repair`), pero un contenido que REVIERTE a un
    hash ya visto no insertaba fila ni tocaba la existente: la revisión vigente
    conservaba su fetched_at ORIGINAL y la intermedia —superada— uno posterior,
    así que ambas lecturas devolvían la SUPERADA (y tras una reparación de
    primary el /v1 servía un salario ya retirado). `_canonicalize` sí trataba
    bien el revert, de modo que el puntero canónico y estas lecturas
    discrepaban."""
    factory, created = db
    scope = _seed_scope(factory, created)
    ext = f"g3rev-{uuid.uuid4().hex[:8]}"

    def _cosecha(salario):
        _sink_batch(factory, scope, [
            RawListing(external_id=ext, url=f"https://x/{ext}",
                       payload={"title": "SRE", "company_name": "ACME AG",
                                "salary_original": salario}),
        ])

    _cosecha("80k")
    _cosecha("90k")
    _cosecha("80k")  # el portal REVIERTE al contenido anterior

    def _revisiones():
        async def go():
            async with factory() as s:
                return (
                    await s.execute(
                        sa.text(
                            "SELECT count(*) AS n FROM source_listing_revisions r "
                            "JOIN source_listing_incarnations i "
                            "  ON i.id = r.incarnation_id "
                            "JOIN source_listings l ON l.id = i.source_listing_id "
                            "WHERE l.external_id = :e"
                        ),
                        {"e": ext},
                    )
                ).one()

        return asyncio.run(go())

    # El revert NO crea fila (el par (incarnación, content_hash) ya existía).
    assert _revisiones().n == 2

    def _vigente():
        """La consulta EXACTA de los dos call-sites del raw vigente."""

        async def go():
            async with factory() as s:
                return (
                    await s.execute(
                        sa.text(
                            "SELECT DISTINCT ON (r.incarnation_id) r.raw "
                            "FROM source_listing_revisions r "
                            "JOIN source_listing_incarnations i "
                            "  ON i.id = r.incarnation_id "
                            "JOIN source_listings l ON l.id = i.source_listing_id "
                            "WHERE l.external_id = :e "
                            "ORDER BY r.incarnation_id, r.fetched_at DESC, r.id"
                        ),
                        {"e": ext},
                    )
                ).one()

        return asyncio.run(go())

    # antes: '90k' (la revisión SUPERADA)
    assert _vigente().raw["salary_original"] == "80k"


_VIGENTE_SQL = (  # la consulta EXACTA de los tres call-sites del raw vigente
    "SELECT DISTINCT ON (r.incarnation_id) r.raw, r.incarnation_id "
    "FROM source_listing_revisions r "
    "JOIN source_listing_incarnations i ON i.id = r.incarnation_id "
    "JOIN source_listings l ON l.id = i.source_listing_id "
    "WHERE l.external_id = :e ORDER BY r.incarnation_id, r.fetched_at DESC, r.id"
)


def _lst_titulo(ext, titulo):
    return RawListing(
        external_id=ext, url=f"https://x/{ext}",
        payload={"title": titulo, "company_name": "ACME AG"},
    )


def _canonica(factory, ext):
    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT orv.content->>'title' FROM vacancies v "
                        "JOIN offer_revisions orv "
                        "  ON orv.id = v.current_offer_revision_id "
                        "JOIN source_listing_incarnations i ON i.vacancy_id = v.id "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.external_id = :e"
                    ),
                    {"e": ext},
                )
            ).scalar_one()

    return asyncio.run(go())


def _dos_tx_entrelazadas(factory, scope, ext):
    """Dos escritores sobre la MISMA fuente, entrelazados: A sella el reloj de
    su transacción PRIMERO (preprocesado del lote + espera del advisory lock)
    pero escribe la ÚLTIMA. Es el caso para el que existe el lock por fuente."""

    async def go():
        async with factory() as sa_, factory() as sb:
            # A arranca: su transaction_timestamp() queda sellado aquí.
            await sa_.execute(sa.text("SELECT transaction_timestamp()"))
            # B arranca DESPUÉS y termina ANTES.
            await RawListingSink().handle(sb, scope, (_lst_titulo(ext, "SRE 90k"),))
            await sb.commit()
            # A por fin toma el lock y escribe contenido NUEVO.
            await RawListingSink().handle(sa_, scope, (_lst_titulo(ext, "SRE 100k"),))
            await sa_.commit()

    asyncio.run(go())


def test_g5_la_revision_de_una_tx_lenta_no_se_inserta_con_marca_anterior(db):
    """Regresión G5-P2-4: el `GREATEST(fetched_at, now())` daba monotonía POR
    FILA pero NO cubría el INSERT de una revisión NUEVA, que tomaba el
    server_default de la columna (`now()` = transaction_timestamp()). El sink
    sella el reloj de su tx ANTES del preprocesado del lote y ANTES del
    advisory lock por fuente, así que una tx que arranca primero y escribe
    después insertaba su revisión con un `fetched_at` MENOR que el de la
    revisión ya superada: bajo `ORDER BY fetched_at DESC` los tres call-sites
    del raw vigente devolvían la SUPERADA mientras `_canonicalize` (último
    escritor) dejaba la canónica con el contenido NUEVO. La clase G3-A-P3-3
    seguía abierta pese al commit que decía cerrarla."""
    factory, created = db
    scope = _seed_scope(factory, created)
    ext = f"g5mono-{uuid.uuid4().hex[:8]}"
    _sink_batch(factory, scope, [_lst_titulo(ext, "SRE 80k")])
    _dos_tx_entrelazadas(factory, scope, ext)

    def _vigente():
        async def go():
            async with factory() as s:
                return (await s.execute(sa.text(_VIGENTE_SQL), {"e": ext})).one()

        return asyncio.run(go())

    # El raw vigente y la canónica describen el MISMO contenido: el último
    # escrito. Antes del fix: 'SRE 90k' (la superada) vs 'SRE 100k'.
    assert _vigente().raw["title"] == "SRE 100k"
    assert _canonica(factory, ext) == "SRE 100k"


def test_g5_la_reparacion_no_repunta_la_canonica_al_contenido_superado(db):
    """El daño extremo a extremo de la inversión (G5-P2-4): con el orden
    invertido, `_rebuild_canonical_after_repair` reconstruye la canónica desde
    el raw «vigente» equivocado y RETIRA el contenido que /v1 estaba
    sirviendo — exactamente el síntoma que G3-A-P3-3 documenta."""
    factory, created = db
    scope = _seed_scope(factory, created)
    ext = f"g5rep-{uuid.uuid4().hex[:8]}"
    _sink_batch(factory, scope, [_lst_titulo(ext, "SRE 80k")])
    _dos_tx_entrelazadas(factory, scope, ext)
    antes = _canonica(factory, ext)
    assert antes == "SRE 100k"

    async def reparar():
        async with factory() as s:
            tgt = (
                await s.execute(
                    sa.text(
                        "SELECT v.id AS vid, i.id AS iid FROM vacancies v "
                        "JOIN source_listing_incarnations i ON i.vacancy_id = v.id "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.external_id = :e"
                    ),
                    {"e": ext},
                )
            ).one()
            await RawListingSink()._rebuild_canonical_after_repair(
                s, [(tgt.vid, tgt.iid)]
            )
            await s.commit()

    asyncio.run(reparar())
    assert _canonica(factory, ext) == antes  # antes del fix: 'SRE 90k'


def test_e2e_run_scope_with_real_sink(db):
    """E2E A-03+A-04: runner + sink real — grafo y estado commiteados juntos."""
    factory, created = db
    scope = _seed_scope(factory, created)

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ) as http:
            return await run_scope(scope, FakeProvider(), RawListingSink(), http, session_factory=factory)

    r = asyncio.run(go())
    assert r.status == "ok" and r.listings == 2
    c = _counts(factory, created["source"])
    assert (c.slots, c.incs, c.revs) == (2, 2, 2)

    async def state():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text("SELECT last_complete_at FROM source_scope_state WHERE scope_id=:i"),
                    {"i": scope},
                )
            ).scalar_one()

    assert asyncio.run(state()) is not None
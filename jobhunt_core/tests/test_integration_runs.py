"""Runs de cosecha idempotentes (A-11) contra Postgres real.

DoD: el reintento NO duplica — mismo run_key ⇒ mismo harvest_run; los scopes
terminados con éxito se saltan; errores y colgados se re-ejecutan (el sink es
idempotente). Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import runs
from jobhunt_core.config import settings
from jobhunt_core.harvest.types import ScopeRunResult
from jobhunt_core.tests import dbcleanup

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"sources": [], "scopes": [], "runs": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            await dbcleanup.purge_runs(s, created["runs"])
            await dbcleanup.purge_source_graph(s, created["sources"], created["scopes"])
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _seed_scopes(factory, created, n=2):
    async def go():
        async with factory() as s:
            source_id = uuid.uuid4()
            created["sources"].append(source_id)
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, 'arbeitnow', 0)"),
                {"id": source_id},
            )
            out = []
            for _ in range(n):
                scope_id = uuid.uuid4()
                created["scopes"].append(scope_id)
                await s.execute(
                    sa.text(
                        "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                        "VALUES (:id, :src, '{}'::jsonb, 0)"
                    ),
                    {"id": scope_id, "src": source_id},
                )
                out.append(scope_id)
            await s.commit()
            return out

    return asyncio.run(go())


def _run_all(run_key, created):
    from jobhunt_core.tasks.harvest import run_all_task

    r = run_all_task.apply(args=[run_key])
    assert r.successful()
    rid = uuid.UUID(r.result["run_id"])
    if rid not in created["runs"]:
        created["runs"].append(rid)
    return r.result


def _rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def test_run_id_deterministic():
    assert runs.run_id_for("2026-07-24") == runs.run_id_for("2026-07-24")
    assert runs.run_id_for("2026-07-24") != runs.run_id_for("2026-07-25")


def test_start_run_idempotent(db):
    factory, created = db

    async def twice():
        async with factory() as s:
            r1 = await runs.start_run(s, "ventana-test-idem")
            r2 = await runs.start_run(s, "ventana-test-idem")
            await s.commit()
            return r1, r2

    r1, r2 = asyncio.run(twice())
    created["runs"].append(r1)
    assert r1 == r2
    n = _rows(factory, "SELECT count(*) AS n FROM harvest_runs WHERE id = :r", r=r1)
    assert n[0].n == 1  # UNA fila pese a dos start


def test_retry_skips_done_and_reruns_errors(db, monkeypatch):
    """DoD (repro): run con scope A ok y scope B en error → el REINTENTO del
    mismo run_key salta A (no duplica) y re-ejecuta SOLO B; sin filas nuevas."""
    import jobhunt_core.tasks.harvest as harvest_task

    factory, created = db
    scope_a, scope_b = _seed_scopes(factory, created, n=2)
    calls: list[str] = []

    async def fake_impl(scope_id, claim_token=None):
        calls.append(scope_id)
        if scope_id == str(scope_b) and len([c for c in calls if c == scope_id]) == 1:
            raise RuntimeError("fuente rota (simulada)")
        return ScopeRunResult(scope_id=scope_id, status="ok", listings=1, pages=1)

    monkeypatch.setattr(harvest_task, "_run_scope_impl", fake_impl)

    r1 = _run_all("ventana-retry", created)
    assert r1["status"] == "error"  # B falló
    assert r1["executed"] == 2 and r1["skipped"] == 0
    assert r1["scopes"][str(scope_a)] == "ok"
    assert r1["scopes"][str(scope_b)] == "error"

    r2 = _run_all("ventana-retry", created)  # REINTENTO del mismo run lógico
    assert r2["run_id"] == r1["run_id"]  # mismo run (id determinista)
    assert r2["executed"] == 1 and r2["skipped"] == 1  # solo B re-ejecutado
    assert r2["scopes"][str(scope_a)] == "skipped"
    assert r2["scopes"][str(scope_b)] == "ok"
    assert r2["status"] == "ok"

    assert calls.count(str(scope_a)) == 1  # A JAMÁS se re-ejecutó
    assert calls.count(str(scope_b)) == 2

    rows = _rows(
        factory,
        "SELECT count(*) AS n FROM source_harvest_runs WHERE run_id = :r",
        r=uuid.UUID(r1["run_id"]),
    )
    assert rows[0].n == 2  # sin duplicados
    run = _rows(
        factory,
        "SELECT status, finished_at FROM harvest_runs WHERE id = :r",
        r=uuid.UUID(r1["run_id"]),
    )[0]
    assert run.status == "ok" and run.finished_at is not None


def test_hung_scope_is_rerun_on_retry(db, monkeypatch):
    """Un scope COLGADO (claim sin finish: el worker murió) se re-ejecuta en
    el reintento — el sink idempotente hace seguro repetirlo."""
    import jobhunt_core.tasks.harvest as harvest_task

    factory, created = db
    (scope_a,) = _seed_scopes(factory, created, n=1)

    async def hang_setup():
        async with factory() as s:
            run_id = await runs.start_run(s, "ventana-colgada")
            created["runs"].append(run_id)
            assert await runs.claim_scope_run(s, run_id, scope_a)
            await s.commit()  # claim persistido, finish JAMÁS llega (crash)
            return run_id

    run_id = asyncio.run(hang_setup())

    calls: list[str] = []

    async def fake_impl(scope_id, claim_token=None):
        calls.append(scope_id)
        return ScopeRunResult(scope_id=scope_id, status="ok")

    monkeypatch.setattr(harvest_task, "_run_scope_impl", fake_impl)
    monkeypatch.setattr(runs, "SCOPE_LEASE_S", 0)  # lease vencido: colgado real
    r = _run_all("ventana-colgada", created)
    assert r["run_id"] == str(run_id)
    assert r["executed"] == 1 and calls == [str(scope_a)]  # re-ejecutado
    assert r["status"] == "ok"


def test_concurrent_claim_only_one_winner(db):
    """Rev. 1ª A-11 (repro): dos run_all solapados del MISMO run_key sobre un
    scope fresco — el claim atómico da el scope a UN solo worker (la fila
    'running' con lease vigente NO se re-arma)."""
    factory, created = db
    (scope_a,) = _seed_scopes(factory, created, n=1)

    async def two_claims():
        async with factory() as s1, factory() as s2:
            run_id = await runs.start_run(s1, "ventana-solapada")
            created["runs"].append(run_id)
            first = await runs.claim_scope_run(s1, run_id, scope_a)
            await s1.commit()
            # Segundo worker: mismo run_id determinista, fila running FRESCA.
            await runs.start_run(s2, "ventana-solapada")
            second = await runs.claim_scope_run(s2, run_id, scope_a)
            await s2.commit()
            return first, second

    first, second = asyncio.run(two_claims())
    assert first is not None and second is None  # UN solo ganador (token vs None)


def test_disabled_scope_between_attempts_does_not_poison_run(db, monkeypatch):
    """Rev. 1ª A-11 (repro): el scope B falla y luego se DESHABILITA — el
    reintento cierra su fila huérfana como 'skipped' y el run agrega SOLO
    sobre habilitados: termina 'ok', no 'error' para siempre."""
    import jobhunt_core.tasks.harvest as harvest_task

    factory, created = db
    scope_a, scope_b = _seed_scopes(factory, created, n=2)

    async def fake_fail_b(scope_id, claim_token=None):
        if scope_id == str(scope_b):
            raise RuntimeError("rota")
        return ScopeRunResult(scope_id=scope_id, status="ok")

    monkeypatch.setattr(harvest_task, "_run_scope_impl", fake_fail_b)
    r1 = _run_all("ventana-disable", created)
    assert r1["status"] == "error"

    async def disable_b():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE harvest_scopes SET enabled = false WHERE id = :i"),
                {"i": scope_b},
            )
            await s.commit()

    asyncio.run(disable_b())
    r2 = _run_all("ventana-disable", created)
    assert r2["status"] == "ok"  # B deshabilitado NO envenena el run
    row = _rows(
        factory,
        "SELECT status, finished_at FROM source_harvest_runs "
        "WHERE run_id = :r AND scope_id = :s",
        r=uuid.UUID(r2["run_id"]), s=scope_b,
    )[0]
    assert row.status in ("skipped", "error")  # sin fila 'running' huérfana
    assert row.status != "running"


def test_second_retry_all_skipped_recomputes_ok(db, monkeypatch):
    """Rev. 1ª A-11: run totalmente 'ok' → un reintento posterior lo re-abre,
    salta TODO y recalcula 'ok' (sin re-ejecutar nada)."""
    import jobhunt_core.tasks.harvest as harvest_task

    factory, created = db
    _seed_scopes(factory, created, n=2)
    calls: list[str] = []

    async def fake_ok(scope_id, claim_token=None):
        calls.append(scope_id)
        return ScopeRunResult(scope_id=scope_id, status="ok")

    monkeypatch.setattr(harvest_task, "_run_scope_impl", fake_ok)
    r1 = _run_all("ventana-doble-ok", created)
    assert (r1["status"], r1["executed"]) == ("ok", 2)

    r2 = _run_all("ventana-doble-ok", created)
    assert (r2["status"], r2["executed"], r2["skipped"]) == ("ok", 0, 2)
    assert len(calls) == 2  # NADA re-ejecutado
    run = _rows(
        factory,
        "SELECT status, finished_at FROM harvest_runs WHERE id = :r",
        r=uuid.UUID(r2["run_id"]),
    )[0]
    assert run.status == "ok" and run.finished_at is not None  # re-cerrado


def test_finish_run_leaves_run_open_while_other_worker_in_flight(db):
    """Rev. 2ª A-11 (repro determinista): un scope 'running' con lease
    VIGENTE (otro worker del mismo run_key) NO cierra el run como 'error' —
    finish_run devuelve 'running' sin tocar harvest_runs; al terminar el otro
    worker, converge a 'ok' sin ventana de mentira."""
    factory, created = db
    scope_x, scope_y = _seed_scopes(factory, created, n=2)

    async def flow():
        async with factory() as s:
            run_id = await runs.start_run(s, "ventana-en-vuelo")
            created["runs"].append(run_id)
            tok_x = await runs.claim_scope_run(s, run_id, scope_x)
            assert tok_x is not None
            assert await runs.finish_scope_run(s, run_id, scope_x, "ok", tok_x)
            # 'Otro worker' reclama Y y sigue trabajando (lease vigente):
            tok_y = await runs.claim_scope_run(s, run_id, scope_y)
            assert tok_y is not None
            overall_1 = await runs.finish_run(s, run_id)
            await s.commit()
            run_row_1 = (
                await s.execute(
                    sa.text("SELECT status, finished_at FROM harvest_runs WHERE id = :r"),
                    {"r": run_id},
                )
            ).one()
            # El otro worker termina; el ÚLTIMO cierra el run:
            assert await runs.finish_scope_run(s, run_id, scope_y, "ok", tok_y)
            overall_2 = await runs.finish_run(s, run_id)
            await s.commit()
            run_row_2 = (
                await s.execute(
                    sa.text("SELECT status, finished_at FROM harvest_runs WHERE id = :r"),
                    {"r": run_id},
                )
            ).one()
            return overall_1, run_row_1, overall_2, run_row_2

    overall_1, row_1, overall_2, row_2 = asyncio.run(flow())
    assert overall_1 == "running"  # NO 'error' con trabajo en vuelo
    assert row_1.status == "running" and row_1.finished_at is None  # sin mentir
    assert overall_2 == "ok" and row_2.finished_at is not None  # convergencia


def test_finish_scope_run_fenced_by_claim_token(db):
    """REGRESIÓN P1 rev. externa integral: tras un REARM por lease vencido, el worker VIEJO no debe
    poder cerrar el scope del NUEVO. finish_scope_run exige el claim_token VIGENTE: el token viejo
    devuelve False (no sobrescribe) y el estado del worker nuevo permanece; el nuevo sí cierra."""
    factory, created = db
    (scope_a,) = _seed_scopes(factory, created, n=1)

    async def flow():
        async with factory() as s:
            run_id = await runs.start_run(s, "ventana-fencing")
            created["runs"].append(run_id)
            tok_old = await runs.claim_scope_run(s, run_id, scope_a)  # worker A
            assert tok_old is not None
            # Backdate started_at: el lease (900s) vence sin tocar SCOPE_LEASE_S.
            await s.execute(
                sa.text(
                    "UPDATE source_harvest_runs SET started_at = clock_timestamp() "
                    "- make_interval(secs => 3600) WHERE run_id = :r AND scope_id = :s"
                ),
                {"r": run_id, "s": scope_a},
            )
            tok_new = await runs.claim_scope_run(s, run_id, scope_a)  # worker B re-arma
            assert tok_new is not None and tok_new != tok_old
            # Worker A (desahuciado) intenta cerrar con su token VIEJO → False, no sobrescribe.
            assert await runs.finish_scope_run(s, run_id, scope_a, "error", tok_old) is False
            row = (
                await s.execute(
                    sa.text(
                        "SELECT status, claim_token::text ct, finished_at "
                        "FROM source_harvest_runs WHERE run_id = :r AND scope_id = :s"
                    ),
                    {"r": run_id, "s": scope_a},
                )
            ).one()
            assert row.status == "running" and row.finished_at is None  # estado de B intacto
            assert row.ct == str(tok_new)
            # Worker B sí cierra con su token vigente.
            assert await runs.finish_scope_run(s, run_id, scope_a, "ok", tok_new) is True
            await s.commit()

    asyncio.run(flow())


def test_record_failure_fenced_by_claim_token(db):
    """REGRESIÓN P1 rev. externa integral ronda 2: el token protegía SOLO el cierre; un worker
    DESAHUCIADO (lease vencido, scope re-armado por otro) seguía mutando source_scope_state — su
    _record_failure_safe incrementaba consecutive_failures pisando al vigente (que cosechó con 0).
    Ahora toda mutación se condiciona al token vigente."""
    from jobhunt_core.harvest.runner import _record_failure_safe, _still_claim_owner

    factory, created = db
    (scope_a,) = _seed_scopes(factory, created, n=1)

    async def flow():
        async with factory() as s:
            run_id = await runs.start_run(s, "ventana-failfence")
            created["runs"].append(run_id)
            tok_old = await runs.claim_scope_run(s, run_id, scope_a)  # worker A
            # Backdate → lease vencido → worker B re-arma con token NUEVO.
            await s.execute(
                sa.text(
                    "UPDATE source_harvest_runs SET started_at = clock_timestamp() "
                    "- make_interval(secs => 3600) WHERE run_id = :r AND scope_id = :s"
                ),
                {"r": run_id, "s": scope_a},
            )
            tok_new = await runs.claim_scope_run(s, run_id, scope_a)
            assert tok_new is not None and tok_new != tok_old
            # Estado del VIGENTE (B): cosecha OK, 0 fallos.
            await s.execute(
                sa.text(
                    "INSERT INTO source_scope_state (scope_id, consecutive_failures) "
                    "VALUES (:s, 0) ON CONFLICT (scope_id) DO UPDATE SET consecutive_failures = 0"
                ),
                {"s": scope_a},
            )
            await s.commit()
        # Worker A (desahuciado) intenta contabilizar un fallo → NO debe incrementar.
        async with factory() as s:
            assert await _still_claim_owner(s, str(scope_a), tok_old) is False
            await s.rollback()
        async with factory() as s:
            await _record_failure_safe(s, str(scope_a), tok_old)
        async with factory() as s:
            cf = (
                await s.execute(
                    sa.text(
                        "SELECT consecutive_failures FROM source_scope_state WHERE scope_id = :s"
                    ),
                    {"s": scope_a},
                )
            ).scalar_one()
            assert cf == 0  # FENCING: el desahuciado NO incrementó
        # Worker B (vigente) sí contabiliza su propio fallo.
        async with factory() as s:
            await _record_failure_safe(s, str(scope_a), tok_new)
        async with factory() as s:
            cf2 = (
                await s.execute(
                    sa.text(
                        "SELECT consecutive_failures FROM source_scope_state WHERE scope_id = :s"
                    ),
                    {"s": scope_a},
                )
            ).scalar_one()
            assert cf2 == 1

    asyncio.run(flow())


def test_run_scope_success_fenced_by_claim_token(db):
    """REGRESIÓN P1 rev. externa integral ronda 2 (camino de ÉXITO): un worker DESAHUCIADO cuyo
    fetch tiene ÉXITO NO debe persistir cursor/listings — el guard _still_claim_owner ANTES de
    sink.handle lo detecta (stale) y no pisa el estado del vigente; el vigente sí persiste."""
    import httpx

    from jobhunt_core.harvest.runner import run_scope
    from jobhunt_core.tests.test_integration_harvest import (
        CollectSink,
        FakeProvider,
        _provider_cursor_of,
        _state,
    )

    factory, created = db
    (scope_a,) = _seed_scopes(factory, created, n=1)

    async def claim_and_rearm():
        async with factory() as s:
            run_id = await runs.start_run(s, "ventana-succ-fence")
            created["runs"].append(run_id)
            tok_old = await runs.claim_scope_run(s, run_id, scope_a)
            await s.execute(
                sa.text(
                    "UPDATE source_harvest_runs SET started_at = clock_timestamp() "
                    "- make_interval(secs => 3600) WHERE run_id = :r AND scope_id = :s"
                ),
                {"r": run_id, "s": scope_a},
            )
            tok_new = await runs.claim_scope_run(s, run_id, scope_a)
            await s.commit()
            return tok_old, tok_new

    tok_old, tok_new = asyncio.run(claim_and_rearm())

    def _run_tok(sink, token):
        async def go():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(500))
            ) as http:
                return await run_scope(
                    scope_a, FakeProvider(), sink, http,
                    session_factory=factory, claim_token=token,
                )

        return asyncio.run(go())

    # Worker DESAHUCIADO (tok_old): fetch OK, pero el guard aborta ANTES de sink.handle.
    sink = CollectSink()
    result = _run_tok(sink, tok_old)
    assert result.status == "stale"  # fenceado antes de persistir
    assert sink.batches == []  # sink.handle NO se llamó
    assert _state(factory, scope_a) is None  # cursor NO escrito

    # Worker VIGENTE (tok_new): persiste normalmente.
    sink2 = CollectSink()
    result2 = _run_tok(sink2, tok_new)
    assert result2.status == "ok"
    assert sink2.batches and _provider_cursor_of(_state(factory, scope_a)) is not None


def test_record_failure_tokenless_fenced_by_state(db):
    """REGRESIÓN P2 rev. externa integral ronda 3: run_scope_task individual corre SIN token. Si
    OTRO run (run_all) COSECHÓ mientras corría, su _record_failure_safe NO debe incrementar
    consecutive_failures pisando el estado del vigente (que cosechó con 0). La autoritatividad se
    condiciona al ESTADO (cursor, last_complete_at) inalterado — CLAVE: last_complete_at es el epoch
    monotónico; el VALOR del cursor NO basta porque un feed ESTACIONARIO re-escribe el mismo valor
    (la refutación adversarial de la 1ª versión del fix)."""
    from jobhunt_core.harvest.runner import _record_failure_safe

    factory, created = db
    (scope_a,) = _seed_scopes(factory, created, n=1)
    sid = str(scope_a)

    async def _cf():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT consecutive_failures FROM source_scope_state WHERE scope_id = :s"
                    ),
                    {"s": sid},
                )
            ).scalar_one()

    async def _snap():
        async with factory() as s:
            r = (
                await s.execute(
                    sa.text(
                        "SELECT cursor, last_complete_at FROM source_scope_state "
                        "WHERE scope_id = :s"
                    ),
                    {"s": sid},
                )
            ).one()
            return (r.cursor, r.last_complete_at)

    async def flow():
        # Estado inicial: cursor C, 5 fallos, SIN cosecha completa aún.
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO source_scope_state (scope_id, cursor, consecutive_failures) "
                    "VALUES (:s, CAST(:c AS jsonb), 5)"
                ),
                {"s": sid, "c": '{"last_top_seen": 300}'},
            )
            await s.commit()
        stale_snap = await _snap()  # snapshot pre-fetch del run OBSOLETO (cursor C, last_complete NULL)

        # El VIGENTE (run_all) cosecha COMPLETO — feed ESTACIONARIO: MISMO valor de cursor, pero
        # avanza last_complete_at y resetea a 0.
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE source_scope_state SET last_complete_at = clock_timestamp(), "
                    "consecutive_failures = 0 WHERE scope_id = :s"  # cursor NO cambia (mismo valor)
                ),
                {"s": sid},
            )
            await s.commit()

        # Run OBSOLETO sin token: aunque el cursor VALE lo mismo, last_complete_at cambió → NO cuenta.
        async with factory() as s:
            await _record_failure_safe(s, sid, token=None, state_snapshot=stale_snap)
        assert await _cf() == 0  # obsoleto: NO pisó el 0 del vigente

        # Run legítimo sin token cuyo snapshot COINCIDE con el estado actual → sí cuenta.
        async with factory() as s:
            await _record_failure_safe(s, sid, token=None, state_snapshot=await _snap())
        assert await _cf() == 1

    asyncio.run(flow())


def test_record_failure_tokenless_first_run_no_state_row(db):
    """REGRESIÓN P2 rev. externa integral ronda 4: en el PRIMER run source_scope_state AÚN NO existe;
    FOR UPDATE no bloquea el hueco de una fila inexistente. Con el lock de harvest_scopes primero, un
    run sin token (snapshot (None,None)) que llega DESPUÉS de que el vigente (B) insertara su cosecha
    (failures=0) observa el estado nuevo y DESCARTA su fallo — no lo pisa a 1."""
    from jobhunt_core.harvest.runner import _record_failure_safe

    factory, created = db
    (scope_a,) = _seed_scopes(factory, created, n=1)
    sid = str(scope_a)

    async def flow():
        # NO existe fila (primer run). El VIGENTE (B) cosecha COMPLETO e inserta failures=0.
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO source_scope_state (scope_id, cursor, consecutive_failures, "
                    "last_complete_at) VALUES (:s, CAST(:c AS jsonb), 0, clock_timestamp())"
                ),
                {"s": sid, "c": '{"last_top_seen": 300}'},
            )
            await s.commit()
        # A tenía snapshot (None, None) (sin fila al empezar su fetch) → OBSOLETO → NO cuenta.
        async with factory() as s:
            await _record_failure_safe(s, sid, token=None, state_snapshot=(None, None))
        async with factory() as s:
            cf = (
                await s.execute(
                    sa.text(
                        "SELECT consecutive_failures FROM source_scope_state WHERE scope_id = :s"
                    ),
                    {"s": sid},
                )
            ).scalar_one()
            assert cf == 0  # observó el estado del vigente y descartó su fallo

    asyncio.run(flow())


def test_still_authoritative_serializes_on_harvest_scope_lock(db):
    """REGRESIÓN P2 rev. externa integral ronda 4: _still_authoritative (sin token) bloquea
    harvest_scopes ANTES de leer source_scope_state, para que el camino de éxito (FOR UPDATE OF hs)
    no intercale un INSERT del estado inexistente. Se comprueba que, mientras un run sostiene ese
    lock, un FOR UPDATE OF hs competidor BLOQUEA (lock_timeout)."""
    from jobhunt_core.harvest.runner import _still_authoritative

    factory, created = db
    (scope_a,) = _seed_scopes(factory, created, n=1)
    sid = str(scope_a)

    async def flow():
        async with factory() as a, factory() as b:
            # A bloquea harvest_scopes (la fila de sss NO existe → (None,None)); NO commitea.
            assert await _still_authoritative(a, sid, None, (None, None)) is True
            # B (camino de éxito) intenta el mismo lock con timeout corto → BLOQUEA.
            await b.execute(sa.text("SET LOCAL lock_timeout = '400ms'"))
            with pytest.raises(Exception) as exc:
                await b.execute(
                    sa.text("SELECT 1 FROM harvest_scopes WHERE id = :s FOR UPDATE"),
                    {"s": sid},
                )
            assert "lock" in str(exc.value).lower() or "timeout" in str(exc.value).lower()
            await a.rollback()
            await b.rollback()

    asyncio.run(flow())


def test_run_all_task_registered():
    from jobhunt_core.celery_app import celery_app

    assert "jobhunt.harvest.run_all" in celery_app.tasks

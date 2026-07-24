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
            if created["runs"]:
                await s.execute(
                    sa.text("DELETE FROM source_harvest_runs WHERE run_id = ANY(:r)"),
                    {"r": created["runs"]},
                )
                await s.execute(
                    sa.text("DELETE FROM harvest_runs WHERE id = ANY(:r)"),
                    {"r": created["runs"]},
                )
            for sid in created["scopes"]:
                await s.execute(
                    sa.text("DELETE FROM source_scope_state WHERE scope_id=:i"), {"i": sid}
                )
                await s.execute(sa.text("DELETE FROM harvest_scopes WHERE id=:i"), {"i": sid})
            await s.execute(
                sa.text("DELETE FROM sources WHERE id = ANY(:s)"),
                {"s": created["sources"]},
            )
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

    async def fake_impl(scope_id):
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

    async def fake_impl(scope_id):
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
    assert (first, second) == (True, False)  # UN solo ganador


def test_disabled_scope_between_attempts_does_not_poison_run(db, monkeypatch):
    """Rev. 1ª A-11 (repro): el scope B falla y luego se DESHABILITA — el
    reintento cierra su fila huérfana como 'skipped' y el run agrega SOLO
    sobre habilitados: termina 'ok', no 'error' para siempre."""
    import jobhunt_core.tasks.harvest as harvest_task

    factory, created = db
    scope_a, scope_b = _seed_scopes(factory, created, n=2)

    async def fake_fail_b(scope_id):
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

    async def fake_ok(scope_id):
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
            assert await runs.claim_scope_run(s, run_id, scope_x)
            await runs.finish_scope_run(s, run_id, scope_x, "ok")
            # 'Otro worker' reclama Y y sigue trabajando (lease vigente):
            assert await runs.claim_scope_run(s, run_id, scope_y)
            overall_1 = await runs.finish_run(s, run_id)
            await s.commit()
            run_row_1 = (
                await s.execute(
                    sa.text("SELECT status, finished_at FROM harvest_runs WHERE id = :r"),
                    {"r": run_id},
                )
            ).one()
            # El otro worker termina; el ÚLTIMO cierra el run:
            await runs.finish_scope_run(s, run_id, scope_y, "ok")
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


def test_run_all_task_registered():
    from jobhunt_core.celery_app import celery_app

    assert "jobhunt.harvest.run_all" in celery_app.tasks

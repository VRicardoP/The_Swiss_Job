"""Entrega `match.evaluated` outbox→inbox por consumidor (A-10) contra
Postgres real.

DoD: at-least-once, idempotente por (consumer_id, event_id); event_id
determinista; dead-letter + alerta. El inbox vive en la BD del CONSUMIDOR
(aquí un inbox simulado en memoria con dedup por (consumer, event_id)).
Ejecutar vía core-migrate.
"""

import asyncio
import logging
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import delivery, matching
from jobhunt_core.config import settings
from jobhunt_core.tests import test_integration_matching as tim

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"sources": [], "scopes": [], "models": [], "consumers": [], "policies": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            cons = created["consumers"]
            if cons:
                # Outbox por sujeto (deliveries caen por ON DELETE CASCADE).
                await s.execute(
                    sa.text(
                        "DELETE FROM integration_outbox WHERE subject_profile_id IN "
                        "(SELECT id FROM profiles WHERE consumer_id = ANY(:c))"
                    ),
                    {"c": cons},
                )
                await s.execute(
                    sa.text(
                        "DELETE FROM profile_vacancy_state WHERE profile_id IN "
                        "(SELECT id FROM profiles WHERE consumer_id = ANY(:c))"
                    ),
                    {"c": cons},
                )
                await s.execute(
                    sa.text(
                        "DELETE FROM match_evaluations WHERE profile_id IN "
                        "(SELECT id FROM profiles WHERE consumer_id = ANY(:c))"
                    ),
                    {"c": cons},
                )
                for tbl in (
                    "profile_embeddings", "profile_revision_activations",
                    "profile_revisions",
                ):
                    await s.execute(
                        sa.text(
                            f"DELETE FROM {tbl} WHERE profile_id IN "
                            "(SELECT id FROM profiles WHERE consumer_id = ANY(:c))"
                        ),
                        {"c": cons},
                    )
                await s.execute(
                    sa.text("DELETE FROM profiles WHERE consumer_id = ANY(:c)"),
                    {"c": cons},
                )
                await s.execute(
                    sa.text("DELETE FROM consumers WHERE id = ANY(:c)"), {"c": cons}
                )
            srcs = created["sources"]
            if srcs:
                vac_ids = (
                    await s.execute(
                        sa.text(
                            "SELECT DISTINCT i.vacancy_id FROM source_listing_incarnations i "
                            "JOIN source_listings l ON l.id = i.source_listing_id "
                            "WHERE l.source_id = ANY(:srcs)"
                        ),
                        {"srcs": srcs},
                    )
                ).scalars().all()
                if vac_ids:
                    await s.execute(
                        sa.text(
                            "DELETE FROM dedup_candidates "
                            "WHERE vacancy_a = ANY(:v) OR vacancy_b = ANY(:v)"
                        ),
                        {"v": vac_ids},
                    )
                    await s.execute(
                        sa.text(
                            "UPDATE vacancies SET current_offer_revision_id = NULL "
                            "WHERE id = ANY(:v)"
                        ),
                        {"v": vac_ids},
                    )
                    await s.execute(
                        sa.text("DELETE FROM offer_revision_sources WHERE vacancy_id = ANY(:v)"),
                        {"v": vac_ids},
                    )
                    await s.execute(
                        sa.text("DELETE FROM offer_revisions WHERE vacancy_id = ANY(:v)"),
                        {"v": vac_ids},
                    )
                await s.execute(
                    sa.text(
                        "DELETE FROM link_evidence WHERE source_listing_id IN "
                        "(SELECT id FROM source_listings WHERE source_id = ANY(:srcs))"
                    ),
                    {"srcs": srcs},
                )
                await s.execute(
                    sa.text(
                        "DELETE FROM source_listing_revisions WHERE incarnation_id IN ("
                        "SELECT i.id FROM source_listing_incarnations i "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.source_id = ANY(:srcs))"
                    ),
                    {"srcs": srcs},
                )
                await s.execute(
                    sa.text(
                        "DELETE FROM source_listing_incarnations WHERE source_listing_id IN "
                        "(SELECT id FROM source_listings WHERE source_id = ANY(:srcs))"
                    ),
                    {"srcs": srcs},
                )
                if vac_ids:
                    await s.execute(
                        sa.text("DELETE FROM vacancies WHERE id = ANY(:v)"), {"v": vac_ids}
                    )
                await s.execute(
                    sa.text("DELETE FROM source_listings WHERE source_id = ANY(:srcs)"),
                    {"srcs": srcs},
                )
                for sid in created["scopes"]:
                    await s.execute(
                        sa.text("DELETE FROM source_scope_state WHERE scope_id=:i"), {"i": sid}
                    )
                    await s.execute(
                        sa.text("DELETE FROM harvest_scopes WHERE id=:i"), {"i": sid}
                    )
                await s.execute(
                    sa.text("DELETE FROM sources WHERE id = ANY(:srcs)"), {"srcs": srcs}
                )
            if created["policies"]:
                await s.execute(
                    sa.text("DELETE FROM scoring_policies WHERE id = ANY(:p)"),
                    {"p": created["policies"]},
                )
            for mid in created["models"]:
                await s.execute(
                    sa.text("DELETE FROM offer_embeddings WHERE model_id = :m"), {"m": mid}
                )
                await s.execute(
                    sa.text(
                        f"DROP TABLE IF EXISTS {settings.CORE_DB_SCHEMA}."
                        f"offer_embeddings_{mid.hex[:16]}"
                    )
                )
                await s.execute(
                    sa.text("DELETE FROM embedding_models WHERE id = :m"), {"m": mid}
                )
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


class FakeInbox:
    """Inbox del CONSUMIDOR (vive en su BD, ADR-06): dedup por
    (consumer_id, event_id) — el consumo idempotente del contrato."""

    def __init__(self, fail_times: int = 0):
        self.rows: dict[tuple[str, str], dict] = {}
        self.calls = 0
        self._fail_times = fail_times

    def transport(self, destination: str, event: dict) -> None:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("BFF caído (simulado)")
        self.rows.setdefault((destination, event["event_id"]), event)


def _setup_evaluated(factory, created, titles=("backend python", "data eng")):
    pid, mid, polid, vacs = tim._setup(factory, created, list(titles))
    tim._evaluate(factory, pid, mid, polid)
    return pid, vacs


def _rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def _dispatch(limit=100):
    from jobhunt_core.tasks.delivery import dispatch_outbox_task

    r = dispatch_outbox_task.apply(kwargs={"limit": limit})
    assert r.successful()
    return r.result


def test_emission_same_tx_deterministic_and_no_reemission(db):
    """ADR-05: outbox en la MISMA tx que la evaluación; event_id determinista
    (uuid5 de type:eval_key); re-evaluar NO re-emite."""
    factory, created = db
    pid, vacs = _setup_evaluated(factory, created)

    events = _rows(
        factory,
        "SELECT o.event_id, o.type, o.aggregate_id, o.subject_profile_id, "
        "o.version, o.payload FROM integration_outbox o "
        "WHERE o.subject_profile_id = :p", p=pid,
    )
    assert len(events) == 2
    for e in events:
        assert e.type == "match.evaluated" and e.version == 1
        assert e.event_id == matching.event_id_for("match.evaluated", e.aggregate_id)
        assert set(e.payload) == {"eval_key", "profile_id", "vacancy_id"}  # solo IDs
    deliveries = _rows(
        factory,
        "SELECT d.destination, d.state FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )
    assert len(deliveries) == 2
    assert all(d.destination == "tenant-match" and d.state == "pending" for d in deliveries)

    # Reintento de evaluación: mismos eval_key ⇒ mismos event_id ⇒ nada nuevo.
    mid, polid = created["models"][0], created["policies"][0]
    tim._evaluate(factory, pid, mid, polid)
    n = _rows(
        factory,
        "SELECT count(*) AS n FROM integration_outbox WHERE subject_profile_id = :p",
        p=pid,
    )
    assert n[0].n == 2


def test_dispatch_delivers_and_consumer_inbox_dedups(db):
    """E2E: pending → delivered con ack; re-entrega forzada → el transporte
    corre OTRA vez (at-least-once) y el inbox del consumidor desduplica por
    (consumer_id, event_id)."""
    factory, created = db
    pid, vacs = _setup_evaluated(factory, created)
    inbox = FakeInbox()
    delivery.set_transport(inbox.transport)
    try:
        r = _dispatch()
        assert (r["claimed"], r["delivered"], r["failed"], r["dead"]) == (2, 2, 0, 0)
        assert len(inbox.rows) == 2
        states = _rows(
            factory,
            "SELECT d.state, d.ack_at FROM integration_outbox_deliveries d "
            "JOIN integration_outbox o ON o.event_id = d.event_id "
            "WHERE o.subject_profile_id = :p", p=pid,
        )
        assert all(s.state == "delivered" and s.ack_at is not None for s in states)

        assert _dispatch()["claimed"] == 0  # nada elegible tras entregar

        # RE-ENTREGA (ack perdido simulado): el productor reintenta...
        async def reset():
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "UPDATE integration_outbox_deliveries "
                        "SET state = 'pending', next_attempt_at = clock_timestamp() "
                        "WHERE event_id IN (SELECT event_id FROM integration_outbox "
                        "WHERE subject_profile_id = :p)"
                    ),
                    {"p": pid},
                )
                await s.commit()

        asyncio.run(reset())
        r = _dispatch()
        assert r["delivered"] == 2
        assert inbox.calls == 4  # el transporte corrió otra vez (at-least-once)
        assert len(inbox.rows) == 2  # ...y el inbox DEDUPLICÓ (idempotente)
    finally:
        delivery.set_transport(None)


def test_failure_backoff_then_dead_letter_with_alert(db, monkeypatch, caplog):
    """Fallos → pending con backoff creciente; al agotar MAX_ATTEMPTS →
    DEAD-LETTER con ALERTA (DoD)."""
    factory, created = db
    pid, vacs = _setup_evaluated(factory, created, titles=("backend python",))
    monkeypatch.setattr(delivery, "MAX_ATTEMPTS", 3)
    monkeypatch.setattr(delivery, "BACKOFF_BASE_S", 0)  # reintento inmediato
    inbox = FakeInbox(fail_times=10**6)  # siempre falla
    delivery.set_transport(inbox.transport)
    try:
        for expected_attempts in (1, 2):
            r = _dispatch()
            assert r["failed"] == 1 and r["dead"] == 0
            row = _rows(
                factory,
                "SELECT d.attempts, d.state, d.last_error "
                "FROM integration_outbox_deliveries d "
                "JOIN integration_outbox o ON o.event_id = d.event_id "
                "WHERE o.subject_profile_id = :p", p=pid,
            )[0]
            assert (row.attempts, row.state) == (expected_attempts, "pending")
            assert "BFF caído" in row.last_error

        with caplog.at_level(logging.ERROR, logger="jobhunt_core.delivery"):
            r = _dispatch()
        assert r["dead"] == 1
        row = _rows(
            factory,
            "SELECT d.state FROM integration_outbox_deliveries d "
            "JOIN integration_outbox o ON o.event_id = d.event_id "
            "WHERE o.subject_profile_id = :p", p=pid,
        )[0]
        assert row.state == "dead"
        assert any("DEAD-LETTER" in r.getMessage() for r in caplog.records)  # alerta
        assert _dispatch()["claimed"] == 0  # dead NO se reintenta
    finally:
        delivery.set_transport(None)


def test_expired_lease_is_reclaimed(db, monkeypatch):
    """At-least-once real: un claim cuyo productor murió (inflight con lease
    caducado) se RE-reclama y termina entregándose."""
    factory, created = db
    pid, vacs = _setup_evaluated(factory, created, titles=("backend python",))
    monkeypatch.setattr(delivery, "LEASE_S", 0)  # lease caducado al instante

    async def claim_and_die():
        async with factory() as s:
            claimed, _lease = await delivery.claim_deliveries(s, limit=10)
            await s.commit()  # claim persistido... y el "worker" muere aquí
            return len(claimed)

    assert asyncio.run(claim_and_die()) == 1
    row = _rows(
        factory,
        "SELECT d.state FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )[0]
    assert row.state == "inflight"  # colgado

    inbox = FakeInbox()
    delivery.set_transport(inbox.transport)
    try:
        r = _dispatch()
        assert r["delivered"] == 1  # re-reclamado y entregado
        assert len(inbox.rows) == 1
    finally:
        delivery.set_transport(None)


def test_no_transport_preserves_pending_without_burning_attempts(db):
    factory, created = db
    pid, vacs = _setup_evaluated(factory, created, titles=("backend python",))
    assert delivery.get_transport() is None
    r = _dispatch()
    assert (r["claimed"], r["skipped"], r["delivered"]) == (1, 1, 0)
    row = _rows(
        factory,
        "SELECT d.state, d.attempts FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )[0]
    assert (row.state, row.attempts) == ("pending", 0)  # intento NO consumido


def test_wide_consumer_name_does_not_abort_evaluation(db):
    """Auditoría A-10 #1 (repro): un consumer de 61-100 chars reventaba el
    INSERT de la entrega (VARCHAR(60)) y — misma tx — revertía la evaluación
    COMPLETA. Con core0005 (destination a 100) todo persiste."""
    factory, created = db
    long_name = "swiss-jobhunter-bff-prod-eu-west-frontend-consumer-tenant-0001"
    assert len(long_name) > 60

    from jobhunt_core import embeddings, profiles

    pid, mid, polid, vacs = tim._setup(factory, created, ["backend python"])

    async def rename():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE consumers SET name = :n WHERE id = :c"),
                {"n": long_name, "c": created["consumers"][0]},
            )
            await s.commit()

    asyncio.run(rename())
    r = tim._evaluate(factory, pid, mid, polid)
    assert r["evaluated"] == 1  # la evaluación PERSISTE
    rows = _rows(
        factory,
        "SELECT d.destination FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )
    assert [d.destination for d in rows] == [long_name]


def test_emission_routes_to_each_consumers_bff(db):
    """Auditoría A-10 #3: dos consumidores DISTINTOS → cada entrega va al
    destino de SU consumer, y el despacho entrega a cada BFF exactamente sus
    event_id."""
    from jobhunt_core import embeddings, matching as m, profiles

    factory, created = db
    pid_a, vacs = _setup_evaluated(factory, created, titles=("backend python",))
    mid, polid = created["models"][0], created["policies"][0]

    async def second_tenant_profile():
        async with factory() as s:
            cid_b = await profiles.ensure_consumer(s, "tenant-otro-bff")
            created["consumers"].append(cid_b)
            pid_b = await profiles.upsert_profile(s, cid_b, "user-b")
            await profiles.save_profile_revision(
                s, pid_b, {"title": "data dev", "skills": ["sql"]}
            )
            await s.commit()
            return pid_b

    pid_b = asyncio.run(second_tenant_profile())
    from jobhunt_core.tasks.embedding import run_pending_task

    embeddings.set_backend_factory(lambda name, version: tim.DirectionalBackend())
    try:
        run_pending_task.apply(kwargs={"limit": 100})
    finally:
        embeddings.set_backend_factory(None)
    tim._evaluate(factory, pid_b, mid, polid)

    dests = _rows(
        factory,
        "SELECT o.subject_profile_id, d.destination "
        "FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id IN (:a, :b)", a=pid_a, b=pid_b,
    )
    by_subject = {}
    for r in dests:
        by_subject.setdefault(r.subject_profile_id, set()).add(r.destination)
    assert by_subject[pid_a] == {"tenant-match"}
    assert by_subject[pid_b] == {"tenant-otro-bff"}  # routing por SU consumer

    inbox = FakeInbox()
    delivery.set_transport(inbox.transport)
    try:
        _dispatch()
    finally:
        delivery.set_transport(None)
    per_dest = {}
    for (dest, eid), event in inbox.rows.items():
        per_dest.setdefault(dest, set()).add(event["payload"]["profile_id"])
        # Auditoría (hallazgo sin verificar, comprobado a mano): la FORMA del
        # evento transportado es utilizable — dict con payload dict de IDs.
        assert isinstance(event["payload"], dict)
        assert set(event) >= {"event_id", "type", "aggregate_id", "version", "payload"}
    assert per_dest["tenant-match"] == {str(pid_a)}
    assert per_dest["tenant-otro-bff"] == {str(pid_b)}


def test_late_mark_from_superseded_claim_cannot_resurrect_state(db, monkeypatch):
    """Auditoría A-10 #2 (fencing): un claim SUPERADO (lease caducado y
    re-reclamado) intenta marcar tarde — ni pisa un delivered ni resucita un
    dead: sus marks no tocan NADA."""
    factory, created = db
    pid, vacs = _setup_evaluated(factory, created, titles=("backend python",))
    monkeypatch.setattr(delivery, "LEASE_S", 0)  # el primer claim caduca ya

    async def claim1():
        async with factory() as s:
            rows, lease = await delivery.claim_deliveries(s, limit=10)
            await s.commit()
            return rows, lease

    rows1, lease1 = asyncio.run(claim1())
    assert len(rows1) == 1

    # Un segundo dispatcher re-reclama y ENTREGA.
    monkeypatch.setattr(delivery, "LEASE_S", 120)
    inbox = FakeInbox()
    delivery.set_transport(inbox.transport)
    try:
        assert _dispatch()["delivered"] == 1
    finally:
        delivery.set_transport(None)

    # Mark TARDÍO del claim viejo (fallo) → fencing: no toca el delivered.
    async def late_fail():
        async with factory() as s:
            await delivery.mark_failed(
                s,
                [
                    {
                        "eid": rows1[0].event_id, "dest": rows1[0].destination,
                        "attempts": rows1[0].attempts + 1, "error": "tarde",
                    }
                ],
                lease1,
            )
            await s.commit()

    asyncio.run(late_fail())
    row = _rows(
        factory,
        "SELECT d.state, d.ack_at FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )[0]
    assert row.state == "delivered" and row.ack_at is not None  # INTACTO


def test_delivery_task_registered_and_routed():
    from jobhunt_core.celery_app import celery_app

    assert "jobhunt.delivery.dispatch_outbox" in celery_app.tasks
    assert celery_app.conf.task_routes["jobhunt.delivery.*"] == {"queue": "core.default"}

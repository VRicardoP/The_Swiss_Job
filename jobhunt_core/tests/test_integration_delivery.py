"""Entrega `match.evaluated` outbox→inbox por consumidor (A-10) contra
Postgres real (incluye regresiones de la 2ª revisión Opus post-commit).

DoD: at-least-once, idempotente por (consumer_id, event_id); event_id
determinista; dead-letter + alerta. El inbox vive en la BD del CONSUMIDOR
(aquí un inbox simulado en memoria con dedup por (consumer, event_id)).
Ejecutar vía core-migrate.
"""

import asyncio
import logging
import os
import time
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import delivery, matching
from jobhunt_core.config import settings
from jobhunt_core.tests import dbcleanup
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
            await dbcleanup.purge_consumer_graph(s, created["consumers"])
            await dbcleanup.purge_source_graph(s, created["sources"], created["scopes"])
            await dbcleanup.purge_policies(s, created["policies"])
            for mid in created["models"]:
                await dbcleanup.purge_model(s, mid)
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


def _dejar_pendientes(factory, pid, n: int) -> list:
    """Deja EXACTAMENTE `n` entregas pendientes del perfil y aparta el resto
    (marcadas como entregadas). El corpus de la BD compartida crece con la
    suite, así que un perfil puede acabar con decenas de evaluaciones: sin
    esto, la cabeza de la cola y el `rows[0]` de un claim dependerían del
    orden en que se ejecuten los demás tests. Devuelve los event_id que
    quedan vivos, en el orden en que el claim los verá."""
    vivos = [
        r.event_id for r in _rows(
            factory,
            "SELECT d.event_id FROM integration_outbox_deliveries d "
            "JOIN integration_outbox o ON o.event_id = d.event_id "
            "WHERE o.subject_profile_id = :p AND d.state = 'pending' "
            "ORDER BY d.event_id", p=pid,
        )
    ]
    assert len(vivos) >= n, f"el perfil solo tiene {len(vivos)} entregas"
    sobra = vivos[n:]
    if sobra:
        async def apartar():
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "UPDATE integration_outbox_deliveries "
                        "SET state = 'delivered', ack_at = clock_timestamp() "
                        "WHERE event_id = ANY(:ids)"
                    ),
                    {"ids": sobra},
                )
                await s.commit()

        asyncio.run(apartar())
    return vivos[:n]


def _sync_exec(sql, params=None):
    """Escritura por una conexión SÍNCRONA aparte: se usa desde dentro del
    transporte, que corre en el event loop del dispatcher (allí `asyncio.run`
    sería un error)."""
    url = settings.CORE_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    engine = sa.create_engine(
        url,
        poolclass=sa.pool.NullPool,
        connect_args={"options": f"-csearch_path={settings.CORE_DB_SCHEMA},public"},
    )
    try:
        with engine.begin() as c:
            c.execute(sa.text(sql), params or {})
    finally:
        engine.dispose()


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


def test_no_transport_claims_nothing_and_burns_no_attempts(db):
    """2ª rev. A-10: sin transporte NO se reclama nada — attempts intacto de
    raíz (un release + retry de la task podía inflarlo)."""
    factory, created = db
    pid, vacs = _setup_evaluated(factory, created, titles=("backend python",))
    assert delivery.get_transport() is None
    r = _dispatch()
    assert (r["claimed"], r["delivered"], r["no_transport"]) == (0, 0, True)
    row = _rows(
        factory,
        "SELECT d.state, d.attempts FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )[0]
    assert (row.state, row.attempts) == ("pending", 0)  # intento NO consumido


def test_reclaim_by_expired_lease_burns_no_attempts(db):
    """Regresión G2-P3-4: `attempts` se consumía AL RECLAMAR, así que un
    dispatcher que muere entre el claim commiteado y los marks (OOM, redeploy
    en bucle) devolvía el evento por lease caducado y cada re-claim quemaba un
    intento SIN que el transporte hubiera corrido jamás — tras MAX_ATTEMPTS-1
    claims fantasma, el PRIMER fallo real dead-leterreaba el evento con una
    única ejecución real. Ahora el intento lo consume el RESULTADO."""
    factory, created = db
    pid, _vacs = _setup_evaluated(factory, created, titles=("backend python",))

    async def claim_and_expire():
        """Claim commiteado + lease vencido: el proceso murió antes de marcar."""
        async with factory() as s:
            rows, _lease = await delivery.claim_deliveries(s, limit=10)
            await s.commit()
        async with factory() as s:
            await s.execute(sa.text(
                "UPDATE integration_outbox_deliveries d "
                "SET lease = clock_timestamp() - interval '1 second' "
                "FROM integration_outbox o "
                "WHERE o.event_id = d.event_id AND o.subject_profile_id = :p"
            ), {"p": pid})
            await s.commit()
        return rows

    for _ in range(delivery.MAX_ATTEMPTS + 3):  # crash-loop largo
        assert len(asyncio.run(claim_and_expire())) == 1
    row = _rows(
        factory,
        "SELECT d.state, d.attempts FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )[0]
    assert row.attempts == 0  # antes: 11 intentos fantasma

    # Y el primer fallo REAL del transporte es el intento 1: reintento, no dead.
    inbox = FakeInbox(fail_times=1)
    delivery.set_transport(inbox.transport)
    try:
        r = _dispatch()
    finally:
        delivery.set_transport(None)
    assert (r["failed"], r["dead"]) == (1, 0)
    row = _rows(
        factory,
        "SELECT d.state, d.attempts FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )[0]
    assert (row.state, row.attempts) == ("pending", 1)  # el intento REAL sí cuenta


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
    # 2ª rev.: con MAX_ATTEMPTS=1 la clasificación local diría 'dead' — la
    # ALERTA y el contador deben gobernarse por la transición REAL (cero).
    monkeypatch.setattr(delivery, "MAX_ATTEMPTS", 1)

    async def late_fail():
        async with factory() as s:
            result = await delivery.mark_failed(
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
            return result

    with caplog_at_error() as records:
        result = asyncio.run(late_fail())
    assert result == {"dead": 0, "retried": 0}  # NADA transicionó
    assert not any("DEAD-LETTER" in r.getMessage() for r in records)  # sin página falsa
    row = _rows(
        factory,
        "SELECT d.state, d.ack_at FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )[0]
    assert row.state == "delivered" and row.ack_at is not None  # INTACTO


import contextlib


@contextlib.contextmanager
def caplog_at_warning():
    """Idéntico a caplog_at_error pero al nivel WARNING (G6-P2-2)."""
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = H(level=logging.WARNING)
    lg = logging.getLogger("jobhunt_core.delivery")
    lg.addHandler(h)
    try:
        yield records
    finally:
        lg.removeHandler(h)


@contextlib.contextmanager
def caplog_at_error():
    """Captura records ERROR del logger de delivery sin la fixture caplog
    (usable dentro de contextos anidados)."""
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = H(level=logging.ERROR)
    lg = logging.getLogger("jobhunt_core.delivery")
    lg.addHandler(h)
    try:
        yield records
    finally:
        lg.removeHandler(h)


def test_stats_expose_lag_and_states(db):
    """2ª rev. A-10 (GATE A / ADR-06: monitorizar lag + dead-letter):
    conteos por estado, edad del evento no entregado más viejo (P2-6) y
    dead_total."""
    factory, created = db
    pid, vacs = _setup_evaluated(factory, created)

    async def get_stats():
        async with factory() as s:
            return await delivery.stats(s)

    st = asyncio.run(get_stats())
    assert st["by_state"].get("pending", 0) >= 2
    assert st["oldest_pending_s"] >= 0.0
    assert st["dead_total"] == 0

    inbox = FakeInbox()
    delivery.set_transport(inbox.transport)
    try:
        _dispatch()
    finally:
        delivery.set_transport(None)
    st = asyncio.run(get_stats())
    assert st["by_state"].get("delivered", 0) >= 2
    # Todo entregado: sin eventos pending/inflight la edad vuelve a 0.
    assert st["oldest_pending_s"] == 0.0


def _stats(factory):
    async def go():
        async with factory() as s:
            return await delivery.stats(s)

    return asyncio.run(go())


def _scalar_deliv(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).scalar()

    return asyncio.run(go())


def test_stats_lag_is_event_age_never_next_retry(db, monkeypatch):
    """Regresión P2-6 (rev. externa parte 2): una entrega FALLIDA con
    `next_attempt_at` en el FUTURO (backoff) debe dar un lag POSITIVO y
    CRECIENTE — la edad del EVENTO. La métrica anterior medía
    clock_timestamp() − next_attempt_at: negativa justo cuando el evento
    envejecía esperando su reintento (el escenario del revisor)."""
    import time as time_mod

    factory, created = db
    pid, vacs = _setup_evaluated(factory, created, titles=("backend python",))
    # Fallo real por la maquinaria: backoff deja next_attempt_at en el futuro.
    monkeypatch.setattr(delivery, "BACKOFF_BASE_S", 3600)
    inbox = FakeInbox(fail_times=10**6)
    delivery.set_transport(inbox.transport)
    try:
        r = _dispatch()
        assert r["failed"] == 1
    finally:
        delivery.set_transport(None)
    row = _rows(
        factory,
        "SELECT d.state, d.next_attempt_at > clock_timestamp() AS future "
        "FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )[0]
    assert (row.state, row.future) == ("pending", True)  # reintento FUTURO

    st1 = _stats(factory)
    assert st1["oldest_pending_s"] > 0.0  # jamás negativo ni aplanado
    time_mod.sleep(0.2)
    st2 = _stats(factory)
    assert st2["oldest_pending_s"] > st1["oldest_pending_s"]  # CRECE

    # inflight también cuenta (evento sin entregar): un claim colgado no
    # saca el evento de la medición.
    async def to_inflight():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE integration_outbox_deliveries "
                    "SET state = 'inflight', lease = clock_timestamp() + "
                    "make_interval(secs => 120) WHERE event_id IN ("
                    "SELECT event_id FROM integration_outbox "
                    "WHERE subject_profile_id = :p)"
                ),
                {"p": pid},
            )
            await s.commit()

    asyncio.run(to_inflight())
    st3 = _stats(factory)
    assert st3["oldest_pending_s"] > 0.0


def test_stats_dead_total_counts_dead_letters(db, monkeypatch):
    """Regresión P2-6: un evento en DEAD-LETTER aparece en dead_total (la
    fuente del gate `outbox_dead` de §6) y deja de contar en el lag."""
    factory, created = db
    pid, vacs = _setup_evaluated(factory, created, titles=("backend python",))
    monkeypatch.setattr(delivery, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(delivery, "BACKOFF_BASE_S", 0)
    inbox = FakeInbox(fail_times=10**6)
    delivery.set_transport(inbox.transport)
    try:
        r = _dispatch()
        assert r["dead"] == 1
    finally:
        delivery.set_transport(None)
    st = _stats(factory)
    assert st["dead_total"] == 1
    assert st["by_state"].get("dead") == 1
    assert st["oldest_pending_s"] == 0.0  # dead no es pending/inflight
    # G1-P2-3 (core0030): la transición real estampa dead_at — sin él, el
    # gate outbox_dead no puede acotar el conteo a la ventana del ciclo.
    dead_at = _scalar_deliv(
        factory,
        "SELECT dead_at FROM integration_outbox_deliveries WHERE state = 'dead'",
    )
    assert dead_at is not None


def test_g3_lease_vencido_no_pierde_el_intento_ni_el_dead_letter(db, monkeypatch):
    """Regresión G3-P2-2: con `attempts` consumido por el RESULTADO (G2-P3-4)
    los dos escritores quedaron DETRÁS del fence, así que un lote cuyo
    transporte supera el lease (G2-H-7) —re-reclamado por el siguiente beat—
    perdía el intento Y el `last_error` en TODOS sus marks: con fallos REALES
    y repetidos el contador nunca avanzaba, el DEAD-LETTER (DoD A-10) era
    INALCANZABLE y el evento reintentaba para siempre, mudo. Ahora el intento
    y el error se persisten FUERA del fence (monótonos, solo sobre 'inflight')
    y la entrega con los intentos agotados que NADIE posee se retira a
    dead-letter con alerta al re-reclamar."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python",))
    _dejar_pendientes(factory, pid, 1)
    monkeypatch.setattr(delivery, "MAX_ATTEMPTS", 3)

    def _deliv():
        return _rows(
            factory,
            "SELECT d.state, d.attempts, d.last_error, d.dead_at "
            "FROM integration_outbox_deliveries d "
            "JOIN integration_outbox o ON o.event_id = d.event_id "
            "WHERE o.subject_profile_id = :p", p=pid,
        )[0]

    async def ciclo():
        async with factory() as s:
            rows, lease = await delivery.claim_deliveries(s, limit=10)
            await s.commit()
        assert len(rows) == 1
        # El transporte se pasa del lease: el siguiente beat re-reclama y
        # nuestro token deja de ser el vigente (el fence nos descartará).
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE integration_outbox_deliveries d "
                    "SET lease = clock_timestamp() - interval '1 second' "
                    "FROM integration_outbox o WHERE o.event_id = d.event_id "
                    "AND o.subject_profile_id = :p"
                ),
                {"p": pid},
            )
            await s.commit()
        async with factory() as s:
            res = await delivery.mark_failed(
                s,
                [{"eid": rows[0].event_id, "dest": rows[0].destination,
                  "attempts": rows[0].attempts + 1,
                  "error": "timeout real del transporte"}],
                lease,
            )
            await s.commit()
        return res

    for n in range(1, delivery.MAX_ATTEMPTS + 1):
        # El fence sigue descartando la TRANSICIÓN DE ESTADO (correcto: la
        # entrega ya no es nuestra)…
        assert asyncio.run(ciclo()) == {"dead": 0, "retried": 0}
        r = _deliv()
        # …pero el intento EJECUTADO y su error ya no se pierden.
        assert (r.state, r.attempts) == ("inflight", n)  # antes: attempts 0 SIEMPRE
        assert r.last_error == "timeout real del transporte"  # antes: None

    async def retirar():
        async with factory() as s:
            n = await delivery.retire_exhausted(s)
            await s.commit()
            return n

    with caplog_at_error() as records:
        assert asyncio.run(retirar()) == 1  # antes: reintento infinito
    assert any("DEAD-LETTER" in rec.getMessage() for rec in records)
    r = _deliv()
    assert (r.state, r.attempts) == ("dead", delivery.MAX_ATTEMPTS)
    assert r.dead_at is not None
    assert r.last_error == "timeout real del transporte"


def test_g3_retire_exhausted_respeta_al_dueno_vigente_y_los_terminales(db, monkeypatch):
    """El rescate de G3-P2-2 solo toca lo que NADIE posee: una entrega
    inflight con lease VIGENTE (otro dispatcher está entregando ahora mismo)
    no se retira aunque tenga los intentos agotados — nada de páginas falsas
    ni de robarle la transición al dueño."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python",))
    _dejar_pendientes(factory, pid, 1)
    monkeypatch.setattr(delivery, "MAX_ATTEMPTS", 1)

    async def inflight_vigente():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE integration_outbox_deliveries d "
                    "SET state = 'inflight', attempts = 5, "
                    "lease = clock_timestamp() + make_interval(secs => 120) "
                    "FROM integration_outbox o WHERE o.event_id = d.event_id "
                    "AND o.subject_profile_id = :p"
                ),
                {"p": pid},
            )
            n = await delivery.retire_exhausted(s)
            await s.commit()
            return n

    assert asyncio.run(inflight_vigente()) == 0
    row = _rows(
        factory,
        "SELECT d.state FROM integration_outbox_deliveries d "
        "JOIN integration_outbox o ON o.event_id = d.event_id "
        "WHERE o.subject_profile_id = :p", p=pid,
    )[0]
    assert row.state == "inflight"  # intacta


def test_g3_veneno_que_mata_al_dispatcher_se_retira_y_desbloquea_la_cola(
    db, monkeypatch
):
    """Cierre de G3-H-1 (hipótesis CONFIRMADA): desde que el intento lo consume
    el RESULTADO y no el claim (G2-P3-4), una entrega cuyo payload MATA al
    proceso del dispatcher (OOM, segfault del driver) no llega nunca a marcar,
    así que `attempts` se queda en 0, el DEAD-LETTER por agotamiento es
    inalcanzable y —por el `ORDER BY next_attempt_at NULLS FIRST`— secuestra la
    CABEZA de la cola: el resto del outbox no avanza. El contador `claims`
    (core0032), separado de `attempts`, cuenta los reclamos CONSECUTIVOS sin
    resultado y retira el veneno con una razón propia."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python", "data eng"))
    monkeypatch.setattr(delivery, "MAX_CLAIMS_WITHOUT_RESULT", 4)

    veneno, sano = _dejar_pendientes(factory, pid, 2)
    # El sano espera su turno DETRÁS (next_attempt_at no nulo): con limit=1 la
    # cabeza de la cola es siempre el veneno mientras siga vivo.
    async def _atrasar():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE integration_outbox_deliveries "
                    "SET next_attempt_at = clock_timestamp() "
                    "WHERE event_id = :e"
                ),
                {"e": sano},
            )
            await s.commit()

    asyncio.run(_atrasar())

    def _estado(eid):
        return _rows(
            factory,
            "SELECT state, attempts, claims, last_error FROM "
            "integration_outbox_deliveries WHERE event_id = :e", e=eid,
        )[0]

    async def _ciclo_que_mata_al_proceso():
        """Reclama y MUERE: ni mark_delivered ni mark_failed, como un OOM."""
        async with factory() as s:
            rows, _lease = await delivery.claim_deliveries(s, limit=1)
            await s.commit()
        return rows

    for i in range(delivery.MAX_CLAIMS_WITHOUT_RESULT):
        rows = asyncio.run(_ciclo_que_mata_al_proceso())
        assert [r.event_id for r in rows] == [veneno], f"ciclo {i}: la cabeza cambió"
        # El lease caduca (el proceso murió con el claim commiteado).
        async def _caducar():
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "UPDATE integration_outbox_deliveries SET lease = "
                        "clock_timestamp() - interval '1 second' WHERE event_id = :e"
                    ),
                    {"e": veneno},
                )
                await s.commit()

        asyncio.run(_caducar())
        st = _estado(veneno)
        assert (st.state, st.attempts, st.claims) == ("inflight", 0, i + 1)

    # El siguiente despacho lo retira POR VENENO, con razón propia y alerta —
    # y en el MISMO beat la cola ya avanza: se entrega el mensaje sano.
    inbox = FakeInbox()
    delivery.set_transport(inbox.transport)
    try:
        with caplog_at_error() as records:
            r = _dispatch(limit=1)
    finally:
        delivery.set_transport(None)
    assert r["poisoned"] == 1  # antes: reintento infinito, jamás dead
    assert any("VENENO" in rec.getMessage() for rec in records)
    st = _estado(veneno)
    assert (st.state, st.attempts) == ("dead", 0)  # attempts INTACTO (G2-P3-4)
    assert "veneno" in st.last_error and "destino caído" in st.last_error
    # La cabeza de la cola quedó libre: el sano se entregó en ese mismo ciclo.
    assert (r["claimed"], r["delivered"]) == (1, 1)
    assert _estado(sano).state == "delivered"


def test_g4_transporte_lento_pero_exitoso_no_muere_como_veneno(db, monkeypatch):
    """Regresión G4-P2-2: G3-P2-2 sacó del fence la persistencia del resultado
    SOLO para el FALLO. El camino de ÉXITO —único sitio donde `claims` vuelve
    a 0 tras una entrega buena— seguía fenceado, así que un transporte que
    ENTREGA BIEN pero supera el lease (G2-H-7) perdía todos sus marks:
    `attempts` en 0 (fuera de `retire_exhausted`) y `claims` creciendo hasta el
    tope ⇒ `retire_poisoned` mataba como VENENO un evento entregado
    correctamente en CADA vuelta, con un `last_error` que afirma lo contrario
    de lo ocurrido. Una entrega confirmada es un hecho del transporte,
    independiente de quién posea el lease: se persiste sobre filas aún
    'inflight' (jamás resucita un terminal)."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python",))
    _dejar_pendientes(factory, pid, 1)
    monkeypatch.setattr(delivery, "MAX_CLAIMS_WITHOUT_RESULT", 2)

    def _estado():
        return _rows(
            factory,
            "SELECT d.state, d.attempts, d.claims, d.last_error FROM "
            "integration_outbox_deliveries d "
            "JOIN integration_outbox o ON o.event_id = d.event_id "
            "WHERE o.subject_profile_id = :p", p=pid,
        )[0]

    async def _ciclo_lento_pero_exitoso():
        """claim → el transporte TARDA más que el lease → y ENTREGA BIEN."""
        async with factory() as s:
            rows, lease = await delivery.claim_deliveries(s, limit=10)
            await s.commit()
        assert len(rows) == 1
        async with factory() as s:  # el lote se pasa del lease
            await s.execute(
                sa.text(
                    "UPDATE integration_outbox_deliveries d SET lease = "
                    "clock_timestamp() - interval '1 second' "
                    "FROM integration_outbox o WHERE o.event_id = d.event_id "
                    "AND o.subject_profile_id = :p"
                ),
                {"p": pid},
            )
            await s.commit()
        async with factory() as s:
            n = await delivery.mark_delivered(
                s, [{"eid": rows[0].event_id, "dest": rows[0].destination}], lease
            )
            await s.commit()
        return n

    async def _retirar():
        async with factory() as s:
            n_ex = await delivery.retire_exhausted(s)
            n_po = await delivery.retire_poisoned(s)
            await s.commit()
            return n_ex, n_po

    # La entrega CONFIRMADA se persiste pese al lease perdido…
    assert asyncio.run(_ciclo_lento_pero_exitoso()) == 1  # antes del fix: 0
    st = _estado()
    assert (st.state, st.attempts, st.claims) == ("delivered", 1, 0)
    # …ninguna vía de retirada la toca (antes: dead por VENENO tras N vueltas)…
    assert asyncio.run(_retirar()) == (0, 0)
    assert st.last_error is None

    # …y la cola AVANZA: sin transición, la fila se re-reclamaba y re-entregaba
    # para siempre (re-entrega infinita, G2-H-7).
    async def _reclamar():
        async with factory() as s:
            rows, _lease = await delivery.claim_deliveries(s, limit=10)
            await s.commit()
            return rows

    assert asyncio.run(_reclamar()) == []

    # El fence que SÍ importa sigue en pie: un mark tardío jamás resucita un
    # terminal (la fila ya entregada no vuelve a contar ni a tocar attempts).
    async def _mark_tardio():
        async with factory() as s:
            row = (
                await s.execute(
                    sa.text(
                        "SELECT d.event_id, d.destination FROM "
                        "integration_outbox_deliveries d JOIN integration_outbox o "
                        "ON o.event_id = d.event_id WHERE o.subject_profile_id = :p"
                    ),
                    {"p": pid},
                )
            ).one()
            n = await delivery.mark_delivered(
                s, [{"eid": row.event_id, "dest": row.destination}], None
            )
            await s.commit()
            return n

    assert asyncio.run(_mark_tardio()) == 0
    assert _estado().attempts == 1  # sin doble consumo de intento


def test_g3_los_re_claims_de_un_mensaje_sano_no_lo_retiran_como_veneno(
    db, monkeypatch
):
    """No-regresión de G3-H-1: un mensaje que SÍ produce resultado en cada
    ciclo —destino caído, el caso normal— no se acerca jamás al tope de
    veneno, porque el resultado pone `claims` a 0 aunque el fence descarte la
    transición (lease vencido a mitad del lote, G2-H-7). El veneno es «el
    dispatcher nunca llegó a marcar», no «el destino falla»."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python",))
    _dejar_pendientes(factory, pid, 1)
    monkeypatch.setattr(delivery, "MAX_CLAIMS_WITHOUT_RESULT", 2)
    monkeypatch.setattr(delivery, "MAX_ATTEMPTS", 100)  # aislar la vía de veneno

    def _estado():
        return _rows(
            factory,
            "SELECT d.state, d.attempts, d.claims FROM "
            "integration_outbox_deliveries d "
            "JOIN integration_outbox o ON o.event_id = d.event_id "
            "WHERE o.subject_profile_id = :p", p=pid,
        )[0]

    async def _ciclo_con_fallo_real():
        async with factory() as s:
            rows, lease = await delivery.claim_deliveries(s, limit=10)
            await s.commit()
        async with factory() as s:  # el lote se pasa del lease
            await s.execute(
                sa.text(
                    "UPDATE integration_outbox_deliveries d SET lease = "
                    "clock_timestamp() - interval '1 second' "
                    "FROM integration_outbox o WHERE o.event_id = d.event_id "
                    "AND o.subject_profile_id = :p"
                ),
                {"p": pid},
            )
            await s.commit()
        async with factory() as s:
            await delivery.mark_failed(
                s,
                [{"eid": rows[0].event_id, "dest": rows[0].destination,
                  "attempts": rows[0].attempts + 1, "error": "destino caído"}],
                lease,
            )
            await s.commit()

    for _ in range(delivery.MAX_CLAIMS_WITHOUT_RESULT * 3):
        asyncio.run(_ciclo_con_fallo_real())
        st = _estado()
        assert st.claims == 0, "el resultado tiene que limpiar el contador"

    async def _retirar():
        async with factory() as s:
            n = await delivery.retire_poisoned(s)
            await s.commit()
            return n

    assert asyncio.run(_retirar()) == 0  # jamás veneno: hubo resultados
    st = _estado()
    assert st.state == "inflight" and st.attempts == 6  # los intentos REALES


def test_g5_un_veneno_no_arrastra_a_dead_letter_a_sus_vecinos_entregados(
    db, monkeypatch
):
    """Regresión G5-P2-2: `claims` mide «reclamos sin RESULTADO PERSISTIDO» y
    el dispatcher persistía los resultados UNA SOLA VEZ, al final del lote.
    Un payload que MATA al proceso en la posición K —el escenario exacto para
    el que `claims` existe— hacía que NINGÚN mark del lote se ejecutara: los
    eventos 1..K-1 se transportaban CORRECTAMENTE en cada vuelta y aun así
    llegaban al tope de reclamos junto al veneno, y como el claim es
    determinista el lote era el MISMO cada vuelta. Resultado: hasta 99 vecinos
    ENTREGADOS retirados a DEAD-LETTER con `attempts = 0` y un `last_error`
    que afirma lo contrario de lo ocurrido.

    La muerte del proceso se simula con una BaseException, que el `except
    Exception` del dispatcher NO captura: el bucle se aborta igual que un OOM
    y solo sobrevive lo ya COMMITEADO."""
    from jobhunt_core.tasks.delivery import _dispatch_impl

    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python", "data eng"))
    vivos = _dejar_pendientes(factory, pid, 2)
    sano, veneno = vivos[0], vivos[1]
    monkeypatch.setattr(delivery, "MAX_CLAIMS_WITHOUT_RESULT", 3)
    inbox = FakeInbox()

    def transporte_venenoso(dest, event):
        if event["event_id"] == str(veneno):
            raise BaseException("OOM: el payload tumba al dispatcher")  # noqa: TRY002
        inbox.transport(dest, event)

    def _caducar_leases():
        async def go():
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "UPDATE integration_outbox_deliveries SET lease = "
                        "clock_timestamp() - interval '1 second' "
                        "WHERE state = 'inflight' AND event_id = ANY(:ids)"
                    ),
                    {"ids": vivos},
                )
                await s.commit()

        asyncio.run(go())

    def _estado(eid):
        return _rows(
            factory,
            "SELECT state, attempts, claims, last_error FROM "
            "integration_outbox_deliveries WHERE event_id = :e", e=eid,
        )[0]

    delivery.set_transport(transporte_venenoso)
    try:
        for _ in range(delivery.MAX_CLAIMS_WITHOUT_RESULT):
            with pytest.raises(BaseException, match="OOM"):
                asyncio.run(_dispatch_impl(100))
            _caducar_leases()
    finally:
        delivery.set_transport(None)

    # El vecino SANO quedó ENTREGADO en la PRIMERA vuelta (su resultado se
    # persiste al ocurrir), así que ni se re-reclama ni acumula reclamos.
    st_sano = _estado(sano)
    assert (st_sano.state, st_sano.claims) == ("delivered", 0)
    assert inbox.calls == 1  # antes del fix: una entrega REAL por vuelta
    # El veneno SÍ acumula reclamos sin un solo resultado.
    assert _estado(veneno).claims == delivery.MAX_CLAIMS_WITHOUT_RESULT

    with caplog_at_error() as records:
        async def retirar():
            async with factory() as s:
                n = await delivery.retire_poisoned(s)
                await s.commit()
                return n

        n_po = asyncio.run(retirar())

    assert n_po == 1  # antes del fix: 2 (el sano ENTREGADO, arrastrado)
    assert _estado(veneno).state == "dead"
    assert _estado(sano).state == "delivered"  # jamás dead-letter
    assert len([r for r in records if "VENENO" in r.getMessage()]) == 1


def test_g5_el_exito_confirmado_gana_al_reintento_de_otro_dispatcher(db):
    """Regresión G5-P2-3: la guarda `state = 'inflight'` del éxito NO era
    equivalente al fence que sustituyó — fallaba también cuando otro
    dispatcher ya había RESUELTO la fila. Bajo G2-H-7: A supera el lease
    entregando BIEN, B se re-clama la fila y su transporte da timeout de
    cliente; el `mark_failed` de B manda la fila a `pending` y consume el
    intento, y el `mark_delivered` de A ya no encontraba `inflight`: el ÉXITO
    CONFIRMADO se descartaba en silencio. Repetido, el evento moría por
    MAX_ATTEMPTS con 8 entregas reales al inbox y un `last_error` que afirma
    lo contrario. La guarda correcta es «no terminal»."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python",))
    _dejar_pendientes(factory, pid, 1)

    async def claim():
        async with factory() as s:
            rows, lease = await delivery.claim_deliveries(s, limit=10)
            await s.commit()
            return rows, lease

    def _caducar():
        async def go():
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "UPDATE integration_outbox_deliveries d SET lease = "
                        "clock_timestamp() - interval '1 second' "
                        "FROM integration_outbox o WHERE o.event_id = d.event_id "
                        "AND o.subject_profile_id = :p"
                    ),
                    {"p": pid},
                )
                await s.commit()

        asyncio.run(go())

    def _estado():
        return _rows(
            factory,
            "SELECT d.state, d.attempts, d.last_error, d.next_attempt_at FROM "
            "integration_outbox_deliveries d "
            "JOIN integration_outbox o ON o.event_id = d.event_id "
            "WHERE o.subject_profile_id = :p", p=pid,
        )[0]

    rows_a, lease_a = asyncio.run(claim())   # A se lleva el claim…
    _caducar()                               # …y supera el lease entregando BIEN
    rows_b, lease_b = asyncio.run(claim())   # el beat siguiente re-clama
    assert rows_b

    async def resolver():
        async with factory() as s:
            # B (dueño legítimo) abandona por timeout ANTES de que A marque.
            ko = await delivery.mark_failed(
                s,
                [{"eid": rows_b[0].event_id, "dest": rows_b[0].destination,
                  "attempts": rows_b[0].attempts + 1,
                  "error": "timeout de cliente (504)"}],
                lease_b,
            )
            # …y ahora llega el ÉXITO CONFIRMADO de A.
            ok = await delivery.mark_delivered(
                s, [{"eid": rows_a[0].event_id, "dest": rows_a[0].destination}],
                lease_a,
            )
            await s.commit()
            return ko, ok

    ko, ok = asyncio.run(resolver())
    assert ko == {"dead": 0, "retried": 1}
    assert ok == 1  # antes del fix: 0 (el éxito confirmado, descartado)
    st = _estado()
    assert st.state == "delivered"
    # G5-N-5: la entrega gana al reintento programado y no deja un error que
    # ya no describe la fila.
    assert st.last_error is None and st.next_attempt_at is None
    # La cola AVANZA: nada que re-reclamar (antes moría por MAX_ATTEMPTS).
    assert asyncio.run(claim())[0] == []

    # NO-REGRESIÓN: la guarda sigue siendo «no terminal» — un mark tardío
    # jamás resucita ni re-consume una fila ya entregada.
    async def mark_tardio():
        async with factory() as s:
            n = await delivery.mark_delivered(
                s, [{"eid": rows_a[0].event_id, "dest": rows_a[0].destination}],
                None,
            )
            await s.commit()
            return n

    assert asyncio.run(mark_tardio()) == 0
    assert _estado().attempts == st.attempts


def test_g5_el_desbordamiento_del_lease_deja_rastro_en_el_resumen(db, monkeypatch):
    """Regresión G5-P3-3 (+ cierre de G2-H-7): antes, un lote que superaba el
    lease reportaba `fenced_out = N` — la única señal prevista para observar
    G2-H-7. Al sacar el éxito del fence, los marks del dueño superado se
    escriben igual, `fenced_out` vuelve a 0 y `claims` lo resetea el propio
    mark: un ciclo que re-clama y re-entrega el lote entero era
    INDISTINGUIBLE de uno sano. Ahora el dispatcher RENUEVA el lease del resto
    del lote mientras trabaja y cuenta el desbordamiento en ORIGEN."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python", "data eng"))
    _dejar_pendientes(factory, pid, 2)
    # Renovar tras CADA entrega: el reloj real del test no llega a LEASE_S/2.
    monkeypatch.setattr(delivery, "LEASE_RENEW_AFTER_S", 0)
    inbox = FakeInbox()
    calls = {"n": 0}

    def transporte_lento(dest, event):
        calls["n"] += 1
        inbox.transport(dest, event)
        if calls["n"] == 1:
            # A mitad del lote OTRO dispatcher se re-clama lo que queda. El
            # transporte es SÍNCRONO y corre dentro del loop del dispatcher:
            # la escritura tiene que ir por una conexión síncrona aparte.
            _sync_exec(
                "UPDATE integration_outbox_deliveries d SET lease = "
                "clock_timestamp() + interval '120 seconds', "
                "claims = claims + 1 "
                "FROM integration_outbox o WHERE o.event_id = d.event_id "
                "AND o.subject_profile_id = :p AND d.state = 'inflight'",
                {"p": str(pid)},
            )

    delivery.set_transport(transporte_lento)
    try:
        r = _dispatch(limit=10)
    finally:
        delivery.set_transport(None)

    # La señal existe y es explícita (antes: fenced_out=0 y nada más).
    assert r["lease_renewals"] >= 1
    assert r["lease_overrun"] >= 1


# --------------------------------- transporte sombra REAL (P1-1b, §8 Fase B)


def test_shadow_inbox_transport_persistent_idempotent_at_least_once(db):
    """P1-1b (decisión delegada [EJECUTADA]): el transporte de producción de
    la sombra entrega al inbox PERSISTENTE jobhunt.shadow_inbox con INSERT
    ON CONFLICT DO NOTHING — at-least-once real (el transporte corre otra
    vez en la re-entrega) + consumo idempotente demostrado EN CONTINUO. El
    registro de arranque de worker jamás pisa un transporte ya inyectado."""
    from jobhunt_core.shadow import inbox as shadow_inbox
    from jobhunt_core.tasks.delivery import register_shadow_inbox_transport

    factory, created = db
    pid, vacs = _setup_evaluated(factory, created)

    # Con transporte YA inyectado, register_if_unset NO pisa (tests/Fase C).
    injected = FakeInbox()
    injected_fn = injected.transport  # bound method FIJO (is-comparable)
    delivery.set_transport(injected_fn)
    try:
        assert shadow_inbox.register_if_unset() is False
        assert delivery.get_transport() is injected_fn
    finally:
        delivery.set_transport(None)

    # Arranque del worker (señal worker_process_init): registra el sombra.
    register_shadow_inbox_transport()
    assert delivery.get_transport() is shadow_inbox.shadow_inbox_transport
    try:
        r = _dispatch()
        assert r["delivered"] == 2
        rows = _rows(
            factory,
            "SELECT consumer_id, event_id, payload FROM shadow_inbox "
            "WHERE payload->>'subject_profile_id' = :p", p=str(pid),
        )
        assert len(rows) == 2
        for row in rows:
            assert row.consumer_id == "tenant-match"
            assert row.payload["event_id"] == str(row.event_id)
            assert row.payload["type"] == "match.evaluated"
            assert set(row.payload["payload"]) == {
                "eval_key", "profile_id", "vacancy_id",
            }

        # RE-ENTREGA forzada (ack perdido): el transporte corre OTRA vez y
        # el inbox PERSISTENTE desduplica por PK(consumer_id, event_id).
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
        assert _dispatch()["delivered"] == 2  # at-least-once real
        n = _rows(
            factory,
            "SELECT count(*) AS n FROM shadow_inbox "
            "WHERE payload->>'subject_profile_id' = :p", p=str(pid),
        )[0].n
        assert n == 2  # cero duplicados: idempotente y PERSISTENTE
    finally:
        delivery.set_transport(None)

        async def cleanup():
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "DELETE FROM shadow_inbox "
                        "WHERE payload->>'subject_profile_id' = :p"
                    ),
                    {"p": str(pid)},
                )
                await s.commit()

        asyncio.run(cleanup())


def test_delivery_task_registered_and_routed():
    from jobhunt_core.celery_app import celery_app
    from jobhunt_core.config import settings as core_settings

    assert "jobhunt.delivery.dispatch_outbox" in celery_app.tasks
    assert celery_app.conf.task_routes["jobhunt.delivery.*"] == {"queue": "core.default"}
    assert celery_app.conf.task_routes["jobhunt.delivery.dispatch_outbox"] == {
        "queue": "core.default"
    }
    # P1-1: el despacho va en el beat (cada 5 min) — sin cadencia, la
    # entrega sombra sería un no-op permanente.
    by_task = {e["task"]: e for e in celery_app.conf.beat_schedule.values()}
    assert by_task["jobhunt.delivery.dispatch_outbox"]["schedule"] == float(
        core_settings.CORE_DELIVERY_DISPATCH_EVERY_S
    )


# ------------------------------------- G6: la guarda del UPDATE y su telemetría


def _envenenar(factory, eid, claims):
    """Deja ESA entrega justo en el borde del veneno: 'inflight', lease
    CADUCADO (no la posee nadie) y `claims` al tope, sin rastro del ciclo
    anterior. Devuelve el lease para poder fencear con él."""
    async def go():
        async with factory() as s:
            lease = (
                await s.execute(
                    sa.text(
                        "UPDATE integration_outbox_deliveries SET state = 'inflight', "
                        "lease = clock_timestamp() - interval '5 minutes', "
                        "claims = :c, ack_at = NULL, dead_at = NULL, "
                        "next_attempt_at = NULL, last_error = NULL "
                        "WHERE event_id = :e RETURNING lease"
                    ),
                    {"e": eid, "c": claims},
                )
            ).scalar_one()
            await s.commit()
            return lease

    return asyncio.run(go())


def _carrera_con_retire_poisoned(factory, resolver):
    """CONCURRENCIA REAL (no un mock): B resuelve la fila y RETIENE el lock;
    A arranca su retirada por veneno mientras tanto; B commitea y A termina.
    Es el interleaving exacto que el beat produce con `--concurrency=2` y un
    ciclo que se pasa del tick."""
    async def go():
        async with factory() as sB, factory() as sA:
            res = await resolver(sB)
            tarea = asyncio.create_task(delivery.retire_poisoned(sA))
            await asyncio.sleep(0.5)
            await sB.commit()
            retiradas = await tarea
            await sA.commit()
            return res, retiradas

    return asyncio.run(go())


def test_g6_retire_poisoned_no_pisa_lo_que_otro_dispatcher_acaba_de_resolver(
    db, monkeypatch
):
    """Regresión G6-P2-1: `retire_poisoned` perdió la guarda de estado de su
    PROPIO UPDATE cuando G5-P2-2 metió `state/lease/claims` dentro del subplan
    del LIMIT 1. El subplan es uncorrelated ⇒ Postgres lo resuelve como
    InitPlan UNA vez por sentencia y el recheck EPQ —el que corre cuando el
    UPDATE por fin obtiene el lock que otra transacción tenía— re-evalúa los
    quals con la fila NUEVA pero NO vuelve a ejecutar el InitPlan. Con la
    igualdad contra una constante como ÚNICO qual, la fila se pisaba fuera
    cual fuera el estado al que la otra transacción acababa de llevarla:

    1. una entrega CONFIRMADA (`delivered`, con `ack_at`) acababa `dead` con
       un `last_error` de veneno sobre un evento que el consumidor SÍ recibió,
       y la alerta se contradecía a sí misma («tras 0 reclamos», porque el
       `RETURNING` lee la fila que `mark_delivered` acaba de poner a 0);
    2. un reintento PROGRAMADO (`pending`, 7 intentos por delante) moría
       TERMINAL y el diagnóstico culpaba al mensaje de un 503 del destino;
    3. dos dispatchers retiraban DOS veces la MISMA fila: dos `poisoned`, dos
       alertas y `dead_at` reescrito — justo lo que ventana `outbox_dead`.

    El fix de G5-P2-3 (que un mark TARDÍO del dueño superado SÍ escriba) hace
    que ese «otra transacción» sea el caso NORMAL, no el exótico."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python",))
    (eid,) = _dejar_pendientes(factory, pid, 1)
    monkeypatch.setattr(delivery, "MAX_CLAIMS_WITHOUT_RESULT", 3)

    def _estado():
        return _rows(
            factory,
            "SELECT state, claims, attempts, ack_at, dead_at, next_attempt_at, "
            "last_error FROM integration_outbox_deliveries WHERE event_id = :e",
            e=eid,
        )[0]

    dest = _rows(
        factory,
        "SELECT destination FROM integration_outbox_deliveries WHERE event_id = :e",
        e=eid,
    )[0].destination

    # (1) El dueño LENTO por fin CONFIRMA la entrega.
    _envenenar(factory, eid, delivery.MAX_CLAIMS_WITHOUT_RESULT)
    hechas, retiradas = _carrera_con_retire_poisoned(
        factory,
        lambda s: delivery.mark_delivered(s, [{"eid": eid, "dest": dest}]),
    )
    assert hechas == 1
    assert retiradas == 0, "retire_poisoned retiró una entrega ya CONFIRMADA"
    st = _estado()
    assert (st.state, st.dead_at, st.last_error) == ("delivered", None, None)
    assert st.ack_at is not None

    # (2) El dueño legítimo falla y PROGRAMA el reintento (le quedan intentos).
    lease = _envenenar(factory, eid, delivery.MAX_CLAIMS_WITHOUT_RESULT)
    res, retiradas = _carrera_con_retire_poisoned(
        factory,
        lambda s: delivery.mark_failed(
            s, [{"eid": eid, "dest": dest, "attempts": 1, "error": "BFF 503"}], lease
        ),
    )
    assert res == {"dead": 0, "retried": 1}
    assert retiradas == 0, "retire_poisoned mató un reintento PROGRAMADO"
    st = _estado()
    assert st.state == "pending" and st.next_attempt_at is not None
    assert st.last_error == "BFF 503"  # el destino, no el mensaje

    # (3) Dos dispatchers a la vez sobre un veneno REAL: UNA sola retirada.
    _envenenar(factory, eid, delivery.MAX_CLAIMS_WITHOUT_RESULT)

    async def dos_dispatchers():
        async with factory() as sA, factory() as sB:
            t1 = asyncio.create_task(delivery.retire_poisoned(sA))
            await asyncio.sleep(0.3)
            t2 = asyncio.create_task(delivery.retire_poisoned(sB))
            await asyncio.sleep(0.3)
            n1 = await t1
            await sA.commit()
            n2 = await t2
            await sB.commit()
            return n1, n2

    with caplog_at_error() as records:
        n1, n2 = asyncio.run(dos_dispatchers())
    assert n1 + n2 == 1, "la MISMA fila se retiró dos veces"
    assert len([r for r in records if "VENENO" in r.getMessage()]) == 1
    st = _estado()
    assert st.state == "dead" and st.claims == delivery.MAX_CLAIMS_WITHOUT_RESULT


def test_g7_el_aviso_de_lease_robado_muerde_en_LAS_DOS_direcciones(db, monkeypatch):
    """Regresión G6-P2-2 + G7-P2-2, las dos direcciones y la SEGUNDA con
    CONCURRENCIA REAL.

    G6-P2-2: el `RETURNING` de un UPDATE se evalúa sobre la fila NUEVA, y la
    propia SET de `mark_delivered` pone `lease = NULL`. Así que
    `(d.lease IS DISTINCT FROM :lease)` era TRUE SIEMPRE y el WARNING
    «G2-H-7 en vivo» —el único rastro que sustituye a `fenced_out` tras
    G5-P3-3— se emitía en el 100 % de las entregas SANAS. Una señal atascada
    en ON no es observabilidad: en cuanto el operador aprende a ignorarla, el
    caso REAL pasa inadvertido.

    G7-P2-2: el self-join `prev` con el que G6 lo arregló leía el SNAPSHOT de
    la sentencia, SIN lock, y falla en la dirección contraria. Aquí el
    positivo VERDADERO se construye con el interleaving que produce G2-H-7
    —el re-claim de B commitea MIENTRAS el UPDATE de A espera el lock—, no
    con un cambio ya commiteado ANTES de la sentencia: en ese orden `prev`
    devuelve la versión ANTIGUA (nuestro propio lease) y el aviso NO suena.
    La versión SECUENCIAL de este test (la de G6) pasaba con el defecto
    puesto: por eso se ha reescrito y no añadido.

    El lease de A caduca por el RELOJ (claim con LEASE_S corto), sin tocar la
    fila: su valor tiene que seguir siendo EXACTAMENTE el suyo, o el aviso
    sonaría por el motivo equivocado (falso verde de la propia regresión)."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python", "data eng"))
    sano, robada = _dejar_pendientes(factory, pid, 2)

    async def claim(limit=10):
        async with factory() as s:
            rows, lease = await delivery.claim_deliveries(s, limit=limit)
            await s.commit()
            return {r.event_id: r for r in rows}, lease

    porfila, lease = asyncio.run(claim())
    assert sano in porfila and robada in porfila
    dest = porfila[robada].destination

    # (a) La fila es NUESTRA y su lease está intacto: ni un aviso.
    with caplog_at_warning() as records:
        async def marcar_sana():
            async with factory() as s:
                n = await delivery.mark_delivered(
                    s,
                    [{"eid": sano, "dest": porfila[sano].destination}],
                    lease,
                )
                await s.commit()
                return n

        assert asyncio.run(marcar_sana()) == 1
    assert [r for r in records if "lease ya no era nuestro" in r.getMessage()] == []

    # (b) A vuelve a reclamar con un lease CORTO y lo deja caducar solo; B se
    # la re-clama de VERDAD (claim_deliveries real) y RETIENE el lock mientras
    # A llega tarde con el mark de un transporte YA ejecutado.
    _sync_exec(
        "UPDATE integration_outbox_deliveries SET state = 'pending', "
        "lease = NULL, next_attempt_at = NULL WHERE event_id = :e",
        {"e": str(robada)},
    )
    monkeypatch.setattr(delivery, "LEASE_S", 1)
    _, lease_a = asyncio.run(claim(limit=100))
    monkeypatch.undo()
    time.sleep(2)  # caduca por el RELOJ, no por un UPDATE
    en_bd = _rows(
        factory,
        "SELECT lease FROM integration_outbox_deliveries WHERE event_id = :e",
        e=str(robada),
    )[0].lease
    assert en_bd == lease_a, "la fila ya no lleva el lease de A"

    async def carrera():
        async with factory() as sB, factory() as sA:
            _rows_b, lease_b = await delivery.claim_deliveries(sB, limit=100)
            tarea = asyncio.create_task(
                delivery.mark_delivered(sA, [{"eid": robada, "dest": dest}], lease_a)
            )
            await asyncio.sleep(0.5)
            await sB.commit()  # el ladrón commitea MIENTRAS A espera el lock
            n = await tarea
            await sA.commit()
            return n, lease_b

    with caplog_at_warning() as records:
        n, lease_b = asyncio.run(carrera())
    assert lease_b != lease_a
    assert n == 1  # la entrega CONFIRMADA se persiste igual (G5-P2-3)
    avisos = [r for r in records if "lease ya no era nuestro" in r.getMessage()]
    assert len(avisos) == 1, (
        "el mark aterrizó sobre una fila cuyo lease ya NO era el nuestro y el "
        "único rastro de G2-H-7 no sonó (el lease previo se lee del snapshot "
        "de la sentencia, no de la fila real)"
    )


def test_g6_lease_renewals_solo_cuenta_renovaciones_que_ocurren(db, monkeypatch):
    """Regresión G6-P2-2 (2ª mitad): `lease_renewals` se incrementaba aunque
    `renew_lease` saliera por su early-return SIN renovar nada — el caso del
    ÚLTIMO elemento del lote, donde la cola que queda está vacía. Con el
    umbral cumplido, un lote de UN evento y milisegundos de duración reportaba
    `lease_renewals = 1` y el resumen afirmaba un desbordamiento del lease que
    no había ocurrido. Y el disparo es LEASE_S/2, no LEASE_S (comentario)."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python",))
    _dejar_pendientes(factory, pid, 1)
    monkeypatch.setattr(delivery, "LEASE_RENEW_AFTER_S", 0)  # siempre "vencido"
    assert delivery.LEASE_RENEW_AFTER_S != delivery.LEASE_S  # el umbral es /2
    inbox = FakeInbox()
    delivery.set_transport(inbox.transport)
    try:
        r = _dispatch(limit=10)
    finally:
        delivery.set_transport(None)

    assert (r["claimed"], r["delivered"]) == (1, 1)
    assert r["lease_renewals"] == 0  # antes del fix: 1, sin renovar nada
    assert r["lease_overrun"] == 0


# --------------------------------- G7: la elección de la cabeza bajo concurrencia


def _dos_venenos(db, monkeypatch):
    """DOS candidatos a veneno, no uno. Es el caso BASE del detector, no un
    borde: detrás del veneno nadie llega a transportarse, así que los vecinos
    acumulan `claims` a la vez que él y cruzan el umbral a la vez (G6-N-1).
    Con UNA sola fila —como hacían las regresiones de G5 y G6— la diferencia
    entre «retira la cabeza» y «retira a cualquiera» es INVISIBLE."""
    factory, created = db
    pid, _ = _setup_evaluated(factory, created, titles=("backend python", "data eng"))
    vivos = _dejar_pendientes(factory, pid, 2)
    monkeypatch.setattr(delivery, "MAX_CLAIMS_WITHOUT_RESULT", 3)
    for e in vivos:
        _envenenar(factory, e, delivery.MAX_CLAIMS_WITHOUT_RESULT)
    cabeza, vecino = sorted(vivos, key=str)  # el ORDER BY del subplan
    return factory, cabeza, vecino


def _estados(factory, eids):
    return {
        r.event_id: (r.state, r.attempts, r.dead_at is not None)
        for r in _rows(
            factory,
            "SELECT event_id, state, attempts, dead_at FROM "
            "integration_outbox_deliveries WHERE event_id = ANY(:ids)",
            ids=[str(e) for e in eids],
        )
    }


def test_g7_con_la_cabeza_bloqueada_retire_poisoned_no_ejecuta_al_vecino(
    db, monkeypatch
):
    """Regresión G7-P2-1: `SKIP LOCKED` no evita bloquear — ELIGE OTRA FILA.

    G6 lo añadió al subplan del `LIMIT 1` para cerrar la doble retirada de la
    MISMA fila, y con eso ANULÓ el propio LIMIT 1: con la cabeza bloqueada
    —el caso NORMAL, porque `claim_deliveries` la bloquea con su propio
    `FOR UPDATE OF d SKIP LOCKED`— el subplan la SALTABA y el UPDATE se
    ejecutaba sobre el VECINO. Resultado: un evento con `attempts = 0` que
    JAMÁS se transportó moría en dead-letter (terminal, con un `last_error`
    que afirma lo contrario de lo ocurrido) mientras el veneno REAL sobrevivía
    y seguía secuestrando la cabeza — la inversión completa del detector, y la
    matanza colateral que G5-P2-2 había cerrado.

    Sin cláusula de bloqueo en el subplan quien decide es el recheck EPQ del
    propio UPDATE (G6-P2-1): la cabeza acaba de ser re-clamada ⇒ su lease es
    futuro ⇒ no es elegible ⇒ CERO retiradas. El vecino ni se mira: el subplan
    sigue apuntando a la cabeza."""
    factory, cabeza, vecino = _dos_venenos(db, monkeypatch)

    async def carrera():
        async with factory() as sB, factory() as sA:
            # B (dispatcher 2) re-clama la CABEZA y RETIENE el lock: el
            # interleaving que el beat produce con --concurrency=2.
            rows, _lease = await delivery.claim_deliveries(sB, limit=1)
            tarea = asyncio.create_task(delivery.retire_poisoned(sA))
            await asyncio.sleep(0.5)
            await sB.commit()
            n = await tarea
            await sA.commit()
            return [r.event_id for r in rows], n

    with caplog_at_error() as records:
        reclamadas, retiradas = asyncio.run(carrera())
    st = _estados(factory, [cabeza, vecino])
    assert reclamadas == [cabeza], "el claim de B no cogió la cabeza"
    assert st[vecino][0] != "dead", (
        "el subplan saltó la cabeza bloqueada y ejecutó al VECINO: un evento "
        f"con attempts={st[vecino][1]} que nunca se transportó muere en "
        "dead-letter mientras el veneno real sobrevive"
    )
    assert retiradas == 0 and st[cabeza][0] == "inflight"
    assert [r for r in records if "VENENO" in r.getMessage()] == []


def test_g7_dos_dispatchers_solapados_retiran_UNA_fila_habiendo_dos_candidatos(
    db, monkeypatch
):
    """Regresión G7-P2-1 (2ª mitad): «UNA sola por ciclo» pasaba a ser «una
    por dispatcher». Con dos candidatos y dos ciclos solapados, el `SKIP
    LOCKED` hacía que el segundo dispatcher, en vez de encontrarse la fila ya
    resuelta, se llevara al VECINO: 2 muertas por ciclo, el radio escalando
    con la concurrencia y el gate `outbox_dead` (umbral 0) en ROJO por cada
    una. El test de G6 no lo veía porque tenía UNA sola fila: sin vecino al
    que saltar, el SKIP LOCKED era indistinguible del comportamiento bueno.

    La doble retirada de la MISMA fila —lo único que el SKIP LOCKED decía
    comprar— la cierra ya la guarda de estado del WHERE (G6-P2-1): el segundo
    UPDATE encuentra la cabeza `dead` y no la toca."""
    factory, cabeza, vecino = _dos_venenos(db, monkeypatch)

    async def dos():
        async with factory() as sA, factory() as sB:
            t1 = asyncio.create_task(delivery.retire_poisoned(sA))
            await asyncio.sleep(0.3)
            t2 = asyncio.create_task(delivery.retire_poisoned(sB))
            await asyncio.sleep(0.3)
            n1 = await t1
            await sA.commit()
            n2 = await t2
            await sB.commit()
            return n1, n2

    with caplog_at_error() as records:
        n1, n2 = asyncio.run(dos())
    st = _estados(factory, [cabeza, vecino])
    muertas = [e for e in (cabeza, vecino) if st[e][0] == "dead"]
    assert n1 + n2 == 1, f"el ciclo retiró {n1 + n2} filas (LIMIT 1 anulado)"
    assert muertas == [cabeza], "la retirada no cayó sobre la CABEZA"
    assert st[vecino][0] == "inflight"
    assert len([r for r in records if "VENENO" in r.getMessage()]) == 1

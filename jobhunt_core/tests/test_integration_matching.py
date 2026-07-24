"""Matching + estado + feed (A-08) contra Postgres real.

DoD: eval_key UNIQUE (reintento no duplica); feed = evaluación VIGENTE +
no-dismissed + vacante ACTIVA, keyset por (score_final DESC, vacancy_id).
Ejecutar vía core-migrate.
"""

import asyncio
import hashlib
import math
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import jobhunt_core.harvest.providers  # noqa: F401 — registra extractor/normalizador
from jobhunt_core import embeddings, matching, profiles
from jobhunt_core.config import settings
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing

SHA_A = "a" * 40

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


class DirectionalBackend:
    """Vectores DETERMINISTAS con cosenos distintos por texto: dirección en el
    plano (cos a, sin a) con el ángulo derivado de sha256 (estable entre
    procesos — hash() no lo es)."""

    def encode_batch(self, texts):
        out = []
        for t in texts:
            a = (int(hashlib.sha256(t.encode()).hexdigest()[:8], 16) % 60) * math.pi / 180
            out.append([math.cos(a), math.sin(a)] + [0.0] * (embeddings.EMBED_DIM - 2))
        return out


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
                # Estado ANTES que evaluaciones (FK RESTRICT del current_eval).
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


def _listing(ext, title):
    return RawListing(
        external_id=ext, url=f"https://x/{ext}",
        payload={
            "title": title, "company_name": "ACME AG",
            "description": f"puesto {title}", "tags": ["t"],
        },
    )


def _setup(factory, created, titles, profile_content=None,
           model_specs=(("modelo-match", SHA_A),), backend_factory=None):
    """Fuente + ofertas (sink) + perfil + policy + modelo(s) + embeddings
    (task). Devuelve (profile_id, model_id_del_PRIMERO, policy_id,
    {título: vacancy_id}); los demás model_ids quedan en created["models"]."""
    from jobhunt_core.tasks.embedding import run_pending_task

    async def go():
        async with factory() as s:
            source_id, scope_id = uuid.uuid4(), uuid.uuid4()
            created["sources"].append(source_id)
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
            await RawListingSink().handle(
                s, str(scope_id),
                tuple(_listing(f"j{i}", t) for i, t in enumerate(titles)),
            )
            await s.commit()
            cid = await profiles.ensure_consumer(s, "tenant-match")
            created["consumers"].append(cid)
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await profiles.save_profile_revision(
                s, pid,
                profile_content or {"title": "python dev", "skills": ["python"]},
            )
            mids = []
            for mname, mver in model_specs:
                m = await embeddings.register_model(s, mname, mver)
                created["models"].append(m)
                mids.append(m)
            mid = mids[0]
            polid = await matching.ensure_policy(s, "cosine", "v1")
            created["policies"].append(polid)
            await s.commit()
            vacs = {
                r.title: r.vid
                for r in (
                    await s.execute(
                        sa.text(
                            "SELECT o.content->>'title' AS title, v.id AS vid "
                            "FROM vacancies v JOIN offer_revisions o "
                            "ON o.id = v.current_offer_revision_id "
                            "WHERE v.id IN (SELECT i.vacancy_id "
                            "FROM source_listing_incarnations i "
                            "JOIN source_listings l ON l.id = i.source_listing_id "
                            "WHERE l.source_id = :src)"
                        ),
                        {"src": source_id},
                    )
                ).all()
            }
            return pid, mid, polid, vacs

    result = asyncio.run(go())
    embeddings.set_backend_factory(
        backend_factory or (lambda name, version: DirectionalBackend())
    )
    try:
        r = run_pending_task.apply(kwargs={"limit": 100})
        assert r.successful()
    finally:
        embeddings.set_backend_factory(None)
    return result


def _evaluate(factory, pid, mid, polid):
    async def go():
        async with factory() as s:
            r = await matching.evaluate_profile(s, pid, mid, polid)
            await s.commit()
            return r

    return asyncio.run(go())


def _rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def _feed(factory, pid, limit=20, cursor=None):
    async def go():
        async with factory() as s:
            return await matching.feed(s, pid, limit=limit, cursor=cursor)

    return asyncio.run(go())


def test_evaluate_idempotent_and_state(db):
    """DoD: eval_key determinista — el reintento NO duplica; el estado apunta
    a la evaluación vigente."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python", "data eng", "qa"])

    r1 = _evaluate(factory, pid, mid, polid)
    assert (r1["status"], r1["evaluated"], r1["new_evals"]) == ("ok", 3, 3)
    state1 = {
        r.vacancy_id: r.current_eval_id
        for r in _rows(
            factory,
            "SELECT vacancy_id, current_eval_id FROM profile_vacancy_state "
            "WHERE profile_id = :p", p=pid,
        )
    }
    assert len(state1) == 3 and all(state1.values())

    r2 = _evaluate(factory, pid, mid, polid)  # reintento
    assert r2["new_evals"] == 0  # ni una evaluación duplicada
    n = _rows(
        factory,
        "SELECT count(*) AS n FROM match_evaluations WHERE profile_id = :p", p=pid,
    )
    assert n[0].n == 3
    state2 = {
        r.vacancy_id: r.current_eval_id
        for r in _rows(
            factory,
            "SELECT vacancy_id, current_eval_id FROM profile_vacancy_state "
            "WHERE profile_id = :p", p=pid,
        )
    }
    assert state2 == state1  # current_eval estable (misma fila append-only)


def test_feed_orders_filters_and_paginates(db):
    """DoD del feed: orden score DESC, excluye dismissed y vacantes NO activas
    (archivada/fundida), keyset estable."""
    factory, created = db
    pid, mid, polid, vacs = _setup(
        factory, created,
        ["backend python", "data eng", "qa manual", "contable"],
    )
    _evaluate(factory, pid, mid, polid)

    rows, _ = _feed(factory, pid)
    assert len(rows) == 4
    scores = [float(r.score_final) for r in rows]
    assert scores == sorted(scores, reverse=True)  # score DESC

    async def mutate():
        async with factory() as s:
            await matching.set_dismissed(s, pid, vacs["qa manual"], True)
            await s.execute(
                sa.text("UPDATE vacancies SET archived_at = now() WHERE id = :v"),
                {"v": vacs["contable"]},
            )
            await s.commit()

    asyncio.run(mutate())
    rows, _ = _feed(factory, pid)
    visible = {r.vacancy_id for r in rows}
    assert vacs["qa manual"] not in visible  # dismissed fuera
    assert vacs["contable"] not in visible  # archivada fuera
    assert len(rows) == 2

    # Keyset: página de 1 → cursor → el resto, sin repetir ni saltar.
    p1, cur = _feed(factory, pid, limit=1)
    assert cur is not None
    p2, _ = _feed(factory, pid, limit=20, cursor=cur)
    assert [r.vacancy_id for r in p1] + [r.vacancy_id for r in p2] == [
        r.vacancy_id for r in rows
    ]

    # Restaurar el descartado: vuelve al feed.
    async def undismiss():
        async with factory() as s:
            await matching.set_dismissed(s, pid, vacs["qa manual"], False)
            await s.commit()

    asyncio.run(undismiss())
    rows, _ = _feed(factory, pid)
    assert vacs["qa manual"] in {r.vacancy_id for r in rows}


def test_keyset_tie_breaks_by_vacancy_id(db):
    """Empate REAL de score (dos vacantes con el MISMO texto → mismo vector):
    el keyset desempata por vacancy_id sin repetir ni saltar filas."""
    factory, created = db
    pid, mid, polid, vacs = _setup(
        factory, created, ["backend python", "backend python "]
    )  # mismo texto tras strip → mismo text_hash y score
    _evaluate(factory, pid, mid, polid)
    rows, _ = _feed(factory, pid)
    assert len(rows) == 2
    assert rows[0].score_final == rows[1].score_final  # empate real
    assert rows[0].vacancy_id < rows[1].vacancy_id  # desempate estable
    p1, cur = _feed(factory, pid, limit=1)
    p2, _ = _feed(factory, pid, limit=1, cursor=cur)
    assert {p1[0].vacancy_id, p2[0].vacancy_id} == {r.vacancy_id for r in rows}
    assert p1[0].vacancy_id != p2[0].vacancy_id


def test_state_preserved_on_reevaluation(db):
    """ADR-03: el matching SOLO mueve current_eval_id/updated_at — feedback,
    dismissed, saved y notes son intocables."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python", "data eng"])
    _evaluate(factory, pid, mid, polid)
    vid = vacs["backend python"]

    async def user_state():
        async with factory() as s:
            await matching.set_dismissed(s, pid, vid, True)
            await matching.set_saved(s, pid, vid, True)
            await s.execute(
                sa.text(
                    "UPDATE profile_vacancy_state SET feedback = 'good', notes = 'mía' "
                    "WHERE profile_id = :p AND vacancy_id = :v"
                ),
                {"p": pid, "v": vid},
            )
            await s.commit()

    asyncio.run(user_state())
    _evaluate(factory, pid, mid, polid)  # re-evaluación
    row = _rows(
        factory,
        "SELECT feedback, notes, dismissed_at, saved_at, current_eval_id "
        "FROM profile_vacancy_state WHERE profile_id = :p AND vacancy_id = :v",
        p=pid, v=vid,
    )[0]
    assert (row.feedback, row.notes) == ("good", "mía")
    assert row.dismissed_at is not None and row.saved_at is not None
    assert row.current_eval_id is not None


def test_new_offer_content_appends_new_eval_and_moves_current(db):
    """Contenido nuevo de la oferta ⇒ offer_revision nueva ⇒ eval_key nuevo:
    se AÑADE evaluación (append-only, la vieja queda) y current_eval avanza."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python"])
    _evaluate(factory, pid, mid, polid)
    vid = vacs["backend python"]
    old_eval = _rows(
        factory,
        "SELECT current_eval_id FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v", p=pid, v=vid,
    )[0].current_eval_id

    # La oferta cambia de contenido → nueva canónica → nuevo embedding.
    from jobhunt_core.tasks.embedding import run_pending_task

    async def update_offer():
        async with factory() as s:
            scope_id = created["scopes"][0]
            await RawListingSink().handle(
                s, str(scope_id), (_listing("j0", "backend python senior"),)
            )
            await s.commit()

    asyncio.run(update_offer())
    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        run_pending_task.apply(kwargs={"limit": 100})
    finally:
        embeddings.set_backend_factory(None)

    r = _evaluate(factory, pid, mid, polid)
    assert r["new_evals"] == 1  # evaluación NUEVA por la revisión nueva
    evals = _rows(
        factory,
        "SELECT id FROM match_evaluations WHERE profile_id = :p AND vacancy_id = :v",
        p=pid, v=vid,
    )
    assert len(evals) == 2  # append-only: la vieja permanece
    new_eval = _rows(
        factory,
        "SELECT current_eval_id FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v", p=pid, v=vid,
    )[0].current_eval_id
    assert new_eval != old_eval  # vigente avanzada


def test_current_eval_restrict_blocks_pruning(db):
    """ADR-03 físico: la evaluación VIGENTE no puede podarse (FK RESTRICT)."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python"])
    _evaluate(factory, pid, mid, polid)

    async def prune():
        async with factory() as s:
            await s.execute(
                sa.text("DELETE FROM match_evaluations WHERE profile_id = :p"),
                {"p": pid},
            )
            await s.commit()

    with pytest.raises(IntegrityError):
        asyncio.run(prune())


def test_profile_without_vector_is_noop(db):
    """Sin vector del perfil para el modelo: 'sin_vector', nada roto."""
    factory, created = db

    async def go():
        async with factory() as s:
            cid = await profiles.ensure_consumer(s, "tenant-match")
            created["consumers"].append(cid)
            pid = await profiles.upsert_profile(s, cid, "user-sin-vector")
            await profiles.save_profile_revision(s, pid, {"title": "dev"})
            mid = await embeddings.register_model(s, "modelo-match", SHA_A)
            created["models"].append(mid)
            polid = await matching.ensure_policy(s, "cosine", "v1")
            created["policies"].append(polid)
            await s.commit()
            r = await matching.evaluate_profile(s, pid, mid, polid)
            await s.commit()
            return r

    r = asyncio.run(go())
    assert r == {"status": "sin_vector", "evaluated": 0, "new_evals": 0}


def test_two_active_models_canonical_current_is_deterministic(db):
    """Auditoría A-08 (P2): con DOS modelos 384 activos, current_eval_id lo
    fija SIEMPRE el evaluador CANÓNICO (primer (modelo, política) en orden
    (name, version)) — el feed muestra el mismo score en cada run; el otro
    modelo corre en SOMBRA (append-only)."""
    from jobhunt_core.tasks.matching import run_profile_task

    class OffsetBackend:
        def __init__(self, offset):
            self._offset = offset

        def encode_batch(self, texts):
            out = []
            for t in texts:
                a = ((int(hashlib.sha256(t.encode()).hexdigest()[:8], 16) % 60)
                     + self._offset) * math.pi / 180
                out.append([math.cos(a), math.sin(a)] + [0.0] * (embeddings.EMBED_DIM - 2))
            return out

    factory, created = db
    pid, mid_a, polid, vacs = _setup(
        factory, created, ["backend python", "data eng"],
        model_specs=(("modelo-a", SHA_A), ("modelo-b", "b" * 40)),
        backend_factory=lambda name, version: OffsetBackend(0 if name == "modelo-a" else 25),
    )
    mid_b = created["models"][-1]

    for _ in range(2):  # dos runs: el resultado NO depende del orden físico
        r = run_profile_task.apply(args=[str(pid)])
        assert r.successful()
        cur = _rows(
            factory,
            "SELECT e.model_id, e.score_final FROM profile_vacancy_state s "
            "JOIN match_evaluations e ON e.id = s.current_eval_id "
            "WHERE s.profile_id = :p", p=pid,
        )
        assert {c.model_id for c in cur} == {mid_a}  # SIEMPRE el canónico

    per_vac = _rows(
        factory,
        "SELECT vacancy_id, count(*) AS n FROM match_evaluations "
        "WHERE profile_id = :p GROUP BY vacancy_id", p=pid,
    )
    assert all(r.n == 2 for r in per_vac)  # la sombra (modelo-b) SÍ se apendea
    sombra = _rows(
        factory,
        "SELECT count(*) AS n FROM match_evaluations "
        "WHERE profile_id = :p AND model_id = :m", p=pid, m=mid_b,
    )
    assert sombra[0].n == 2


def test_feed_limit_zero_is_safe(db):
    """Auditoría A-08 (P3): feed(limit=0) devuelve ([], None), sin IndexError."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python"])
    _evaluate(factory, pid, mid, polid)
    rows, cur = _feed(factory, pid, limit=0)
    assert (rows, cur) == ([], None)


def test_candidates_beyond_default_ef_search(db):
    """Auditoría A-08 (P2): hnsw.ef_search por defecto (40) truncaría el top-K
    en silencio — con 45 vacantes activas y limit=100 se evalúan las 45."""
    factory, created = db
    titles = [f"puesto especializado {i}" for i in range(45)]
    pid, mid, polid, vacs = _setup(factory, created, titles)
    r = _evaluate(factory, pid, mid, polid)
    assert r["evaluated"] == 45  # sin truncado silencioso del ANN


def test_profile_revision_change_appends_new_eval(db):
    """Auditoría A-08 (P3): nueva revisión VIGENTE del perfil ⇒ eval_key nuevo.
    Sin embedding aún → 'sin_vector' (no-op); tras embeber → append + current
    avanza a la evaluación de la revisión nueva."""
    from jobhunt_core.tasks.embedding import run_pending_task

    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python"])
    _evaluate(factory, pid, mid, polid)

    async def new_revision():
        async with factory() as s:
            rid = await profiles.save_profile_revision(
                s, pid, {"title": "arquitecto cloud", "skills": ["aws"]}
            )
            await s.commit()
            return rid

    rid2 = asyncio.run(new_revision())
    r = _evaluate(factory, pid, mid, polid)
    assert r["status"] == "sin_vector"  # la vigente nueva aún no tiene vector

    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        run_pending_task.apply(kwargs={"limit": 100})
    finally:
        embeddings.set_backend_factory(None)
    r2 = _evaluate(factory, pid, mid, polid)
    assert r2["new_evals"] == 1  # eval NUEVA por la revisión nueva del perfil
    evals = _rows(
        factory,
        "SELECT profile_revision_id FROM match_evaluations WHERE profile_id = :p",
        p=pid,
    )
    assert len(evals) == 2  # append-only
    cur = _rows(
        factory,
        "SELECT e.profile_revision_id FROM profile_vacancy_state s "
        "JOIN match_evaluations e ON e.id = s.current_eval_id "
        "WHERE s.profile_id = :p", p=pid,
    )
    assert cur[0].profile_revision_id == rid2  # current avanzó a la nueva


def test_inactive_policy_excluded(db):
    """Auditoría A-08 (P3): una política desactivada (re-declaración con
    active=False) deja de evaluarse; ensure_policy devuelve la MISMA fila."""
    from jobhunt_core.tasks.matching import run_profile_task

    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python"])
    key = f"modelo-match@{SHA_A}/cosine@v1"
    r1 = run_profile_task.apply(args=[str(pid)])
    assert key in r1.result["results"]

    async def deactivate():
        async with factory() as s:
            same = await matching.ensure_policy(s, "cosine", "v1", active=False)
            await s.commit()
            return same

    assert asyncio.run(deactivate()) == polid  # misma fila, active declarativo
    r2 = run_profile_task.apply(args=[str(pid)])
    assert r2.result["results"] == {}  # sin políticas activas: nada que evaluar


def test_matching_task_end_to_end_and_not_found(db):
    from jobhunt_core.tasks.matching import run_profile_task

    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python", "data eng"])
    r = run_profile_task.apply(args=[str(pid)])
    assert r.successful()
    assert r.result["status"] == "ok"
    key = f"modelo-match@{SHA_A}/cosine@v1"
    assert r.result["results"][key]["evaluated"] == 2

    r2 = run_profile_task.apply(args=[str(uuid.uuid4())])
    assert r2.successful()
    assert r2.result["status"] == "not_found"  # permanente, sin retry
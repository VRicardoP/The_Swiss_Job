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
from jobhunt_core.tests import dbcleanup

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
            await dbcleanup.purge_consumer_graph(s, created["consumers"])
            await dbcleanup.purge_source_graph(s, created["sources"], created["scopes"])
            await dbcleanup.purge_policies(s, created["policies"])
            for mid in created["models"]:
                await dbcleanup.purge_model(s, mid)
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
                            # ORDER BY: sin él, el orden del dict seguía el
                            # orden físico del heap (compartido entre tests) y
                            # list(vacs.values())[0] cambiaba según qué test
                            # corriera antes — rojo solo en suite completa.
                            "SELECT o.content->>'title' AS title, v.id AS vid "
                            "FROM vacancies v JOIN offer_revisions o "
                            "ON o.id = v.current_offer_revision_id "
                            "JOIN source_listing_incarnations i "
                            "ON i.vacancy_id = v.id "
                            "JOIN source_listings l ON l.id = i.source_listing_id "
                            "WHERE l.source_id = :src "
                            "ORDER BY l.external_id"
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


def _evaluate(factory, pid, mid, polid, limit=100, move=True):
    # P1-3: evaluate_profile toma la FACTORY (trifásico, commit propio).
    async def go():
        return await matching.evaluate_profile(
            factory, pid, mid, polid, limit=limit, move_current=move)

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


def test_feed_no_phantom_cursor_on_exact_last_page(db):
    """REGRESIÓN P3 rev. externa integral: si el resto de filas es EXACTAMENTE `limit`, el feed NO
    debe devolver cursor (la página siguiente saldría VACÍA). Internamente se pide limit+1 para
    distinguir 'hay más' de 'esta es la última'."""
    factory, created = db
    pid, mid, polid, _vacs = _setup(factory, created, ["backend python", "data eng"])
    _evaluate(factory, pid, mid, polid)
    rows, _ = _feed(factory, pid)
    n = len(rows)
    assert n == 2
    # limit == total → última página EXACTA → SIN cursor fantasma.
    page, cur = _feed(factory, pid, limit=n)
    assert len(page) == n and cur is None
    # limit < total → SÍ cursor; la página siguiente trae el resto y ya no da cursor.
    p1, cur1 = _feed(factory, pid, limit=n - 1)
    assert len(p1) == n - 1 and cur1 is not None
    p2, cur2 = _feed(factory, pid, limit=20, cursor=cur1)
    assert len(p2) == 1 and cur2 is None


def test_smaller_canonical_top_k_retires_old_feed_pointers(db):
    """El feed es el top-K vigente; el estado del usuario sobre excluidos vive."""
    factory, created = db
    pid, mid, polid, _vacs = _setup(
        factory, created, ["backend python", "data eng", "qa manual"]
    )
    _evaluate(factory, pid, mid, polid, limit=3)

    rows, _ = _feed(factory, pid)
    excluded = rows[-1].vacancy_id

    async def add_user_state():
        async with factory() as s:
            await matching.set_saved(s, pid, excluded, True)
            await s.execute(
                sa.text(
                    "UPDATE profile_vacancy_state SET notes = 'conservar' "
                    "WHERE profile_id = :p AND vacancy_id = :v"
                ),
                {"p": pid, "v": excluded},
            )
            await s.commit()

    asyncio.run(add_user_state())
    _evaluate(factory, pid, mid, polid, limit=1)

    rows, _ = _feed(factory, pid)
    assert len(rows) == 1 and rows[0].vacancy_id != excluded

    state = _rows(
        factory,
        "SELECT current_eval_id, saved_at, notes "
        "FROM profile_vacancy_state WHERE profile_id = :p AND vacancy_id = :v",
        p=pid, v=excluded,
    )[0]
    assert state.current_eval_id is None
    assert state.saved_at is not None
    assert state.notes == "conservar"


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
        return await matching.evaluate_profile(factory, pid, mid, polid)

    r = asyncio.run(go())
    assert r == {
        "status": "sin_vector", "evaluated": 0, "new_evals": 0,
        "moved_current": False,
    }


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


def test_canonical_skips_model_without_offer_embeddings(db):
    """Rev. A-08 #1 (repro): el modelo A tiene vector de perfil pero CERO
    embeddings de ofertas → 'ok/evaluated=0' NO consume el canónico; B evalúa
    y mueve el estado — el feed no queda vacío."""
    from jobhunt_core.tasks.matching import run_profile_task

    factory, created = db
    pid, mid_a, polid, vacs = _setup(
        factory, created, ["backend python", "data eng"],
        model_specs=(("modelo-a", SHA_A), ("modelo-b", "b" * 40)),
    )
    mid_b = created["models"][-1]

    async def strip_model_a_offer_vectors():
        async with factory() as s:
            await s.execute(
                sa.text("DELETE FROM offer_embeddings WHERE model_id = :m"),
                {"m": mid_a},
            )
            await s.commit()

    asyncio.run(strip_model_a_offer_vectors())
    r = run_profile_task.apply(args=[str(pid)])
    assert r.successful()
    key_a = f"modelo-a@{SHA_A}/cosine@v1"
    assert r.result["results"][key_a]["evaluated"] == 0  # A sin candidatos
    assert r.result["results"][key_a]["moved_current"] is False
    cur = _rows(
        factory,
        "SELECT e.model_id FROM profile_vacancy_state s "
        "JOIN match_evaluations e ON e.id = s.current_eval_id "
        "WHERE s.profile_id = :p", p=pid,
    )
    assert len(cur) == 2 and {c.model_id for c in cur} == {mid_b}  # B movió
    rows, _ = _feed(factory, pid)
    assert len(rows) == 2  # el feed NO queda vacío


def _seed_orphan_embeddings(factory, mid, profile_vec_text, n):
    """Embeddings HISTÓRICOS/huérfanos (text_hash sin canónica vigente) máxima-
    mente cercanos al vector del perfil: consumen el scan ANN filtrado."""

    async def go():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO offer_embeddings (text_hash, model_id, vector) "
                    "VALUES (:th, :m, CAST(:v AS vector))"
                ),
                [
                    {"th": f"{i:064x}", "m": mid, "v": profile_vec_text}
                    for i in range(n)
                ],
            )
            await s.commit()

    asyncio.run(go())


def _profile_vec_text(factory, pid, mid):
    return _rows(
        factory,
        "SELECT pe.vector::text AS v FROM profile_embeddings pe "
        "WHERE pe.profile_id = :p AND pe.model_id = :m", p=pid, m=mid,
    )[0].v


def test_ann_starvation_by_orphan_embeddings(db):
    """Rev. A-08 #2 (repro): 200 embeddings huérfanos MÁS CERCANOS que
    cualquier oferta activa consumían el scan HNSW filtrado (0 candidatos con
    ef_search=40). strict_order sigue escaneando: se evalúan las 30 activas.
    Y el plan del ANN usa de verdad el índice (EXPLAIN)."""
    factory, created = db
    titles = [f"puesto especializado {i}" for i in range(30)]
    pid, mid, polid, vacs = _setup(factory, created, titles)
    vec = _profile_vec_text(factory, pid, mid)
    _seed_orphan_embeddings(factory, mid, vec, 200)

    r = _evaluate(factory, pid, mid, polid, limit=20)
    assert r["evaluated"] == 20  # sin inanición: el filtro no vacía el top-K

    async def explain():
        async with factory() as s:
            await s.execute(sa.text("SET LOCAL hnsw.ef_search = 40"))
            await s.execute(sa.text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
            # Con pocos cientos de filas el planner prefiere Sort exacto (más
            # barato); se desactiva el seq scan para VALIDAR que el camino del
            # índice HNSW existe y casa con la forma de la query.
            await s.execute(sa.text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                (
                    await s.execute(
                        sa.text("EXPLAIN " + matching.CANDIDATES_SQL),
                        {"vec": vec, "mid": mid, "k": 20},
                    )
                ).scalars().all()
            )
            return plan

    plan = asyncio.run(explain())
    assert "Index Scan using" in plan  # el ANN usa el HNSW de la partición


def test_ann_fallback_exact_when_scan_cap_hit(db, monkeypatch):
    """Rev. A-08 #2 (fallback): si el scan iterativo agota su tope de tuplas
    entre huérfanos sin llenar el top-K, el FALLBACK EXACTO responde igual."""
    factory, created = db
    titles = [f"puesto especializado {i}" for i in range(30)]
    pid, mid, polid, vacs = _setup(factory, created, titles)
    vec = _profile_vec_text(factory, pid, mid)
    _seed_orphan_embeddings(factory, mid, vec, 200)

    monkeypatch.setattr(matching, "MAX_SCAN_TUPLES", 50)  # fuerza la inanición
    r = _evaluate(factory, pid, mid, polid, limit=20)
    assert r["evaluated"] == 20  # el exacto llena el top-K igualmente


def test_limit_bounds_are_validated_and_huge_limit_works(db):
    """Rev. A-08 2ª P2#1: limit fuera de rango no llega a SQL; limit > 1000
    funciona (ef_search se acota a 1000 y el objetivo real lo cubre)."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python", "data eng"])
    for bad in (0, -5):
        with pytest.raises(ValueError, match="top-K"):
            _evaluate(factory, pid, mid, polid, limit=bad)
    r = _evaluate(factory, pid, mid, polid, limit=2000)
    assert r["evaluated"] == 2  # sin error del GUC; evalúa todo lo elegible


def test_small_corpus_single_ann_pass_and_fallback_counted(db, monkeypatch):
    """Rev. A-08 2ª P2#2 + rev. A-09 #7 (falso verde): se CUENTAN las
    ejecuciones reales de CANDIDATES_SQL — corpus < limit ⇒ UNA sola pasada;
    inanición forzada ⇒ exactamente dos (ANN + fallback exacto)."""
    from sqlalchemy import event

    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python", "data eng"])

    counter = {"n": 0}
    engine2 = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory2 = async_sessionmaker(engine2, expire_on_commit=False)

    def count_candidates(conn, cursor, statement, parameters, context, executemany):
        if "ORDER BY oe.vector" in statement:
            counter["n"] += 1

    event.listen(engine2.sync_engine, "before_cursor_execute", count_candidates)

    def run_eval():
        async def go():
            return await matching.evaluate_profile(
                factory2, pid, mid, polid, limit=100)

        return asyncio.run(go())

    r = run_eval()
    assert r["evaluated"] == 2
    assert counter["n"] == 1  # corpus < limit: SIN segunda búsqueda

    # Disparo del fallback INDEPENDIENTE del plan: a esta escala el planner
    # arranca desde vacancies (plan exacto) y el HNSW no puede inanirse — se
    # fuerza una primera pasada VACÍA (< objetivo) parcheando el SQL; ambas
    # pasadas ejecutan el MISMO statement, así que el contador demuestra el
    # control de flujo real (la inanición FÍSICA a escala la validó la
    # revisión externa con 20k huérfanos y 1k activas).
    counter["n"] = 0
    monkeypatch.setattr(
        matching, "CANDIDATES_SQL",
        matching.CANDIDATES_SQL.replace(
            "WHERE v.archived_at", "WHERE false AND v.archived_at"
        ),
    )
    r = run_eval()
    assert r["evaluated"] == 0
    assert counter["n"] == 2  # ANN corta (< objetivo) → fallback EJECUTADO

    async def dispose():
        await engine2.dispose()

    asyncio.run(dispose())


def test_state_timestamps_never_regress_across_overlapping_txs(db):
    """Rev. A-08 #3 (repro): T1 abre tx (now() congelado), T2 descarta y
    commitea, T1 guarda DESPUÉS — con clock_timestamp()+GREATEST el estado
    nunca retrocede: updated_at/saved_at >= dismissed_at."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, ["backend python"])
    vid = vacs["backend python"]

    async def overlap():
        async with factory() as s1, factory() as s2:
            await s1.execute(sa.text("SELECT 1"))  # abre la tx de T1 (vieja)
            await asyncio.sleep(0.05)
            await matching.set_dismissed(s2, pid, vid, True)
            await s2.commit()
            await asyncio.sleep(0.05)
            await matching.set_saved(s1, pid, vid, True)  # T1 escribe la última
            await s1.commit()

    asyncio.run(overlap())
    row = _rows(
        factory,
        "SELECT dismissed_at, saved_at, updated_at FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v", p=pid, v=vid,
    )[0]
    assert row.saved_at >= row.dismissed_at  # hora real de escritura
    assert row.updated_at >= row.dismissed_at  # jamás retrocede


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


def test_hybrid_policy_recovers_lexical_candidate_outside_ann_top_k(db):
    """El híbrido recupera una oferta explícita que el top-K ANN deja fuera."""
    factory, created = db
    pid, mid, cosine_id, vacs = _setup(
        factory, created,
        ["warehouse accountant", "kubernetes platform engineer"],
        profile_content={
            "title": "kubernetes engineer", "skills": ["kubernetes"],
        },
    )

    async def configure():
        async with factory() as s:
            hybrid_id = await matching.ensure_policy(
                s, matching.HYBRID_POLICY_NAME, matching.HYBRID_POLICY_VERSION,
                weights=matching.HYBRID_POLICY_WEIGHTS,
            )
            created["policies"].append(hybrid_id)
            # La valla de canonicidad (P1-C) solo deja mover el feed a la
            # política canónica: para que el híbrido lo mueva, el coseno del
            # _setup deja de estar activo. Desde el contrato 2026-09-04 una
            # política no canónica con move_current se DESCARTA entera, así
            # que el eval de coseno de abajo va en SOMBRA explícita.
            await matching.ensure_policy(s, "cosine", "v1", active=False)
            profile_vec = [1.0, 0.0] + [0.0] * (embeddings.EMBED_DIM - 2)
            lexical_vec = [0.0, 1.0] + [0.0] * (embeddings.EMBED_DIM - 2)
            await s.execute(
                sa.text(
                    "UPDATE profile_embeddings SET vector = CAST(:v AS vector) "
                    "WHERE profile_id = :p AND model_id = :m"
                ),
                {"v": str(profile_vec), "p": pid, "m": mid},
            )
            hashes = {
                row.title: row.text_hash
                for row in (
                    await s.execute(
                        sa.text(
                            "SELECT content->>\047title\047 AS title, text_hash "
                            "FROM offer_revisions WHERE vacancy_id = ANY(:ids)"
                        ),
                        {"ids": list(vacs.values())},
                    )
                ).all()
            }
            for title, vector in (
                ("warehouse accountant", profile_vec),
                ("kubernetes platform engineer", lexical_vec),
            ):
                await s.execute(
                    sa.text(
                        "UPDATE offer_embeddings SET vector = CAST(:v AS vector) "
                        "WHERE text_hash = :h AND model_id = :m"
                    ),
                    {"v": str(vector), "h": hashes[title], "m": mid},
                )
            await s.commit()
            return hybrid_id

    hybrid_id = asyncio.run(configure())
    assert _evaluate(
        factory, pid, mid, cosine_id, limit=1, move=False)["evaluated"] == 1
    cosine_vacancy = _rows(
        factory,
        "SELECT vacancy_id FROM match_evaluations "
        "WHERE profile_id = :p AND scoring_policy_id = :sp",
        p=pid, sp=cosine_id,
    )[0].vacancy_id
    assert cosine_vacancy == vacs["warehouse accountant"]

    # Una RELATIVA ya no puede mover el feed (valla pair_absolute, Fase 1
    # cierre definitivo): el rescate léxico se afirma sobre el cálculo directo.
    assert _evaluate(
        factory, pid, mid, hybrid_id, limit=1, move=False)["evaluated"] == 1

    async def calculo():
        async with factory() as s:
            return await matching.compute_policy_feed(
                s, pid, mid, hybrid_id, limit=1)

    filas = asyncio.run(calculo())["rows"]
    assert filas[0]["vacancy_id"] == vacs["kubernetes platform engineer"]
    assert filas[0]["score_parts"]["algorithm"] == "hybrid_rrf_v1"
    assert filas[0]["score_parts"]["lexical_rank"] == 1


def test_policy_version_rejects_different_weights(db):
    """Una versión publicada no puede cambiar de algoritmo silenciosamente."""
    factory, created = db

    async def conflict():
        async with factory() as s:
            policy_id = await matching.ensure_policy(
                s, "immutable-policy", "v1", weights={"algorithm": "cosine"}
            )
            created["policies"].append(policy_id)
            await s.commit()
            with pytest.raises(ValueError, match="weights distintos"):
                await matching.ensure_policy(
                    s, "immutable-policy", "v1", weights={"algorithm": "other"}
                )

    asyncio.run(conflict())


def test_v4_reproduce_exactamente_a_v3_y_su_receta_es_reconstruible(db):
    """P1-A (revisión externa 2026-09-02): v2 y v3 persisten el MISMO JSON
    ({"algorithm": "hybrid_rrf_v2"}) — el peso 0.25 solo existía en el binario
    y la diferencia 1.15→0.25 no era reconstruible desde datos. v4 lleva la
    receta completa en la fila, el evaluador la valida y deriva de ella el
    comportamiento, y con el mismo peso produce EXACTAMENTE el mismo orden y
    las mismas puntuaciones que v3."""
    factory, created = db
    pid, mid, _, vacs = _setup(
        factory, created,
        ["python backend developer", "senior python engineer",
         "data engineer python sql", "warehouse operative",
         "kubernetes platform engineer", "frontend react developer"],
        profile_content={"title": "python developer", "skills": ["python", "sql"]},
    )
    # El defecto documentado: la fila de v2/v3 no contiene el peso…
    assert "lexical_weight" not in matching.HYBRID2_POLICY_WEIGHTS
    # …y la de v4 sí: receta completa, reconstruible desde datos.
    assert matching.HYBRID4_POLICY_WEIGHTS == {
        "algorithm": "hybrid_rrf", "lexical_query": "v2",
        "lexical_weight": 0.25, "rrf_k": 60,
    }

    async def policies():
        async with factory() as s:
            v3 = await matching.ensure_policy(
                s, matching.HYBRID_POLICY_NAME, "v3",
                weights=matching.HYBRID2_POLICY_WEIGHTS)
            v4 = await matching.ensure_policy(
                s, matching.HYBRID_POLICY_NAME, matching.HYBRID4_POLICY_VERSION,
                weights=matching.HYBRID4_POLICY_WEIGHTS)
            created["policies"] += [v3, v4]
            await s.commit()
            return v3, v4

    v3_id, v4_id = asyncio.run(policies())
    n3 = _evaluate(factory, pid, mid, v3_id, move=False)["evaluated"]
    n4 = _evaluate(factory, pid, mid, v4_id, move=False)["evaluated"]
    assert n3 == n4 > 0

    def ranking(spid):
        return _rows(
            factory,
            "SELECT vacancy_id, score_final, scores FROM match_evaluations "
            "WHERE profile_id = :p AND scoring_policy_id = :sp "
            "ORDER BY score_final DESC, vacancy_id", p=pid, sp=spid)

    r3, r4 = ranking(v3_id), ranking(v4_id)
    assert [(r.vacancy_id, r.score_final) for r in r3] == \
        [(r.vacancy_id, r.score_final) for r in r4]
    # v4 deja constancia de su receta en CADA fila; v3 no puede (ese es el bug).
    assert all(
        r.scores.get("recipe") == matching.HYBRID4_POLICY_WEIGHTS for r in r4
    )
    assert all("recipe" not in r.scores for r in r3)


def test_una_receta_no_soportada_no_evalua_nada(db):
    """Una fila hybrid_rrf cuya receta el binario no implementa (aquí un rrf_k
    distinto) es un error nombrable ANTES de evaluar, no una evaluación bajo
    régimen mezclado como la del feed_n=2380."""
    factory, created = db
    pid, mid, _, _ = _setup(factory, created, ["python developer"])

    async def go():
        async with factory() as s:
            mala = dict(matching.HYBRID4_POLICY_WEIGHTS, rrf_k=61)
            polid = await matching.ensure_policy(
                s, matching.HYBRID_POLICY_NAME, "v99", weights=mala)
            created["policies"].append(polid)
            await s.commit()
        with pytest.raises(ValueError, match="rrf_k"):
            await matching.evaluate_profile(
                factory, pid, mid, polid, move_current=False)
        async with factory() as s:
            n = (await s.execute(sa.text(
                "SELECT count(*) FROM match_evaluations "
                "WHERE scoring_policy_id = :sp"), {"sp": polid})).scalar_one()
            assert n == 0

    asyncio.run(go())


def test_un_worker_pre_flip_no_puede_restaurar_el_feed_antiguo(db):
    """P1-C (revisión externa 2026-09-02): un worker que leyó «coseno es
    canónico» en una transacción YA CERRADA, se pausó, y retoma DESPUÉS de la
    promoción, evaluaba coseno con move_current=True y devolvía el feed al
    régimen antiguo. La valla de canonicidad comprueba y escribe en la MISMA
    transacción (FOR SHARE sobre la canónica), y el flip
    (declare_active_policies) actualiza todas las filas, serializándose con
    ella. Desde la revisión 2026-09-04 la valla vive en la revalidación
    ATÓMICA de la tupla y su deriva DESCARTA el resultado completo (sin
    escribir nada), en vez de degradar a «registrada sin mover»."""
    factory, created = db
    pid, mid, cosine_id, _ = _setup(
        factory, created, ["python developer", "warehouse operative"])
    # Worker A leyó las políticas: coseno canónico. Feed inicial bajo coseno.
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True

    # Operador: alta de la candidata ABSOLUTA y flip atómico al conjunto
    # exacto {candidata} (una relativa ya no puede ser canónica).
    async def promote():
        async with factory() as s:
            cand = await matching.ensure_policy(
                s, "cosine-nueva", "v1", weights={"algorithm": "cosine"},
                active=False)
            created["policies"].append(cand)
            await matching.declare_active_policies(s, [cand])
            await s.commit()
            return cand

    v4_id = asyncio.run(promote())
    # Worker B rematerializa el feed bajo la candidata (ahora canónica).
    assert _evaluate(factory, pid, mid, v4_id)["moved_current"] is True

    # Worker A retoma con su decisión caducada: la revalidación lo descarta
    # ENTERO (sin escribir ni mover — contrato 2026-09-04).
    r = _evaluate(factory, pid, mid, cosine_id)
    assert r["status"] == "descartado_por_deriva"
    assert r["moved_current"] is False
    refs = _rows(
        factory,
        "SELECT DISTINCT e.scoring_policy_id AS spid "
        "FROM profile_vacancy_state s "
        "JOIN match_evaluations e ON e.id = s.current_eval_id "
        "WHERE s.profile_id = :p AND s.current_eval_id IS NOT NULL", p=pid)
    assert [str(x.spid) for x in refs] == [str(v4_id)]


def test_declarar_un_conjunto_activo_invalido_aborta(db):
    """P1-D: declarar ids inexistentes (o un conjunto vacío) es error — el
    conjunto activo cometido debe coincidir EXACTAMENTE con lo declarado."""
    factory, created = db

    async def go():
        async with factory() as s:
            polid = await matching.ensure_policy(s, "cosine", "v1")
            created["policies"].append(polid)
            await s.commit()
            with pytest.raises(ValueError, match="no satisfecha"):
                await matching.declare_active_policies(s, [polid, uuid.uuid4()])
            await s.rollback()
            with pytest.raises(ValueError, match="vac\u00edo"):
                await matching.declare_active_policies(s, [])
            # tras los abortos, el conjunto activo declarado sigue funcionando
            await matching.declare_active_policies(s, [polid])
            await s.commit()

    asyncio.run(go())


def test_el_redeploy_no_cambia_la_canonicidad(db):
    """P1-D (revisión externa 2026-09-02): el runbook del NAS desactivaba
    coseno y reactivaba hybrid-rrf/v1 (0.098/0.000 medidos) en cada
    despliegue — doble autoridad sobre la canonicidad. El bootstrap asegura
    FILAS del catálogo sin tocar la activación; solo declare_active_policies
    la mueve. Redeploy pre-promoción preserva {cosine, v4-sombra};
    post-promoción, {v4}; tras rollback, {cosine}; y NINGÚN redeploy puede
    reactivar v1/v2/v3."""
    factory, created = db

    async def go():
        async with factory() as s:
            ids = await matching.bootstrap_policy_catalog(s)
            created["policies"] += list(ids.values())
            cos = ids[("cosine-baseline", "v1")]
            v4 = ids[
                (matching.HYBRID_POLICY_NAME, matching.HYBRID4_POLICY_VERSION)
            ]
            legacy = {
                str(ids[(matching.HYBRID_POLICY_NAME, v)])
                for v in ("v1", "v2", "v3")
            }

            async def activas():
                return {
                    str(r[0]) for r in (await s.execute(
                        sa.text(
                            "SELECT id FROM scoring_policies WHERE active "
                            "AND id = ANY(CAST(:c AS uuid[]))"),
                        {"c": [str(x) for x in ids.values()]},
                    )).all()
                }

            # Estados operativos LEGALES (canónica absoluta); el redeploy
            # (bootstrap) preserva cada uno. {v4} en solitario ya no es un
            # estado legal: la valla pair_absolute lo rechaza.
            for conjunto in ([cos, v4], [cos]):
                await matching.declare_active_policies(s, conjunto)
                await matching.bootstrap_policy_catalog(s)  # ← redeploy
                assert await activas() == {str(x) for x in conjunto}
                assert not (await activas()) & legacy
            with pytest.raises(ValueError, match="RELATIVO"):
                await matching.declare_active_policies(s, [v4])
            await s.rollback()

    asyncio.run(go())

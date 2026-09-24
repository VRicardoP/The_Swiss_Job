"""Reproducciones externas; ejecutar montado bajo jobhunt_core/tests.

Este fichero son TRES sondas independientes pegadas una tras otra —líneas 1, 75
y 116, cada una con su docstring y su propio bloque de imports—. No es un módulo:
nada lo importa y cada bloque se ejecuta por separado montado bajo los tests del
core. De ahí que `asyncio`, `db` y `_setup` aparezcan «redefinidos» y que haya
imports a media altura: es la forma del registro, no deuda.

La supresión es de fichero a propósito. Anotar las 26 apariciones una a una daría
a entender que cada repetición es una decisión de código, y no lo es.
"""

# ruff: noqa: E402, F401, F811
import asyncio
import time
from types import SimpleNamespace

import sqlalchemy as sa

from jobhunt_core import matching, dev_eval
from jobhunt_core.tests.test_integration_matching import db, _setup, _evaluate
from jobhunt_core.tests.test_integration_cross_encoder import _xenc_policy, _StubEngine


def test_new_exclusions_empty_feed_must_remove_previous_results(db):
    factory, created = db
    pid, mid, pol, vacs = _setup(
        factory, created, ["python developer", "python engineer"]
    )
    assert _evaluate(factory, pid, mid, pol)["evaluated"] == 2

    async def check():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO profile_exclusions (profile_id,kind,pattern) VALUES (:p,'title_contains','python')"
                ),
                {"p": pid},
            )
            await s.commit()
        result = await matching.evaluate_profile(factory, pid, mid, pol)
        assert result["evaluated"] == 0
        async with factory() as s:
            rows, _ = await matching.feed(s, pid)
        assert rows == [], f"Se siguen sirviendo {len(rows)} ofertas excluidas"

    asyncio.run(check())


def test_fragment_cannot_overrun_soft_limit_in_second_batch(monkeypatch):
    from jobhunt_core.tasks.materialize import MATERIALIZE_FRAGMENT_SECONDS

    clock = [0.0]
    scored = []
    items = list(range(128))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, *args, **kwargs):
            return SimpleNamespace(scalar_one=lambda: "consumer")

        async def commit(self):
            pass

    async def prepare(*args, **kwargs):
        clock[0] += 135.0
        remaining = [i for i in items if i not in scored]
        return {
            "status": "ok_prep",
            "profile_revision_id": "rev",
            "prep": {"misses": remaining, "documentos": remaining},
        }

    def infer(prep):
        clock[0] += 16.0 * len(prep["misses"])
        return {i: i for i in prep["misses"]}

    async def persist(s, p, rows, *args):
        scored.extend(rows)

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(matching, "compute_policy_feed", prepare)
    monkeypatch.setattr(matching, "_ce_score_misses", infer)
    monkeypatch.setattr(matching, "_ce_assemble", lambda prep, fresh: list(fresh))
    monkeypatch.setattr(matching, "_persist_eval_rows", persist)
    result = asyncio.run(
        matching.materialize_misses(
            Session, "p", "m", "pol", budget_seconds=MATERIALIZE_FRAGMENT_SECONDS
        )
    )
    assert clock[0] < 1800, (
        f"fragmento terminó en {clock[0]} segundos simulados: {result}"
    )


def test_delegated_ce_does_not_keep_projector_recovery_on_forever(db):
    from jobhunt_core.shadow import projector

    factory, created = db
    pid, mid, cosine, vacs = _setup(factory, created, ["python developer"])
    ce_pol = _xenc_policy(factory, created)

    async def check():
        async with factory() as s:
            await matching.declare_active_policies(s, [cosine, ce_pol])
            await s.commit()
        await projector._evaluate_and_record(factory, pid)
        await projector._evaluate_and_record(factory, pid)
        async with factory() as s:
            result = await s.execute(
                sa.text(projector._RECOVERY_NEEDED_SQL).bindparams(
                    sa.bindparam("excluded", expanding=True),
                    sa.bindparam("evaluated", expanding=True),
                ),
                {
                    "excluded": [projector._NO_PROFILE],
                    "evaluated": [projector._NO_PROFILE],
                    "dim": 384,
                    "cap": 200,
                },
            )
            pending = [r.id for r in result.all()]
        assert pid not in pending, (
            "El CE delegado mantiene permanentemente encendida la recuperación"
        )

    asyncio.run(check())


"""Interleaving real entre materializacion y publicacion."""
import asyncio

from jobhunt_core import matching, profiles, cross_encoder as ce, embeddings
from jobhunt_core.tasks.embedding import _run_pending_impl
from jobhunt_core.tasks import materialize
from jobhunt_core.tests.test_integration_matching import db, _setup, DirectionalBackend
from jobhunt_core.tests.test_integration_cross_encoder import _xenc_policy, _StubEngine


def test_real_cv_edit_reopens_unbudgeted_inference(db, monkeypatch):
    factory, created = db
    pid, mid, _, _ = _setup(factory, created, ["python developer", "python engineer"])
    pol = _xenc_policy(factory, created)
    engine = _StubEngine()
    original = matching.materialize_misses
    before_publication = []

    async def materialize_then_edit(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert result["status"] == "ok" and result["remaining"] == 0
        before_publication.append(engine.docs_scored)
        async with factory() as session:
            rev = await profiles.current_revision(session, pid)
            await profiles.save_profile_revision(
                session, pid, dict(rev.content, target_roles=["Backend Developer"])
            )
            await session.commit()
        await _run_pending_impl(100, session_factory=factory)
        return result

    monkeypatch.setattr(matching, "materialize_misses", materialize_then_edit)
    ce.set_engine_factory(lambda m, r: engine)
    embeddings.set_backend_factory(lambda m, r: DirectionalBackend())
    try:
        result = asyncio.run(materialize._con_factory(factory, pid, pol, 60.0))
    finally:
        ce.set_engine_factory(None)
        embeddings.set_backend_factory(None)
    extra = engine.docs_scored - before_publication[0]
    assert extra == 0, f"La publicacion infirio {extra} documentos nuevos: {result}"


"""El mismo sello no debe certificar dos restricciones distintas."""
import asyncio
import os

from jobhunt_core import dev_eval
from jobhunt_core.tests.test_integration_matching import db, _setup
from jobhunt_core.tests.test_integration_dev_eval import _judgments_file


def test_same_seal_cannot_certify_two_different_exclusions(db):
    factory, created = db
    pid, mid, _, vacs = _setup(
        factory, created, ["python developer", "python engineer"]
    )
    judgments = _judgments_file([f"P,{vid},2" for vid in vacs.values()])

    async def check():
        async with factory() as session:
            seal = await dev_eval.build_universe_manifest(
                session, {"P": pid}, model_id=str(mid), allow_unknown_release=True
            )
        reports = []
        for exclusions in ([], [next(iter(vacs.values()))]):
            try:
                async with factory() as session:
                    report = await dev_eval.evaluate_dev(
                        session,
                        "cosine:v1",
                        {"P": pid},
                        judgments,
                        model_id=str(mid),
                        universe=seal,
                        exclude_vacancy_ids=exclusions,
                        allow_unknown_release=True,
                    )
            except ValueError:
                if exclusions:
                    return
                raise
            reports.append(report["payload"]["profiles"]["P"])
        assert (
            not all(r["elegible"] for r in reports)
            or reports[0]["feed_n"] == reports[1]["feed_n"]
        ), (
            f"Mismo sello, ambos elegibles, tamaños {[r['feed_n'] for r in reports]}, nDCG {[r['ndcg10'] for r in reports]}"
        )

    try:
        asyncio.run(check())
    finally:
        os.unlink(judgments)

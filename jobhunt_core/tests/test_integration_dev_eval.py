"""Evaluador canónico de desarrollo (Fase 1 del cierre v5) contra Postgres.

La desviación de fórmula (lineal vs la del gate) produjo un falso avance
(0.557/0.617 vs 0.440/0.538 reales): estas regresiones fijan que la única
métrica de desarrollo es la de shadow.metrics y que el evaluador falla
CERRADO ante mezclas y ambigüedades. Ejecutar vía core-migrate.
"""

import asyncio
import json
import math
import os
import tempfile

import pytest

from jobhunt_core import dev_eval, matching
from jobhunt_core.tests.test_integration_matching import (  # noqa: F401
    _evaluate, _rows, _setup, db,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

TITULOS = [
    "python backend developer", "senior python engineer",
    "data engineer python sql", "warehouse operative",
    "kubernetes platform engineer", "frontend react developer",
]


def _judgments_file(lineas):
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8"
    )
    f.write("".join(f"{ln}\n" for ln in lineas))
    f.close()
    return f.name


def _shadow_policy(factory, created, version="v4"):
    async def go():
        async with factory() as s:
            polid = await matching.ensure_policy(
                s, matching.HYBRID_POLICY_NAME, version,
                weights=matching.HYBRID4_POLICY_WEIGHTS, active=False)
            created["policies"].append(polid)
            await s.commit()
            return polid

    return asyncio.run(go())


def _run_eval(factory, polid_spec, profiles, judgments, unsure=None):
    async def go():
        async with factory() as s:
            return await dev_eval.evaluate_dev(
                s, polid_spec, profiles, judgments, unsure_path=unsure)

    return asyncio.run(go())


def test_el_evaluador_usa_la_formula_del_gate_no_la_lineal(db):
    """La métrica de desarrollo es EXACTAMENTE la de shadow.metrics (ganancia
    graduada). Con rel-2 arriba, la fórmula lineal da OTRO número: el test
    calcula ambos a mano y exige el del gate."""
    factory, created = db
    pid, mid, _, vacs = _setup(
        factory, created, TITULOS,
        profile_content={"title": "python developer", "skills": ["python"]})
    polid = _shadow_policy(factory, created)
    assert _evaluate(factory, pid, mid, polid)["evaluated"] > 0
    feed = _rows(
        factory,
        "SELECT vacancy_id FROM match_evaluations "
        "WHERE profile_id = :p AND scoring_policy_id = :sp "
        "ORDER BY score_final DESC, vacancy_id", p=pid, sp=polid)
    # rel-2 al primero, rel-1 al segundo, rel-0 al tercero: las dos fórmulas
    # divergen (graduada: (2^rel-1)/log2(pos+1); lineal: rel/log2(pos+1)).
    j = [
        f"P1,{feed[0].vacancy_id},2",
        f"P1,{feed[1].vacancy_id},1",
        f"P1,{feed[2].vacancy_id},0",
    ]
    out = _run_eval(factory, "hybrid-rrf:v4", {"P1": pid}, _judgments_file(j))
    r = out["profiles"]["P1"]
    dcg_gate = 3 / math.log2(2) + 1 / math.log2(3)
    idcg_gate = dcg_gate  # el ideal ordena 2,1,0 igual
    assert r["dcg"] == round(dcg_gate, 6)
    assert r["ndcg10"] == round(dcg_gate / idcg_gate, 6) == 1.0
    # la lineal habría dado dcg distinto — si alguien la reintroduce, muerde
    dcg_lineal = 2 / math.log2(2) + 1 / math.log2(3)
    assert round(dcg_lineal, 6) != r["dcg"]
    assert out["manifest"]["recipe"] == matching.HYBRID4_POLICY_WEIGHTS
    assert out["manifest"]["corpus_generation"] is not None


def test_el_feed_sombra_no_mezcla_revisiones_ni_politicas(db):
    """Filas de otra política, de una revisión de perfil anterior o de una
    offer_revision obsoleta NO entran; una vacante archivada tampoco."""
    factory, created = db
    pid, mid, cosine_id, vacs = _setup(
        factory, created, TITULOS,
        profile_content={"title": "python developer", "skills": ["python"]})
    polid = _shadow_policy(factory, created)
    # evaluación bajo la PRIMERA revisión del perfil
    assert _evaluate(factory, pid, mid, polid)["evaluated"] > 0
    # también bajo cosine (otra política): no debe contaminar
    assert _evaluate(factory, pid, mid, cosine_id)["evaluated"] > 0

    async def mutate():
        async with factory() as s:
            from jobhunt_core import profiles as core_profiles
            # nueva revisión vigente (cambio SOLO de preferencias: mismo
            # texto embebible ⇒ mismo text_hash ⇒ el vector se reutiliza y la
            # reevaluación de abajo funciona sin re-embeder) → las evals
            # viejas quedan atadas a la revisión anterior
            await core_profiles.save_profile_revision(
                s, pid, {"title": "python developer", "skills": ["python"],
                         "languages": ["English"]})
            # y una vacante se archiva
            import sqlalchemy as sa
            await s.execute(sa.text(
                "UPDATE vacancies SET archived_at = now() WHERE id = :v"),
                {"v": vacs["warehouse operative"]})
            await s.commit()

    asyncio.run(mutate())

    async def go():
        async with factory() as s:
            filas, prid = await matching.shadow_feed(s, pid, polid, mid)
            return filas

    filas = asyncio.run(go())
    # la revisión vigente cambió y no hay evals bajo ella ⇒ feed vacío;
    # nada de la revisión anterior ni de cosine se cuela
    assert filas == []

    # El worker de embeddings COPIA el vector por text_hash (revisión nueva,
    # mismo texto): el backend envenenado prueba que NO hay forward pass.
    from jobhunt_core import embeddings
    from jobhunt_core.tasks.embedding import run_pending_task

    class _Poison:
        def encode_batch(self, texts):
            raise AssertionError(
                "forward pass redundante: el cambio era solo de preferencias")

    embeddings.set_backend_factory(lambda name, version: _Poison())
    try:
        r = run_pending_task.apply(kwargs={"limit": 100})
        assert r.successful()
    finally:
        embeddings.set_backend_factory(None)

    # re-evaluar bajo la revisión nueva: el feed reaparece SIN la archivada
    assert _evaluate(factory, pid, mid, polid)["evaluated"] > 0
    filas = asyncio.run(go())
    assert filas, "sin feed tras reevaluar bajo la revisión vigente"
    assert vacs["warehouse operative"] not in {f.vacancy_id for f in filas}


def test_idcg_cero_es_no_medible_jamas_verde(db):
    factory, created = db
    pid, mid, _, vacs = _setup(factory, created, TITULOS[:2])
    polid = _shadow_policy(factory, created)
    assert _evaluate(factory, pid, mid, polid)["evaluated"] > 0
    j = [f"P1,{v},0" for v in list(vacs.values())[:2]]
    out = _run_eval(factory, "hybrid-rrf:v4", {"P1": pid}, _judgments_file(j))
    r = out["profiles"]["P1"]
    assert r["no_medible"] is True and r["ndcg10"] == 0.0
    assert r["causa_no_medible"]


def test_juicios_ambiguos_o_rotos_abortan(db):
    factory, created = db
    pid, mid, _, vacs = _setup(factory, created, TITULOS[:2])
    polid = _shadow_policy(factory, created)
    assert _evaluate(factory, pid, mid, polid)["evaluated"] > 0
    v = str(list(vacs.values())[0])
    # mismo par con rel distinta ⇒ ambiguo
    with pytest.raises(ValueError, match="ambiguo"):
        _run_eval(factory, "hybrid-rrf:v4", {"P1": pid},
                  _judgments_file([f"P1,{v},2", f"P1,{v},1"]))
    # vacante inexistente ⇒ typo que no puede degradar en silencio
    with pytest.raises(ValueError, match="inexistentes"):
        _run_eval(factory, "hybrid-rrf:v4", {"P1": pid},
                  _judgments_file(
                      [f"P1,00000000-0000-0000-0000-000000000000,1"]))
    # unsure que también aparece juzgado ⇒ ambiguo
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"P1": [v]}, f); f.close()
    with pytest.raises(ValueError, match="ambiguo"):
        _run_eval(factory, "hybrid-rrf:v4", {"P1": pid},
                  _judgments_file([f"P1,{v},1"]), unsure=f.name)
    # política inexistente ⇒ error nombrable
    with pytest.raises(ValueError, match="inexistente"):
        _run_eval(factory, "hybrid-rrf:v99x", {"P1": pid},
                  _judgments_file([f"P1,{v},1"]))


def test_la_salida_es_determinista(db):
    factory, created = db
    pid, mid, _, vacs = _setup(factory, created, TITULOS[:3])
    polid = _shadow_policy(factory, created)
    assert _evaluate(factory, pid, mid, polid)["evaluated"] > 0
    j = _judgments_file([f"P1,{list(vacs.values())[0]},2"])
    a = _run_eval(factory, "hybrid-rrf:v4", {"P1": pid}, j)
    b = _run_eval(factory, "hybrid-rrf:v4", {"P1": pid}, j)
    a["manifest"].pop("generated_at"); b["manifest"].pop("generated_at")
    assert a == b

"""Evaluador canónico de desarrollo — feed COHERENTE de una sola ejecución.

Revisión externa 2026-09-03 (P1): eval_key no lleva generación del corpus,
RRF/rerank persisten rangos relativos, ON CONFLICT DO NOTHING conserva scores
viejos ⇒ el almacén append-only es una UNIÓN de generaciones que ninguna
ejecución produjo (feed 1800→1814). Estas regresiones fijan que la medición
usa compute_policy_feed (cálculo directo, sin persistir) con la semántica del
feed canónico (dismissed incluido) y manifiesto reproducible. Ejecutar vía
core-migrate.
"""

import asyncio
import json
import math
import os
import tempfile
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core import dev_eval, embeddings, matching
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.tests.test_integration_matching import (  # noqa: F401
    DirectionalBackend, _evaluate, _listing, _rows, _setup, db,
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
                s, polid_spec, profiles, judgments, unsure_path=unsure,
                allow_unknown_release=True)

    return asyncio.run(go())


def _evaluate_shadow(factory, pid, mid, polid, limit=100):
    async def go():
        async with factory() as s:
            r = await matching.evaluate_profile(
                s, pid, mid, polid, limit=limit, move_current=False)
            await s.commit()
            return r

    return asyncio.run(go())


def _compute(factory, pid, mid, polid, limit=matching.CANONICAL_EVAL_LIMIT,
             exclude_dismissed=True):
    async def go():
        async with factory() as s:
            return await matching.compute_policy_feed(
                s, pid, mid, polid, limit=limit,
                exclude_dismissed=exclude_dismissed)

    return asyncio.run(go())


# ------------------------------------------------- P1: una sola ejecución


def test_el_feed_de_medicion_es_de_una_sola_ejecucion(db):
    """El almacén conserva la unión histórica (limit+1 filas, rangos de G1);
    la medición NO puede leerlo: compute_policy_feed devuelve exactamente la
    ejecución de AHORA — tamaño objetivo, rangos actuales, determinista."""
    factory, created = db
    pid, mid, _, vacs = _setup(
        factory, created, TITULOS,
        profile_content={"title": "python developer", "skills": ["python"]})
    polid = _shadow_policy(factory, created)
    K = len(TITULOS)

    # G1: ejecución completa persistida (SOMBRA: una relativa ya no puede
    # mover el feed — valla Fase 1 del cierre definitivo).
    assert _evaluate_shadow(factory, pid, mid, polid, limit=K)["evaluated"] == K
    g1 = _rows(
        factory,
        "SELECT vacancy_id, (scores->>'semantic_rank')::int AS sr "
        "FROM match_evaluations WHERE profile_id = :p AND "
        "scoring_policy_id = :sp", p=pid, sp=polid)
    top1_g1 = next(r.vacancy_id for r in g1 if r.sr == 1)

    # Cambio de corpus: una oferta NUEVA pegada al vector del perfil.
    async def sink_offer():
        async with factory() as s:
            scope_id = created["scopes"][0]
            await RawListingSink().handle(
                s, str(scope_id), (_listing("j-nuevo", "python developer"),))
            await s.commit()

    async def pin_vector():
        async with factory() as s:
            # el vector del perfil, clavado: la nueva pasa a rango semántico 1
            vec = (await s.execute(sa.text(
                "SELECT pe.vector::text FROM profile_embeddings pe "
                "WHERE pe.model_id = :m ORDER BY pe.profile_revision_id "
                "LIMIT 1"), {"m": mid})).scalar_one()
            await s.execute(sa.text(
                "UPDATE offer_embeddings SET vector = CAST(:v AS vector) "
                "WHERE text_hash IN (SELECT o.text_hash FROM offer_revisions o "
                " JOIN vacancies va ON va.current_offer_revision_id = o.id "
                " WHERE o.content->>'title' = 'python developer') "
                "AND model_id = :m"), {"v": vec, "m": mid})
            await s.commit()

    asyncio.run(sink_offer())
    # La tarea Celery hace su propio asyncio.run: se aplica FUERA del loop.
    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        from jobhunt_core.tasks.embedding import run_pending_task
        r = run_pending_task.apply(kwargs={"limit": 100})
        assert r.successful(), r.traceback
    finally:
        embeddings.set_backend_factory(None)
    asyncio.run(pin_vector())

    # G2: reevaluación persistida — el almacén queda con la UNIÓN.
    assert _evaluate_shadow(factory, pid, mid, polid, limit=K)["evaluated"] == K
    union = _rows(
        factory,
        "SELECT vacancy_id, (scores->>'semantic_rank')::int AS sr "
        "FROM match_evaluations WHERE profile_id = :p AND "
        "scoring_policy_id = :sp", p=pid, sp=polid)
    # El DEFECTO del almacén, documentado: limit+1 filas vigentes y el rango
    # de G1 conservado para el viejo top-1 (ON CONFLICT DO NOTHING).
    assert len(union) == K + 1
    assert next(r.sr for r in union if r.vacancy_id == top1_g1) == 1

    # La MEDICIÓN: cálculo directo — tamaño objetivo, rangos de G2.
    r = _compute(factory, pid, mid, polid, limit=K)
    assert r["status"] == "ok" and len(r["rows"]) == K
    por_vac = {f["vacancy_id"]: f for f in r["rows"]}
    assert top1_g1 in por_vac, "el viejo top-1 sigue en el corpus"
    assert por_vac[top1_g1]["score_parts"]["semantic_rank"] == 2, (
        "el cálculo directo debe reflejar el rango ACTUAL, no el de G1")
    # determinista: dos ejecuciones, mismas filas
    assert _compute(factory, pid, mid, polid, limit=K)["rows"] == r["rows"]


def test_descartada_no_aparece_y_sin_estado_si(db):
    """Semántica del feed canónico en la medición: dismissed_at excluye; una
    vacante sin fila de estado sigue visible; y el cálculo coincide fila a
    fila con feed() cuando la política es la canónica."""
    factory, created = db
    pid, mid, cosine_id, vacs = _setup(factory, created, TITULOS)
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True

    def canonico():
        async def go():
            async with factory() as s:
                filas, _cur = await matching.feed(s, pid, limit=50)
                return [(f.vacancy_id, str(f.score_final)) for f in filas]

        return asyncio.run(go())

    def calculo():
        r = _compute(factory, pid, mid, cosine_id)
        assert r["status"] == "ok"
        return [(f["vacancy_id"], f"{f['score']:.2f}") for f in r["rows"]]

    assert calculo() == canonico()

    # descarte de la primera vacante → desaparece de ambos
    primera = canonico()[0][0]

    async def dismiss():
        async with factory() as s:
            await s.execute(sa.text(
                "UPDATE profile_vacancy_state SET dismissed_at = now() "
                "WHERE profile_id = :p AND vacancy_id = :v"),
                {"p": pid, "v": primera})
            await s.commit()

    asyncio.run(dismiss())
    assert primera not in [v for v, _ in canonico()]
    assert primera not in [v for v, _ in calculo()]
    assert calculo() == canonico()
    # las demás (con fila de estado NO descartada) y cualquier vacante sin
    # fila siguen visibles: el resto del feed no se encogió
    assert len(calculo()) == len(TITULOS) - 1


def _feed_actual(factory, pid):
    async def go():
        async with factory() as s:
            filas, _ = await matching.feed(s, pid, limit=50)
            return [(f.vacancy_id, str(f.score_final)) for f in filas]

    return asyncio.run(go())


def _cosine2_policy(factory, created):
    async def go():
        async with factory() as s:
            polid = await matching.ensure_policy(
                s, "cosine-nueva", "v1", weights={"algorithm": "cosine"},
                active=False)
            created["policies"].append(polid)
            await s.commit()
            return polid

    return asyncio.run(go())


def test_promover_y_rollback_conservan_el_feed(db):
    """El mismo conjunto y orden que mide el desarrollo es el que sirve el
    feed al promover; el rollback restaura exactamente el anterior. La
    candidata es ABSOLUTA (una relativa ya no puede ser canónica)."""
    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS)
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True

    feed_cosine = _feed_actual(factory, pid)
    polid = _cosine2_policy(factory, created)
    medido = [(f["vacancy_id"], f"{f['score']:.2f}")
              for f in _compute(factory, pid, mid, polid)["rows"]]

    async def declare(ids):
        async with factory() as s:
            await matching.declare_active_policies(s, ids)
            await s.commit()

    asyncio.run(declare([polid]))
    assert _evaluate(factory, pid, mid, polid)["moved_current"] is True
    assert _feed_actual(factory, pid) == medido, (
        "lo medido en desarrollo debe ser EXACTAMENTE lo servido al promover")

    asyncio.run(declare([cosine_id]))
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    assert _feed_actual(factory, pid) == feed_cosine


# --------------------------------- Fase 1 cierre definitivo: pair_absolute


def test_una_politica_relativa_no_puede_ser_canonica(db):
    """G1→corpus nuevo→G2 deja en el almacén una mezcla que la materialización
    de una política RELATIVA (RRF) serviría (winners recupera filas con rangos
    de G1). El sistema debe RECHAZAR la canonicidad de una relativa — en la
    declaración y sin importar el estado previo."""
    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS)
    polid = _shadow_policy(factory, created)  # hybrid-rrf v4: RELATIVA

    async def go():
        async with factory() as s:
            # canónica relativa en solitario: rechazada
            with pytest.raises(ValueError, match="RELATIVO"):
                await matching.declare_active_policies(s, [polid])
            await s.rollback()
            # relativa DETRÁS de una absoluta canónica: permitida (sombra)
            await matching.declare_active_policies(s, [cosine_id, polid])
            await s.commit()

    asyncio.run(go())


def test_valla_final_rechaza_mover_feed_con_relativa(db):
    """Aun si un bypass activa una relativa en solitario (SQL directo, sin la
    autoridad), evaluate_profile(move_current=True) falla CERRADO antes de
    tocar profile_vacancy_state."""
    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS)
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    antes = _feed_actual(factory, pid)
    polid = _shadow_policy(factory, created)

    async def bypass_y_evaluar():
        async with factory() as s:
            await s.execute(sa.text(
                "UPDATE scoring_policies SET active = (id = :v)"),
                {"v": polid})
            await s.commit()
        async with factory() as s:
            with pytest.raises(ValueError, match="RELATIVO"):
                await matching.evaluate_profile(
                    s, pid, mid, polid, move_current=True)
            await s.rollback()
        async with factory() as s:  # restaurar activación para el teardown
            await matching.declare_active_policies(s, [cosine_id])
            await s.commit()

    asyncio.run(bypass_y_evaluar())
    assert _feed_actual(factory, pid) == antes  # el feed no se tocó


def test_la_sombra_relativa_sigue_permitida(db):
    """RRF puede seguir calculándose y midiéndose en sombra sin mover feed."""
    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS)
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    antes = _feed_actual(factory, pid)
    polid = _shadow_policy(factory, created)
    r = _evaluate_shadow(factory, pid, mid, polid)
    assert r["status"] == "ok" and r["evaluated"] > 0
    assert r["moved_current"] is False
    assert _compute(factory, pid, mid, polid)["rows"]
    assert _feed_actual(factory, pid) == antes


def test_absoluta_estable_tras_cambio_de_corpus(db):
    """Una política ABSOLUTA tras G1→corpus nuevo→G2 sirve exactamente las
    filas del cálculo G2 y una pareja vieja conserva su score."""
    factory, created = db
    pid, mid, cosine_id, vacs = _setup(factory, created, TITULOS)
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    v0 = list(vacs.values())[0]
    score_g1 = _rows(
        factory,
        "SELECT score_final FROM match_evaluations WHERE profile_id = :p "
        "AND scoring_policy_id = :sp AND vacancy_id = :v",
        p=pid, sp=cosine_id, v=v0)[0].score_final

    async def sink_offer():
        async with factory() as s:
            await RawListingSink().handle(
                s, str(created["scopes"][0]),
                (_listing("j-g2", "python developer expert"),))
            await s.commit()

    asyncio.run(sink_offer())
    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        from jobhunt_core.tasks.embedding import run_pending_task
        r = run_pending_task.apply(kwargs={"limit": 100})
        assert r.successful(), r.traceback
    finally:
        embeddings.set_backend_factory(None)

    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    medido = [(f["vacancy_id"], f"{f['score']:.2f}")
              for f in _compute(factory, pid, mid, cosine_id)["rows"]]
    assert _feed_actual(factory, pid) == medido
    score_g2 = _rows(
        factory,
        "SELECT score_final FROM match_evaluations WHERE profile_id = :p "
        "AND scoring_policy_id = :sp AND vacancy_id = :v",
        p=pid, sp=cosine_id, v=v0)[0].score_final
    assert score_g2 == score_g1  # la pareja vieja conserva su score


# ------------------------------------------------- fórmula y fail-closed


def test_el_evaluador_usa_la_formula_del_gate_no_la_lineal(db):
    factory, created = db
    pid, mid, _, vacs = _setup(
        factory, created, TITULOS,
        profile_content={"title": "python developer", "skills": ["python"]})
    _shadow_policy(factory, created)
    # primera pasada para conocer el orden actual (determinista)
    sonda = _run_eval(factory, "hybrid-rrf:v4", {"P1": pid},
                      _judgments_file([f"P1,{list(vacs.values())[0]},1"]))
    top = [f["vacancy_id"] for f in sonda["payload"]["profiles"]["P1"]["top10"]]
    j = [f"P1,{top[0]},2", f"P1,{top[1]},1", f"P1,{top[2]},0"]
    out = _run_eval(factory, "hybrid-rrf:v4", {"P1": pid}, _judgments_file(j))
    r = out["payload"]["profiles"]["P1"]
    dcg_gate = 3 / math.log2(2) + 1 / math.log2(3)
    assert r["dcg"] == round(dcg_gate, 6)
    assert r["ndcg10"] == 1.0
    dcg_lineal = 2 / math.log2(2) + 1 / math.log2(3)
    assert round(dcg_lineal, 6) != r["dcg"]
    assert out["payload"]["recipe"] == matching.HYBRID4_POLICY_WEIGHTS
    assert out["payload"]["corpus_generation"] is not None


def test_idcg_cero_es_no_medible_jamas_verde(db):
    factory, created = db
    pid, mid, _, vacs = _setup(factory, created, TITULOS[:2])
    _shadow_policy(factory, created)
    j = [f"P1,{v},0" for v in list(vacs.values())[:2]]
    out = _run_eval(factory, "hybrid-rrf:v4", {"P1": pid}, _judgments_file(j))
    r = out["payload"]["profiles"]["P1"]
    assert r["no_medible"] is True and r["ndcg10"] == 0.0
    assert r["causa_no_medible"]


def test_fallos_cerrados_del_evaluador(db):
    factory, created = db
    pid, mid, _, vacs = _setup(factory, created, TITULOS[:2])
    _shadow_policy(factory, created)
    v = str(list(vacs.values())[0])
    with pytest.raises(ValueError, match="ambiguo"):
        _run_eval(factory, "hybrid-rrf:v4", {"P1": pid},
                  _judgments_file([f"P1,{v},2", f"P1,{v},1"]))
    with pytest.raises(ValueError, match="inexistentes"):
        _run_eval(factory, "hybrid-rrf:v4", {"P1": pid},
                  _judgments_file(
                      [f"P1,00000000-0000-0000-0000-000000000000,1"]))
    with pytest.raises(ValueError, match="inexistente"):
        _run_eval(factory, "hybrid-rrf:v99x", {"P1": pid},
                  _judgments_file([f"P1,{v},1"]))
    # perfil sin vector → no medible, jamás un feed vacío en silencio
    with pytest.raises(ValueError, match="no medible"):
        _run_eval(factory, "hybrid-rrf:v4", {"PX": str(uuid.uuid4())},
                  _judgments_file([]))
    # unsure malformado: raíz lista, valor no-lista, elemento escalar, solape
    for contenido in (["a"], {"P1": "no-lista"}, {"P1": [123]},
                      {"P1": [v]}):
        f = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(contenido, f); f.close()
        with pytest.raises(ValueError):
            _run_eval(factory, "hybrid-rrf:v4", {"P1": pid},
                      _judgments_file([f"P1,{v},1"]), unsure=f.name)


def test_release_desconocida_es_error_en_operacion(db):
    factory, created = db
    pid, mid, _, vacs = _setup(factory, created, TITULOS[:2])
    _shadow_policy(factory, created)

    async def go():
        async with factory() as s:
            with pytest.raises(ValueError, match="RELEASE_SHA"):
                await dev_eval.evaluate_dev(
                    s, "hybrid-rrf:v4", {"P1": pid},
                    _judgments_file([f"P1,{list(vacs.values())[0]},1"]))

    if os.environ.get("RELEASE_SHA", "unknown") == "unknown":
        asyncio.run(go())
    else:
        pytest.skip("RELEASE_SHA identificable en este entorno")


def test_el_payload_es_determinista_y_sellado(db):
    """El payload reproducible es idéntico entre corridas (sin quitar campos)
    y su hash lo sella; lo volátil (generated_at) vive FUERA del payload."""
    factory, created = db
    pid, mid, _, vacs = _setup(factory, created, TITULOS[:3])
    _shadow_policy(factory, created)
    j = _judgments_file([f"P1,{list(vacs.values())[0]},2"])
    a = _run_eval(factory, "hybrid-rrf:v4", {"P1": pid}, j)
    b = _run_eval(factory, "hybrid-rrf:v4", {"P1": pid}, j)
    assert a["payload"] == b["payload"]
    assert a["payload_sha256"] == b["payload_sha256"]
    assert "generated_at" not in a["payload"]

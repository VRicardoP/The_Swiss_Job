"""Loader del ESTRATO POSITIVO (shadow/stratum) contra Postgres real.

Regresiones que muerden (propuesta §4 del estrato, 2026-08-25):
(a) la numeración de la hoja se reproduce EXACTA (orden del render del miner:
sorted estable por -confianza) — sin eso el pair_id apunta a otro par;
(b) el loader excluye ambiguous-owner y los sintéticos B-26/27/28, resuelve
refs LEGACY por encarnación primaria, registra la cohorte SIN congelar y es
IDEMPOTENTE (re-ejecutar inserta 0);
(c) actas desparejadas / vacantes sin slot legacy / etiquetas fuera de
vocabulario ⇒ error FUERTE, nada se carga a medias;
(d) congelada la cohorte (freeze del OPERADOR, acto aparte), el loader da
error claro y el trigger de core0025 rechaza cualquier mutación directa.

Mismo aislamiento que test_integration_shadow_labels: BD desechable de la
suite (conftest P2-8), limpieza por refs/sources. Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core.config import settings
from jobhunt_core.shadow import labels, stratum
from jobhunt_core.tests import dbcleanup

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"sources": [], "dedup_refs": [], "cohorts": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            # Cohortes primero: una SELLADA bloquea el DELETE de sus pares
            # (trigger core0025) — se desmonta con el límite declarado (DDL
            # de OWNER), igual que _desmonta_cohorte de shadow_labels.
            await s.execute(
                sa.text(
                    "ALTER TABLE labeled_dedup_cohorts "
                    "DISABLE TRIGGER labeled_dedup_cohorts_frozen_guard"
                )
            )
            await s.execute(
                sa.text("DELETE FROM labeled_dedup_cohorts WHERE source = ANY(:c)"),
                {"c": created["cohorts"]},
            )
            await s.execute(
                sa.text(
                    "ALTER TABLE labeled_dedup_cohorts "
                    "ENABLE TRIGGER labeled_dedup_cohorts_frozen_guard"
                )
            )
            await dbcleanup.purge_shadow(s, dedup_refs=created["dedup_refs"])
            await dbcleanup.purge_source_graph(s, created["sources"], [])
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _mk_legacy_pair(factory, source_id, prefix, n):
    """n vacantes con slot legacy y encarnación PRIMARIA (lo que resuelve el
    loader). Devuelve [(vacancy_id_str, external_id)]."""
    out = []

    async def go():
        async with factory() as s:
            for i in range(n):
                vid, lid, iid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                ext = f"{prefix}-{i}"
                await s.execute(
                    sa.text("INSERT INTO vacancies (id) VALUES (:i)"), {"i": vid}
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listings "
                        "(id, source_id, external_id, url_normalized) "
                        "VALUES (:i, :s, :e, :u)"
                    ),
                    {"i": lid, "s": source_id, "e": ext, "u": f"https://fx/{ext}"},
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listing_incarnations "
                        "(id, source_listing_id, vacancy_id, seq, url) "
                        "VALUES (:i, :l, :v, 1, :u)"
                    ),
                    {"i": iid, "l": lid, "v": vid, "u": f"https://fx/{ext}/1"},
                )
                await s.execute(
                    sa.text(
                        "UPDATE vacancies SET primary_incarnation_id = :p "
                        "WHERE id = :i"
                    ),
                    {"p": iid, "i": vid},
                )
                out.append((str(vid), ext))
            await s.commit()

    _run(go())
    return out


def _mk_source(factory, name):
    sid = uuid.uuid4()

    async def go():
        async with factory() as s:
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:i, :n, 0)"),
                {"i": sid, "n": name},
            )
            await s.commit()

    _run(go())
    return sid


def _cand(id_a, id_b, conf):
    """Candidato mínimo del payload JSON_CANDIDATOS (solo las claves que el
    loader usa: ids + confianza para reproducir la numeración)."""
    return {"id_a": id_a, "id_b": id_b, "confianza": conf}


def _load(factory, candidates, labels_by_pair, **kw):
    async def go():
        async with factory() as s:
            summary = await stratum.load_positive_stratum(
                s, candidates, labels_by_pair, **kw
            )
            await s.commit()
            return summary

    return _run(go())


def _pairs_in_cohort(factory, cohort):
    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT job_ref_a, job_ref_b, verdict "
                        "FROM labeled_dedup_pairs WHERE source = :src "
                        "ORDER BY job_ref_a, job_ref_b"
                    ),
                    {"src": cohort},
                )
            ).all()

    return _run(go())


# ------------------------------------------------- numeración de la hoja


def test_pair_ids_reproducen_la_numeracion_de_la_hoja():
    """El JSON de candidatos conserva el orden de MINERÍA; la hoja renderiza
    sorted por -confianza (estable). B-26 debe ser el 26º de la TABLA, no el
    26º del JSON — un desliza-uno cargaría veredictos en pares ajenos."""
    candidates = {
        "B": [
            _cand("v1", "v2", 0.9),
            _cand("v3", "v4", 0.65),  # baja confianza: va al FINAL (B-03)
            _cand("v5", "v6", 0.9),   # empate 0.9: estable tras el primero
        ],
        "M": [_cand("v7", "v8", 0.65)],
    }
    ids = stratum.pair_ids_from_candidates(candidates)
    assert ids == {
        "B-01": ("v1", "v2"),
        "B-02": ("v5", "v6"),
        "B-03": ("v3", "v4"),
        "M-01": ("v7", "v8"),
    }


# ----------------------------------------------- carga + exclusiones (§4.1)


def test_loader_excluye_ambiguos_y_sinteticos_y_es_idempotente(db):
    factory, created = db
    p = uuid.uuid4().hex[:8]
    cohort = f"stratum-fx-{p}"
    created["cohorts"].append(cohort)
    src = _mk_source(factory, f"legacy:strfx{p}")
    created["sources"].append(src)
    vacs = _mk_legacy_pair(factory, src, p, 4)
    created["dedup_refs"] += [ext for _v, ext in vacs]
    (va, ra), (vb, rb), (vc, rc), (vd, rd) = vacs

    # Hoja: 28 candidatos B (confianza DESCENDENTE ⇒ numeración = orden del
    # JSON), 1 C, 1 M. Solo B-01 y M-01 necesitan vacantes reales: B-26/27/28
    # (sintéticos) y C-01 (ambiguous-owner) se excluyen ANTES de resolver.
    b_cands = [_cand(va, vb, 0.9)] + [
        _cand(f"fake-{i}a", f"fake-{i}b", round(0.9 - i * 0.001, 3))
        for i in range(1, 28)
    ]
    candidates = {"B": b_cands, "C": [_cand("fake-ca", "fake-cb", 0.8)],
                  "M": [_cand(vc, vd, 0.7)]}
    labels_json = {
        "B-01": "duplicate",
        "B-26": "duplicate", "B-27": "duplicate", "B-28": "duplicate",
        "C-01": "ambiguous-owner",
        "M-01": "distinct",
    }

    summary = _load(factory, candidates, labels_json, cohort=cohort)
    assert summary == {
        "cohorte": cohort,
        "total_hoja": 30,
        "etiquetados": 6,
        "cargables": 2,
        "insertados": 2,
        "ya_presentes": 0,
        "excluidos_ambiguous_owner": 1,
        "excluidos_sinteticos": 3,
        "congelada": False,
    }
    rows = _pairs_in_cohort(factory, cohort)
    # refs LEGACY (external_id de la encarnación primaria), veredictos del acta
    assert {(r.job_ref_a, r.job_ref_b, r.verdict) for r in rows} == {
        (ra, rb, "duplicate"),
        (rc, rd, "distinct"),
    }

    # cohorte REGISTRADA pero SIN congelar (el freeze es acto del operador)
    async def cohort_row():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT frozen_at FROM labeled_dedup_cohorts "
                        "WHERE source = :src"
                    ),
                    {"src": cohort},
                )
            ).one()

    assert _run(cohort_row()).frozen_at is None

    # idempotencia: re-ejecutar no duplica ni pisa
    again = _load(factory, candidates, labels_json, cohort=cohort)
    assert again["insertados"] == 0
    assert again["ya_presentes"] == 2
    assert len(_pairs_in_cohort(factory, cohort)) == 2


def test_loader_falla_fuerte_con_actas_desparejadas(db):
    factory, created = db
    p = uuid.uuid4().hex[:8]
    cohort = f"stratum-err-{p}"
    created["cohorts"].append(cohort)
    candidates = {"B": [_cand("no-existe-a", "no-existe-b", 0.9)]}

    # pair_id sin candidato en la hoja (actas de rondas distintas)
    with pytest.raises(ValueError, match="desparejadas"):
        _load(factory, candidates, {"B-02": "duplicate"}, cohort=cohort)
    # etiqueta fuera de vocabulario
    with pytest.raises(ValueError, match="etiqueta desconocida"):
        _load(factory, candidates, {"B-01": "dup"}, cohort=cohort)
    # vacante sin slot legacy resoluble: error FUERTE, no carga parcial
    fake = {"B": [_cand(str(uuid.uuid4()), str(uuid.uuid4()), 0.9)]}
    with pytest.raises(ValueError, match="B-01"):
        _load(factory, fake, {"B-01": "duplicate"}, cohort=cohort)
    assert _pairs_in_cohort(factory, cohort) == []


# --------------------------------------- inmutabilidad tras el freeze (§4.1)


def test_cohorte_estrato_congelada_rechaza_loader_y_mutaciones(db):
    """El estrato congelado recibe la MISMA inmutabilidad que el holdout
    (trigger core0025, sin código nuevo): loader ⇒ error claro; INSERT/
    UPDATE/DELETE directos ⇒ excepción del trigger."""
    factory, created = db
    p = uuid.uuid4().hex[:8]
    cohort = f"stratum-frz-{p}"
    created["cohorts"].append(cohort)
    src = _mk_source(factory, f"legacy:strfz{p}")
    created["sources"].append(src)
    vacs = _mk_legacy_pair(factory, src, p, 2)
    created["dedup_refs"] += [ext for _v, ext in vacs]
    (va, ra), (vb, rb) = vacs
    candidates = {"B": [_cand(va, vb, 0.9)]}
    _load(factory, candidates, {"B-01": "duplicate"}, cohort=cohort)

    async def freeze():
        async with factory() as s:
            await labels.freeze_dedup_cohort(
                s, cohort, {"sha256_hoja": "fx", "sha256_acta": "fx"}
            )
            await s.commit()

    _run(freeze())

    # el loader ya no toca la cohorte, aunque sería idempotente
    with pytest.raises(stratum.StratumFrozenError, match="CONGELADA"):
        _load(factory, candidates, {"B-01": "duplicate"}, cohort=cohort)

    for sql, params in (
        ("INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, "
         "source) VALUES (:a, :b, 'duplicate', :src)",
         {"a": ra, "b": f"{p}-otro", "src": cohort}),
        ("UPDATE labeled_dedup_pairs SET verdict = 'distinct' "
         "WHERE job_ref_a = :a AND job_ref_b = :b", {"a": ra, "b": rb}),
        ("DELETE FROM labeled_dedup_pairs "
         "WHERE job_ref_a = :a AND job_ref_b = :b", {"a": ra, "b": rb}),
    ):
        with pytest.raises(DBAPIError, match="CONGELADA"):

            async def mutate(sql=sql, params=params):
                async with factory() as s:
                    await s.execute(sa.text(sql), params)
                    await s.commit()

            _run(mutate())

    # el par sigue intacto con su veredicto original
    rows = _pairs_in_cohort(factory, cohort)
    assert [(r.job_ref_a, r.job_ref_b, r.verdict) for r in rows] == [
        (ra, rb, "duplicate")
    ]

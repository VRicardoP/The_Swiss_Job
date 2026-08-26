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
        "excluidos_reciclado": 0,
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


def test_par_de_otra_cohorte_si_entra_en_esta(db):
    """Regresión G1-P3-3 (core0031): la unicidad del par canónico es POR
    cohorte — un par ya presente en OTRA cohorte (holdout/seed/curado) entra
    igualmente en la del estrato. Antes, el ON CONFLICT del índice GLOBAL lo
    descartaba y el loader lo contaba como `ya_presentes` (que sugiere «ya
    estaba en ESTA cohorte»): la cohorte se cargaba incompleta en silencio y
    su recall informativo se calculaba sobre un subconjunto arbitrario."""
    factory, created = db
    p = uuid.uuid4().hex[:8]
    cohort = f"stratum-two-{p}"
    other = f"holdout-fx-{p}"
    created["cohorts"] += [cohort, other]
    src = _mk_source(factory, f"legacy:strtwo{p}")
    created["sources"].append(src)
    vacs = _mk_legacy_pair(factory, src, p, 2)
    created["dedup_refs"] += [ext for _v, ext in vacs]
    (va, ra), (vb, rb) = vacs

    # El MISMO par canónico ya vive en OTRA cohorte.
    async def seed_other():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO labeled_dedup_pairs "
                    "(job_ref_a, job_ref_b, verdict, source) "
                    "VALUES (:a, :b, 'duplicate', :src)"
                ),
                {"a": min(ra, rb), "b": max(ra, rb), "src": other},
            )
            await s.commit()

    _run(seed_other())

    candidates = {"B": [_cand(va, vb, 0.9)]}
    summary = _load(factory, candidates, {"B-01": "duplicate"}, cohort=cohort)
    assert summary["insertados"] == 1  # antes: 0 (descartado por el índice global)
    assert summary["ya_presentes"] == 0  # el contador ya no miente
    assert len(_pairs_in_cohort(factory, cohort)) == 1
    assert len(_pairs_in_cohort(factory, other)) == 1  # la otra cohorte, intacta

    # Idempotencia DENTRO de la cohorte: re-cargar sí es ya_presentes.
    again = _load(factory, candidates, {"B-01": "duplicate"}, cohort=cohort)
    assert again["insertados"] == 0 and again["ya_presentes"] == 1


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


def test_modo_desconocido_del_miner_falla_fuerte():
    """Regresión G1 H-14b: un modo del miner fuera de 'ABCDEFM' desaparecía en
    SILENCIO de la numeración (sus pares jamás se numeraban y la hoja quedaba
    incompleta sin error). Ahora: ValueError."""
    with pytest.raises(ValueError, match="fuera de 'ABCDEFM'"):
        stratum.pair_ids_from_candidates(
            {"Z": [_cand("a", "b", 0.9)], "B": [_cand("c", "d", 0.8)]}
        )


def _mk_slot_reciclado(factory, source_id, prefix):
    """Slot RECICLADO auténtico (harvest/sink.py:495-509): UN source_listing
    con DOS encarnaciones (seq 1 CERRADA y seq 2 activa) sobre vacantes
    DISTINTAS, cada una primaria de la suya — más una tercera vacante con slot
    propio. Devuelve (v_seq1, v_seq2, v_otra, ext_reciclado, ext_otro)."""
    ext, ext_otro = f"{prefix}-reciclado", f"{prefix}-otro"
    v1, v2, v3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lid, lid3 = uuid.uuid4(), uuid.uuid4()
    i1, i2, i3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def go():
        async with factory() as s:
            for lid_, ext_ in ((lid, ext), (lid3, ext_otro)):
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listings "
                        "(id, source_id, external_id, url_normalized) "
                        "VALUES (:i, :s, :e, :u)"
                    ),
                    {"i": lid_, "s": source_id, "e": ext_, "u": f"https://fx/{ext_}"},
                )
            for vid, lid_, iid, seq in ((v1, lid, i1, 1), (v2, lid, i2, 2),
                                        (v3, lid3, i3, 1)):
                if iid is i2:
                    # El reciclado CIERRA la vieja antes de abrir la nueva
                    # (sink.py:304 + el índice parcial uq_incarnation_active).
                    await s.execute(
                        sa.text(
                            "UPDATE source_listing_incarnations "
                            "SET ended_at = now() WHERE id = :i"
                        ),
                        {"i": i1},
                    )
                await s.execute(
                    sa.text("INSERT INTO vacancies (id) VALUES (:i)"), {"i": vid}
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listing_incarnations "
                        "(id, source_listing_id, vacancy_id, seq, url) "
                        "VALUES (:i, :l, :v, :q, :u)"
                    ),
                    {"i": iid, "l": lid_, "v": vid, "q": seq, "u": f"https://fx/{iid}"},
                )
                await s.execute(
                    sa.text(
                        "UPDATE vacancies SET primary_incarnation_id = :p "
                        "WHERE id = :i"
                    ),
                    {"p": iid, "i": vid},
                )
            await s.commit()

    _run(go())
    return str(v1), str(v2), str(v3), ext, ext_otro


def test_g7n6_un_slot_reciclado_no_se_carga_en_silencio(db):
    """REGRESIÓN G7-N-6: las dos direcciones del mapeo `vacancy ↔ job_ref` no
    eran inversas.

    `_resolve_legacy_refs` resuelve por `v.primary_incarnation_id` y
    `labels.map_job_refs_to_vacancies` —la que usan las MÉTRICAS— por
    `ORDER BY i.seq DESC`. «1 vacante = 1 identidad primaria» es cierto, pero
    no implica «1 identidad = 1 vacante»: sobre un slot RECICLADO un
    `external_id` legacy mapea a N vacantes, cada una primaria de la suya.
    Medido en el clúster el 2026-08-26: 569 `source_listings` `legacy:*` con
    más de una vacante y 569 round-trips que no vuelven.

    Consecuencias que este test fija, con DOS pares que solo son distintos en
    el espacio de VACANTES y que canonizaban al MISMO par de job_refs:
      (a) el segundo INSERT moría en el `ON CONFLICT DO NOTHING` contado como
          `ya_presentes` y SIN un solo log (el módulo no tiene logger), así
          que su veredicto —aquí CONTRADICTORIO— se evaporaba;
      (b) el ref superviviente resuelve a la vacante de mayor `seq`, que no es
          la que el etiquetador juzgó.
    Ahora es un error FUERTE que NOMBRA los pares, y nada se carga a medias."""
    factory, created = db
    prefix = "g7n6-" + uuid.uuid4().hex[:8]
    sid = _mk_source(factory, f"legacy:{prefix}")
    created["sources"].append(sid)
    v1, v2, v3, ext, ext_otro = _mk_slot_reciclado(factory, sid, prefix)
    created["dedup_refs"] += [ext, ext_otro]
    cohort = f"stratum-{prefix}"
    created["cohorts"].append(cohort)

    # El round-trip NO vuelve: el ref del slot reciclado resuelve a la de seq 2.
    async def vuelta():
        async with factory() as s:
            return await labels.map_job_refs_to_vacancies(s, [ext, ext_otro])

    de_vuelta = _run(vuelta())
    assert str(de_vuelta[ext]) == v2 and v2 != v1

    candidates = {"B": [_cand(v1, v3, 0.9), _cand(v2, v3, 0.8)]}
    with pytest.raises(ValueError) as exc:
        _load(
            factory, candidates,
            {"B-01": "duplicate", "B-02": "distinct"},  # veredictos OPUESTOS
            cohort=cohort,
        )
    assert "RECICLADO" in str(exc.value)
    assert "B-01" in str(exc.value)  # el par se NOMBRA, no se pierde
    assert _pairs_in_cohort(factory, cohort) == []

    # La vacante VIGENTE del slot (la de mayor seq) sí carga: el guard no
    # cierra el slot reciclado entero, solo las vacantes que no vuelven.
    ok = _load(factory, {"B": [_cand(v2, v3, 0.9)]}, {"B-01": "duplicate"},
               cohort=cohort)
    assert (ok["insertados"], ok["ya_presentes"]) == (1, 0)
    filas = _pairs_in_cohort(factory, cohort)
    assert len(filas) == 1 and filas[0].verdict == "duplicate"
    assert sorted((filas[0].job_ref_a, filas[0].job_ref_b)) == sorted((ext, ext_otro))


def test_g8p3_4_excluir_desbloquea_sin_falsear_el_acta_ni_el_contador(db):
    """REGRESIÓN G8-P3-4: las guardas fail-closed de G7-N-6 muerden bien y no
    tienen falsos positivos, pero el módulo no ofrecía CÓMO desbloquearse.

    Sobre el acta ratificada rechazan 13 `pair_id` por round-trip y 0 por
    colisión, dejando 187 cargables — y la carga aún no se ha ejecutado. Sin
    bandera, el operador solo podía editar el JSON del acta RATIFICADA (el
    artefacto de trazabilidad) o pasar los pares por `synthetic_excluded`,
    donde se contarían como `excluidos_sinteticos`: una etiqueta FALSA en un
    resumen auditable, porque no son los sintéticos B-26/27/28 del corpus de
    test sino slots reciclados.

    Se fija (a) que excluir desbloquea la carga, (b) que el par excluido no
    entra, (c) que el conteo sale por `excluidos_reciclado` y NO contamina
    `excluidos_sinteticos`, y (d) que una errata en la bandera falla FUERTE en
    vez de excluir nada mientras el resumen dice lo contrario."""
    factory, created = db
    prefix = "g8p34-" + uuid.uuid4().hex[:8]
    sid = _mk_source(factory, f"legacy:{prefix}")
    created["sources"].append(sid)
    v1, v2, v3, ext, ext_otro = _mk_slot_reciclado(factory, sid, prefix)
    created["dedup_refs"] += [ext, ext_otro]
    cohort = f"stratum-{prefix}"
    created["cohorts"].append(cohort)

    candidates = {"B": [_cand(v1, v3, 0.9), _cand(v2, v3, 0.8)]}
    labels = {"B-01": "duplicate", "B-02": "distinct"}

    # (d) una errata NO excluye en silencio.
    with pytest.raises(ValueError) as err:
        _load(factory, candidates, labels, cohort=cohort,
              manual_excluded=frozenset({"B-99"}))
    assert "B-99" in str(err.value)

    # (a) el par que la guarda NOMBRA se excluye y la carga sale adelante.
    resumen = _load(factory, candidates, labels, cohort=cohort,
                    manual_excluded=frozenset({"B-01"}))
    assert resumen["cargables"] == 1
    assert resumen["insertados"] == 1
    # (c) el contador dice lo que es, y no miente por la vía de los sintéticos.
    assert resumen["excluidos_reciclado"] == 1
    assert resumen["excluidos_sinteticos"] == 0
    assert resumen["excluidos_ambiguous_owner"] == 0

    # (b) el par cargado es el OTRO, con su veredicto.
    cargados = _pairs_in_cohort(factory, cohort)
    assert len(cargados) == 1
    assert cargados[0][2] == "distinct"


def test_g8_parse_excluidos_tolera_el_copiar_y_pegar_del_error():
    """La guarda imprime `['B-17', 'C-05', ...]`; el operador copia y pega. El
    troceado tiene que quedarse con los pair_ids y no con los espacios."""
    assert stratum.parse_excluidos("B-17, C-05 ,E-09") == frozenset(
        {"B-17", "C-05", "E-09"}
    )
    assert stratum.parse_excluidos("") == frozenset()
    assert stratum.parse_excluidos("  ,, ") == frozenset()


def test_g8n6_la_colision_entre_pares_nombra_los_grupos_ordenados(db):
    """Primera regresión de la guarda de COLISIÓN, y G8-N-6 de paso.

    Sobre el acta real dispara 0 veces —la de round-trip ya elimina un miembro
    de cada grupo que chocaba— así que nadie la había ejercitado. Aquí choca
    de verdad y sin reciclados de por medio: dos `pair_id` DISTINTOS de la
    hoja con los mismos dos lados en orden opuesto canonizan al mismo
    `(LEAST, GREATEST)`, así que el segundo INSERT moriría en el
    `ON CONFLICT DO NOTHING` contado como `ya_presentes` y, con veredictos
    contradictorios, ganaría el primero por orden alfabético del pair_id.

    G8-N-6: el mensaje ordena los GRUPOS pero antes no las listas internas
    —a diferencia de `sorted(bad)` y `sorted(reciclados)`—, así que el error
    salía como `[['B-02','B-01']]`. Un diagnóstico que el operador copia y
    pega a `--excluir` no puede depender del orden de iteración de un dict."""
    factory, created = db
    prefix = "g8n6-" + uuid.uuid4().hex[:8]
    sid = _mk_source(factory, f"legacy:{prefix}")
    created["sources"].append(sid)
    vac = _mk_legacy_pair(factory, sid, prefix, 2)
    created["dedup_refs"] += [ext for _v, ext in vac]
    cohort = f"stratum-{prefix}"
    created["cohorts"].append(cohort)
    (va, _ea), (vb, _eb) = vac

    candidates = {"B": [_cand(va, vb, 0.9), _cand(vb, va, 0.8)]}
    with pytest.raises(ValueError) as exc:
        # el acta llega con B-02 ANTES que B-01: el orden de iteración del
        # dict es el que se cuela en el mensaje si no se ordena la lista.
        _load(factory, candidates,
              {"B-02": "distinct", "B-01": "duplicate"}, cohort=cohort)
    assert "canonizan al MISMO par" in str(exc.value)
    assert "[['B-01', 'B-02']]" in str(exc.value), str(exc.value)
    assert _pairs_in_cohort(factory, cohort) == []

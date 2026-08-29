"""Dedup semántico nivel 3 (F-5) — generador de candidatos cross-source.

La capacidad que dedup_recall exigía y la Fase B no construyó (techo medido
0,073). Estos tests fijan: detección cross-source por similitud, el descarte
INTRA-fuente por diseño (el 94 % de los falsos positivos del legacy), el
umbral, la idempotencia (uq_dedup_pair) y que la métrica del gate cuenta el
candidato como "core dice duplicado". BD vía core-migrate.
"""

import asyncio
import os
import re
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import embeddings
from jobhunt_core.config import settings
from jobhunt_core.harvest import normalize as normalize_mod
from jobhunt_core.dedup import scan_semantic_candidates
from jobhunt_core.harvest.sink import RawListing, RawListingSink
from jobhunt_core.tests import dbcleanup

class KeywordBackend:
    """Vectores DETERMINISTAS por palabra clave: 'python' en el texto ⇒ eje X,
    si no ⇒ eje Y. Mismo título ⇒ sim 1.0; títulos de familias distintas ⇒
    sim 0.0 EXACTA. (DirectionalBackend no sirve aquí: su ángulo depende del
    TEXTO COMPUESTO por la receta —título+empresa+descripción—, no del título,
    y dos textos "lejanos" pueden caer a <18° por azar del sha.)"""

    def encode_batch(self, texts):
        out = []
        for t in texts:
            v = [1.0, 0.0] if "python" in t.lower() else [0.0, 1.0]
            out.append(v + [0.0] * (embeddings.EMBED_DIM - 2))
        return out

class CasiBackend(KeywordBackend):
    """KeywordBackend + un vector a EXACTAMENTE 0.96 del eje X para textos
    con 'casi': cos([1,0],[0.96,0.28]) = 0.96 y ‖(0.96,0.28)‖ = 1.0 — por
    encima del umbral 0.95 pero ESTRICTAMENTE más lejos que los intra (1.0),
    que es la geometría que reprodujo B-2."""

    def encode_batch(self, texts):
        out = []
        for t in texts:
            if "casi" in t.lower():
                out.append([0.96, 0.28] + [0.0] * (embeddings.EMBED_DIM - 2))
            else:
                out.extend(super().encode_batch([t]))
        return out


class VecinoBackend(KeywordBackend):
    """[0.99, 0.1] para 'cerca': cos con el eje X = 0.99/√0.9901 ≈ 0.99497 —
    por encima del umbral 0.95 y MÁS LEJOS que los 350 intra idénticos
    (dist 0): la geometría del revisor de la ronda 2 que reproduce el
    underfill REAL del HNSW (350+5 ⇒ 0/5 vecinos, estable)."""

    def encode_batch(self, texts):
        out = []
        for t in texts:
            if "cerca" in t.lower():
                out.append([0.99, 0.1] + [0.0] * (embeddings.EMBED_DIM - 2))
            else:
                out.extend(super().encode_batch([t]))
        return out


pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

SHA = "e" * 40


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"sources": [], "scopes": [], "models": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "DELETE FROM dedup_candidates WHERE vacancy_a IN ("
                    " SELECT i.vacancy_id FROM source_listing_incarnations i"
                    " JOIN source_listings l ON l.id = i.source_listing_id"
                    " WHERE l.source_id = ANY(:srcs)) OR vacancy_b IN ("
                    " SELECT i.vacancy_id FROM source_listing_incarnations i"
                    " JOIN source_listings l ON l.id = i.source_listing_id"
                    " WHERE l.source_id = ANY(:srcs))"
                ),
                {"srcs": created["sources"]},
            )
            await dbcleanup.purge_source_graph(s, created["sources"], created["scopes"])
            for mid in created["models"]:
                await dbcleanup.purge_model(s, mid)
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _listing(ext, title, loc=None, company="ACME AG"):
    payload = {
        "title": title, "company_name": company,
        "description": f"puesto {title}", "tags": ["t"],
    }
    if loc is not None:
        payload["location"] = loc
    return RawListing(external_id=ext, url=f"https://x/{ext}", payload=payload)


def _setup(factory, created, por_fuente, backend_cls=KeywordBackend,
           name_prefix="dedup-src"):
    """Siembra N fuentes con sus títulos, registra modelo y embebe con el
    backend determinista (mismo título ⇒ mismo vector ⇒ sim 1.0)."""
    from jobhunt_core.tasks.embedding import run_pending_task

    async def go():
        async with factory() as s:
            for i, titles in enumerate(por_fuente):
                source_id, scope_id = uuid.uuid4(), uuid.uuid4()
                created["sources"].append(source_id)
                created["scopes"].append(scope_id)
                name = f"{name_prefix}-{i}-{source_id.hex[:6]}"
                # Sin normalizador el sink NO crea la revisión canónica
                # (current_offer_revision_id queda NULL) y el corpus del
                # generador no ve la vacante. Uno trivial por fuente.
                normalize_mod.register_normalizer(
                    name,
                    lambda raw: {
                        "title": raw.get("title"),
                        "company": raw.get("company_name"),
                        "description": raw.get("description"),
                        "tags": raw.get("tags") or [],
                        # location NO entra en text_hash pero SÍ en la regla
                        # del exacto-intra (multi-ciudad) — debe viajar.
                        "location": raw.get("location"),
                    },
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO sources (id, name, tier) VALUES (:id, :n, 0)"
                    ),
                    {"id": source_id, "n": name},
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
                    tuple(
                        _listing(f"s{i}-j{j}", *(t if isinstance(t, tuple) else (t,)))
                        for j, t in enumerate(titles)
                    ),
                )
                await s.commit()
            mid = await embeddings.register_model(s, "modelo-dedup", SHA)
            created["models"].append(mid)
            await s.commit()
            return mid

    mid = asyncio.run(go())
    embeddings.set_backend_factory(lambda name, version: backend_cls())
    try:
        r = run_pending_task.apply(kwargs={"limit": 400})
        assert r.successful()
    finally:
        embeddings.set_backend_factory(None)
    return mid


def _scan(factory, window=0):
    async def go():
        async with factory() as s:
            r = await scan_semantic_candidates(s, window_hours=window)
            await s.commit()
            return r

    return asyncio.run(go())


def _pairs(factory, created):
    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT d.similarity, d.state FROM dedup_candidates d "
                        "WHERE d.vacancy_a IN ("
                        " SELECT i.vacancy_id FROM source_listing_incarnations i"
                        " JOIN source_listings l ON l.id = i.source_listing_id"
                        " WHERE l.source_id = ANY(:srcs))"
                    ),
                    {"srcs": created["sources"]},
                )
            ).all()

    return asyncio.run(go())


def test_cross_source_igual_titulo_genera_candidato_e_intra_no(db):
    factory, created = db
    # misma oferta en DOS fuentes (cross, sim 1.0) + un near-dup INTRA-fuente
    _setup(factory, created, [["python dev", "python dev"], ["python dev"]])
    r = _scan(factory)
    assert r["status"] == "ok" and r["candidatos_nuevos"] >= 1
    pares = _pairs(factory, created)
    # 2 pares cross-source por ANN + 1 INTRA por contenido EXACTO (regla
    # ratificada 2026-08-23: mismo text_hash + misma location ⇒ duplicado;
    # aquí ambos listings de s0 comparten título y location por fixture).
    assert len(pares) == 3
    assert r["candidatos_exactos_intra"] >= 1
    assert all(p.state == "pending" and float(p.similarity) >= 0.99 for p in pares)
    # idempotencia: segunda pasada no duplica (uq_dedup_pair)
    r2 = _scan(factory)
    assert r2["candidatos_nuevos"] == 0 and r2["candidatos_exactos_intra"] == 0
    assert len(_pairs(factory, created)) == 3


def test_titulos_distintos_bajo_umbral_no_generan(db):
    factory, created = db
    # familias distintas del KeywordBackend: 'python dev' ⇒ eje X,
    # 'guardabosques' ⇒ eje Y — ortogonales EXACTOS, sim 0.0 < 0.95
    _setup(factory, created, [["python dev"], ["guardabosques"]])
    r = _scan(factory)
    assert r["candidatos_nuevos"] == 0
    assert _pairs(factory, created) == []


def test_b2_concentracion_intra_no_oculta_al_vecino_cross(db):
    """Regresión B-2 (auditoría externa 2026-08-23): 6 vacantes de la MISMA
    fuente a sim 1.0 entre sí + 1 cross-source a 0.96, con k=5. Con la
    exclusión de la propia fuente en Python DESPUÉS del LIMIT, los vecinos
    intra consumían el presupuesto k y el par cross era invisible desde el
    lado concentrado (solo el lado contrario aportaba 5 de los 6 pares).
    Con la exclusión en SQL antes del LIMIT deben salir los 6 pares cross."""
    factory, created = db
    _setup(
        factory, created,
        # títulos DISTINTOS (text_hash distinto ⇒ el exacto-intra no dispara)
        # pero todos con 'python' ⇒ mismo vector ⇒ sim 1.0 entre los 6
        [[(f"python dev {j}", None, f"Emp{'abcdef'[j]}rossa AG")
          for j in range(6)], ["casi python dev"]],
        backend_cls=CasiBackend,
    )
    r = _scan(factory)
    assert r["status"] == "ok" and r["candidatos_exactos_intra"] == 0
    pares = _pairs(factory, created)
    assert len(pares) == 6
    assert all(
        p.state == "pending" and abs(float(p.similarity) - 0.96) < 0.005
        for p in pares
    )


def test_la_metrica_cuenta_el_candidato_como_deteccion(db):
    """El puente con el gate: un par en dedup_candidates con state='pending'
    hace que _dedup_confusion cuente TP para un par etiquetado 'duplicate'
    (es la definición de "core dice duplicado" que F-5 debía alcanzar)."""
    from jobhunt_core.shadow.metrics import _dedup_candidate_pairs

    factory, created = db
    _setup(factory, created, [["python dev"], ["python dev"]])
    _scan(factory)

    async def go():
        async with factory() as s:
            vacs = (
                await s.execute(
                    sa.text(
                        "SELECT DISTINCT i.vacancy_id FROM source_listing_incarnations i "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.source_id = ANY(:srcs)"
                    ),
                    {"srcs": created["sources"]},
                )
            ).all()
            ids = sorted((r.vacancy_id for r in vacs), key=str)
            return await _dedup_candidate_pairs(s, ids)

    detected = asyncio.run(go())
    assert len(detected) == 1  # el par cross-source, canónico


def test_exacto_intra_respeta_multi_ciudad(db):
    """Regla ratificada (2026-08-23, curación D-34/D-40): contenido idéntico
    pero LOCATION distinta = publicación multi-ciudad legítima — NO candidato.
    Misma location ⇒ sí."""
    factory, created = db
    mid = _setup(factory, created, [[]])  # fuente sin ofertas: solo el modelo

    async def go():
        async with factory() as s:
            src = created["sources"][0]
            scope = created["scopes"][0]
            # dos pares intra: uno misma location, otro ciudad distinta
            from jobhunt_core.harvest.sink import RawListing, RawListingSink
            def _l(ext, loc):
                return RawListing(
                    external_id=ext, url=f"https://x/{ext}",
                    payload={"title": "python dev", "company_name": "ACME AG",
                             "description": "d", "tags": [], "location": loc},
                )
            await RawListingSink().handle(
                s, str(scope),
                (_l("l-a", "Zurich"), _l("l-b", "Zurich"),
                 _l("l-c", "Berna")),
            )
            await s.commit()

    asyncio.run(go())
    r = _scan(factory)
    # (l-a,l-b) misma location ⇒ candidato exacto; (·,l-c) Berna ⇒ NO por
    # exacto... aunque el ANN puede añadirlos si comparten vector — por eso
    # se comprueba el DESGLOSE del exacto, no el total.
    assert r["candidatos_exactos_intra"] == 1


def test_gate_puntua_solo_la_cohorte_holdout(db):
    """Regresión auditoría Nº2 (2026-08-23, BLOQUEANTE 1): _dedup_rows y
    _labels_ready_row puntúan SOLO la cohorte DEDUP_EVAL_COHORT. Con la
    mezcla antigua, un TP de development absorbía el FN del holdout
    (recall 1/2 = 0.5); filtrado, el holdout suspende solo: recall 0.0."""
    from jobhunt_core.shadow.labels import DEDUP_EVAL_COHORT
    from jobhunt_core.shadow.metrics import M_DEDUP_RECALL, _dedup_rows, _labels_ready_row

    factory, created = db
    # 4 fuentes legacy:* — s0/s1 mismo título (candidato cross = detección);
    # s2/s3 ortogonales (sin candidato). external_ids únicos por run.
    _setup(
        factory, created,
        [["python dev"], ["python dev"], ["python dev x"], ["guardabosques"]],
        name_prefix="legacy:dedup-test",
    )
    _scan(factory)  # genera el candidato del par s0-s1

    refs = {}

    async def prepara():
        async with factory() as s:
            rows = (
                await s.execute(
                    sa.text(
                        "SELECT l.external_id, s2.name FROM source_listings l "
                        "JOIN sources s2 ON s2.id = l.source_id "
                        "WHERE l.source_id = ANY(:srcs)"
                    ),
                    {"srcs": created["sources"]},
                )
            ).all()
            for r in rows:
                # name = legacy:dedup-test-<i>-<hex>: índice de fuente
                refs[int(r.name.split("-")[2])] = r.external_id
            ins = sa.text(
                "INSERT INTO labeled_dedup_pairs "
                "(job_ref_a, job_ref_b, verdict, source) "
                "VALUES (:a, :b, 'duplicate', :src) "
                "ON CONFLICT (LEAST(job_ref_a, job_ref_b), "
                "GREATEST(job_ref_a, job_ref_b), source) DO NOTHING"
            )
            # development: el par DETECTADO (TP si contara)
            await s.execute(ins, {"a": refs[0], "b": refs[1], "src": "curado-test"})
            # holdout: par NO detectado (FN real del examen)
            await s.execute(
                ins, {"a": refs[2], "b": refs[3], "src": DEDUP_EVAL_COHORT}
            )
            await s.commit()

    asyncio.run(prepara())
    try:
        async def evalua():
            async with factory() as s:
                rows = await _dedup_rows(s)
                ready = await _labels_ready_row(s, [])
                return rows, ready

        rows, ready = asyncio.run(evalua())
        recall = next(r for r in rows if r[0] == M_DEDUP_RECALL)
        # SOLO el holdout: tp=0, fn=1 ⇒ 0.0 (mezclado habría sido 0.5)
        assert recall[1] == 0.0
        assert recall[2]["cohorte"] == DEDUP_EVAL_COHORT
        assert recall[2]["pares"] == 1
        # la precondición cuenta lo que el gate puntúa: 1 par, no 2
        assert ready[2]["pares_dedup"] == 1
    finally:
        async def limpia():
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "DELETE FROM labeled_dedup_pairs "
                        "WHERE job_ref_a = ANY(:refs) OR job_ref_b = ANY(:refs)"
                    ),
                    {"refs": list(refs.values())},
                )
                await s.commit()

        asyncio.run(limpia())


def test_hnsw_underfill_cae_al_scan_exacto(db):
    """Regresión auditoría Nº2 IMPORTANTE 1, versión de la RONDA 2 de la
    revisión: mi afirmación de que el underfill real era irreproducible en
    tests quedó REFUTADA — el revisor lo reprodujo estable con 350 vectores
    intra [1,0,…] + 5 cross [0.99,0.1,…] (ef_search=40, strict_order,
    max_scan_tuples=1, seq scan vetado ⇒ 0/5 vecinos). Esta es esa
    geometría, de verdad: el aproximado se queda a cero, el fallback exacto
    (espía SOLO-observador: enable_indexscan = off ejecutado) recupera los
    5 pares a ~0.995. Barato: solo UNA revisión queda "nueva" en la ventana
    (las 354 distracciones se envejecen), así que el scan procesa una única
    consulta kNN sobre un HNSW que contiene toda la geometría."""
    import jobhunt_core.dedup as dedup_mod

    factory, created = db
    _setup(
        factory, created,
        [[(f"python dev {j}", None,
           f"emp{chr(97 + j // 26)}{chr(97 + j % 26)} AG") for j in range(350)],
         [(f"cerca {j}", None, f"otr{chr(97 + j)} AG") for j in range(5)]],
        backend_cls=VecinoBackend,
    )

    # Envejecer todo menos s0-j0: la ventana de 1 h deja UNA consulta.
    async def envejece():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE offer_revisions SET created_at = "
                    "  created_at - interval '2 hours' "
                    "WHERE vacancy_id IN ("
                    "  SELECT i.vacancy_id FROM source_listing_incarnations i "
                    "  JOIN source_listings l ON l.id = i.source_listing_id "
                    "  WHERE l.source_id = ANY(:srcs) "
                    "    AND l.external_id <> 's0-j0')"
                ),
                {"srcs": created["sources"]},
            )
            await s.commit()

    asyncio.run(envejece())

    ejecutadas: list[str] = []
    original = dedup_mod.MAX_SCAN_TUPLES
    dedup_mod.MAX_SCAN_TUPLES = 1  # el presupuesto del repro del revisor
    try:
        async def go():
            async with factory() as s:
                real = s.execute

                async def espia(stmt, *args, **kwargs):
                    q = str(stmt)
                    ejecutadas.append(q)
                    res = await real(stmt, *args, **kwargs)
                    # Los vetos seqscan/sort son ARTIFICIO DEL TEST para
                    # forzar el plan HNSW en la primera pasada; dejarlos
                    # puestos durante el fallback penalizaría también a la
                    # consulta exacta (el planner re-elige el índice
                    # estrangulado y devuelve 0 otra vez). El espía los
                    # levanta mientras la rama exacta está activa y los
                    # repone después — simétrico a los toggles del scan.
                    if "enable_indexscan = off" in q:
                        await real(sa.text("SET LOCAL enable_seqscan = on"))
                        await real(sa.text("SET LOCAL enable_sort = on"))
                    elif "enable_indexscan = on" in q:
                        await real(sa.text("SET LOCAL enable_seqscan = off"))
                        await real(sa.text("SET LOCAL enable_sort = off"))
                    return res

                s.execute = espia  # SOLO observa y gestiona los vetos
                # sin seq scan ni sort el ORDER BY vectorial solo puede
                # resolverse por el índice HNSW (a esta escala el planner
                # preferiría nested loops + Sort y jamás habría underfill)
                await s.execute(sa.text("SET LOCAL enable_seqscan = off"))
                await s.execute(sa.text("SET LOCAL enable_sort = off"))
                r = await scan_semantic_candidates(s, window_hours=1)
                await s.commit()
                return r

        r = asyncio.run(go())
    finally:
        dedup_mod.MAX_SCAN_TUPLES = original

    assert r["status"] == "ok" and r["escaneadas"] == 1
    # la rama exacta SE EJECUTÓ (el aproximado devolvió 0/5 reales)
    assert any("enable_indexscan = off" in q for q in ejecutadas)
    pares = _pairs(factory, created)
    assert len(pares) == 5
    assert all(float(p.similarity) >= 0.99 for p in pares)


def test_ann_respeta_la_regla_multi_ciudad(db):
    """Regresión TRACK R.2a (2026-08-24): el examen del holdout midió
    precision 0.636 — el ANN proponía como duplicado el mismo texto
    publicado en ciudades DISTINTAS (la regla multi-ciudad existía solo en
    el exacto-intra). Con el guard de ubicación en SQL (antes del LIMIT,
    lección B-2): ciudad incompatible ⇒ sin candidato; contención
    («Zürich» ⊂ «Zürich, Zürich») o ubicación vacía ⇒ candidato."""
    factory, created = db
    _setup(
        factory, created,
        [
            [("python dev", "Zürich", "Uno AG")],
            [("python dev", "Bern", "DosBe AG"),               # multi-ciudad: NO
             ("python dev b2", "Zürich, Zürich", "TresCe AG"), # contenida: SÍ
             ("python dev b3", "", "CuatroDe AG")],            # sin dato: SÍ
        ],
    )
    r = _scan(factory)
    assert r["status"] == "ok"
    pares = _pairs(factory, created)
    # a–b2 y a–b3; a–b1 (Bern) queda vetado pese a sim 1.0
    assert len(pares) == 2
    assert all(float(p.similarity) >= 0.99 for p in pares)
    assert r["candidatos_exactos_intra"] == 0


def test_generador_lexico_cross_portal(db):
    """Regresión TRACK R.2b (2026-08-24): el examen del holdout midió
    recall 0.259 — el ANN a 0.95 detecta 0/9 duplicados cross-portal
    reales (dev-2): descripciones dispares y EMPRESA escrita distinta por
    portal. El generador léxico los captura por token significativo de
    empresa + trgm de título >= 0.65 + ubicación compatible v2, y respeta:
    roles distintos (trgm bajo), multi-ciudad (loc incompatible), remoto
    solo con remoto, y mismo portal excluido."""
    factory, created = db
    # títulos casi idénticos LÉXICAMENTE pero en ejes distintos del
    # KeywordBackend («python» solo en la fuente 0): embedding sim = 0 ⇒ el
    # ANN queda fuera y el camino léxico se mide AISLADO (sin él, el ANN a
    # sim 1.0 insertaba el par primero y el léxico moría en el ON CONFLICT)
    _setup(
        factory, created,
        [
            [("python klassische archäologie (open rank)", "Basel",
              "Universität Basel")],
            [("pyton klassische archäologie (open rank)", "Basel-Stadt",
              "University of Basel"),           # dup: token basel + trgm alto
             ("pyton klassische archäologie (open rank)", "Genf",
              "Universität Basel"),             # multi-ciudad: loc incompatible
             ("bibliothek klassische sammlung", "Basel",
              "Universität Basel"),             # rol distinto: trgm bajo
             ("pyton klassische archäologie (open rank)", "remote",
              "Otra Uni AG")],                  # sin token común de empresa
        ],
    )
    r = _scan(factory)
    assert r["status"] == "ok"
    assert r["candidatos_lexicos"] == 1  # SOLO el par Basel–Basel-Stadt
    pares = _pairs(factory, created)
    # el léxico añade el par aunque el embedding no llegue a 0.95 (los
    # títulos difieren => vectores KeywordBackend ortogonales o no: da
    # igual — la similitud registrada es el trgm del título)
    assert len(pares) == 1  # y NINGÚN candidato del ANN (ejes ortogonales)
    assert 0.65 <= float(pares[0].similarity) <= 1.0  # trgm del título


def test_backfill_lexico_cubre_corpus_viejo_y_firma_de_gran_empleador(db):
    """Regresión revisión Track R, P1-1 + P1-2: (a) el beat (ventana 48 h)
    NO ve pares con ambas revisiones viejas — reproducción del revisor:
    scan sin argumentos ⇒ 0; lexical_backfill ⇒ 1; segundo backfill
    idempotente ⇒ 0. (b) El par pertenece a un GRAN empleador (51 vacantes
    Megacorp): el tope de frecuencia por token lo silenciaba; la rama de
    FIRMA completa de empresa lo recupera."""
    from jobhunt_core.dedup import lexical_backfill, scan_semantic_candidates as scan

    factory, created = db
    _setup(
        factory, created,
        [[(f"puesto {chr(97 + j // 5)}{chr(97 + j % 5)}" * 3, "Berlin",
           "Megacorp AG") for j in range(50)]
         + [("python data engineer", "Berlin", "Megacorp AG")],
         [("pyton data engineer", "Berlin", "Megacorp GmbH")]],
    )

    async def go():
        async with factory() as s:
            await s.execute(sa.text(
                "UPDATE offer_revisions SET created_at = created_at - interval '72 hours' "
                "WHERE vacancy_id IN (SELECT i.vacancy_id FROM source_listing_incarnations i "
                " JOIN source_listings l ON l.id = i.source_listing_id "
                " WHERE l.source_id = ANY(:s))"), {"s": created["sources"]})
            await s.commit()
            r_beat = await scan(s)  # camino real del beat: ventana 48 h
            await s.commit()
            b1 = await lexical_backfill(s)
            await s.commit()
            b2 = await lexical_backfill(s)
            await s.commit()
            return r_beat, b1, b2

    r_beat, b1, b2 = asyncio.run(go())
    assert r_beat["candidatos_lexicos"] == 0  # invisible para el beat
    assert b1 >= 1                            # el backfill lo encuentra
    assert b2 == 0                            # idempotente
    pares = _pairs(factory, created)
    assert len(pares) == 1
    # Re-confirmación P1-A: la TAREA Celery ejecuta de verdad la corrutina
    # (devolvía el objeto coroutine sin correr el backfill)
    from jobhunt_core.tasks.maintenance import dedup_lex_backfill_task

    r_task = dedup_lex_backfill_task.apply()
    assert r_task.successful()
    assert r_task.result["candidatos"] == 0  # tercera pasada: idempotente


def test_compatibilidad_de_ubicacion_semantica(db):
    """Regresión revisión Track R, P1-3: los casos fijados por el revisor
    sobre la expresión ÚNICA compartida por ANN y léxico."""
    from jobhunt_core.dedup import _loc_compat_sql

    factory, _ = db
    casos = [
        ("Zürich", "Zürich, Zürich", True),
        ("Basel", "Basel-Stadt", True),
        ("York", "New York", False),
        ("Bern", "Bernau", False),
        ("remote", "Remote - Europe", True),
        # Revisión FASE 2 P1-3: el comodín unilateral queda RETIRADO (la
        # evidencia era vacua: 0/12 pares elegibles). Bilateral restaurado.
        ("remote", "Berlin", False),
        ("Bulgaria, Romania", "Greece, Bulgaria", True),
        ("", "Berlin", True),
        ("LU", "Emmen", True),
        # Re-confirmación P1-B: país/cantón compartido en COLA de lista no
        # es la misma ciudad
        ("Berlin, Germany", "Munich, Germany", False),
        ("Schänis, St. Gallen", "Flums, St. Gallen", False),
        ("Dublin", "Dublin, County Dublin", True),
        # FASE 2 (P1-2): SOLO el prefijo postal se retira — los dígitos
        # semánticos de zona se conservan
        ("Triesen, Liechtenstein", "Landstrasse 83, 9495 Triesen", True),
        ("District 1", "District 2", False),
        # fase 3: husos horarios = remoto (evidencia proxify CET/Anywhere)
        ("CET (+/- 3 hours)", "Anywhere in the World", True),
        ("Grand Est", "remote", False),
        # auditoría C1 P3-1: ciudad + huso NO es remoto — sigue casando
        # consigo misma y NO casa con remoto puro
        ("Zürich (CET)", "Zürich", True),
        ("Zürich (CET)", "remote", False),
        # C2-P3: alfabetos no latinos son CONCRETOS; husos en DE también
        ("Женева (CET)", "remote", False),
        ("CET (+/- 3 Stunden)", "Anywhere in the World", True),
        ("CET (+/- 1 Stunde)", "remote", True),   # C3: singular DE
        ("CET-Zeit", "Anywhere in the World", True),  # C3: sufijo Zeit
        ("Sector 4", "Sector 5", False),
    ]

    async def go():
        async with factory() as s:
            out = []
            for a, b, _e in casos:
                v = (await s.execute(
                    sa.text("SELECT " + _loc_compat_sql("CAST(:a AS text)", "CAST(:b AS text)")),
                    {"a": a, "b": b})).scalar_one()
                out.append(v)
            return out

    got = asyncio.run(go())
    for (a, b, esperado), v in zip(casos, got):
        assert v is esperado, f"{a!r} vs {b!r}: {v} != {esperado}"


def test_similarity_es_el_maximo_mientras_pending(db):
    """Regresión revisión Track R, P2-2: el par conserva la MÁXIMA evidencia
    entre generadores mientras esté pending (semántica del sink); los
    resueltos no se reabren ni actualizan."""
    factory, created = db
    _setup(factory, created, [["python dev"], ["python dev"]])

    async def go():
        async with factory() as s:
            vacs = (await s.execute(sa.text(
                "SELECT DISTINCT i.vacancy_id FROM source_listing_incarnations i "
                "JOIN source_listings l ON l.id = i.source_listing_id "
                "WHERE l.source_id = ANY(:s) ORDER BY 1"), {"s": created["sources"]})).all()
            a, b = vacs[0].vacancy_id, vacs[1].vacancy_id
            await s.execute(sa.text(
                "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
                "VALUES (gen_random_uuid(), :a, :b, 0.500)"), {"a": a, "b": b})
            await s.commit()
            return a, b

    a, b = asyncio.run(go())
    _scan(factory)  # el ANN ve sim 1.0 ⇒ actualiza el pending 0.500
    pares = _pairs(factory, created)
    assert len(pares) == 1 and float(pares[0].similarity) >= 0.99

    async def resuelve_y_reescanea():
        async with factory() as s:
            await s.execute(sa.text(
                "UPDATE dedup_candidates SET state = 'rejected', similarity = 0.400 "
                "WHERE vacancy_a IN (:a, :b)"), {"a": a, "b": b})
            await s.commit()

    asyncio.run(resuelve_y_reescanea())
    _scan(factory)
    pares = _pairs(factory, created)
    assert pares[0].state == "rejected"          # no se reabre
    assert float(pares[0].similarity) == 0.400   # ni se actualiza


def test_fase2_intra_normalizado_y_revalidacion_por_regla(db):
    """FASE 2 Track R: (a) el brazo INTRA-fuente detecta el duplicado con
    texto distinto cuyo título NORMALIZADO coincide (fuera (m/w/d), %,
    dígitos), con umbral propio 0.90 que deja fuera al rol parecido; la
    multi-ciudad intra queda vetada por ubicación. (b) El barrido
    revalidate_pending_candidates aplica la regla de ubicación UNIFORME a
    los pendientes: rechaza el que la viola, no toca al compatible ni al
    ya resuelto."""
    from jobhunt_core.dedup import revalidate_pending_candidates

    factory, created = db
    _setup(
        factory, created,
        # ronda 2 P1-2: % se CONSERVA — la variante 80-100% vs 80% pasa a
        # ser FN intra conocido (pronunciamiento del revisor: dos pensums
        # pueden ser dos plazas). El dup del test es la variante de género.
        [[("Fachperson Betreuung (m/w/d)", "Luzern", "Stift Uno AG"),
          ("Fachperson Betreuung", "Luzern", "Stift Uno AG"),
          ("Fachperson Beteiligung 80%", "Luzern", "Stift Uno AG"),
          ("Fachperson Betreuung 60%", "Bern", "Stift Uno AG")]],
    )
    r = _scan(factory)
    assert r["status"] == "ok"
    assert r["candidatos_lexicos"] == 1  # SOLO el par normalizado idéntico
    pares = _pairs(factory, created)
    assert len(pares) == 1 and float(pares[0].similarity) >= 0.99

    async def prepara_y_revalida():
        async with factory() as s:
            vacs = (await s.execute(sa.text(
                "SELECT i.vacancy_id, l.external_id "
                "FROM source_listing_incarnations i "
                "JOIN source_listings l ON l.id = i.source_listing_id "
                "WHERE l.source_id = ANY(:s) ORDER BY l.external_id"),
                {"s": created["sources"]})).all()
            v = {r2.external_id: r2.vacancy_id for r2 in vacs}
            # pendiente que VIOLA la regla (Luzern vs Bern) + uno resuelto
            await s.execute(sa.text(
                "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
                "VALUES (gen_random_uuid(), :a, :b, 0.910)"),
                {"a": v["s0-j0"], "b": v["s0-j3"]})
            await s.execute(sa.text(
                "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, "
                "similarity, state) VALUES (gen_random_uuid(), :a, :b, 0.920, "
                "CAST('rejected' AS dedup_candidate_state))"),
                {"a": v["s0-j2"], "b": v["s0-j3"]})
            await s.commit()
            prev = await revalidate_pending_candidates(s)  # preview: no escribe
            ap = await revalidate_pending_candidates(s, apply=True)
            await s.commit()
            seg = await revalidate_pending_candidates(s, apply=True)
            await s.commit()
            meta = (await s.execute(sa.text(
                "SELECT resolved_by, resolved_at FROM dedup_candidates "
                "WHERE state = 'rejected' AND resolved_by IS NOT NULL"))).all()
            return prev, ap, seg, meta

    prev, ap, seg, meta = asyncio.run(prepara_y_revalida())
    # preview y aplicación COINCIDEN (P2-1); idempotencia: segunda ⇒ 0
    assert prev["n"] == 1 and ap["n"] == 1
    assert prev["hash_ids"] == ap["hash_ids"]
    assert seg["n"] == 0
    # procedencia registrada en la misma sentencia
    assert meta and all(
        m.resolved_by == "rule:track-r-location-v2" and m.resolved_at
        for m in meta
    )
    pares = sorted(_pairs(factory, created), key=lambda p: float(p.similarity))
    estados = [(float(p.similarity), p.state) for p in pares]
    assert (0.910, "rejected") in estados   # violaba la regla ⇒ rechazado
    assert (0.920, "rejected") in estados   # ya resuelto: intacto
    assert any(s == "pending" and sim >= 0.99 for sim, s in estados)


def test_normalizacion_de_titulo_allowlist(db):
    """Revisión FASE 2 P1-1: la normalización solo elimina ruido de una
    ALLOWLIST estrecha ((m/w/d) y variantes, prefijo ^eks:) — los
    porcentajes se CONSERVAN (ronda 2 P1-2) y lo desconocido
    se CONSERVA: Frontend/Backend, C/C++/C#, L1/L2, Python 2/3 y ciudades
    entre paréntesis siguen siendo distinguibles."""
    from jobhunt_core.dedup import _title_norm_sql

    casos_iguales = [  # ruido mecánico: deben normalizar IGUAL
        ("Ingeniera (w/m/d)", "Ingeniera"),
        ("Developer (m/f/d)", "Developer"),
        # ronda 2 P2-1: SOLO el prefijo anclado ^eks: (literal probado)
        ("Eks: Vil du være med", "Vil du være med"),
    ]
    casos_distintos = [  # semántica: deben normalizar DISTINTO
        ("Software Engineer (Frontend)", "Software Engineer (Backend)"),
        ("L1 Support", "L2 Support"),
        ("Python 2 maintainer", "Python 3 maintainer"),
        ("Staff Engineer (Campinas)", "Staff Engineer (Mexico City)"),
        # ronda 2 P1-2: % FUERA de la allowlist — pensums distintos son
        # DOS plazas (IPOS-03)
        ("Fachperson Betreuung Kinder 55%", "Fachperson Betreuung Kinder 27%"),
        # 'eks' fuera del prefijo inicial permanece
        ("Eks: algo", "algo eks algo"),
    ]
    factory, _ = db

    async def norm(x):
        async with factory() as s:
            return (await s.execute(
                sa.text("SELECT " + _title_norm_sql("CAST(:x AS text)")),
                {"x": x})).scalar_one()

    for a, b in casos_iguales:
        na, nb = asyncio.run(norm(a)), asyncio.run(norm(b))
        assert na == nb.lower().strip() or na == nb, (a, b, na, nb)
    for a, b in casos_distintos:
        na, nb = asyncio.run(norm(a)), asyncio.run(norm(b))
        assert na != nb, (a, b, na)


def test_reproducciones_adversariales_fase2_del_revisor(db):
    """Las 3 reproducciones de la revisión FASE 2 que generaban FP: (1)
    Frontend/Backend intra (norm agresiva); (2) District 1/2 cross (dígitos
    de zona borrados); (3) Remote/Berlin cross (comodín unilateral). Con
    las fronteras conservadoras: CERO candidatos."""
    factory, created = db
    _setup(
        factory, created,
        [[("Software Engineer (Frontend)", "Berlin", "ACME AG"),
          ("Software Engineer (Backend)", "Berlin", "ACME AG"),
          ("python warehouse operator", "District 1", "ACME AG"),
          ("python platform engineer", "Remote", "ACME AG")],
         [("python warehouse operator", "District 2", "ACME AG"),
          ("python platform engineer", "Berlin", "ACME AG")]],
    )
    r = _scan(factory)
    assert r["status"] == "ok"
    # ANN: 'python…' cruzados comparten eje (sim 1.0) pero District 1/2 y
    # Remote/Berlin deben quedar vetados; léxico intra: Frontend≠Backend.
    assert _pairs(factory, created) == []


def test_lenguajes_c_no_colapsan_en_el_trigram(db):
    """Ronda 2 FASE 2, P1-1: pg_trgm ignora +/# al construir palabras —
    conservarlos en la cadena no bastaba (similarity('c developer',
    'c++ developer') = 1). Los tokens de lenguaje se CODIFICAN con límites
    (C++⇒cplusplus, C#⇒csharp) antes de la limpieza. Se verifica la
    similitud REAL del filtro y el scan end-to-end: 0 candidatos."""
    from jobhunt_core.dedup import _title_norm_sql

    factory, created = db

    async def sim(a, b):
        async with factory() as s:
            return float((await s.execute(sa.text(
                "SELECT similarity(" + _title_norm_sql("CAST(:a AS text)")
                + ", " + _title_norm_sql("CAST(:b AS text)") + ")"),
                {"a": a, "b": b})).scalar_one())

    assert asyncio.run(sim("C developer", "C++ developer")) < 0.9
    assert asyncio.run(sim("C++ developer", "C# developer")) < 0.9
    assert asyncio.run(sim("C developer", "C# developer")) < 0.9

    _setup(
        factory, created,
        [[("C developer", "Berlin", "ACME AG"),
          ("C++ developer", "Berlin", "ACME AG"),
          ("C# developer", "Berlin", "ACME AG")]],
    )
    r = _scan(factory)
    assert r["status"] == "ok"
    assert _pairs(factory, created) == []


def test_prefijo_eks_recupera_el_dup_sin_bajar_umbral(db):
    """Ronda 2 FASE 2, P2-1: el dup real con prefijo «Eks:» a 0.895 se
    recupera SOLO con la allowlist anclada ^eks: — el umbral intra 0.90 no
    se toca (7 de 8 IHARD eran distinct en esa banda)."""
    factory, created = db
    _setup(
        factory, created,
        [[("Eks: Vil du være med å skape hverdagsmagi?", "Tromsø", "Barnehage Uno AS"),
          ("Vil du være med å skape hverdagsmagi?", "Tromsø", "Barnehage Uno AS")]],
    )
    r = _scan(factory)
    assert r["candidatos_lexicos"] == 1
    pares = _pairs(factory, created)
    assert len(pares) == 1 and float(pares[0].similarity) >= 0.99


# --- Auditoría de OPTIMIZACIÓN 2026-08-25 (OPT-ALTA-1 y OPT-ALTA-2) -------

# Fragmento EXACTO de la CTE firma reescrita (GROUP BY). Si alguien vuelve a
# tocar la CTE, el `assert in` de abajo muere y obliga a re-verificar la
# equivalencia contra la forma correlada de referencia.
_FIRMA_GROUP_BY = (
    "), firma AS ("
    "  SELECT c.id, c.source_id, c.title_n, c.loc, c.th, c.created_at, g.f "
    "  FROM corpus c "
    "  JOIN (SELECT id, string_agg(DISTINCT tok, ' ' ORDER BY tok) AS f "
    "        FROM tok GROUP BY id) g ON g.id = c.id"
)

# La forma ORIGINAL (subconsulta correlada O(n²), 64 de los 67 s del beat a
# 24k) conservada aquí como ORÁCULO de equivalencia semántica.
_FIRMA_CORRELADA = (
    "), firma AS ("
    "  SELECT c.id, c.source_id, c.title_n, c.loc, c.th, c.created_at, "
    "         (SELECT string_agg(x.tok, ' ' ORDER BY x.tok) "
    "          FROM (SELECT DISTINCT t2.tok FROM tok t2 "
    "                WHERE t2.id = c.id) x) AS f "
    "  FROM corpus c "
    "  WHERE EXISTS (SELECT 1 FROM tok WHERE tok.id = c.id)"
)


def test_opt1_firma_group_by_equivale_a_la_correlada(db):
    """Mordida OPT-ALTA-1 (auditoría de optimización 2026-08-25): la CTE
    firma por GROUP BY debe producir EXACTAMENTE el mismo conjunto de
    candidatos que la subconsulta correlada original (24,6× más lenta).
    Fixture con las tres aristas: (a) par detectable SOLO por firma (token
    'stadtverwaltung' repartido en 51 empresas distintas > maxfreq=50 ⇒ la
    vía token queda capada), (b) empresas de solo stopwords/tokens cortos
    ('AG & Co' ⇒ 0 tokens ⇒ fuera de firma — la arista JOIN⇔EXISTS), y
    (c) fillers sin colisión de firma ni de trgm."""
    from jobhunt_core.dedup import _lex_sql

    factory, created = db
    sufijos = [f"{a}{b}{c}" for a in "ab" for b in "abcde" for c in "abcde"]
    assert len(sufijos) == 50
    _setup(
        factory, created,
        [
            [("sachbearbeiter steuern", "Basel", "Stadtverwaltung")]
            + [(f"beruf {s}", "Basel", f"Stadtverwaltung {s}") for s in sufijos],
            [("sachbearbeiter steuern", "Basel", "Stadtverwaltung")],
            [("reinigungskraft", "Basel", "AG & Co")],
            [("reinigungskraft", "Basel", "AG & Co")],
        ],
    )

    sql_nueva = _lex_sql(window=False)
    # Muerde: si la reescritura OPT-1 desaparece o muta, esto falla primero.
    assert _FIRMA_GROUP_BY in sql_nueva
    sql_vieja = sql_nueva.replace(_FIRMA_GROUP_BY, _FIRMA_CORRELADA)
    assert sql_vieja != sql_nueva

    params = {
        "trgm": float(settings.CORE_DEDUP_LEX_TRGM_MIN),
        "trgm_intra": float(settings.CORE_DEDUP_LEX_TRGM_INTRA_MIN),
        "maxfreq": int(settings.CORE_DEDUP_LEX_TOKEN_MAX_FREQ),
    }

    async def run(sql, p=params):
        # Inserta, lee el conjunto y ROLLBACK: ambas formas parten de la
        # misma tabla vacía y son comparables byte a byte.
        async with factory() as s:
            await s.execute(sa.text(sql), p)
            rows = (
                await s.execute(
                    sa.text(
                        "SELECT d.vacancy_a, d.vacancy_b, d.similarity "
                        "FROM dedup_candidates d "
                        "WHERE d.vacancy_a IN ("
                        " SELECT i.vacancy_id FROM source_listing_incarnations i"
                        " JOIN source_listings l ON l.id = i.source_listing_id"
                        " WHERE l.source_id = ANY(:srcs))"
                    ),
                    {"srcs": created["sources"]},
                )
            ).all()
            await s.rollback()
            return sorted((str(r[0]), str(r[1]), str(r[2])) for r in rows)

    set_nueva = asyncio.run(run(sql_nueva))
    set_vieja = asyncio.run(run(sql_vieja))
    # Byte-idénticos, y NO vacíos: exactamente el par que SOLO firma ve
    # (el token está capado y las empresas sin tokens quedan fuera).
    assert set_nueva == set_vieja
    assert len(set_nueva) == 1

    # C9 P3-1: el camino del BEAT (window=True) también contra el oráculo —
    # nuevos_f se deriva de firma y un cambio futuro en filtro_ventana
    # podría divergir SOLO en la pasada incremental. Se envejece la fuente 0
    # (el par sobrevive: basta un miembro en la ventana).
    async def envejecer():
        async with factory() as s:
            await s.execute(sa.text(
                "UPDATE offer_revisions SET created_at = created_at - interval '72 hours' "
                "WHERE vacancy_id IN (SELECT i.vacancy_id FROM source_listing_incarnations i "
                " JOIN source_listings l ON l.id = i.source_listing_id "
                " WHERE l.source_id = :src)"), {"src": created["sources"][0]})
            await s.commit()

    asyncio.run(envejecer())
    sql_nueva_w = _lex_sql(window=True)
    assert _FIRMA_GROUP_BY in sql_nueva_w
    sql_vieja_w = sql_nueva_w.replace(_FIRMA_GROUP_BY, _FIRMA_CORRELADA)
    assert sql_vieja_w != sql_nueva_w
    params_w = dict(params, ventana=48)
    set_nueva_w = asyncio.run(run(sql_nueva_w, params_w))
    set_vieja_w = asyncio.run(run(sql_vieja_w, params_w))
    assert set_nueva_w == set_vieja_w
    assert len(set_nueva_w) == 1  # mismo par: la fuente 1 sigue en ventana


def test_opt2_conteo_omitido_con_k_vecinos_llenos(db):
    """Mordida OPT-ALTA-2 (a): si el kNN llenó su k, _KNN_COUNT_SQL NO debe
    ejecutarse (por el LIMIT :k del conteo, objetivo <= k ⇒ el fallback no
    puede dispararse). Se sabotea el SQL del conteo con una tabla
    inexistente: si el scan lo ejecutara (comportamiento anterior), el test
    muere con ProgrammingError."""
    import jobhunt_core.dedup as dedup_mod

    factory, created = db
    # C9 P3-2: k desde settings, no hardcodeado — si k cambia por env, el
    # fixture sigue llenando el kNN exacto.
    k = int(settings.CORE_DEDUP_KNN)
    _setup(
        factory, created,
        [
            [("python lead", "Basel", "Empresa Uno AG")],
            [(f"python dev {i}", "Basel", "Empresa Dos AG") for i in range(k)],
        ],
    )

    async def go():
        async with factory() as s:
            # La fuente 1 sale de la ventana: solo 'python lead' se escanea,
            # y sus k vecinos cross-source llenan el kNN exacto.
            await s.execute(sa.text(
                "UPDATE offer_revisions SET created_at = created_at - interval '72 hours' "
                "WHERE vacancy_id IN (SELECT i.vacancy_id FROM source_listing_incarnations i "
                " JOIN source_listings l ON l.id = i.source_listing_id "
                " WHERE l.source_id = :src)"), {"src": created["sources"][1]})
            await s.commit()
            r = await scan_semantic_candidates(s, window_hours=1)
            await s.commit()
            return r

    original = dedup_mod._KNN_COUNT_SQL
    dedup_mod._KNN_COUNT_SQL = "SELECT no FROM tabla_inexistente_opt2"
    try:
        r = asyncio.run(go())
    finally:
        dedup_mod._KNN_COUNT_SQL = original
    assert r["status"] == "ok"
    assert r["escaneadas"] == 1
    assert r["candidatos_nuevos"] == k  # los k vecinos a sim 1.0


def test_opt2_conteo_se_ejecuta_con_underfill(db):
    """Mordida OPT-ALTA-2 (b): con MENOS de k vecinos el conteo SÍ se
    ejecuta (el sabotaje revienta), y con el SQL real el camino completo
    sigue respondiendo — el fallback P2-3 queda intacto justo en las filas
    donde hoy puede dispararse."""
    import jobhunt_core.dedup as dedup_mod

    factory, created = db
    _setup(
        factory, created,
        [
            [("java lead", "Basel", "Firma Tres AG")],
            [(f"java dev {i}", "Basel", "Firma Cuatro AG") for i in range(2)],
        ],
    )

    async def envejecer():
        async with factory() as s:
            await s.execute(sa.text(
                "UPDATE offer_revisions SET created_at = created_at - interval '72 hours' "
                "WHERE vacancy_id IN (SELECT i.vacancy_id FROM source_listing_incarnations i "
                " JOIN source_listings l ON l.id = i.source_listing_id "
                " WHERE l.source_id = :src)"), {"src": created["sources"][1]})
            await s.commit()

    async def scan1h():
        async with factory() as s:
            r = await scan_semantic_candidates(s, window_hours=1)
            await s.commit()
            return r

    asyncio.run(envejecer())
    # 2 elegibles < k=5 ⇒ el conteo DEBE ejecutarse: el sabotaje muerde.
    original = dedup_mod._KNN_COUNT_SQL
    dedup_mod._KNN_COUNT_SQL = "SELECT no FROM tabla_inexistente_opt2"
    try:
        with pytest.raises(sa.exc.DBAPIError):
            asyncio.run(scan1h())
    finally:
        dedup_mod._KNN_COUNT_SQL = original
    # Y con el SQL real, el camino completo responde: 2 pares a sim 1.0.
    r = asyncio.run(scan1h())
    assert r["status"] == "ok"
    assert r["candidatos_nuevos"] == 2


def test_el_vector_no_viaja_y_el_orden_kNN_sigue_siendo_una_constante(db):
    """GUARDA de O-3 (auditoría de eficiencia 2026-08-27), dos mitades.

    1. TRANSPORTE. El barrido traía `oe.vector` al cliente para devolverlo
       inmediatamente como parámetro de la consulta siguiente. Medido:
       `avg(length(oe.vector::text)) = 4 690` bytes y 8 311 filas en la
       ventana de 48 h ⇒ ~39 MB de bajada y ~39 MB de subida de datos que
       nunca necesitaron salir de la base. El corpus ya no lo selecciona.

    2. FORMA DEL PLAN. Es la mitad que podía torcerse en silencio: pgvector
       solo usa el índice HNSW si el operando derecho del `<=>` del ORDER BY
       es una CONSTANTE en tiempo de ejecución. La sonda por id no está
       correlacionada con la consulta de fuera, así que el planificador la
       resuelve como InitPlan y el kNN recibe un `$n`. Si alguien la
       convirtiera en correlacionada (un `SubPlan`), el ORDER BY dejaría de
       ser indexable y la consulta caería a barrido secuencial del corpus
       entero SIN que ningún test de comportamiento se enterara.
    """
    import jobhunt_core.dedup as dedup_mod

    factory, _created = db
    assert "oe.vector" not in dedup_mod._CORPUS_SQL
    assert ":vec" not in dedup_mod._KNN_SQL

    async def plan() -> str:
        async with factory() as s:
            rows = (
                await s.execute(
                    sa.text("EXPLAIN " + dedup_mod._KNN_SQL),
                    {
                        "mid": uuid.uuid4(), "k": 5, "vid": uuid.uuid4(),
                        "src": uuid.uuid4(), "loc": "Zürich",
                    },
                )
            ).scalars().all()
            await s.rollback()
            return "\n".join(rows)

    texto = asyncio.run(plan())
    assert "InitPlan" in texto, texto
    # El operando del <=> es un Param ($n), no una expresión correlacionada.
    assert re.search(r"<=> \$\d+", texto), texto
    assert "SubPlan" not in texto, texto


def test_una_cohorte_de_evaluacion_ausente_se_dice_con_todas_las_letras(db):
    """El gate publicaba `pares_dedup: 0` sin nombrar la cohorte, y ese cero se lee
    como «aún llenándose» cuando puede significar «el examen que corrijo no existe».
    Pasó de verdad: `DEDUP_EVAL_COHORT` apuntaba a un holdout perdido y el reloj de
    los siete ciclos no podía arrancar, sin que ninguna métrica lo dijera. Un cero
    que no distingue «todavía no» de «nunca» deja esperando indefinidamente."""
    from jobhunt_core.shadow.labels import DEDUP_EVAL_COHORT
    from jobhunt_core.shadow.metrics import _labels_ready_row

    factory, _ = db

    async def evalua():
        async with factory() as s:
            return await _labels_ready_row(s, [])

    ready = asyncio.run(evalua())
    detalles = ready[2]
    # La cohorte se NOMBRA siempre: sin eso no se puede saber contra qué se puntúa.
    assert detalles["cohorte"] == DEDUP_EVAL_COHORT
    assert detalles["cohorte_existe"] is False
    assert "NO existe" in detalles["diagnostico"]
    assert "PROTOCOLO_HOLDOUT_DEDUP" in detalles["diagnostico"]
    assert ready[1] == 0, "sin cohorte el gate no puede estar verde"


def test_con_la_cohorte_cargada_el_diagnostico_desaparece(db):
    """Y al revés: existiendo la cohorte, no se emite un diagnóstico que confunda.
    Una guarda que grita siempre deja de leerse."""
    from jobhunt_core.shadow.labels import DEDUP_EVAL_COHORT
    from jobhunt_core.shadow.metrics import _labels_ready_row

    factory, _ = db

    async def prepara():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO labeled_dedup_cohorts (source, manifest) "
                    "VALUES (:src, '{}'::jsonb) ON CONFLICT (source) DO NOTHING"
                ),
                {"src": DEDUP_EVAL_COHORT},
            )
            await s.commit()

    async def evalua():
        async with factory() as s:
            return await _labels_ready_row(s, [])

    asyncio.run(prepara())
    try:
        detalles = asyncio.run(evalua())[2]
        assert detalles["cohorte_existe"] is True
        assert "diagnostico" not in detalles
    finally:
        async def limpia():
            async with factory() as s:
                await s.execute(
                    sa.text("DELETE FROM labeled_dedup_cohorts WHERE source = :src"),
                    {"src": DEDUP_EVAL_COHORT},
                )
                await s.commit()

        asyncio.run(limpia())

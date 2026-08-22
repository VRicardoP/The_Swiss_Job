"""Dedup semántico nivel 3 (F-5) — generador de candidatos cross-source.

La capacidad que dedup_recall exigía y la Fase B no construyó (techo medido
0,073). Estos tests fijan: detección cross-source por similitud, el descarte
INTRA-fuente por diseño (el 94 % de los falsos positivos del legacy), el
umbral, la idempotencia (uq_dedup_pair) y que la métrica del gate cuenta el
candidato como "core dice duplicado". BD vía core-migrate.
"""

import asyncio
import os
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


def _listing(ext, title):
    return RawListing(
        external_id=ext, url=f"https://x/{ext}",
        payload={
            "title": title, "company_name": "ACME AG",
            "description": f"puesto {title}", "tags": ["t"],
        },
    )


def _setup(factory, created, por_fuente):
    """Siembra N fuentes con sus títulos, registra modelo y embebe con el
    backend determinista (mismo título ⇒ mismo vector ⇒ sim 1.0)."""
    from jobhunt_core.tasks.embedding import run_pending_task

    async def go():
        async with factory() as s:
            for i, titles in enumerate(por_fuente):
                source_id, scope_id = uuid.uuid4(), uuid.uuid4()
                created["sources"].append(source_id)
                created["scopes"].append(scope_id)
                name = f"dedup-src-{i}-{source_id.hex[:6]}"
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
                        _listing(f"s{i}-j{j}", t) for j, t in enumerate(titles)
                    ),
                )
                await s.commit()
            mid = await embeddings.register_model(s, "modelo-dedup", SHA)
            created["models"].append(mid)
            await s.commit()
            return mid

    mid = asyncio.run(go())
    embeddings.set_backend_factory(lambda name, version: KeywordBackend())
    try:
        r = run_pending_task.apply(kwargs={"limit": 100})
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
    # exactamente los pares cross-source; el intra (s0-j0 vs s0-j1) NO está
    assert len(pares) == 2  # (s0-j0, s1-j0) y (s0-j1, s1-j0)
    assert all(p.state == "pending" and float(p.similarity) >= 0.99 for p in pares)
    # idempotencia: segunda pasada no duplica (uq_dedup_pair)
    r2 = _scan(factory)
    assert r2["candidatos_nuevos"] == 0
    assert len(_pairs(factory, created)) == 2


def test_titulos_distintos_bajo_umbral_no_generan(db):
    factory, created = db
    # familias distintas del KeywordBackend: 'python dev' ⇒ eje X,
    # 'guardabosques' ⇒ eje Y — ortogonales EXACTOS, sim 0.0 < 0.95
    _setup(factory, created, [["python dev"], ["guardabosques"]])
    r = _scan(factory)
    assert r["candidatos_nuevos"] == 0
    assert _pairs(factory, created) == []


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

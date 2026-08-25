"""Regresiones de la auditoría G1 — costuras hacia el core (catálogo/matching).

- P2-17: el catálogo enrutado a core presentaba hash=UUID (36 chars): toda
  acción sobre el feed (POST /applications, /documents/generate con
  job_hash max_length=32 y lookup Job.hash==) moría con 422/404. Ahora, con
  Job local de respaldo (url UNIQUE), se presenta el MD5 legacy accionable
  (la misma resolución determinista que la costura de matching); y el
  detalle de esa identidad MD5 se sirve del Job local también en
  core_primary (sin FallbackCatalog).
- P3-27: la exclusión por feedback negativo del feed core solo miraba el
  candidato ELEGIDO — un `dismissed` bajo el MD5 de otro listing de la misma
  vacante no se aplicaba y la oferta reaparecía.
"""

import uuid

import httpx
import pytest

from services.catalog.core_client import CoreCatalog
from services.catalog.port import CatalogSearchParams
from tests.test_catalog_contract import (
    CASE_CORE_REFS,
    CASE_LOCAL_REFS,
    TEST_CONSUMER_KEY,
    fake_core_transport,
    seed_local_cases,
)


def _core_catalog_with_db(db) -> CoreCatalog:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            headers={"Authorization": f"Bearer {TEST_CONSUMER_KEY}"},
            timeout=1.0,
            transport=fake_core_transport(),
        )

    return CoreCatalog(client_factory=factory, db=db)


@pytest.mark.asyncio
class TestP217IdentidadAccionable:
    async def test_search_presenta_md5_para_items_con_respaldo_local(self, db_session):
        await seed_local_cases(db_session)
        await db_session.commit()
        catalog = _core_catalog_with_db(db_session)

        result = await catalog.search(CatalogSearchParams(limit=20, offset=0))
        hashes = {b.hash for b in result.data}

        local_md5 = set(CASE_LOCAL_REFS.values())
        assert hashes & local_md5, "los items con Job local llevan MD5 accionable"
        for h in hashes & local_md5:
            assert len(h) == 32, "el MD5 cabe en job_hash (max_length=32)"

    async def test_get_por_uuid_presenta_md5_accionable(self, db_session):
        await seed_local_cases(db_session)
        await db_session.commit()
        catalog = _core_catalog_with_db(db_session)

        name = next(iter(CASE_CORE_REFS))
        job = await catalog.get(CASE_CORE_REFS[name])
        assert job is not None
        assert job.hash == CASE_LOCAL_REFS[name]

    async def test_get_por_md5_sirve_el_detalle_sin_fallback(self, db_session):
        """En core_primary no hay FallbackCatalog: el MD5 que este cliente
        presenta debe poder resolverse a detalle."""
        await seed_local_cases(db_session)
        await db_session.commit()
        catalog = _core_catalog_with_db(db_session)

        name = next(iter(CASE_LOCAL_REFS))
        job = await catalog.get(CASE_LOCAL_REFS[name])
        assert job is not None
        assert job.hash == CASE_LOCAL_REFS[name]

    async def test_sin_sesion_conserva_el_uuid(self):
        """Compatibilidad: sin db (tests/construcciones antiguas) no se
        resuelve nada y el UUID del core se presenta tal cual."""

        def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                base_url="http://core-api:8000/v1",
                headers={"Authorization": f"Bearer {TEST_CONSUMER_KEY}"},
                timeout=1.0,
                transport=fake_core_transport(),
            )

        catalog = CoreCatalog(client_factory=factory)
        result = await catalog.search(CatalogSearchParams(limit=5, offset=0))
        assert all(len(b.hash) == 36 for b in result.data)


# ---------------------------------------------------------------------------
# P3-27 — exclusión por feedback negativo en TODOS los candidatos del item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p3_27_dismissed_bajo_otro_listing_tambien_excluye(db_session):
    from models.job import Job
    from models.match_result import MatchResult
    from models.user import User
    from core.security import hash_password
    from services.matching.core_client import CoreMatching
    from services.matching.identity import set_profile_link

    marker = uuid.uuid4().hex[:8]
    user = User(
        email=f"g1p327-{marker}@example.com",
        hashed_password=hash_password("TestPass1!"),
        gdpr_consent=True,
    )
    db_session.add(user)
    await db_session.flush()

    # Dos listings legacy de la MISMA vacante, ambos con Job local.
    hash_a = f"g1p327a-{marker}".ljust(32, "0")[:32]
    hash_b = f"g1p327b-{marker}".ljust(32, "0")[:32]
    for h, src, ext in ((hash_a, "src_a", "ext-a"), (hash_b, "src_b", "ext-b")):
        db_session.add(
            Job(
                hash=h,
                source=src,
                title="Multi Listing Role",
                company="Acme",
                url=f"https://e.ch/{h[:12]}",
                is_active=True,
            )
        )
    # El usuario descartó la vacante bajo el listing B (no el primary A).
    db_session.add(
        MatchResult(
            user_id=user.id,
            job_hash=hash_b,
            score_embedding=0.9,
            score_salary=0,
            score_location=0,
            score_recency=0,
            score_llm=0,
            score_final=90,
            matching_skills=[],
            missing_skills=[],
            feedback="dismissed",
        )
    )
    await db_session.commit()
    core_profile = uuid.uuid4()
    await set_profile_link(db_session, user.id, core_profile, updated_by="tests")

    feed_item = {
        "evaluation": {
            "eval_key": "e1",
            "score_final": 0.9,
            "scores": {},
            "explanation": None,
            "matching_skills": [],
            "missing_skills": [],
        },
        "vacancy": {
            "id": str(uuid.uuid4()),
            "title": "Multi Listing Role",
            "company": "Acme",
            "primary_listing": {
                "source": "legacy:src_a",
                "external_id": "ext-a",
                "url": f"https://e.ch/{hash_a[:12]}",
            },
            "listings": [
                {
                    "source": "legacy:src_a",
                    "external_id": "ext-a",
                    "url": f"https://e.ch/{hash_a[:12]}",
                },
                {
                    "source": "legacy:src_b",
                    "external_id": "ext-b",
                    "url": f"https://e.ch/{hash_b[:12]}",
                },
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [feed_item], "next_cursor": None})

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            timeout=1.0,
            transport=httpx.MockTransport(handler),
        )

    matching = CoreMatching(db_session, client_factory=factory)
    results, total = await matching.results(user.id)

    assert total == 0, (
        "el dismissed bajo el listing B debe excluir la vacante aunque el "
        "candidato elegido sea el A"
    )

"""Regresiones de la auditoría G3 — lote G (matching, re-ranking, catálogo core).

- P2-11: `_has_engagement` ignoraba `feedback_implicit`, así que `_save_results`
  BORRABA en cada corrida las filas cuya única interacción era implícita — pese a
  que `match_result_service.record_implicit_feedback` promete en su docstring que
  esa fila «queda protegida del cleanup» y a que `maintenance_tasks` la cuenta
  como `attached`. De rebote la oferta dejaba de estar adjunta y a los 60 días
  `cleanup_stale_jobs` la borraba con su cascada.
- P3-9: `_save_results` escribía `score_llm=0.0` y `explanation=NULL` encima de
  los valores buenos del día anterior en todo result que no pasara por el
  re-ranking de ESA corrida (modo avalancha: >MATCH_LLM_RERANK_MAX ⇒ solo se
  re-rankean MATCH_LLM_RERANK_TOP). La explicación «por qué encaja» aparecía y
  desaparecía según el volumen del día.
- P3-1: `_parse_llm_response` aceptaba una respuesta VÁLIDA pero INCOMPLETA
  (9 resultados para un lote de 25 — qwen truncado) y la cacheaba 7 días con los
  índices ausentes sin score LLM, sin probar siquiera Gemini.
- P3-5: `_resolve_legacy_hashes` entregaba el hash MD5 «accionable» del fix
  G1/P2-17 para ofertas ARCHIVADAS, cuyo detalle responde 404 (`LocalCatalog.get`
  sí filtra por `is_active`): item del feed inabrible.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import hash_password
from models.job import Job
from models.match_result import MatchResult
from models.user import User
from services.catalog.core_client import CoreCatalog, clear_catalog_feed_cache
from services.catalog.port import CatalogSearchParams
from services.groq_service import GroqService
from services.job_matcher import DEFAULT_WEIGHTS
from services.match_service import MatchService
from tests.conftest import random_email
from tests.test_catalog_contract import (
    CASE_CORE_REFS,
    CASE_LOCAL_REFS,
    TEST_CONSUMER_KEY,
    fake_core_transport,
    seed_local_cases,
)

# ---------------------------------------------------------------------------
# Helpers comunes
# ---------------------------------------------------------------------------


async def _make_user(db: AsyncSession) -> uuid.UUID:
    user = User(email=random_email(), hashed_password=hash_password("TestPass123!"))
    db.add(user)
    await db.commit()
    return user.id


async def _make_job(db: AsyncSession, job_hash: str) -> None:
    db.add(
        Job(
            hash=job_hash,
            source="test",
            title=f"Job {job_hash}",
            company="C",
            url=f"https://example.ch/{job_hash}",
            is_active=True,
        )
    )
    await db.commit()


async def _make_row(db: AsyncSession, user_id: uuid.UUID, job_hash: str, **over):
    row = MatchResult(
        user_id=user_id,
        job_hash=job_hash,
        score_embedding=0.1,
        score_salary=0.1,
        score_location=0.1,
        score_recency=0.1,
        score_llm=0.0,
        score_final=50.0,
        matching_skills=[],
        missing_skills=[],
    )
    for key, value in over.items():
        setattr(row, key, value)
    db.add(row)
    await db.commit()
    return row


_PROFILE = SimpleNamespace(
    cv_text="cv", skills=["python"], watchlist_schools_enabled=False
)


def _scored(job, *, reranked: bool = False) -> dict:
    """Result de stage 2 (sin veredicto LLM) o ya pasado por el rerank real.

    La variante re-rankeada se construye con el productor de verdad
    (`_apply_llm_result`), no marcándola a mano: así el test no depende de
    cómo se represente internamente «esta corrida tuvo veredicto».
    """
    r = {
        "job": job,
        "score_embedding": 0.7,
        "score_salary": 0.5,
        "score_location": 0.5,
        "score_recency": 0.5,
        "score_language": 0.5,
        "score_llm": 0.0,
        "score_final": 61.0,
        "urgency_score": 0,
        "matching_skills": ["python"],
        "missing_skills": [],
    }
    if reranked:
        MatchService(db=None)._apply_llm_result(
            r,
            {"score": 44, "reason": "explicacion de hoy"},
            _PROFILE,
            DEFAULT_WEIGHTS,
        )
    return r


async def _reload(db: AsyncSession, user_id: uuid.UUID, job_hash: str):
    from sqlalchemy import select

    db.expire_all()
    return (
        await db.execute(
            select(MatchResult).where(
                MatchResult.user_id == user_id, MatchResult.job_hash == job_hash
            )
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# P2-11 — el feedback implícito es engagement
# ---------------------------------------------------------------------------


class TestP211FeedbackImplicitoEsEngagement:
    def test_has_engagement_cuenta_el_feedback_implicito(self):
        """Repro exacta del informe: la fila que crea record_implicit_feedback."""
        row = SimpleNamespace(
            feedback=None,
            feedback_implicit=[{"action": "opened"}],
            application_status="detected",
            draft_letter=None,
        )
        assert MatchService._has_engagement(row) is True

    def test_lista_vacia_no_es_engagement(self):
        """No-regresión: `[]` no es interacción (por verdad, no `is not None`)."""
        row = SimpleNamespace(
            feedback=None,
            feedback_implicit=[],
            application_status="detected",
            draft_letter=None,
        )
        assert MatchService._has_engagement(row) is False

    def test_fila_limpia_sigue_podandose(self):
        row = SimpleNamespace(
            feedback=None,
            feedback_implicit=None,
            application_status="detected",
            draft_letter=None,
        )
        assert MatchService._has_engagement(row) is False

    async def test_save_results_no_borra_la_fila_solo_implicita(
        self, db_session: AsyncSession
    ):
        """La corrida que no reencuentra el job NO puede borrar su señal implícita."""
        user_id = await _make_user(db_session)
        await _make_job(db_session, "g3implicit0000000000000000000001")
        await _make_row(
            db_session,
            user_id,
            "g3implicit0000000000000000000001",
            feedback_implicit=[{"action": "opened", "duration_ms": 12000}],
        )

        await MatchService(db=db_session)._save_results(user_id, [])

        row = await _reload(db_session, user_id, "g3implicit0000000000000000000001")
        assert row is not None, (
            "la fila con feedback_implicit queda protegida del prune "
            "(promesa de record_implicit_feedback y criterio `attached`)"
        )
        assert row.feedback_implicit == [{"action": "opened", "duration_ms": 12000}]

    async def test_save_results_sigue_podando_la_fila_limpia(
        self, db_session: AsyncSession
    ):
        """No-regresión: sin interacción alguna, la huérfana se sigue borrando."""
        user_id = await _make_user(db_session)
        await _make_job(db_session, "g3clean0000000000000000000000001")
        await _make_row(db_session, user_id, "g3clean0000000000000000000000001")

        await MatchService(db=db_session)._save_results(user_id, [])

        assert (
            await _reload(db_session, user_id, "g3clean0000000000000000000000001")
            is None
        )


# ---------------------------------------------------------------------------
# P3-9 — la explicación no se borra si no hubo re-ranking
# ---------------------------------------------------------------------------


class TestP39ExplicacionSobreviveALaAvalancha:
    def test_score_values_omite_llm_sin_veredicto(self):
        job = SimpleNamespace(source="test", category="A")
        values = MatchService._score_values(_scored(job))
        assert "explanation" not in values, (
            "sin veredicto del LLM, el UPDATE no puede pisar la explicacion previa"
        )
        assert "score_llm" not in values

    def test_score_values_incluye_llm_con_veredicto(self):
        job = SimpleNamespace(source="test", category="A")
        values = MatchService._score_values(_scored(job, reranked=True))
        assert values["explanation"] == "explicacion de hoy"
        assert values["score_llm"] == 0.44

    async def test_la_cola_no_re_rankeada_conserva_la_explicacion(
        self, db_session: AsyncSession
    ):
        user_id = await _make_user(db_session)
        job_hash = "g3tail00000000000000000000000001"
        await _make_job(db_session, job_hash)
        await _make_row(
            db_session,
            user_id,
            job_hash,
            score_llm=0.9,
            explanation="encaja por tu experiencia en Python (ayer)",
        )
        job = await db_session.get(Job, job_hash)

        # Corrida en modo avalancha: este result queda FUERA del rerank.
        await MatchService(db=db_session)._save_results(user_id, [_scored(job)])

        row = await _reload(db_session, user_id, job_hash)
        assert row.explanation == "encaja por tu experiencia en Python (ayer)"
        assert row.score_llm == 0.9
        # Los scores recomputables SÍ se refrescan.
        assert row.score_embedding == 0.7

    async def test_el_result_re_rankeado_si_pisa_la_explicacion(
        self, db_session: AsyncSession
    ):
        """No-regresión: con veredicto del LLM, el valor nuevo manda."""
        user_id = await _make_user(db_session)
        job_hash = "g3head00000000000000000000000001"
        await _make_job(db_session, job_hash)
        await _make_row(
            db_session, user_id, job_hash, score_llm=0.9, explanation="de ayer"
        )
        job = await db_session.get(Job, job_hash)

        await MatchService(db=db_session)._save_results(
            user_id, [_scored(job, reranked=True)]
        )

        row = await _reload(db_session, user_id, job_hash)
        assert row.explanation == "explicacion de hoy"
        assert row.score_llm == 0.44

    async def test_alta_nueva_sin_veredicto_no_rompe_el_insert(
        self, db_session: AsyncSession
    ):
        """El INSERT sin score_llm/explanation cae en los defaults de la columna."""
        user_id = await _make_user(db_session)
        job_hash = "g3insert000000000000000000000001"
        await _make_job(db_session, job_hash)
        job = await db_session.get(Job, job_hash)

        await MatchService(db=db_session)._save_results(user_id, [_scored(job)])

        row = await _reload(db_session, user_id, job_hash)
        assert row is not None
        assert row.score_llm == 0.0
        assert row.explanation is None


# ---------------------------------------------------------------------------
# P3-1 — una respuesta corta del LLM no es una respuesta válida
# ---------------------------------------------------------------------------


class _FakeGemini:
    """Doble de GeminiService que devuelve un rerank COMPLETO del lote."""

    def __init__(self, batch_len: int):
        self._text = json.dumps(
            [{"index": i, "score": 60, "reason": "gemini"} for i in range(batch_len)]
        )
        self.calls = 0

    @property
    def is_available(self) -> bool:
        return True

    async def get_chat_response(
        self, user_message, system_prompt=None, temperature=0.4, max_tokens=4096
    ) -> str:
        self.calls += 1
        return self._text


def _groq_with_response(llm_text: str) -> tuple[GroqService, list]:
    svc = GroqService.__new__(GroqService)
    svc.client = MagicMock()  # is_available True
    svc.redis = None
    svc.get_chat_response = AsyncMock(return_value=llm_text)
    cached_writes: list = []

    async def _record_cache(key, value):
        cached_writes.append(value)

    svc._set_cached = _record_cache
    return svc, cached_writes


_TEN = [{"title": f"Job {i}", "company": "Co"} for i in range(10)]


class TestP31LoteIncompleto:
    def test_parse_respuesta_corta_lanza(self):
        """Repro del informe: 4 resultados para un lote de 10."""
        short = json.dumps([{"index": i, "score": 80} for i in range(4)])
        with pytest.raises(ValueError):
            GroqService._parse_llm_response(short, 10)

    def test_parse_cobertura_completa_pasa(self):
        full = json.dumps([{"index": i, "score": 80} for i in range(10)])
        assert len(GroqService._parse_llm_response(full, 10)) == 10

    async def test_lote_incompleto_prueba_gemini_y_no_cachea_groq(self):
        short = json.dumps([{"index": i, "score": 80} for i in range(4)])
        groq, cached_writes = _groq_with_response(short)
        gemini = _FakeGemini(batch_len=10)

        results = await groq.rerank_jobs(
            profile_text="dev",
            profile_skills=["python"],
            candidates=_TEN,
            fallback=gemini,
        )

        assert gemini.calls == 1, "la respuesta corta debe probar el fallback"
        assert len(results) == 10, "ningún índice del lote se queda sin score LLM"
        assert all(r["score"] == 60 for r in results)
        # Lo cacheado es la respuesta COMPLETA de Gemini, nunca la corta de Groq.
        assert cached_writes and all(len(w) == 10 for w in cached_writes)

    async def test_lote_incompleto_sin_fallback_degrada_sin_cachear(self):
        short = json.dumps([{"index": i, "score": 80} for i in range(4)])
        groq, cached_writes = _groq_with_response(short)

        results = await groq.rerank_jobs(
            profile_text="dev", profile_skills=["python"], candidates=_TEN
        )

        assert all(r["score"] == 0 for r in results)
        assert cached_writes == [], (
            "una respuesta corta JAMÁS debe envenenar la caché 7 días"
        )


# ---------------------------------------------------------------------------
# P3-5 — el hash accionable solo para ofertas activas
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_catalog_feed_cache():
    clear_catalog_feed_cache()
    yield
    clear_catalog_feed_cache()


def _core_catalog_with_db(db) -> CoreCatalog:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            headers={"Authorization": f"Bearer {TEST_CONSUMER_KEY}"},
            timeout=1.0,
            transport=fake_core_transport(),
        )

    return CoreCatalog(client_factory=factory, db=db)


_ARCHIVED_CASE = "python_zurich"


class TestP35HashAccionableSoloActivos:
    async def test_search_no_presenta_el_md5_de_una_oferta_archivada(
        self, db_session: AsyncSession
    ):
        await seed_local_cases(db_session)
        await db_session.execute(
            update(Job)
            .where(Job.hash == CASE_LOCAL_REFS[_ARCHIVED_CASE])
            .values(is_active=False)
        )
        await db_session.commit()
        catalog = _core_catalog_with_db(db_session)

        result = await catalog.search(CatalogSearchParams(limit=20, offset=0))
        hashes = {b.hash for b in result.data}

        assert CASE_LOCAL_REFS[_ARCHIVED_CASE] not in hashes, (
            "el MD5 de una oferta archivada no es accionable: su detalle da 404"
        )
        assert CASE_CORE_REFS[_ARCHIVED_CASE] in hashes, "se conserva el UUID del core"

    async def test_get_por_uuid_no_reescribe_a_md5_archivado(
        self, db_session: AsyncSession
    ):
        await seed_local_cases(db_session)
        await db_session.execute(
            update(Job)
            .where(Job.hash == CASE_LOCAL_REFS[_ARCHIVED_CASE])
            .values(is_active=False)
        )
        await db_session.commit()
        catalog = _core_catalog_with_db(db_session)

        job = await catalog.get(CASE_CORE_REFS[_ARCHIVED_CASE])
        assert job is not None
        assert job.hash != CASE_LOCAL_REFS[_ARCHIVED_CASE]

    async def test_la_oferta_activa_sigue_presentando_su_md5(
        self, db_session: AsyncSession
    ):
        """No-regresión del fix G1/P2-17."""
        await seed_local_cases(db_session)
        catalog = _core_catalog_with_db(db_session)

        result = await catalog.search(CatalogSearchParams(limit=20, offset=0))
        hashes = {b.hash for b in result.data}

        assert hashes & set(CASE_LOCAL_REFS.values()), (
            "los items con Job local ACTIVO conservan el MD5 accionable"
        )

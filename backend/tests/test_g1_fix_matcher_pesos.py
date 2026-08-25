"""Regresiones de la auditoría G1 — familia matcher/pesos.

- P2-11: pesos por usuario PARCIALES inflaban el score >100 (las claves
  ausentes se rellenaban con su default, no con 0).
- P3-14: la cota única «hasta X» (min=None, max=X) se computaba como X/2.
- P3-15: el umbral se aplicaba ANTES del rerank LLM — un job a 34.9 pre-LLM
  que el LLM subiría por encima del umbral se descartaba sin verlo.
- P3-19: el urgency scorer solo veía el snippet (200 chars); los deadlines
  suelen ir al final del anuncio.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.job_matcher import JobMatcher
from services.match_service import MatchService


class TestP211PesosParciales:
    def test_pesos_parciales_no_inflan(self):
        """G1/P2-11: {'embedding':0.5,'llm':0.5} debe dar 100, no 150."""
        matcher = JobMatcher()
        score = matcher.compute_final_score(
            1.0, 1.0, 1.0, 1.0, 100.0, 1.0, weights={"embedding": 0.5, "llm": 0.5}
        )
        assert score == 100.0

    def test_defaults_intactos(self):
        matcher = JobMatcher()
        score = matcher.compute_final_score(1.0, 1.0, 1.0, 1.0, 100.0, 1.0)
        assert score == 100.0


class TestP314CotaUnica:
    def test_hasta_x_no_se_penaliza(self):
        """G1/P3-14: min=None, max=90000 con usuario 80k-100k → ratio 1.0."""
        assert JobMatcher.compute_salary_match(80_000, 100_000, None, 90_000) == 1.0

    def test_desde_x_simetrico(self):
        assert JobMatcher.compute_salary_match(80_000, 100_000, 90_000, None) == 1.0

    def test_usuario_con_cota_unica(self):
        assert JobMatcher.compute_salary_match(None, 90_000, 85_000, 95_000) == 1.0


def _scored(job, score_final):
    return {
        "job": job,
        "score_embedding": 0.5,
        "score_salary": 0.5,
        "score_location": 0.5,
        "score_recency": 0.5,
        "score_language": 0.5,
        "score_llm": 0.0,
        "score_final": score_final,
        "explanation": None,
        "matching_skills": [],
        "missing_skills": [],
    }


def _fake_job(**over):
    base = dict(
        title="Dev",
        company="C",
        description="d",
        description_snippet="d",
        tags=[],
        location="Zug",
        remote=False,
        language="en",
        contract_type=None,
        source="test_source",
        category="A",
        hash="h" * 32,
        embedding=[1.0, 0.0],
        salary_min_chf=None,
        salary_max_chf=None,
        first_seen_at=datetime.now(timezone.utc),
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.anyio
class TestP315UmbralAntesDelRerank:
    async def test_job_bajo_umbral_pre_llm_llega_al_rerank(self):
        """G1/P3-15: un 34.9 pre-LLM que el LLM sube a 55 debe SOBREVIVIR."""
        svc = MatchService(db=None, groq=AsyncMock())
        weights = {
            "embedding": 0.35,
            "salary": 0.10,
            "location": 0.10,
            "recency": 0.10,
            "llm": 0.25,
            "language": 0.10,
        }
        profile = SimpleNamespace(cv_embedding=[1.0, 0.0], score_weights=weights)
        svc._get_profile = AsyncMock(return_value=profile)
        svc._get_excluded_hashes = AsyncMock(return_value=set())
        svc._get_active_filters = AsyncMock(return_value=[])
        svc._stage1_vector_search = AsyncMock(return_value=[object()])
        borderline = _scored(_fake_job(), 34.9)
        hopeless = _scored(_fake_job(), 5.0)  # bajo incluso el prefiltro (10.0)
        svc._stage2_multifactor_score = Mock(return_value=[borderline, hopeless])

        seen_by_rerank: list[float] = []

        async def fake_rerank(qualified, profile, weights):
            seen_by_rerank.extend(r["score_final"] for r in qualified)
            for r in qualified:
                if r["score_final"] == 34.9:
                    r["score_final"] = 55.0  # el LLM lo sube
            return qualified

        svc._maybe_rerank = fake_rerank
        saved = {}

        async def fake_save(uid, results):
            saved["results"] = results

        svc._save_results = fake_save
        svc._notify_watchlist_priority = AsyncMock()

        result = await svc.run_matching(uuid.uuid4(), min_score=35.0)

        assert 34.9 in seen_by_rerank, "el borderline debe llegar al rerank"
        assert result["status"] == "success"
        finals = [r["score_final"] for r in saved["results"]]
        assert finals == [55.0], (
            "sobrevive el job subido por el LLM; el resto queda bajo el umbral"
        )

    async def test_umbral_definitivo_se_sigue_aplicando(self):
        """Sin LLM que los suba, los jobs del margen NO deben persistirse."""
        svc = MatchService(db=None, groq=None)
        weights = {
            "embedding": 0.45,
            "salary": 0.10,
            "location": 0.10,
            "recency": 0.10,
            "llm": 0.15,
            "language": 0.10,
        }
        profile = SimpleNamespace(cv_embedding=[1.0, 0.0], score_weights=weights)
        svc._get_profile = AsyncMock(return_value=profile)
        svc._get_excluded_hashes = AsyncMock(return_value=set())
        svc._get_active_filters = AsyncMock(return_value=[])
        svc._stage1_vector_search = AsyncMock(return_value=[object()])
        svc._stage2_multifactor_score = Mock(
            return_value=[_scored(_fake_job(), 30.0)]
        )
        saved = {}

        async def fake_save(uid, results):
            saved["results"] = results

        svc._save_results = fake_save
        svc._notify_watchlist_priority = AsyncMock()

        result = await svc.run_matching(uuid.uuid4(), min_score=35.0)
        assert result["status"] == "no_jobs"
        assert saved["results"] == []


class TestP319UrgencyDescripcionCompleta:
    def test_urgency_ve_la_descripcion_entera(self, monkeypatch):
        """G1/P3-19: el deadline al final del anuncio debe llegar al scorer."""
        captured = {}

        def fake_urgency(job, description=""):
            captured["description"] = description
            return 0.0

        monkeypatch.setattr(
            "services.urgency_scorer.compute_urgency_score", fake_urgency
        )

        full = "Intro. " + ("bla " * 100) + "Bewerbungsfrist: 30. September 2026"
        job = _fake_job(
            description=full,
            description_snippet=full[:200],
            source="swiss_schools_zis",
            category="H",
        )
        profile = SimpleNamespace(
            cv_embedding=[1.0, 0.0],
            salary_min=None,
            salary_max=None,
            locations=[],
            languages=[],
            skills=[],
            watchlist_schools_enabled=True,
            score_weights=None,
        )
        svc = MatchService(db=None, groq=None)
        results = svc._stage2_multifactor_score(
            profile=profile, candidates=[job], weights=None
        )
        assert len(results) == 1
        assert "Bewerbungsfrist" in captured["description"], (
            "el urgency scorer debe recibir la descripción COMPLETA, no el snippet"
        )

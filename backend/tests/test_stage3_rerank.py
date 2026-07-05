"""Test de caracterización para MatchService._stage3_llm_rerank.

Fija el merge del re-ranking LLM antes de refactorizar: para cada candidato con
score LLM > 0 se actualiza score_llm/explanation/skills y se recalcula
score_final (compute_final_score * multiplicador de categoría); los de score 0
se dejan intactos; la lista se reordena por score_final desc.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.match_service import MatchService

_WEIGHTS = {
    "embedding": 0.35,
    "salary": 0.15,
    "location": 0.10,
    "recency": 0.15,
    "llm": 0.15,
    "language": 0.10,
}


def _job():
    return SimpleNamespace(
        title="Dev",
        company="C",
        description="d",
        tags=["python"],
        location="Zurich",
        remote=False,
        language="en",
        contract_type=None,
        source="test_source",
        category="A",  # multiplicador 1.0
    )


def _scored(score_final: float) -> dict:
    return {
        "job": _job(),
        "score_embedding": 0.5,
        "score_salary": 0.5,
        "score_location": 0.5,
        "score_recency": 0.5,
        "score_language": 0.5,
        "score_llm": 0.0,
        "score_final": score_final,
        "explanation": None,
        "matching_skills": ["rule"],
        "missing_skills": ["rule_miss"],
    }


@pytest.mark.anyio
class TestStage3Rerank:
    async def test_merges_llm_recomputes_and_sorts(self):
        groq = AsyncMock()
        groq.rerank_jobs = AsyncMock(
            return_value=[
                {
                    "global_index": 0,
                    "score": 90,
                    "reason": "great fit",
                    "matching_skills": ["llm_match"],
                    "missing_skills": ["llm_miss"],
                },
                {
                    "global_index": 1,
                    "score": 0,
                    "reason": "",
                    "matching_skills": [],
                    "missing_skills": [],
                },
            ]
        )
        svc = MatchService(db=None, groq=groq)
        profile = SimpleNamespace(
            cv_text="cv", skills=["python"], watchlist_schools_enabled=False
        )

        r0 = _scored(score_final=10.0)  # base baja; el LLM la sube
        r1 = _scored(score_final=50.0)  # sin score LLM

        out = await svc._stage3_llm_rerank(profile, [r0, r1], _WEIGHTS)

        # r0: score LLM 90 → mergeado y score_final recalculado.
        assert r0["score_llm"] == 0.9
        assert r0["explanation"] == "great fit"
        assert r0["matching_skills"] == ["llm_match"]
        assert r0["missing_skills"] == ["llm_miss"]
        assert r0["score_final"] != 10.0

        # r1: score LLM 0 → NO se toca (skills rule-based preservados).
        assert r1["score_llm"] == 0.0
        assert r1["explanation"] is None
        assert r1["matching_skills"] == ["rule"]

        # Reordenado por score_final desc.
        assert out[0]["score_final"] >= out[1]["score_final"]
        # Groq llamado con el fallback Gemini.
        assert groq.rerank_jobs.call_args.kwargs["fallback"] is svc.gemini

    async def test_no_llm_results_leaves_scores_untouched(self):
        groq = AsyncMock()
        groq.rerank_jobs = AsyncMock(return_value=[])  # sin resultados LLM
        svc = MatchService(db=None, groq=groq)
        profile = SimpleNamespace(
            cv_text="cv", skills=[], watchlist_schools_enabled=False
        )
        r = _scored(score_final=42.0)

        out = await svc._stage3_llm_rerank(profile, [r], _WEIGHTS)

        assert out[0]["score_final"] == 42.0
        assert out[0]["score_llm"] == 0.0
        assert out[0]["matching_skills"] == ["rule"]

"""G4/P2-6 — un veredicto LLM de score 0 no marcaba la corrida como evaluada.

`0-39 = poor fit` es salida DOCUMENTADA del prompt de re-ranking
(`services/groq_service.py:43`). El fix G3/P3-9 ató la marca «esta corrida SÍ
tuvo veredicto» a `_apply_llm_result`, al que solo se llegaba con `score > 0`,
así que un poor fit legítimo dejaba de escribir: la fila conservaba PARA
SIEMPRE el `score_llm` y la explicación de otro día —texto que el usuario ve en
`MatchCard`— junto a un `score_final` recalculado con `llm=0`. Con los pesos
reales (`llm: 0.15`) eso son 13.8 puntos de incoherencia sobre la clave de
orden Y sobre `MATCH_SCORE_THRESHOLD`: una oferta que solo superaba el umbral
por el aporte del LLM salía de `qualified`, entraba en la poda de
`_save_results` y, sin engagement, se borraba.

Lo que NO puede volver a escribir ceros es el lote DEGRADADO (LLM caído), que
es lo que G3/P3-9 cerró: ahora se distingue por la marca `degraded` que pone
`GroqService._fallback_results`.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.groq_service import GroqService
from services.job_matcher import DEFAULT_WEIGHTS
from services.match_service import MatchService

_PROFILE = SimpleNamespace(cv_text="cv", skills=["python"])


def _scored(job) -> dict:
    return {
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


def _job():
    return SimpleNamespace(
        source="test",
        category="A",
        title="Redactor",
        company="C",
        description="d",
        tags=[],
        location="Bern",
        remote=False,
        language="de",
        contract_type=None,
    )


async def _rerank_con(veredictos: list[dict]) -> dict:
    """Corre el stage 3 real con la respuesta del LLM que se le indique."""
    service = MatchService(db=None)
    service.groq = SimpleNamespace(rerank_jobs=AsyncMock(return_value=veredictos))
    service.gemini = None
    resultados = [_scored(_job())]
    await service._stage3_llm_rerank(_PROFILE, resultados, DEFAULT_WEIGHTS)
    return resultados[0]


@pytest.mark.asyncio
class TestP26VeredictoDeScoreCero:
    async def test_un_poor_fit_marca_la_corrida_como_evaluada(self):
        r = await _rerank_con(
            [
                {
                    "global_index": 0,
                    "score": 0,
                    "reason": "el perfil no encaja con el puesto",
                    "matching_skills": [],
                    "missing_skills": [],
                }
            ]
        )

        values = MatchService._score_values(r)
        assert "score_llm" in values, (
            "un veredicto de 0 —salida documentada del prompt— no marca la "
            "corrida como evaluada: la fila conserva el score_llm y la "
            "explicación de otro día junto a un score_final calculado con llm=0"
        )
        assert values["score_llm"] == 0.0
        assert values["explanation"] == "el perfil no encaja con el puesto"

    async def test_un_lote_degradado_sigue_sin_pisar_la_explicacion(self):
        """No-regresión de G3/P3-9: los ceros del LLM caído no son veredicto."""
        degradados = GroqService._fallback_results(1)
        for d in degradados:
            d["global_index"] = d["index"]
        r = await _rerank_con(degradados)

        values = MatchService._score_values(r)
        assert "score_llm" not in values
        assert "explanation" not in values

    async def test_un_veredicto_positivo_sigue_aplicandose(self):
        r = await _rerank_con(
            [{"global_index": 0, "score": 44, "reason": "encaja bien"}]
        )
        values = MatchService._score_values(r)
        assert values["score_llm"] == 0.44
        assert values["explanation"] == "encaja bien"

    async def test_sin_veredicto_para_ese_indice_no_se_escribe(self):
        """La cola no re-rankeada (modo avalancha) conserva lo suyo."""
        r = await _rerank_con([{"global_index": 7, "score": 80, "reason": "otra"}])
        assert "score_llm" not in MatchService._score_values(r)

"""Regresiones de la auditoría G1 — rerank Groq.

- P2-12: un JSON malformado producía scores 0 que se CACHEABAN 7 días
  (indistinguibles de un «poor fit» legítimo) y, al no lanzar, el camino del
  except (que degrada SIN cachear) nunca corría.
- P3-16: el índice del LLM se confiaba sin validar — un LLM 1-based
  desplazaba todos los scores un puesto (score/explicación al job vecino).
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.groq_service import GroqService


def _svc_with_response(llm_text: str) -> tuple[GroqService, list]:
    """Servicio con cliente mockeado y registro de escrituras a la caché."""
    svc = GroqService.__new__(GroqService)
    svc.client = MagicMock()  # is_available True
    svc.redis = None
    svc.get_chat_response = AsyncMock(return_value=llm_text)
    cached_writes: list = []

    async def _record_cache(key, value):
        cached_writes.append(value)

    svc._set_cached = _record_cache
    return svc, cached_writes


_CANDIDATES = [
    {"title": "Python Engineer", "company": "TechCo"},
    {"title": "Data Analyst", "company": "DataCo"},
]


@pytest.mark.anyio
class TestP212JsonMalformado:
    async def test_json_truncado_degrada_sin_cachear(self):
        """G1/P2-12: el caso real qwen truncado por max_tokens."""
        truncated = '[{"index": 0, "score": 90, "reason": "great'  # sin cerrar
        svc, cached_writes = _svc_with_response(truncated)

        results = await svc.rerank_jobs(
            profile_text="dev", profile_skills=["python"], candidates=_CANDIDATES
        )

        # Degrada a ceros (el pipeline sigue), pero NO envenena la caché.
        assert all(r["score"] == 0 for r in results)
        assert cached_writes == [], (
            "los ceros de un JSON malformado JAMÁS deben escribirse en la caché"
        )

    async def test_respuesta_valida_si_se_cachea(self):
        valid = json.dumps(
            [
                {"index": 0, "score": 90, "reason": "ok"},
                {"index": 1, "score": 40, "reason": "meh"},
            ]
        )
        svc, cached_writes = _svc_with_response(valid)
        results = await svc.rerank_jobs(
            profile_text="dev", profile_skills=["python"], candidates=_CANDIDATES
        )
        assert [r["score"] for r in results] == [90, 40]
        assert len(cached_writes) == 1


@pytest.mark.anyio
class TestP316IndiceSinValidar:
    async def test_llm_1based_no_desplaza_scores(self):
        """G1/P3-16: índices 1-based deben degradar, no asignar al vecino."""
        one_based = json.dumps(
            [
                {"index": 1, "score": 90, "reason": "a"},
                {"index": 2, "score": 10, "reason": "b"},
            ]
        )
        svc, cached_writes = _svc_with_response(one_based)
        results = await svc.rerank_jobs(
            profile_text="dev", profile_skills=["python"], candidates=_CANDIDATES
        )
        # Sin el fix: el job 0 recibía el score del 1 (90 desplazado) y un
        # global_index fuera de rango. Con el fix: degrade honesto a ceros.
        assert all(r["score"] == 0 for r in results)
        assert cached_writes == []

    def test_indice_duplicado_lanza(self):
        dup = json.dumps(
            [{"index": 0, "score": 90}, {"index": 0, "score": 10}]
        )
        with pytest.raises(ValueError):
            GroqService._parse_llm_response(dup, 2)

    def test_indice_valido_pasa(self):
        ok = json.dumps([{"index": 0, "score": 55}, {"index": 1, "score": 45}])
        results = GroqService._parse_llm_response(ok, 2)
        assert [r["index"] for r in results] == [0, 1]

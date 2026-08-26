"""Regresión de la auditoría G2 — P3-4: Groq con basura no probaba Gemini.

El fix G1/P2-12 hizo que `_parse_llm_response` LANCE ante una respuesta
inválida (cerrando el cacheo de ceros durante 7 días), pero el parseo ocurría
FUERA de `_rerank_call`: la excepción saltaba cuando el fallback ya había
quedado atrás, así que «Groq responde basura» —qwen truncado por max_tokens,
el caso real documentado— degradaba a ceros SIN intentar Gemini, justo el caso
de uso estrella del fallback.
"""

import json

import pytest

from services.groq_service import GroqService


class _FakeGemini:
    """Doble de GeminiService que devuelve un rerank válido."""

    def __init__(self, text: str | None = None):
        self._text = text or json.dumps(
            [{"index": 0, "score": 77, "reason": "buen encaje"}]
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


def _groq_with_response(text: str) -> GroqService:
    svc = GroqService.__new__(GroqService)
    svc.client = object()  # fuerza is_available True
    svc.redis = None

    async def _chat(**_kwargs) -> str:
        return text

    svc.get_chat_response = _chat
    return svc


@pytest.mark.asyncio
class TestP34RerankBasuraCaeAGemini:
    @pytest.mark.parametrize(
        "basura",
        [
            '[{"index": 0, "score": 8',  # truncado por max_tokens (caso real)
            "lo siento, no puedo",  # no es JSON
            '{"index": 0}',  # JSON pero no lista
            '[{"index": 9, "score": 50}]',  # índice fuera del batch
        ],
    )
    async def test_groq_con_basura_prueba_gemini(self, basura):
        groq = _groq_with_response(basura)
        gemini = _FakeGemini()

        out = await groq._rerank_call("prompt", gemini, batch_len=1)

        assert gemini.calls == 1, "el fallback debe cubrir «Groq responde basura»"
        assert out[0]["score"] == 77

    async def test_el_rerank_completo_usa_el_score_de_gemini(self):
        """rerank_jobs no puede degradar a ceros habiendo fallback sano."""
        groq = _groq_with_response('[{"index": 0, "score": 8')
        gemini = _FakeGemini()

        results = await groq.rerank_jobs(
            profile_text="cv",
            profile_skills=["python"],
            candidates=[{"title": "Dev", "company": "Co"}],
            fallback=gemini,
        )

        assert gemini.calls == 1
        assert [r["score"] for r in results] == [77]

    async def test_basura_en_ambos_proveedores_degrada_a_ceros(self):
        """No-regresión: sin respuesta parseable se degrada (sin cachear)."""
        groq = _groq_with_response("basura")
        gemini = _FakeGemini(text="tambien basura")

        results = await groq.rerank_jobs(
            profile_text="cv",
            profile_skills=["python"],
            candidates=[{"title": "Dev", "company": "Co"}],
            fallback=gemini,
        )

        assert [r["score"] for r in results] == [0]

    async def test_groq_sano_no_llama_al_fallback(self):
        """No-regresión: con Groq respondiendo bien, Gemini no se toca."""
        groq = _groq_with_response(json.dumps([{"index": 0, "score": 91}]))
        gemini = _FakeGemini()

        out = await groq._rerank_call("prompt", gemini, batch_len=1)

        assert gemini.calls == 0
        assert out[0]["score"] == 91

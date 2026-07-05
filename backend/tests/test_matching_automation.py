"""Tests del matching automático y el fallback Gemini del re-ranking.

Cubre:
- GroqService._rerank_call: Groq primario, Gemini de fallback.
- rerank_jobs: sin ningún proveedor devuelve [] (degradación limpia).
- run_all_matches: itera perfiles con embedding y agrega resultados.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.groq_service import GroqService


class _FakeGemini:
    """Doble de GeminiService con la interfaz que usa el re-ranking."""

    def __init__(self, available: bool = True, text: str = '[{"index":0,"score":80}]'):
        self._available = available
        self._text = text
        self.calls = 0

    @property
    def is_available(self) -> bool:
        return self._available

    async def get_chat_response(
        self, user_message, system_prompt=None, temperature=0.4, max_tokens=4096
    ) -> str:
        self.calls += 1
        return self._text


# --- _rerank_call: selección de proveedor -------------------------------------


async def test_rerank_uses_gemini_when_groq_absent():
    """Sin Groq, el re-ranking cae directamente a Gemini."""
    groq = GroqService()
    groq.client = None  # garantiza is_available False sin depender del entorno
    gemini = _FakeGemini()

    out = await groq._rerank_call("prompt", gemini)

    assert out == '[{"index":0,"score":80}]'
    assert gemini.calls == 1


async def test_rerank_falls_back_when_groq_raises():
    """Si Groq lanza (p.ej. 401 por key caducada), toma el relevo Gemini."""
    groq = GroqService()
    groq.client = object()  # fuerza is_available True
    groq.get_chat_response = AsyncMock(side_effect=RuntimeError("HTTP 401"))
    gemini = _FakeGemini()

    out = await groq._rerank_call("prompt", gemini)

    assert out == '[{"index":0,"score":80}]'
    groq.get_chat_response.assert_awaited_once()
    assert gemini.calls == 1


async def test_rerank_raises_when_no_provider():
    """Sin Groq ni fallback disponible, _rerank_call lanza para degradar."""
    groq = GroqService()
    groq.client = None
    with pytest.raises(RuntimeError):
        await groq._rerank_call("prompt", None)


async def test_rerank_jobs_empty_without_any_provider():
    """rerank_jobs devuelve [] (no revienta) si no hay ningún LLM."""
    groq = GroqService()
    groq.client = None
    out = await groq.rerank_jobs("cv", ["skill"], [{"title": "x"}], fallback=None)
    assert out == []


# --- run_all_matches: orquestación --------------------------------------------


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _FakeSession:
    def __init__(self, profiles):
        self._profiles = profiles

    async def execute(self, _stmt):
        return _Result(self._profiles)


class _FakeCtx:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *args):
        return False


class _Profile:
    def __init__(self, uid):
        self.user_id = uid


async def test_run_all_matches_iterates_profiles_and_aggregates():
    from tasks.matching_tasks import _run_all_matches_async

    profiles = [_Profile("u1"), _Profile("u2")]
    fake_service = AsyncMock()
    fake_service.run_matching = AsyncMock(
        return_value={"status": "success", "results_count": 3}
    )

    with (
        patch("database.task_session", return_value=_FakeCtx(_FakeSession(profiles))),
        patch("services.groq_service.GroqService"),
        patch("services.gemini_service.GeminiService"),
        patch("services.match_service.MatchService", return_value=fake_service),
    ):
        summary = await _run_all_matches_async()

    assert summary["profiles"] == 2
    assert summary["results"] == 6  # 3 por perfil
    assert fake_service.run_matching.await_count == 2

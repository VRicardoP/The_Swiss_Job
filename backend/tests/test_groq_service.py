"""Tests de GroqService — lo único que queda vivo: disponibilidad y chat.

DT-148 B1: las clases de parseo, fallback, clave de caché y re-ranking probaban
la rama del Stage 3 que `42ecf96` dejó sin llamadores. Se fueron con ella.
"""

from unittest.mock import MagicMock, patch

import pytest

from config import settings
from services.groq_service import GroqService


@pytest.mark.anyio
class TestGroqServiceAvailability:
    def test_not_available_without_api_key(self):
        with patch("services.groq_service.settings") as mock_settings:
            mock_settings.GROQ_API_KEY = ""
            service = GroqService()
            assert service.is_available is False

    def test_available_with_client_set(self):
        svc = GroqService.__new__(GroqService)
        svc.client = MagicMock()
        svc.redis = None
        assert svc.is_available is True

    def test_not_available_with_no_client(self):
        svc = GroqService.__new__(GroqService)
        svc.client = None
        svc.redis = None
        assert svc.is_available is False


@pytest.mark.anyio
class TestGroqServiceReasoningEffort:
    """Envío condicional de reasoning_effort en get_chat_response.

    El modelo rápido (qwen3.6) razona por defecto y quemaría max_tokens en
    <think> rompiendo los parsers JSON — debe recibir reasoning_effort. El
    modelo pesado (gpt-oss-120b) no debe recibir el parámetro.
    """

    @staticmethod
    def _service_with_mock_client() -> tuple[GroqService, MagicMock]:
        svc = GroqService.__new__(GroqService)
        svc.client = MagicMock()
        svc.redis = None
        completion = MagicMock()
        completion.choices = [MagicMock(message=MagicMock(content="ok"))]
        svc.client.chat.completions.create.return_value = completion
        return svc, svc.client.chat.completions.create

    async def test_rerank_model_sends_reasoning_effort(self):
        svc, create = self._service_with_mock_client()
        await svc.get_chat_response("hola", model=settings.GROQ_RERANK_MODEL)
        assert (
            create.call_args.kwargs["reasoning_effort"]
            == settings.GROQ_RERANK_REASONING_EFFORT
        )

    async def test_default_model_sends_reasoning_effort(self):
        # Llamadas sin model (p.ej. letter_generator) usan el modelo rápido
        # por defecto y deben quedar cubiertas igualmente.
        svc, create = self._service_with_mock_client()
        await svc.get_chat_response("hola")
        assert "reasoning_effort" in create.call_args.kwargs

    async def test_heavy_model_does_not_send_reasoning_effort(self):
        svc, create = self._service_with_mock_client()
        await svc.get_chat_response("hola", model=settings.GROQ_MODEL)
        assert "reasoning_effort" not in create.call_args.kwargs

    async def test_empty_setting_disables_the_parameter(self):
        svc, create = self._service_with_mock_client()
        with patch.object(settings, "GROQ_RERANK_REASONING_EFFORT", ""):
            await svc.get_chat_response("hola", model=settings.GROQ_RERANK_MODEL)
        assert "reasoning_effort" not in create.call_args.kwargs

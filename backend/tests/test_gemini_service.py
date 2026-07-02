"""Tests for GeminiService — document generation via Gemini (mocked httpx)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gemini_service import GeminiService


def _mock_async_client(response: MagicMock) -> MagicMock:
    """AsyncClient falso usable como `async with` que devuelve `response` en .post."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    return client


def _resp(status_code: int, payload: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json = MagicMock(return_value=payload)
    return r


def _service_with_key(key: str = "fake-key") -> GeminiService:
    with patch("services.gemini_service.settings") as s:
        s.GEMINI_API_KEY = key
        s.GEMINI_MODEL = "gemini-2.5-flash"
        s.GEMINI_TIMEOUT_SECONDS = 60.0
        return GeminiService()


@pytest.mark.anyio
class TestGeminiAvailability:
    def test_not_available_without_key(self):
        assert _service_with_key("").is_available is False

    def test_available_with_key(self):
        assert _service_with_key().is_available is True


@pytest.mark.anyio
class TestGeminiGenerate:
    async def test_success_returns_text(self):
        svc = _service_with_key()
        payload = {
            "candidates": [{"content": {"parts": [{"text": "# CV\n\nProfil..."}]}}]
        }
        with patch(
            "services.gemini_service.httpx.AsyncClient",
            return_value=_mock_async_client(_resp(200, payload)),
        ):
            out = await svc.get_chat_response("user", system_prompt="sys")
        assert "# CV" in out

    async def test_http_error_raises_with_api_message(self):
        svc = _service_with_key()
        payload = {"error": {"message": "You exceeded your current quota"}}
        with patch(
            "services.gemini_service.httpx.AsyncClient",
            return_value=_mock_async_client(_resp(429, payload)),
        ):
            with pytest.raises(RuntimeError, match="429"):
                await svc.get_chat_response("user")

    async def test_empty_output_raises(self):
        svc = _service_with_key()
        payload = {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]}
        with patch(
            "services.gemini_service.httpx.AsyncClient",
            return_value=_mock_async_client(_resp(200, payload)),
        ):
            with pytest.raises(RuntimeError, match="sin texto"):
                await svc.get_chat_response("user")

    async def test_raises_without_key(self):
        svc = _service_with_key("")
        with pytest.raises(RuntimeError, match="no configurado"):
            await svc.get_chat_response("user")

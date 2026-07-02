"""GeminiService — document generation via Google Gemini (free tier).

Cliente async mínimo (httpx, sin SDK extra) sobre la Generative Language API.
Se usa como proveedor PRIMARIO de generación de CV/carta por su calidad; el
fallback a Groq lo decide DocumentGeneratorService. Interfaz compatible con
GroqService.get_chat_response para poder intercambiarse.
"""

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiService:
    """Cliente async para Gemini generateContent (generación de documentos)."""

    def __init__(self) -> None:
        self._api_key = settings.GEMINI_API_KEY
        self._model = settings.GEMINI_MODEL
        self._timeout = settings.GEMINI_TIMEOUT_SECONDS

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    async def get_chat_response(
        self,
        user_message: str,
        system_prompt: str | None = None,
        temperature: float = 0.4,
        max_tokens: int = 4096,
    ) -> str:
        """Genera texto con Gemini. Lanza en error HTTP/API o salida vacía.

        Lanzar (en vez de degradar en silencio) es deliberado: deja que el llamante
        decida el fallback a Groq. No se registra la URL/params (llevan la API key).
        """
        if not self._api_key:
            raise RuntimeError("Gemini no configurado (GEMINI_API_KEY vacío)")

        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        url = f"{_API_BASE}/{self._model}:generateContent"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, params={"key": self._api_key}, json=payload)

        if resp.status_code != 200:
            # El cuerpo trae .error.message (p.ej. "high demand", "quota"); NO la key.
            raise RuntimeError(
                f"Gemini HTTP {resp.status_code}: {self._error_message(resp)}"
            )

        text = self._extract_text(resp.json())
        if not text:
            reason = (resp.json().get("candidates") or [{}])[0].get(
                "finishReason", "?"
            )
            raise RuntimeError(f"Gemini sin texto (finishReason={reason})")
        return text

    @staticmethod
    def _extract_text(data: dict) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        try:
            return str(resp.json().get("error", {}).get("message", ""))[:200]
        except Exception:
            return ""

"""GroqService — una sola cosa: hablar con la API de Groq por chat.

`settings.GROQ_RERANK_MODEL` (qwen3.8-27b, rápido) es el modelo por defecto y
lo usan la traducción de títulos y el calentamiento del feed;
`settings.GROQ_MODEL` (gpt-oss-120b) lo piden explícitamente las tareas de
documentos. El SDK de Groq es SINCRONO: cada llamada va a `run_in_threadpool`.

DT-148 B1 (2026-10-21): aquí vivía además la rama de re-ranking LLM del Stage 3
del matching —442 LOC con su caché de veredictos, su prompt de sistema y su
huella de perfil—. El Stage 3 murió con `job_matcher.py` en `42ecf96` y la rama
quedó sostenida solo por sus tests. El re-ranking de hoy es el cross-encoder
del núcleo (`jobhunt_core/cross_encoder.py`), que no pasa por Groq.
"""

from typing import Any

from fastapi.concurrency import run_in_threadpool

from config import settings


class GroqService:
    """Async wrapper around the synchronous Groq Python SDK."""

    def __init__(self, redis_client=None):
        self.client = None
        self.redis = redis_client
        if settings.GROQ_API_KEY:
            from groq import Groq

            self.client = Groq(api_key=settings.GROQ_API_KEY)

    @property
    def is_available(self) -> bool:
        return self.client is not None

    async def get_chat_response(
        self,
        user_message: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send a chat completion request to Groq via threadpool."""
        if not self.is_available:
            raise RuntimeError("Groq client not configured (GROQ_API_KEY missing)")

        selected_model = model or settings.GROQ_RERANK_MODEL
        effective_temp = temperature if temperature is not None else 0.2
        effective_max_tokens = max_tokens or 2048

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        # qwen3.6 razona por defecto y agotaría max_tokens en <think> antes de
        # emitir contenido (rompe los parsers JSON de traducción y rerank).
        # Suprimimos el razonamiento SOLO para el modelo rápido; el modelo pesado
        # (gpt-oss-120b) ya emite su razonamiento en un campo aparte.
        extra_params: dict[str, Any] = {}
        if (
            selected_model == settings.GROQ_RERANK_MODEL
            and settings.GROQ_RERANK_REASONING_EFFORT
        ):
            extra_params["reasoning_effort"] = settings.GROQ_RERANK_REASONING_EFFORT

        def _sync_call() -> str:
            completion = self.client.chat.completions.create(
                messages=messages,
                model=selected_model,
                temperature=effective_temp,
                max_tokens=effective_max_tokens,
                **extra_params,
            )
            return completion.choices[0].message.content

        return await run_in_threadpool(_sync_call)

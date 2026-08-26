"""Doble determinista de la frontera de RED de los proveedores LLM.

Sustituye SOLO la llamada saliente: el SDK de Groq (`groq.Groq`) y la costura
HTTP de Gemini (`services.gemini_service._client_factory`). Todo lo que hay
por encima —construcción del prompt, parseo, saneo, batching, caché, elección
de fallback— sigue siendo código real y sigue pudiendo fallar.

Por qué esto AUMENTA la capacidad de refutación en vez de reducirla: un test
cuya luz verde depende de que la API de un tercero esté arriba no refuta nada
(si pasa, no sabes si el código es correcto o si respondió Groq; si falla, no
sabes si rompiste algo o si caducó la clave — modo de fallo que este proyecto
ya ha sufrido). El doble responde con la FORMA que el contrato exige y con
contenido derivado de la ENTRADA, así que la lógica de arriba sigue siendo
observable y falsable.

Que la forma que el doble finge sea la del proveedor REAL lo comprueba
`backend/tests_live/test_llm_contract_live.py`, que queda fuera de `testpaths`
y es el único autorizado a gastar dinero y a depender de la red.
"""

import json
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from services.cv_analyzer import _SYSTEM_PROMPT as CV_SYSTEM_PROMPT
from services.groq_service import RERANK_SYSTEM_PROMPT
from services.translation_service import (
    _RETRY_SYSTEM_PROMPT,
    TRANSLATION_SYSTEM_PROMPT,
)

# Texto genérico para las tareas de PROSA (CV, carta, borrador de watchlist):
# su contrato es «una cadena no vacía», y eso es lo que se devuelve.
GENERIC_TEXT = "# Documento de prueba\n\nContenido generado por el doble LLM."

_JOBS_MARKER = "## Jobs to Evaluate"


def jobs_in_prompt(user_prompt: str) -> list[dict]:
    """Las ofertas que lleva un prompt de re-ranking.

    Si el prompt cambiara de forma, esto revienta en vez de inventarse un lote
    — que es exactamente lo que se quiere de un doble.
    """
    start = user_prompt.index("[", user_prompt.index(_JOBS_MARKER))
    jobs, _ = json.JSONDecoder().raw_decode(user_prompt[start:])
    return jobs


def _rerank_response(user_prompt: str) -> str:
    """Un veredicto por oferta del lote, con los índices que pide el contrato."""
    return json.dumps(
        [
            {
                "index": i,
                # Determinista y derivado de la ENTRADA: un test puede
                # distinguir un lote de otro sin depender de la red.
                "score": 50 + (i % 5) * 10,
                "matching_skills": [],
                "missing_skills": [],
                "reason": f"Doble LLM: evaluación de «{job.get('title', '')}».",
            }
            for i, job in enumerate(jobs_in_prompt(user_prompt))
        ]
    )


def _translation_response(user_prompt: str) -> str:
    """Identidad: la regla 5 del prompt real es «si ya está en inglés, tal cual».

    Devolver el título sin cambiar es una salida VÁLIDA del contrato, y no
    inventa traducciones que ningún test podría comprobar. La lógica de mapeo
    y rescate de la traducción la cubre `tests/test_translation_service.py`
    con dobles propios.
    """
    return json.dumps(json.loads(user_prompt), ensure_ascii=False)


def _cv_response(_user_prompt: str) -> str:
    """Perfil con las claves EXACTAS que documenta el prompt de `CVAnalyzer`."""
    return json.dumps(
        {
            "title": "Bilingual Executive Assistant",
            "skills": ["Translation", "Proofreading", "Customer Support"],
            "languages": ["English", "Spanish"],
            "experience_years": 5,
            "locations": ["Remote"],
            "remote_pref": "any",
        }
    )


# El despacho va por el prompt de SISTEMA, importado de su módulo: si alguien
# reescribe el prompt, el doble le sigue automáticamente en vez de quedarse
# respondiendo a una forma que ya no se pide.
_BY_SYSTEM_PROMPT = {
    RERANK_SYSTEM_PROMPT: _rerank_response,
    TRANSLATION_SYSTEM_PROMPT: _translation_response,
    _RETRY_SYSTEM_PROMPT: _translation_response,
    CV_SYSTEM_PROMPT: _cv_response,
}


def render(system_prompt: str, user_prompt: str) -> str:
    """Respuesta del doble para (system, user)."""
    handler = _BY_SYSTEM_PROMPT.get(system_prompt or "")
    return handler(user_prompt) if handler else GENERIC_TEXT


class _GroqCompletions:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def create(self, *, messages, model=None, **_kwargs):
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        self._calls.append(
            {"provider": "groq", "model": model, "system": system, "user": user}
        )
        content = render(system, user)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _GroqClient:
    """Doble de `groq.Groq` con la única superficie que usa `GroqService`."""

    def __init__(self, calls: list, **_kwargs) -> None:
        self.chat = SimpleNamespace(completions=_GroqCompletions(calls))


class _GeminiResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _GeminiClient:
    """Doble de `httpx.AsyncClient` para el POST de `generateContent`."""

    def __init__(self, calls: list, **_kwargs) -> None:
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, _url, *, headers=None, json=None, **_kwargs):
        payload = json or {}
        instruction = payload.get("systemInstruction", {})
        system = "".join(p.get("text", "") for p in instruction.get("parts", []))
        user = "".join(
            p.get("text", "")
            for content in payload.get("contents", [])
            for p in content.get("parts", [])
        )
        self._calls.append(
            {"provider": "gemini", "model": None, "system": system, "user": user}
        )
        return _GeminiResponse(
            {"candidates": [{"content": {"parts": [{"text": render(system, user)}]}}]}
        )


@contextmanager
def llm_boundary_stub():
    """Activa el doble. Cede la lista de llamadas salientes registradas."""
    calls: list[dict] = []
    with ExitStack() as stack:
        stack.enter_context(patch("groq.Groq", lambda **kw: _GroqClient(calls, **kw)))
        stack.enter_context(
            patch(
                "services.gemini_service._client_factory",
                lambda **kw: _GeminiClient(calls, **kw),
            )
        )
        yield calls

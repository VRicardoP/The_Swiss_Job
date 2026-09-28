"""A19-15 §D — los títulos de la pantalla principal se traducen EN EL FONDO.

Lo que fija, y por qué:

- `translate=false` (la carga de 3.000 ofertas) NO llama al LLM. El control
  negativo instala un Groq que LANZA si se le pide una respuesta: si alguien
  devuelve la traducción al camino de respuesta, la prueba explota en vez de
  ponerse lenta otra vez (mismo patrón que `test_language_store.py`).
- Pero sirve lo que el fondo ya tradujo: una ida a Redis (MGET), sin
  detecciones ni peticiones.
- El fondo traduce sólo lo que falta, acotado por pasada, y sin Redis no paga
  nada (no tendría dónde dejarlo).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import settings
from services.groq_service import GroqService
from services.matching import warm
from services.translation_service import TranslationService


class _RedisFalso:
    def __init__(self, datos: dict[str, str]):
        self.datos = datos
        self.llamadas = 0

    async def mget(self, claves):
        self.llamadas += 1
        return [self.datos.get(k) for k in claves]


def _groq_que_lanza(redis) -> GroqService:
    groq = GroqService(redis_client=redis)
    groq.client = object()  # «disponible», pero pedirle algo es un error

    async def prohibido(*_a, **_k):
        raise AssertionError("el camino de respuesta llamó al LLM")

    groq.get_chat_response = prohibido
    return groq


@pytest.mark.asyncio
async def test_cached_translations_lee_solo_de_redis_en_una_ida():
    redis = _RedisFalso(
        {TranslationService._cache_key("Lehrer gesucht"): "Teacher wanted"}
    )
    translator = TranslationService(_groq_que_lanza(redis))

    out = await translator.cached_translations(
        ["Lehrer gesucht", "Sin traducir", "Lehrer gesucht", ""]
    )

    assert out == {"Lehrer gesucht": "Teacher wanted"}
    assert redis.llamadas == 1, "más de una ida a Redis para un lote"


@pytest.mark.asyncio
async def test_sin_redis_no_hay_cache_y_no_se_llama_al_llm():
    translator = TranslationService(_groq_que_lanza(None))
    assert await translator.cached_translations(["Lehrer gesucht"]) == {}


@pytest.mark.asyncio
async def test_translate_false_sirve_la_cache_sin_tocar_el_llm():
    from routers.match import _build_results_response

    redis = _RedisFalso(
        {TranslationService._cache_key("Lehrer gesucht"): "Teacher wanted"}
    )
    job = MagicMock()
    job.title = "Lehrer gesucht"
    job.language = "de"
    job.hash = "h1"
    item = {"job": job, "score": 80.0, "match": None}

    vistas: list[dict] = []
    with (
        patch(
            "routers.match._to_match_response",
            side_effect=lambda it, tr, la: vistas.append(tr),
        ),
        patch("routers.match.MatchResultsResponse", MagicMock()),
    ):
        await _build_results_response(
            [item], 1, {}, _groq_que_lanza(redis), None, translate=False
        )

    assert vistas == [{"Lehrer gesucht": "Teacher wanted"}]


@pytest.mark.asyncio
async def test_el_fondo_traduce_solo_lo_que_falta_y_acotado(monkeypatch):
    monkeypatch.setattr(settings, "TRANSLATION_WARMUP_MAX_PER_PASS", 2)
    titulos = ["A", "B", "C", "D", "A"]
    backend = MagicMock()
    backend.cached_feed_titles = AsyncMock(return_value=titulos)
    pedidos: list[list[str]] = []

    async def translate_titles(self, items, languages=None):
        pedidos.append([i["title"] for i in items])
        return {}

    monkeypatch.setattr(TranslationService, "translate_titles", translate_titles)
    monkeypatch.setattr(
        TranslationService, "cached_translations", AsyncMock(return_value={"A": "a"})
    )
    monkeypatch.setattr(GroqService, "is_available", property(lambda self: True))
    with patch("services.language_store.lookup", AsyncMock(return_value=({}, set()))):
        n = await warm._traducir_en_fondo(None, backend, uuid.uuid4(), _RedisFalso({}))

    assert n == 2
    assert pedidos == [["B", "C"]], "debía saltar lo cacheado y respetar la cota"


@pytest.mark.asyncio
async def test_el_fondo_sin_redis_no_paga_nada():
    backend = MagicMock()
    backend.cached_feed_titles = AsyncMock(return_value=["A"])
    assert await warm._traducir_en_fondo(None, backend, uuid.uuid4(), None) == 0
    backend.cached_feed_titles.assert_not_called()

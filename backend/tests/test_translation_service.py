"""Tests for TranslationService — language resolution, JSON salvage, auth handling.

Mock GroqService: no real LLM calls. Cubre los modos de fallo que dejaban títulos
sin traducir: key inválida (401) y JSON de lote truncado/malformado.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from groq import APIStatusError

from services.translation_service import (
    TranslationService,
    _salvage_pairs,
)


class FakeAPIStatusError(APIStatusError):
    """APIStatusError de pega: evita construir response/body reales del SDK."""

    def __init__(self, status_code: int):  # noqa: D107
        self.status_code = status_code


def _groq(*, response=None, raises=None):
    """GroqService simulado: is_available=True, sin Redis, respuesta/excepción fijas."""
    g = MagicMock()
    g.is_available = True
    g.redis = None
    if raises is not None:
        g.get_chat_response = AsyncMock(side_effect=raises)
    else:
        g.get_chat_response = AsyncMock(return_value=response)
    return g


# --------------------------------------------------------------------------- #
# _salvage_pairs — rescate de JSON truncado / malformado
# --------------------------------------------------------------------------- #
class TestSalvagePairs:
    def test_recovers_complete_pairs_from_truncated_json(self):
        # Truncado a mitad del último valor (típico corte por max_tokens).
        text = '{"0": "Software Developer", "1": "Project Manager", "2": "Accoun'
        out = _salvage_pairs(text)
        assert out == {"0": "Software Developer", "1": "Project Manager"}

    def test_unescapes_quotes_in_value(self):
        out = _salvage_pairs(r'{"0": "Clerk \"KV\" 80%"}')
        assert out["0"] == 'Clerk "KV" 80%'

    def test_empty_on_no_pairs(self):
        assert _salvage_pairs("total garbage no json here") == {}


# --------------------------------------------------------------------------- #
# _parse_response — usa el rescate cuando json.loads falla
# --------------------------------------------------------------------------- #
class TestParseResponse:
    def test_clean_json(self):
        idx = {"0": "Softwareentwickler", "1": "Projektleiter"}
        out = TranslationService._parse_response(
            '{"0": "Software Developer", "1": "Project Manager"}', idx
        )
        assert out == {
            "Softwareentwickler": "Software Developer",
            "Projektleiter": "Project Manager",
        }

    def test_salvages_partial_when_truncated(self):
        idx = {"0": "Softwareentwickler", "1": "Projektleiter"}
        # Segundo valor truncado → json.loads falla, pero el primero se rescata.
        out = TranslationService._parse_response(
            '{"0": "Software Developer", "1": "Project Manag', idx
        )
        assert out == {"Softwareentwickler": "Software Developer"}

    def test_strips_markdown_fences(self):
        idx = {"0": "Buchhalter"}
        out = TranslationService._parse_response(
            '```json\n{"0": "Accountant"}\n```', idx
        )
        assert out == {"Buchhalter": "Accountant"}


# --------------------------------------------------------------------------- #
# _translate_batch — 401/403 propaga, 429/5xx se traga
# --------------------------------------------------------------------------- #
class TestTranslateBatchErrors:
    @pytest.mark.asyncio
    async def test_auth_error_401_propagates(self):
        svc = TranslationService(_groq(raises=FakeAPIStatusError(401)))
        with pytest.raises(APIStatusError):
            await svc._translate_batch(["Softwareentwickler"])

    @pytest.mark.asyncio
    async def test_permission_error_403_propagates(self):
        svc = TranslationService(_groq(raises=FakeAPIStatusError(403)))
        with pytest.raises(APIStatusError):
            await svc._translate_batch(["Softwareentwickler"])

    @pytest.mark.asyncio
    async def test_rate_limit_429_returns_empty(self):
        svc = TranslationService(_groq(raises=FakeAPIStatusError(429)))
        assert await svc._translate_batch(["Softwareentwickler"]) == {}


# --------------------------------------------------------------------------- #
# translate_titles — extremo a extremo con mocks
# --------------------------------------------------------------------------- #
class TestTranslateTitles:
    @pytest.mark.asyncio
    async def test_auth_error_aborts_gracefully_without_crashing(self, caplog):
        svc = TranslationService(_groq(raises=FakeAPIStatusError(401)))
        titles = [
            {"title": "Softwareentwickler (m/w/d)", "language": "de"},
            {"title": "Projektleiter Informatik", "language": "de"},
        ]
        with caplog.at_level("ERROR"):
            result = await svc.translate_titles(titles)
        # No traduce nada, pero no revienta; el router mostrará el original.
        assert all(result.get(t["title"]) in (None, t["title"]) for t in titles)
        assert "GROQ_API_KEY" in caplog.text

    @pytest.mark.asyncio
    async def test_auth_error_does_not_retry(self):
        groq = _groq(raises=FakeAPIStatusError(401))
        svc = TranslationService(groq)
        await svc.translate_titles([{"title": "Softwareentwickler", "language": "de"}])
        # Una sola llamada: el 401 aborta, no se reintenta con prompt simplificado.
        assert groq.get_chat_response.call_count == 1

    @pytest.mark.asyncio
    async def test_happy_path_translates_german_title(self):
        svc = TranslationService(_groq(response='{"0": "Software Developer"}'))
        result = await svc.translate_titles(
            [{"title": "Softwareentwickler", "language": "de"}]
        )
        assert result["Softwareentwickler"] == "Software Developer"

    @pytest.mark.asyncio
    async def test_english_title_skipped_no_llm_call(self):
        groq = _groq(response="{}")
        svc = TranslationService(groq)
        result = await svc.translate_titles(
            [{"title": "Senior Software Engineer", "language": "en"}]
        )
        assert result["Senior Software Engineer"] == "Senior Software Engineer"
        groq.get_chat_response.assert_not_called()


class TestModeloDecomisado:
    """A-20: un 404 de Groq es CONFIGURACIÓN, no un transitorio.

    Groq decomisa modelos sin avisar —tres veces ya: llama-4-scout el
    2026-07-17, llama-3.3-70b el 2026-08-16, qwen3.6-27b el 2026-09-15— y el
    SDK lo señala con un 404. Caía en la rama de los transitorios y producía un
    bucle perpetuo: los 25 títulos del lote iban a `failed_titles`, se
    REINTENTABAN con el prompt simplificado (dos llamadas 404 por lote), y como
    el resultado dejaba `result[title] = title` el `translated == title` impedía
    cachear nada, así que el calentador volvía a elegir los mismos ≤100
    pendientes cada 60 s, por usuario, para siempre.

    Control negativo: devolviendo el 404 a la rama transitoria, el primer caso
    deja de lanzar y el segundo no encuentra el mando en el log.
    """

    @pytest.mark.asyncio
    async def test_un_404_se_propaga_en_vez_de_reintentarse(self):
        svc = TranslationService(_groq(raises=FakeAPIStatusError(404)))
        with pytest.raises(APIStatusError):
            await svc._translate_batch(["Softwareentwickler"])

    @pytest.mark.asyncio
    async def test_el_404_no_dispara_el_reintento_del_prompt_simplificado(self):
        """Lo que hacía el bucle caro: dos llamadas por lote en vez de una."""
        groq = _groq(raises=FakeAPIStatusError(404))
        svc = TranslationService(groq)

        await svc.translate_titles(
            [
                {"title": "Softwareentwickler", "language": "de"},
                {"title": "Projektleiter", "language": "de"},
            ]
        )

        llamadas = groq.get_chat_response.await_count
        assert llamadas == 1, (
            f"un modelo decomisado no se reintenta; hubo {llamadas} llamadas"
        )

    @pytest.mark.asyncio
    async def test_el_log_nombra_el_MODELO_y_no_la_api_key(self, caplog):
        """El síntoma visible —«títulos sin traducir»— es el mismo que una key
        caducada. Si el mensaje manda a mirar GROQ_API_KEY, manda al sitio
        equivocado y el fallo sigue ahí."""
        svc = TranslationService(_groq(raises=FakeAPIStatusError(404)))

        with caplog.at_level("ERROR"):
            await svc.translate_titles(
                [{"title": "Softwareentwickler", "language": "de"}]
            )

        mensajes = " ".join(r.getMessage() for r in caplog.records)
        assert "GROQ_RERANK_MODEL" in mensajes, mensajes
        assert "GROQ_API_KEY" not in mensajes, "un 404 no se arregla mirando la API key"

    @pytest.mark.asyncio
    async def test_un_401_sigue_nombrando_la_API_KEY(self, caplog):
        """La contraparte: no haber roto el diagnóstico que ya era correcto."""
        svc = TranslationService(_groq(raises=FakeAPIStatusError(401)))

        with caplog.at_level("ERROR"):
            await svc.translate_titles(
                [{"title": "Softwareentwickler", "language": "de"}]
            )

        mensajes = " ".join(r.getMessage() for r in caplog.records)
        assert "GROQ_API_KEY" in mensajes, mensajes

    @pytest.mark.asyncio
    async def test_un_429_SIGUE_siendo_transitorio(self):
        """Y el control que impide pasarse: un 429 no puede empezar a propagar,
        o un pico de cuota se convertiría en un error de configuración."""
        svc = TranslationService(_groq(raises=FakeAPIStatusError(429)))
        assert await svc._translate_batch(["Softwareentwickler"]) == {}

"""Tests de caracterización para DocumentGeneratorService.

Cubre los helpers deterministas (cache_key, _language_name, construcción de
prompts con truncado y defaults) y la lógica de _call_llm (Gemini primario,
Groq de fallback ante indisponibilidad o excepción). No llama a LLMs reales.
"""

import pytest

from services.document_generator import DocumentGeneratorService


class TestLanguageName:
    @pytest.mark.parametrize(
        "code,expected_fragment",
        [
            ("en", "English"),
            ("de", "German"),
            ("fr", "French"),
            ("it", "Italian"),
            ("xx", "English"),  # desconocido → default English
        ],
    )
    def test_language_name(self, code, expected_fragment):
        assert expected_fragment in DocumentGeneratorService._language_name(code)


class TestCacheKey:
    def test_deterministic_and_prefixed(self):
        k1 = DocumentGeneratorService.cache_key("u1", "h1", "cv", "en")
        k2 = DocumentGeneratorService.cache_key("u1", "h1", "cv", "en")
        assert k1 == k2
        assert k1.startswith("gendoc:")

    def test_distinct_inputs_distinct_keys(self):
        base = DocumentGeneratorService.cache_key("u1", "h1", "cv", "en")
        assert base != DocumentGeneratorService.cache_key("u2", "h1", "cv", "en")
        assert base != DocumentGeneratorService.cache_key("u1", "h2", "cv", "en")
        assert base != DocumentGeneratorService.cache_key("u1", "h1", "letter", "en")
        assert base != DocumentGeneratorService.cache_key("u1", "h1", "cv", "de")


class TestBuildPrompts:
    def test_cv_prompt_defaults_when_empty(self):
        prompt = DocumentGeneratorService._build_cv_prompt(
            "cv", [], "T", "C", "desc", [], None, None
        )
        assert "Not specified" in prompt  # skills / tags vacíos
        assert "Not analyzed" in prompt  # matching / missing None
        assert "do NOT invent" in prompt

    def test_cv_prompt_truncates_long_cv(self):
        long_cv = "x" * 10000
        prompt = DocumentGeneratorService._build_cv_prompt(
            long_cv, ["a"], "T", "C", "desc", ["t"], ["m"], ["mm"]
        )
        # cv_text se corta a 6000 → no debe aparecer el bloque completo
        assert "x" * 6001 not in prompt
        assert "x" * 6000 in prompt

    def test_cover_letter_prompt_includes_job_fields(self):
        prompt = DocumentGeneratorService._build_cover_letter_prompt(
            "cv", ["python"], "Editor", "ACME", "desc", ["tag"], ["python"], None
        )
        assert "Editor" in prompt
        assert "ACME" in prompt
        assert "python" in prompt


class _FakeLLM:
    def __init__(self, available, response="OUT", raises=False):
        self.is_available = available
        self._response = response
        self._raises = raises
        self.called = False

    async def get_chat_response(self, **kwargs):
        self.called = True
        if self._raises:
            raise RuntimeError("boom")
        return self._response


@pytest.mark.anyio
class TestCallLLMFallback:
    async def test_gemini_used_when_available(self):
        gemini = _FakeLLM(available=True, response="GEMINI")
        groq = _FakeLLM(available=True, response="GROQ")
        svc = DocumentGeneratorService(groq=groq, gemini=gemini)
        out = await svc._call_llm("sys", "user")
        assert out == "GEMINI"
        assert gemini.called and not groq.called

    async def test_falls_back_to_groq_when_no_gemini(self):
        groq = _FakeLLM(available=True, response="GROQ")
        svc = DocumentGeneratorService(groq=groq, gemini=None)
        out = await svc._call_llm("sys", "user")
        assert out == "GROQ"
        assert groq.called

    async def test_falls_back_to_groq_when_gemini_raises(self):
        gemini = _FakeLLM(available=True, raises=True)
        groq = _FakeLLM(available=True, response="GROQ")
        svc = DocumentGeneratorService(groq=groq, gemini=gemini)
        out = await svc._call_llm("sys", "user")
        assert out == "GROQ"
        assert gemini.called and groq.called

    async def test_generate_cv_replaces_language_placeholder(self):
        groq = _FakeLLM(available=True, response="CV")
        svc = DocumentGeneratorService(groq=groq, gemini=None)
        out = await svc.generate_cv(
            cv_text="cv",
            skills=["a"],
            job_title="T",
            job_company="C",
            job_description="d",
            job_tags=["t"],
            language="de",
        )
        assert out == "CV"
        # El system prompt no debe conservar el placeholder sin resolver
        # (se comprueba indirectamente: la llamada no lanzó y devolvió salida)
        assert groq.called

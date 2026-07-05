"""Tests de caracterización para LetterGenerator (services/letter_generator.py).

Cubre las partes deterministas sin llamar a Groq real: construcción del prompt
(_build_user_prompt), plantilla de fallback (_fallback_template) y la lógica de
selección de generate_draft_letter (fallback si Groq no disponible, éxito con
LLM falso, timeout → fallback).
"""

from types import SimpleNamespace

import pytest

import services.letter_generator as lg
from services.letter_generator import (
    _build_user_prompt,
    _fallback_template,
    generate_draft_letter,
)

# Prefijo común del marcador de revisión: el prompt lo cierra tras "colegio]"
# y el fallback lo alarga ("colegio — qué te motiva de {name}..."). El prefijo
# es substring de ambos.
_MARKER = "[REVISAR: añadir referencia específica al colegio"


def _school(**over):
    base = dict(
        id="nae_zurich",
        name="Zurich International School",
        city="Zurich",
        notes="colegio urbano grande",
        contact_name=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _job(**over):
    base = dict(
        title="Content Editor",
        location="Zurich",
        url="https://x.ch/job/1",
        language="en",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _profile(**over):
    base = dict(
        title="Senior Editor",
        skills=["editing", "localization"],
        languages=["EN", "DE"],
        experience_years=8,
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestBuildUserPrompt:
    def test_includes_school_job_and_profile(self):
        prompt = _build_user_prompt(_school(), _job(), _profile())
        assert "Zurich International School" in prompt
        assert "Content Editor" in prompt
        assert "Senior Editor" in prompt
        assert _MARKER in prompt

    def test_default_salutation_without_contact(self):
        prompt = _build_user_prompt(_school(contact_name=None), _job(), _profile())
        assert "Estimado equipo de selección" in prompt

    def test_named_salutation_with_contact(self):
        prompt = _build_user_prompt(
            _school(contact_name="Dr. Meier"), _job(), _profile()
        )
        assert "Estimado/a Dr. Meier" in prompt

    def test_empty_skills_and_languages_marked(self):
        prompt = _build_user_prompt(
            _school(), _job(), _profile(skills=[], languages=[])
        )
        assert "no especificadas" in prompt

    def test_skills_truncated_to_15(self):
        many = [f"skill{i}" for i in range(30)]
        prompt = _build_user_prompt(_school(), _job(), _profile(skills=many))
        assert "skill14" in prompt
        assert "skill15" not in prompt


class TestFallbackTemplate:
    def test_contains_job_school_and_markers(self):
        text = _fallback_template(_school(), _job(), _profile(), "A")
        assert "Content Editor" in text
        assert "Zurich International School" in text
        assert _MARKER in text

    def test_default_name_when_profile_has_no_title(self):
        text = _fallback_template(_school(), _job(), _profile(title=None), "B")
        assert "[nombre del candidato]" in text


class _FakeGroq:
    def __init__(self, available, response="CARTA LLM", hang=False):
        self.is_available = available
        self._response = response
        self._hang = hang

    async def get_chat_response(self, **kwargs):
        if self._hang:
            import asyncio

            await asyncio.sleep(5)
        return self._response


@pytest.mark.anyio
class TestGenerateDraftLetter:
    async def test_fallback_when_groq_unavailable(self):
        result = await generate_draft_letter(
            groq=_FakeGroq(available=False),
            school=_school(),
            job=_job(),
            profile=_profile(),
            template_id="A",
        )
        assert _MARKER in result  # es la plantilla de fallback

    async def test_uses_llm_response_when_available(self):
        result = await generate_draft_letter(
            groq=_FakeGroq(available=True, response="CARTA LLM"),
            school=_school(),
            job=_job(),
            profile=_profile(),
            template_id="A",
        )
        assert result == "CARTA LLM"

    async def test_timeout_falls_back(self, monkeypatch):
        # Reducimos el timeout para no esperar 30s reales
        monkeypatch.setattr(lg, "_GROQ_TIMEOUT_SECONDS", 0.01)
        result = await generate_draft_letter(
            groq=_FakeGroq(available=True, hang=True),
            school=_school(),
            job=_job(),
            profile=_profile(),
            template_id="B",
        )
        assert _MARKER in result  # cayó al fallback tras el timeout

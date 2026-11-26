"""D10 — el título en inglés lo traduce el CORE y viaja; el BFF no traduce.

`VacancyDTO.title_en` → `_job_view` → `_to_match_response.job_title_en`. Se
instala un Groq que LANZA: si alguien devuelve la traducción al camino de
respuesta, la prueba explota en vez de gastar cuota en silencio.
"""

import pytest

from routers.match import _build_results_response, _to_match_response
from services.groq_service import GroqService
from services.matching.core_client import _job_view
from tests.test_language_transport import _match, _vacancy


@pytest.fixture
def groq_que_lanza(monkeypatch):
    async def estallar(*_a, **_kw):
        raise AssertionError("servir no llama al LLM (D10)")

    monkeypatch.setattr(GroqService, "get_chat_response", estallar)


def _item(**vacancy):
    return {
        "match": _match(),
        "job": _job_view(_vacancy(**vacancy), "core"),
        "school": None,
    }


def test_title_en_del_core_se_sirve(groq_que_lanza):
    resp = _to_match_response(_item(title_en="Software Developer (m/f/d)"))
    assert resp.job_title == "Softwareentwickler (m/w/d)"
    assert resp.job_title_en == "Software Developer (m/f/d)"


@pytest.mark.parametrize("valor", [None, "", "   ", 7, ["x"]])
def test_sin_title_en_valido_se_sirve_el_original(groq_que_lanza, valor):
    extra = {} if valor is None else {"title_en": valor}
    resp = _to_match_response(_item(**extra))
    assert resp.job_title_en is None
    assert resp.job_title == "Softwareentwickler (m/w/d)"


def test_title_en_igual_al_titulo_no_se_marca_como_traducido(groq_que_lanza):
    resp = _to_match_response(_item(title_en="Softwareentwickler (m/w/d)"))
    assert resp.job_title_en is None


@pytest.mark.asyncio
async def test_una_pagina_entera_se_sirve_sin_llm(groq_que_lanza):
    items = [_item(title_en=f"Developer {i}") for i in range(30)]
    out = await _build_results_response(items, 30, {}, db=None)
    assert [r.job_title_en for r in out.data] == [f"Developer {i}" for i in range(30)]

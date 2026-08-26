"""Contrato EN VIVO de la frontera LLM — el único test que gasta dinero.

Queda FUERA de `testpaths` (`pytest.ini` apunta a `tests/`), así que ni
`pytest` ni `pytest tests/` lo recogen. Se lanza a mano:

    docker compose exec -T backend python -m pytest tests_live/ -v

Para qué sirve: `tests/llm_stub.py` sustituye la llamada de red por un doble
que responde con una FORMA concreta. Si el proveedor real cambiara esa forma,
la suite entera seguiría en verde sobre una ficción. Esto es lo que lo
detecta — y quien juzga es el MISMO parser que usa producción, no una
aserción escrita a mano que pudiera divergir de él.

El CONTENIDO de un LLM no es comprobable y aquí no se comprueba: solo la
forma, que es lo único que el doble finge.
"""

import json

import pytest

from config import settings
from services.groq_service import RERANK_SYSTEM_PROMPT, GroqService
from services.translation_service import (
    TRANSLATION_SYSTEM_PROMPT,
    TranslationService,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def groq() -> GroqService:
    if not settings.GROQ_API_KEY:
        pytest.skip("GROQ_API_KEY no configurada")
    return GroqService()


async def test_el_rerank_devuelve_un_veredicto_por_oferta(groq):
    """Forma que finge `llm_stub._rerank_response`."""
    views = [
        {"title": "Content Editor", "company": "Acme", "tags": ["editing"]},
        {"title": "Backend Engineer", "company": "Acme", "tags": ["python"]},
    ]
    prompt = GroqService._rerank_prompt(
        "editing, localisation", "Bilingual editor.", views
    )

    raw = await groq.get_chat_response(
        user_message=prompt,
        system_prompt=RERANK_SYSTEM_PROMPT,
        model=settings.GROQ_RERANK_MODEL,
    )

    results = GroqService._parse_llm_response(raw, len(views))
    assert [r["index"] for r in results] == [0, 1]
    assert all(isinstance(r["score"], (int, float)) for r in results)


async def test_la_traduccion_devuelve_un_objeto_indexado(groq):
    """Forma que finge `llm_stub._translation_response`."""
    index_to_title = {"0": "Softwareentwickler", "1": "Chef de projet"}

    raw = await groq.get_chat_response(
        user_message=json.dumps(index_to_title, ensure_ascii=False),
        system_prompt=TRANSLATION_SYSTEM_PROMPT,
        model=settings.GROQ_RERANK_MODEL,
        temperature=0.05,
        max_tokens=2048,
    )

    out = TranslationService._parse_response(raw, index_to_title)
    assert set(out) == set(index_to_title.values())

"""D10 — unidad de la traducción de títulos del núcleo, sin base de datos."""

import asyncio
import json
import time

import httpx
import pytest

from jobhunt_core import title_translation as tt
from jobhunt_core.config import settings


@pytest.fixture(autouse=True)
def _sin_pausa():
    tt._pause["until"] = 0.0
    yield
    tt._pause["until"] = 0.0


def test_detector_local_decide_ingles_y_aleman():
    assert (
        tt.detect_language("Senior Software Engineer for the cloud platform team")
        == "en"
    )
    assert tt.detect_language("Softwareentwickler (m/w/d)") == "de"
    assert tt.detect_language("Développeur Full Stack") == "fr"


def test_solo_en_y_es_se_saltan_el_llm():
    assert not tt.needs_translation("en")
    assert not tt.needs_translation("es")
    # Desconocido NO es «no necesita»: va al LLM, como en el BFF.
    assert tt.needs_translation("")
    assert tt.needs_translation("de")


def test_parse_tolera_fences_y_rescata_json_truncado():
    titles = ["Lehrer", "Koch", "Bäcker"]
    assert tt.parse_response('```json\n{"0": "Teacher", "1": "Cook"}\n```', titles) == {
        "Lehrer": "Teacher",
        "Koch": "Cook",
    }
    truncado = '{"0": "Teacher", "1": "Cook", "2": "Bak'
    assert tt.parse_response(truncado, titles) == {"Lehrer": "Teacher", "Koch": "Cook"}
    assert tt.parse_response("no es json", titles) == {}
    assert tt.parse_response('{"0": "  "}', titles) == {}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_429_arma_la_pausa_y_respeta_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "CORE_GROQ_API_KEY", "k")

    async def go():
        async with _client(
            lambda r: httpx.Response(429, headers={"retry-after": "7200"})
        ) as c:
            with pytest.raises(tt.ProviderUnavailable):
                await tt._call_llm(c, ["Lehrer"])

    antes = time.monotonic()
    asyncio.run(go())
    assert tt._pause["until"] >= antes + 7200


def test_429_sin_cabecera_pausa_el_minimo(monkeypatch):
    monkeypatch.setattr(settings, "CORE_GROQ_API_KEY", "k")

    async def go():
        async with _client(lambda r: httpx.Response(429)) as c:
            with pytest.raises(tt.ProviderUnavailable):
                await tt._call_llm(c, ["Lehrer"])

    antes = time.monotonic()
    asyncio.run(go())
    assert tt._pause["until"] >= antes + tt.RATE_LIMIT_PAUSE_S


@pytest.mark.parametrize("status", [401, 404, 500, 503])
def test_otros_fallos_no_pausan_pero_paran(monkeypatch, status):
    monkeypatch.setattr(settings, "CORE_GROQ_API_KEY", "k")

    async def go():
        async with _client(lambda r: httpx.Response(status)) as c:
            with pytest.raises(tt.ProviderUnavailable):
                await tt._call_llm(c, ["Lehrer"])

    asyncio.run(go())
    assert tt._pause["until"] == 0.0


def test_timeout_es_proveedor_no_disponible(monkeypatch):
    monkeypatch.setattr(settings, "CORE_GROQ_API_KEY", "k")

    def handler(request):
        raise httpx.ReadTimeout("lento", request=request)

    async def go():
        async with _client(handler) as c:
            with pytest.raises(tt.ProviderUnavailable):
                await tt._call_llm(c, ["Lehrer"])

    asyncio.run(go())


def test_peticion_lleva_modelo_y_esfuerzo_de_razonamiento(monkeypatch):
    monkeypatch.setattr(settings, "CORE_GROQ_API_KEY", "k")
    vistos = []

    def handler(request):
        vistos.append((request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"0": "Teacher"}'}}]}
        )

    async def go():
        async with _client(handler) as c:
            return await tt._call_llm(c, ["Lehrer"])

    assert asyncio.run(go()) == '{"0": "Teacher"}'
    auth, body = vistos[0]
    assert auth == "Bearer k"
    assert body["model"] == settings.CORE_TITLE_TRANSLATION_MODEL == "qwen/qwen3.8-27b"
    assert body["reasoning_effort"] == "none"
    assert json.loads(body["messages"][1]["content"]) == {"0": "Lehrer"}


def test_sin_clave_no_toca_la_base(monkeypatch):
    """Control negativo del interruptor: sin clave ni siquiera abre sesión."""
    monkeypatch.setattr(settings, "CORE_GROQ_API_KEY", "")

    def factory():
        raise AssertionError("sin clave no debe abrir sesión")

    out = asyncio.run(tt.run_pass(factory))
    assert out == {"discovered": 0, "detected": 0, "translated": 0, "missed": 0}


def test_beat_y_ruta_de_la_tarea():
    from jobhunt_core.celery_app import celery_app

    assert celery_app.conf.task_routes["jobhunt.maintenance.translate_titles"] == {
        "queue": "core.default"
    }
    # El beat de la suite corre sin clave: la entrada no existe.
    assert ("maintenance-translate-titles" in celery_app.conf.beat_schedule) == bool(
        settings.CORE_GROQ_API_KEY
    )

"""La detección de idioma NO vive en el camino de respuesta.

Punto 5. El router deducía el idioma de cada oferta servida para el indicador
de la UI: 50,1 ms por título, ~90 s por petición de 1.800. Memoizarlo quitó el
coste repetido pero no la PRIMERA carga, que volvía a pagarlo entera tras cada
arranque o expulsión de caché.

Ahora se resuelve UNA vez por título distinto, en una tarea de fondo, y se
persiste. Lo que estas pruebas fijan:

- servir NO detecta, ni siquiera con títulos nunca vistos (el detector que se
  instala aquí LANZA: si la detección vuelve al router, esto explota en vez de
  ponerse lento otra vez);
- los tres estados son distinguibles y `''` —resuelto como desconocido— no se
  reintenta nunca;
- encolar es idempotente: la segunda carga de la misma página no inserta nada.
"""

import uuid
from datetime import datetime, timezone

import pytest

from tests.conftest import TestSessionLocal
import sqlalchemy as sa

from models.title_language import JobTitleLanguage
from routers.match import _build_results_response, _to_match_response
from services import language_store
from services.matching.core_client import CoreMatchView, _job_view
from services.translation_service import TranslationService


@pytest.fixture
def detector_que_estalla(monkeypatch):
    def estallar(cls, text):
        raise AssertionError(f"el camino de respuesta detectó el idioma de {text!r}")

    monkeypatch.setattr(TranslationService, "_detect_language", classmethod(estallar))


def _match():
    return CoreMatchView(
        id=uuid.uuid4(),
        job_hash=uuid.uuid4().hex,
        score_final=71.0,
        score_embedding=0.7,
        score_salary=0.0,
        score_location=0.0,
        score_recency=0.0,
        score_llm=0.0,
        explanation=None,
        matching_skills=[],
        missing_skills=[],
        feedback=None,
        application_status="detected",
        urgency_score=0.0,
        draft_letter=None,
        created_at=datetime.now(timezone.utc),
    )


def _item(title, language=None):
    vacancy = {
        "id": str(uuid.uuid4()),
        "title": title,
        "company": "Acme",
        "primary_listing": {
            "source": "core",
            "external_id": "x",
            "url": "https://example.com/j/1",
        },
        "listings": [],
    }
    if language is not None:
        vacancy["language"] = language
    return {"match": _match(), "job": _job_view(vacancy, "core"), "school": None}


# --- Clave canónica ---------------------------------------------------------


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("  Softwareentwickler  ", "Softwareentwickler"),
        ("", ""),
        ("   ", ""),
        (None, ""),
        (7, ""),
    ],
)
def test_normalise(entrada, esperado):
    assert language_store.normalise(entrada) == esperado


def test_normalise_trunca_al_limite_del_indice():
    largo = "x" * 900
    assert len(language_store.normalise(largo)) == 500


# --- Estados del almacén ----------------------------------------------------


async def test_pendiente_no_se_sirve_como_resuelto(db_session):
    """Pendiente y nunca-visto deben ser el MISMO estado para el consumidor."""
    await language_store.record_pending(
        session_factory=TestSessionLocal, titles=["Título pendiente"]
    )
    assert (await language_store.lookup(db_session, ["Título pendiente"]))[0] == {}


async def test_desconocido_resuelto_se_sirve_y_no_se_reintenta(db_session):
    """`''` significa «ya se intentó»: sale del lookup y sale de los pendientes."""
    await language_store.record_pending(
        session_factory=TestSessionLocal, titles=["Título raro"]
    )
    await language_store.store_resolved(db_session, {"Título raro": ""})

    assert (await language_store.lookup(db_session, ["Título raro"]))[0] == {
        "Título raro": ""
    }
    assert "Título raro" not in await language_store.pending_titles(db_session, 100)


async def test_resuelto_se_sirve(db_session):
    await language_store.record_pending(
        session_factory=TestSessionLocal, titles=["Softwareentwickler"]
    )
    await language_store.store_resolved(db_session, {"Softwareentwickler": "de"})
    assert (await language_store.lookup(db_session, ["Softwareentwickler"]))[0] == {
        "Softwareentwickler": "de"
    }


async def test_encolar_es_idempotente(db_session):
    titulos = ["Uno", "Dos", "Uno"]
    assert (
        await language_store.record_pending(
            session_factory=TestSessionLocal, titles=titulos
        )
        == 2
    )
    assert (
        await language_store.record_pending(
            session_factory=TestSessionLocal, titles=titulos
        )
        == 0
    )


async def test_encolar_ignora_titulos_vacios(db_session):
    assert (
        await language_store.record_pending(
            session_factory=TestSessionLocal, titles=["", "   ", None]
        )
        == 0
    )


# --- El camino de respuesta -------------------------------------------------


def test_servir_no_detecta_aunque_el_titulo_sea_desconocido(detector_que_estalla):
    resp = _to_match_response(
        _item("Título jamás visto"), translations={}, languages={}
    )
    assert resp.job_language is None, "sin dato, el indicador se omite; NO se deduce"


def test_servir_usa_el_idioma_derivado(detector_que_estalla):
    item = _item("Softwareentwickler")
    resp = _to_match_response(
        item, translations={}, languages={"Softwareentwickler": "de"}
    )
    assert resp.job_language == "de"


def test_el_idioma_del_core_gana_al_derivado(detector_que_estalla):
    """La canónica es el dato de la fuente; el derivado es nuestra deducción."""
    item = _item("Softwareentwickler", language="fr")
    resp = _to_match_response(
        item, translations={}, languages={"Softwareentwickler": "de"}
    )
    assert resp.job_language == "fr"


def test_desconocido_resuelto_no_inventa_indicador(detector_que_estalla):
    resp = _to_match_response(_item("??"), translations={}, languages={"??": ""})
    assert resp.job_language is None


async def test_construir_la_pagina_no_detecta_y_encola_lo_que_falta(
    db_session, detector_que_estalla, monkeypatch
):
    """La prueba de frontera: una página entera de títulos nuevos se sirve sin
    una sola detección, y deja encolado exactamente lo que no sabía."""
    import database

    monkeypatch.setattr(database, "async_session", TestSessionLocal)
    titulos = [f"Título nuevo {i}" for i in range(5)]
    resultados = [_item(t) for t in titulos]

    resp = await _build_results_response(resultados, total=5, weights={}, db=db_session)

    assert [d.job_language for d in resp.data] == [None] * 5
    pendientes = set(await language_store.pending_titles(db_session, 100))
    assert pendientes >= set(titulos)


async def test_la_segunda_carga_no_vuelve_a_encolar(db_session, detector_que_estalla):
    resultados = [_item("Título repetido")]
    await _build_results_response(resultados, total=1, weights={}, db=db_session)
    antes = await db_session.scalar(
        sa.select(sa.func.count()).select_from(JobTitleLanguage)
    )
    await _build_results_response(resultados, total=1, weights={}, db=db_session)
    despues = await db_session.scalar(
        sa.select(sa.func.count()).select_from(JobTitleLanguage)
    )
    assert antes == despues


# --- La tarea de fondo ------------------------------------------------------


async def test_la_tarea_resuelve_los_pendientes_y_los_saca_de_la_cola(
    db_session, monkeypatch
):
    """Quien detecta es la tarea, no la petición — y deja la cola vacía."""
    import database

    monkeypatch.setattr(database, "async_session", TestSessionLocal)
    import contextlib

    from tasks import language_tasks

    @contextlib.asynccontextmanager
    async def sesion_de_prueba():
        yield db_session

    monkeypatch.setattr("database.task_session", sesion_de_prueba)
    monkeypatch.setattr(
        TranslationService,
        "_detect_language",
        classmethod(lambda cls, text: "de" if "entwickler" in text else ""),
    )

    await language_store.record_pending(
        session_factory=TestSessionLocal,
        titles=["Softwareentwickler", "Zzz indecidible"],
    )

    resultado = await language_tasks._resolve(batch=100)

    assert resultado["resolved"] == 2
    assert resultado["unknown"] == 1
    assert resultado["pending_left"] == 0
    resueltos, _vistos = await language_store.lookup(
        db_session, ["Softwareentwickler", "Zzz indecidible"]
    )
    assert resueltos == {"Softwareentwickler": "de", "Zzz indecidible": ""}


async def test_la_tarea_respeta_el_tamano_del_lote(db_session, monkeypatch):
    """El NAS tiene dos núcleos: la tarea no puede tragarse la cola entera."""
    import contextlib

    from tasks import language_tasks

    @contextlib.asynccontextmanager
    async def sesion_de_prueba():
        yield db_session

    monkeypatch.setattr("database.task_session", sesion_de_prueba)
    monkeypatch.setattr(
        TranslationService, "_detect_language", classmethod(lambda cls, text: "en")
    )

    await language_store.record_pending(
        session_factory=TestSessionLocal, titles=[f"T{i}" for i in range(10)]
    )
    resultado = await language_tasks._resolve(batch=4)

    assert resultado["resolved"] == 4
    assert resultado["pending_left"] == 6


async def test_sin_pendientes_la_tarea_no_toca_el_detector(db_session, monkeypatch):
    import contextlib

    from tasks import language_tasks

    @contextlib.asynccontextmanager
    async def sesion_de_prueba():
        yield db_session

    monkeypatch.setattr("database.task_session", sesion_de_prueba)
    monkeypatch.setattr(
        TranslationService,
        "_detect_language",
        classmethod(lambda cls, text: (_ for _ in ()).throw(AssertionError("detectó"))),
    )
    assert await language_tasks._resolve(batch=100) == {
        "resolved": 0,
        "pending_left": 0,
    }

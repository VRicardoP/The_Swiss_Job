"""El idioma debe VIAJAR desde la canónica, no volver a deducirse al servir.

Contexto (punto 5, 2026-09-22). `_to_match_response` detecta el idioma de cada
oferta servida cuando el campo viene vacío: 50,1 ms por llamada, 1.800 ofertas
por petición. La primera hipótesis fue que el hueco lo causaban los
normalizadores nativos, que no exponían `language`. Una revalidación externa
demostró que ESO NO BASTA: el campo tampoco lo declaraba `VacancyDTO`, ni lo
transmitía `_vacancy_dtos`, ni lo asignaba `_job_view`. Un dato perfecto en la
canónica se perdía igual en el camino.

Estas pruebas fijan la frontera completa del lado del BFF:

- con idioma válido, el detector NO se invoca (se instala uno que LANZA: si
  alguien deshace el transporte, la prueba explota en vez de ponerse lenta);
- sin idioma, tampoco se detecta: eso vive ahora en la tarea de fondo
  (`tests/test_language_store.py`);
- con una forma inválida, se trata como ausente y no tumba la página.

El transporte cubre sólo el 3,1 % del feed medido; el resto lo resuelve el
almacén derivado. Ninguno de los dos vuelve a detectar al servir.
"""

import uuid
from datetime import datetime, timezone

import pytest

from routers.match import _to_match_response
from services.matching.core_client import CoreMatchView, _job_view
from services.translation_service import TranslationService


def _match():
    return CoreMatchView(
        id=uuid.uuid4(), job_hash="h" * 32, score_final=71.0,
        score_embedding=0.7, score_salary=0.0, score_location=0.0,
        score_recency=0.0, score_llm=0.0, explanation=None,
        matching_skills=[], missing_skills=[], feedback=None,
        application_status="detected", urgency_score=0.0, draft_letter=None,
        created_at=datetime.now(timezone.utc),
    )


def _vacancy(**extra):
    base = {
        "id": str(uuid.uuid4()),
        "title": "Softwareentwickler (m/w/d)",
        "company": "Acme",
        "primary_listing": {"source": "core", "external_id": "x",
                            "url": "https://example.com/j/1"},
        "listings": [],
    }
    base.update(extra)
    return base


@pytest.fixture
def detector_que_estalla(monkeypatch):
    """Detectar es exactamente lo que NO debe ocurrir con idioma conocido."""
    def estallar(cls, text):
        raise AssertionError(f"se detectó el idioma de {text!r} teniéndolo ya")

    monkeypatch.setattr(TranslationService, "_detect_language", classmethod(estallar))


# --- Mapeo del BFF: el campo llega desde el VacancyDTO del core ------------

def test_job_view_transporta_el_idioma_del_core():
    assert _job_view(_vacancy(language="de"), "core").language == "de"


def test_job_view_normaliza_espacios_y_caja():
    assert _job_view(_vacancy(language="  DE  "), "core").language == "de"


@pytest.mark.parametrize("valor", [None, "", "   ", 7, ["de"], {"code": "de"}])
def test_job_view_trata_lo_inutilizable_como_ausente(valor):
    """Un 200 con una forma inesperada no puede romper la página: el idioma es
    un indicador, no la identidad de la oferta."""
    assert _job_view(_vacancy(language=valor), "core").language is None


def test_job_view_sin_campo_es_ausente():
    assert _job_view(_vacancy(), "core").language is None


# --- Frontera servida: lo que el router hace con ese campo -----------------

def test_con_idioma_conocido_el_router_no_detecta(detector_que_estalla):
    item = {"match": _match(), "job": _job_view(_vacancy(language="fr"), "core"),
            "school": None}
    resp = _to_match_response(item, translations={})
    assert resp.job_language == "fr"


def test_sin_idioma_el_router_no_deduce_nada(detector_que_estalla):
    """Comportamiento NUEVO y explícito (2026-09-23): sin dato no hay
    indicador, y sobre todo no hay detección.

    Esta prueba afirmaba lo contrario —que el router deducía— y era cierto
    hasta que la deducción se movió a `tasks.language_tasks`. Se cambia a
    conciencia, no por comodidad: deducir aquí costaba 50,1 ms por oferta
    servida. Que el título quede sin indicador durante una carga es el precio
    declarado; lo resuelve la tarea de fondo para la siguiente.
    Ver `tests/test_language_store.py`."""
    item = {"match": _match(), "job": _job_view(_vacancy(), "core"), "school": None}
    resp = _to_match_response(item, translations={})
    assert resp.job_language is None


def test_un_idioma_invalido_no_impide_servir(detector_que_estalla):
    """Inválido se degrada a ausente ANTES del router; con título vacío no hay
    nada que detectar, así que la oferta se sirve sin indicador."""
    item = {"match": _match(),
            "job": _job_view(_vacancy(title="", language=7), "core"),
            "school": None}
    resp = _to_match_response(item, translations={})
    assert resp.job_language is None

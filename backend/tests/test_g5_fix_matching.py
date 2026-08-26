"""G5 — familia del MATCHING (`services/match_service.py`).

- **P3-7**: un veredicto de «poor fit» sin `reason` BORRABA la explicación con
  una cadena vacía. Superficie NUEVA de `0a6327e`: antes, el `score > 0` se
  saltaba esos veredictos enteros. `explanation` pasaba de conservar el texto
  bueno de otra corrida a guardarse como `""`, que es lo que el usuario ve
  —en blanco— en `MatchCard`. Y como ese veredicto SÍ es real, se **cachea**
  (`GROQ_CACHE_TTL_DAYS=7`) y se re-aplica en cada corrida durante la semana,
  incluso con el LLM caído.
- **P3-8**: el mensaje de `0a6327e` reclamaba cerrar además una pérdida de datos
  por poda. No la cerraba: para un poor fit el `score_final` es EXACTAMENTE el
  de la etapa 2 (que ya calcula con `llm_score=0.0`), así que el umbral ve lo
  mismo con y sin el fix. Verificado: 49.5 y 49.5. Y la poda tampoco es la vía
  de pérdida que se le atribuía — `_save_results` solo borra huérfanas SIN
  engagement. Aquí se fija por test lo que el código hace de verdad.
"""

from types import SimpleNamespace

import pytest

from services.job_matcher import JobMatcher
from services.match_service import MatchService

_WEIGHTS = {
    "embedding": 0.35,
    "salary": 0.15,
    "location": 0.10,
    "recency": 0.15,
    "llm": 0.15,
    "language": 0.10,
}


def _job():
    return SimpleNamespace(
        title="Dev",
        company="C",
        description="d",
        tags=["python"],
        location="Zurich",
        remote=False,
        language="en",
        contract_type=None,
        source="test_source",
        category="A",
        hash="h" * 32,
    )


def _scored(explanation=None) -> dict:
    return {
        "job": _job(),
        "score_embedding": 0.7,
        "score_salary": 0.5,
        "score_location": 0.5,
        "score_recency": 0.5,
        "score_language": 0.5,
        "score_llm": 0.0,
        "score_final": 49.5,
        "explanation": explanation,
        "matching_skills": ["rule"],
        "missing_skills": ["rule_miss"],
    }


class TestP37LaExplicacionNoSeBorraConUnaCadenaVacia:
    def test_un_veredicto_sin_reason_conserva_el_texto_anterior(self):
        svc = MatchService(db=None)
        r = _scored(explanation="Encaja bien con tu perfil de backend.")

        svc._apply_llm_result(
            r, {"index": 0, "score": 0}, SimpleNamespace(skills=[]), _WEIGHTS
        )

        assert r["explanation"] == "Encaja bien con tu perfil de backend.", (
            "un poor fit sin `reason` borra la explicación con '' — y ese "
            "vacío se cachea 7 días y se re-aplica en cada corrida"
        )
        # El veredicto SÍ se aplica: el score baja, que es lo correcto.
        assert r["score_llm"] == 0.0

    def test_reason_vacia_explicita_tampoco_borra(self):
        svc = MatchService(db=None)
        r = _scored(explanation="texto bueno")

        svc._apply_llm_result(
            r,
            {"index": 0, "score": 0, "reason": ""},
            SimpleNamespace(skills=[]),
            _WEIGHTS,
        )

        assert r["explanation"] == "texto bueno"

    def test_un_veredicto_CON_reason_si_actualiza(self):
        """Guardarraíl: el camino normal no se rompe."""
        svc = MatchService(db=None)
        r = _scored(explanation="texto viejo")

        svc._apply_llm_result(
            r,
            {"index": 0, "score": 80, "reason": "texto nuevo"},
            SimpleNamespace(skills=[]),
            _WEIGHTS,
        )

        assert r["explanation"] == "texto nuevo"

    def test_la_columna_no_se_pisa_con_None_al_persistir(self):
        """`_score_values` escribía `explanation = None` cuando la clave no
        estaba: borraba exactamente lo mismo que el `""`."""
        r = _scored(explanation=None)
        r["llm_verdict"] = True
        r["score_llm"] = 0.0

        values = MatchService._score_values(r)

        assert "score_llm" in values
        assert "explanation" not in values, (
            "sin explicación en esta corrida no se puede tocar la columna"
        )

    def test_con_explicacion_si_viaja_a_la_columna(self):
        r = _scored(explanation="razón real")
        r["llm_verdict"] = True
        r["score_llm"] = 0.8

        values = MatchService._score_values(r)

        assert values["explanation"] == "razón real"


class TestP38ElPoorFitNoMueveElUmbral:
    def test_score_final_de_un_poor_fit_es_el_de_la_etapa_2(self):
        """La aritmética que el mensaje de `0a6327e` presentaba como pérdida.

        La etapa 2 ya calcula con `llm_score=0.0`; aplicar un veredicto de
        score 0 recalcula con las MISMAS entradas. El umbral ve lo mismo.
        """
        matcher = JobMatcher()
        kw = dict(
            embedding_score=0.7,
            salary_score=0.5,
            location_score=0.5,
            recency_score=0.5,
            language_score=0.5,
            weights=_WEIGHTS,
        )
        etapa2 = matcher.compute_final_score(llm_score=0.0, **kw)

        svc = MatchService(db=None)
        r = _scored()
        svc._apply_llm_result(
            r,
            {"index": 0, "score": 0, "reason": "no encaja"},
            SimpleNamespace(skills=[]),
            _WEIGHTS,
        )

        assert r["score_final"] == pytest.approx(etapa2), (
            "el fix no cambia el score_final de un poor fit: la mitad de "
            "«borrado» que su mensaje reclamaba no existía"
        )

    def test_un_veredicto_bueno_SI_mueve_el_score(self):
        """Guardarraíl: el aporte del LLM sigue contando cuando es positivo."""
        svc = MatchService(db=None)
        r = _scored()
        antes = r["score_final"]

        svc._apply_llm_result(
            r,
            {"index": 0, "score": 85, "reason": "encaja"},
            SimpleNamespace(skills=[]),
            _WEIGHTS,
        )

        assert r["score_final"] > antes

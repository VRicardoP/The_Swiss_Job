"""G6/P3-1 y G6/P3-5 — dos guardas de `match_service` que no cubrían su caso.

P3-1: la guarda de `82bac59` era de TRUTHINESS. `""`, `None` y la clave ausente
quedaban protegidos, pero `"   "`, `"\\n\\t"` y `"\\xa0"` son truthy en Python y se
escribían en `explanation`. En `MatchCard.jsx` el render es
`{match.explanation && (…)}`: el `""` es falsy en JS y OCULTA el bloque; `"   "`
es truthy y pinta el recuadro de info con el icono y ningún texto — peor que el
`""` que el fix venía a quitar. Y se cachea 7 días igual.

P3-5: `score_llm`/`explanation` solo viajan si ESTA corrida tuvo veredicto del
LLM (guarda de G3/P3-9), pero `matching_skills`/`missing_skills` se escribían
SIEMPRE. En modo avalancha (`> MATCH_LLM_RERANK_MAX` ⇒ solo el top
`MATCH_LLM_RERANK_TOP` se re-rankea) una oferta fuera del top pisaba con `[]` las
skills que el LLM había enriquecido el día anterior.
"""

import types

import pytest

from services.job_matcher import JobMatcher
from services.match_service import LLM_VERDICT_KEY, MatchService

_PESOS = {
    "embedding": 0.35,
    "salary": 0.15,
    "location": 0.10,
    "recency": 0.15,
    "llm": 0.15,
    "language": 0.10,
}


class _Perfil:
    skills: list[str] = []
    preferred_categories = None


@pytest.fixture
def servicio():
    svc = MatchService.__new__(MatchService)
    svc.matcher = JobMatcher()
    svc._category_multiplier_for = lambda job, profile: 1.0
    return svc


def _result_con_explicacion_previa() -> dict:
    return {
        "job": types.SimpleNamespace(category=None, title="x"),
        "score_embedding": 0.7,
        "score_salary": 0.5,
        "score_location": 0.5,
        "score_recency": 0.5,
        "score_language": 0.5,
        "score_llm": 0.0,
        "score_final": 49.5,
        "matching_skills": [],
        "missing_skills": [],
        "urgency_score": 0.0,
        "explanation": "TEXTO BUENO DE OTRA CORRIDA",
    }


class TestUnaReasonEnBlancoNoBorraLaExplicacion:
    @pytest.mark.parametrize(
        "reason",
        ["   ", "\n\t", "\xa0", "", None],
        ids=["espacios", "salto+tab", "nbsp", "vacia", "None"],
    )
    def test_el_texto_bueno_de_otra_corrida_sobrevive(self, servicio, reason):
        r = _result_con_explicacion_previa()
        llm = {"index": 0, "score": 0}
        if reason is not None:
            llm["reason"] = reason
        servicio._apply_llm_result(r, llm, _Perfil(), _PESOS)
        assert r["explanation"] == "TEXTO BUENO DE OTRA CORRIDA"
        assert (
            MatchService._score_values(r)["explanation"]
            == "TEXTO BUENO DE OTRA CORRIDA"
        )

    @pytest.mark.parametrize(
        "reason", ["   ", "\n\t", "\xa0"], ids=["espacios", "salto+tab", "nbsp"]
    )
    def test_sin_texto_previo_la_columna_ni_se_toca(self, servicio, reason):
        """El caso de la fila nueva: en blanco no es una explicación."""
        r = _result_con_explicacion_previa()
        del r["explanation"]
        servicio._apply_llm_result(
            r, {"index": 0, "score": 0, "reason": reason}, _Perfil(), _PESOS
        )
        assert "explanation" not in MatchService._score_values(r)

    def test_una_reason_con_contenido_si_se_escribe(self, servicio):
        r = _result_con_explicacion_previa()
        servicio._apply_llm_result(
            r,
            {"index": 0, "score": 40, "reason": "encaja en backend"},
            _Perfil(),
            _PESOS,
        )
        assert MatchService._score_values(r)["explanation"] == "encaja en backend"

    def test_la_clave_ausente_sigue_protegida(self, servicio):
        r = _result_con_explicacion_previa()
        servicio._apply_llm_result(r, {"index": 0, "score": 0}, _Perfil(), _PESOS)
        assert r["explanation"] == "TEXTO BUENO DE OTRA CORRIDA"


class TestLasSkillsVaciasNoPisanLasDelLLM:
    def test_sin_veredicto_del_LLM_las_listas_vacias_no_viajan(self):
        """Modo avalancha: la cola no re-rankeada no puede borrar nada."""
        r = _result_con_explicacion_previa()
        values = MatchService._score_values(r)
        assert "matching_skills" not in values
        assert "missing_skills" not in values
        # La asimetría era justo esta: score_llm/explanation ya estaban fuera.
        assert "score_llm" not in values
        assert "explanation" not in values

    def test_las_listas_con_contenido_si_viajan(self, servicio):
        r = _result_con_explicacion_previa()
        servicio._apply_llm_result(
            r,
            {
                "index": 0,
                "score": 70,
                "reason": "ok",
                "matching_skills": ["python", "sql"],
                "missing_skills": ["kubernetes"],
            },
            _Perfil(),
            _PESOS,
        )
        values = MatchService._score_values(r)
        assert values["matching_skills"] == ["python", "sql"]
        assert values["missing_skills"] == ["kubernetes"]
        assert r[LLM_VERDICT_KEY] is True

    def test_el_INSERT_nuevo_sigue_teniendo_las_columnas_NOT_NULL(self):
        """Omitirlas del dict no puede romper el `pg_insert`: hay default."""
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from models.match_result import MatchResult

        r = _result_con_explicacion_previa()
        stmt = pg_insert(MatchResult).values(
            user_id="11111111-1111-1111-1111-111111111111",
            job_hash="a" * 32,
            **MatchService._score_values(r),
        )
        compilada = str(stmt.compile(dialect=postgresql.dialect()))
        assert "matching_skills" in compilada
        assert "missing_skills" in compilada

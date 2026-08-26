"""G8/P2-3, P2-4, P3-4 y P3-5 — la tarjeta que se contradice a sí misma y el
bloque LLM que dejó de ser atómico.

P2-3: la unión de G7 pintaba la MISMA skill en verde «✓» y tachada «te falta».
`filter_missing_skills` contrasta contra `profile.skills`, nunca contra
`matching_skills`, y el prompt le enseña al LLM los MISMOS tags que la regla
acaba de marcar «missing»: 18 de las 71 filas con veredicto LLM en producción.

P2-4: con la unión, las skills se escriben SIEMPRE, pero `score_llm` y
`explanation` siguieron bajo la guarda de veredicto. En avalancha permanente
(los dos perfiles de producción están por encima de `MATCH_LLM_RERANK_MAX`)
solo 50 filas por corrida pasan por el LLM: las demás quedaban con las listas
de regla puras y la prosa del LLM de otro día — 53 de 71 filas con
`matching_skills = []` y las 71 explicaciones intactas.

P3-4: la CACHÉ de Groq era un segundo borde por el que la respuesta del LLM
entraba sin sanear, y `_unir_skills` no era total en su ARGUMENTO pese a su
docstring.

P3-5: `matching_skills` no se contrastaba NUNCA con el perfil: una skill
alucinada se persistía como «tuya».
"""

import types

import pytest

from services import match_service as ms
from services.job_matcher import JobMatcher
from services.match_service import LLM_VERDICT_KEY, MatchService, _unir_skills

_PESOS = {
    "embedding": 0.35,
    "salary": 0.15,
    "location": 0.10,
    "recency": 0.15,
    "llm": 0.15,
    "language": 0.10,
}


class _Perfil:
    def __init__(self, skills):
        self.skills = skills
        self.preferred_categories = None


@pytest.fixture
def servicio():
    svc = MatchService.__new__(MatchService)
    svc.matcher = JobMatcher()
    svc._category_multiplier_for = lambda job, profile: 1.0
    return svc


def _result(**extra) -> dict:
    base = {
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
    }
    base.update(extra)
    return base


class TestLaTarjetaNoPuedeContradecirse:
    """P2-3 — ninguna skill puede salir en verde y tachada a la vez."""

    def test_la_repro_exacta_de_la_auditoria(self, servicio):
        r = _result(matching_skills=["python"], missing_skills=["kubernetes"])
        servicio._apply_llm_result(
            r,
            {
                "score": 80,
                "reason": "encaja",
                "matching_skills": ["Kubernetes", "Rust", "Fluent German"],
                "missing_skills": ["Rust"],
            },
            _Perfil(["python"]),
            _PESOS,
        )
        en_verde = {s.lower() for s in r["matching_skills"]}
        tachadas = {s.lower() for s in r["missing_skills"]}
        assert not (en_verde & tachadas), (r["matching_skills"], r["missing_skills"])

    def test_el_invariante_aguanta_aunque_el_llm_repita_la_skill(self, servicio):
        """La guarda es local a donde se cierran las dos listas: sigue valiendo
        aunque el LLM devuelva la MISMA skill en las dos y el perfil la
        respalde."""
        r = _result(matching_skills=["python"], missing_skills=[])
        servicio._apply_llm_result(
            r,
            {
                "score": 80,
                "reason": "encaja",
                "matching_skills": ["Python"],
                "missing_skills": ["python"],
            },
            _Perfil(["python"]),
            _PESOS,
        )
        en_verde = {s.lower() for s in r["matching_skills"]}
        assert not (en_verde & {s.lower() for s in r["missing_skills"]})


class TestUnaSkillAlucinadaNoSeFirmaComoTuya:
    """P3-5 — `matching_skills` no se contrastaba con el perfil."""

    def test_el_idioma_que_el_usuario_no_declara_no_entra(self, servicio):
        r = _result(matching_skills=["python"], missing_skills=[])
        servicio._apply_llm_result(
            r,
            {
                "score": 80,
                "reason": "encaja",
                "matching_skills": ["Rust", "Fluent German"],
                "missing_skills": [],
            },
            _Perfil(["python"]),
            _PESOS,
        )
        assert r["matching_skills"] == ["python"]

    def test_el_sinonimo_del_perfil_SI_entra(self, servicio):
        """El respaldo se decide con la misma maquinaria que ya sabe qué falta
        de verdad: «copywriting» en el perfil respalda «Content Writer»."""
        r = _result(matching_skills=[], missing_skills=[])
        servicio._apply_llm_result(
            r,
            {
                "score": 80,
                "reason": "encaja",
                "matching_skills": ["Content Writer"],
                "missing_skills": [],
            },
            _Perfil(["copywriting"]),
            _PESOS,
        )
        assert r["matching_skills"] == ["Content Writer"]

    def test_el_dedup_conserva_la_variante_con_mayusculas(self):
        """Menor de P3-5: `base` sale de `_compute_skill_overlap`, que
        lowercasea, y la tarjeta pasaba de «Python / English» a
        «python / english»."""
        assert _unir_skills(["python", "english"], ["Python", "English", "Excel"]) == [
            "Python",
            "English",
            "Excel",
        ]


class TestElBloqueDelLlmEsAtomico:
    """P2-4 — la cola conservaba la prosa y perdía las listas."""

    def test_la_cola_no_conserva_la_explicacion_de_otro_dia(self):
        r = _result(
            score_llm=0.85,
            explanation="Encaja por su Localization y Technical Writing",
            matching_skills=[],
            missing_skills=["python"],
        )
        r[ms.LLM_SKIPPED_KEY] = True
        valores = MatchService._score_values(r)
        assert valores["score_llm"] == 0.0
        assert valores["explanation"] is None

    def test_con_veredicto_se_escribe_lo_de_hoy(self):
        r = _result(score_llm=0.85, explanation="encaja de verdad")
        r[LLM_VERDICT_KEY] = True
        valores = MatchService._score_values(r)
        assert valores["score_llm"] == 0.85
        assert valores["explanation"] == "encaja de verdad"

    def test_un_lote_DEGRADADO_no_borra_nada(self):
        """El LLM caído no lleva ninguna de las dos marcas: sus ceros no son un
        veredicto y borrarían una explicación buena."""
        r = _result(score_llm=0.85, explanation="encaja de verdad")
        valores = MatchService._score_values(r)
        assert "score_llm" not in valores
        assert "explanation" not in valores

    @pytest.mark.asyncio
    async def test_maybe_rerank_marca_la_cola_y_no_la_cabeza(
        self, servicio, monkeypatch
    ):
        from config import settings

        monkeypatch.setattr(settings, "MATCH_LLM_RERANK_MAX", 2, raising=False)
        monkeypatch.setattr(settings, "MATCH_LLM_RERANK_TOP", 1, raising=False)
        servicio.groq = types.SimpleNamespace(is_available=True)
        servicio.gemini = None

        async def _sin_llm(profile, scored_results, weights):
            return scored_results

        servicio._stage3_llm_rerank = _sin_llm
        cualificadas = [
            _result(score_final=90),
            _result(score_final=80),
            _result(score_final=70),
        ]
        salida = await servicio._maybe_rerank(cualificadas, _Perfil(["python"]), _PESOS)
        marcadas = [r for r in salida if r.get(ms.LLM_SKIPPED_KEY)]
        assert len(marcadas) == 2
        assert not cualificadas[0].get(ms.LLM_SKIPPED_KEY)


class TestLaCacheDeGroqYaNoEsUnBordeSinSanear:
    """P3-4."""

    def test_lo_leido_de_la_cache_se_sanea(self):
        from services.groq_service import _sanear_cacheados

        crudo = [
            {
                "index": 0,
                "score": 80,
                "matching_skills": ["python", None, 7, "  "],
                "missing_skills": {"a": 1},
                "reason": ["no", "soy", "texto"],
            }
        ]
        assert _sanear_cacheados(crudo) == [
            {
                "index": 0,
                "score": 80,
                "matching_skills": ["python"],
                "missing_skills": [],
                "reason": "",
            }
        ]

    def test_la_clave_de_cache_lleva_version_de_esquema(self):
        from services.groq_service import GroqService

        assert GroqService._cache_key("x").startswith("groq:rerank:v")

    @pytest.mark.parametrize("extra", ["Python, Excel", {"a": 1}, 7, None])
    def test_unir_skills_es_total_en_su_argumento(self, extra):
        """El `int` reproducía EXACTAMENTE el daño de G7/P3-3: `TypeError` →
        `except` POR USUARIO de `tasks/matching_tasks.py` → se pierde el
        matching completo del perfil."""
        assert _unir_skills(["python"], extra) == ["python"]

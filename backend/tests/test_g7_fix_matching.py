"""G7/P2-3, P3-3 y P3-4 — la guarda buena de `explanation`, copiada a dos
columnas donde la premisa es falsa, y el payload del LLM sin sanear.

P2-3: G6/P3-5 aplicó a `matching_skills`/`missing_skills` la guarda «una lista
vacía no aporta nada que merezca borrar lo que ya había». Es cierto para
`explanation` —que solo produce el LLM y solo a veces— y falso para estas dos:
`_stage2_multifactor_score` las recalcula SIEMPRE y `_compute_skill_overlap` las
deriva del perfil ACTUAL. Una lista vacía aquí no es «hoy no hubo dato», es «el
dato correcto de hoy es ninguno». Con la guarda, `[]` no podía escribirse jamás:
el usuario adquiere la última skill que le faltaba, `missing_skills` pasa a `[]`,
no se escribe, y la tarjeta sigue diciendo «te falta k8s» PARA SIEMPRE — en la
oferta de encaje perfecto, y sin ninguna corrida futura capaz de pisarlo.

P3-3: `reason` llegaba del LLM sin comprobación de tipo y el `.strip()` de
`_apply_llm_result` subía como AttributeError hasta el `except` POR USUARIO de
`tasks/matching_tasks.py`: se perdía el matching entero de ese perfil.

P3-4: `matching_skills` se persistía cruda (`missing_skills` sí pasaba por
`filter_missing_skills`). Un `[None]` o un `[{...}]` en la columna hace que
`schemas/match.py` —`list[str]` estricto— lance `ValidationError`: 500 en
`/api/v1/match/results` ENTERO, no una tarjeta rota.
"""

import json
import types

import pytest

from services.groq_service import GroqService
from services.job_matcher import JobMatcher
from services.match_service import MatchService

_PESOS = {
    "embedding": 0.35,
    "salary": 0.15,
    "location": 0.10,
    "recency": 0.15,
    "llm": 0.15,
    "language": 0.10,
}


class _Perfil:
    preferred_categories = None

    def __init__(self, skills: list[str] | None = None):
        # G8/P3-5: `matching_skills` ya no se persiste sin contrastar con el
        # perfil, así que los tests que comprueban la UNIÓN tienen que darle al
        # perfil la skill que el LLM va a reconocer. Antes daba igual: entraba
        # cualquier cosa que dijera el LLM, incluida una alucinación.
        self.skills = ["python"] if skills is None else skills


@pytest.fixture
def servicio():
    svc = MatchService.__new__(MatchService)
    svc.matcher = JobMatcher()
    svc._category_multiplier_for = lambda job, profile: 1.0
    return svc


def _json(valor: object) -> str:
    """Serializa un valor tal cual lo devolvería el LLM, para el borde de parseo."""
    return json.dumps(valor)


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


class TestLasListasVaciasVuelvenAPoderEscribirse:
    """P2-3 — son deterministas del perfil: `[]` es un valor, no una ausencia."""

    @pytest.mark.parametrize("clave", ["matching_skills", "missing_skills"])
    def test_una_lista_vacia_viaja_al_UPDATE(self, clave):
        r = _result(**{clave: [], "matching_skills": ["k8s"], "missing_skills": ["go"]})
        r[clave] = []
        values = MatchService._score_values(r)
        assert clave in values, "la clave desaparecía y el UPDATE conservaba lo viejo"
        assert values[clave] == []

    def test_el_usuario_que_adquiere_la_ultima_skill_ve_la_tarjeta_limpia(self):
        """El caso irrecuperable: encaje perfecto que se quedaba congelado."""
        matching, missing = MatchService._compute_skill_overlap(
            user_skills=["python", "k8s"], job_tags=["python", "k8s"]
        )
        assert missing == []
        values = MatchService._score_values(_result(matching_skills=matching))
        assert values["missing_skills"] == []

    def test_vaciar_el_perfil_vacia_las_coincidentes(self):
        matching, _ = MatchService._compute_skill_overlap(
            user_skills=[], job_tags=["python", "k8s"]
        )
        assert matching == []
        values = MatchService._score_values(_result(matching_skills=matching))
        assert values["matching_skills"] == []


class TestElEnriquecimientoDelLLMSeSumaNoSustituye:
    """P2-3 — es la UNIÓN lo que protege de la avalancha, no el silencio."""

    def test_las_skills_del_LLM_se_unen_a_las_de_regla(self, servicio):
        r = _result(matching_skills=["python"], missing_skills=["go"])
        servicio._apply_llm_result(
            r,
            {
                "index": 0,
                "score": 70,
                "reason": "ok",
                "matching_skills": ["fastapi"],
                "missing_skills": ["kubernetes"],
            },
            _Perfil(["python", "fastapi"]),
            _PESOS,
        )
        assert r["matching_skills"] == ["python", "fastapi"]
        assert r["missing_skills"] == ["go", "kubernetes"]

    def test_no_duplica_ignorando_mayusculas(self, servicio):
        r = _result(matching_skills=["python"])
        servicio._apply_llm_result(
            r,
            {"index": 0, "score": 70, "reason": "ok", "matching_skills": ["Python"]},
            _Perfil(),
            _PESOS,
        )
        # G8/P3-5 (menor): a igualdad de skill gana la variante con mayúsculas.
        # `base` sale de `_compute_skill_overlap`, que lowercasea, y la tarjeta
        # mostraba «python» donde el usuario escribió «Python».
        assert r["matching_skills"] == ["Python"]

    def test_el_filtro_de_sinonimos_sigue_aplicandose_a_la_union(self, servicio):
        """`filter_missing_skills` corre DESPUÉS de unir, no antes."""
        r = _result(missing_skills=[])
        servicio._apply_llm_result(
            r,
            {"index": 0, "score": 70, "reason": "ok", "missing_skills": ["python"]},
            _Perfil(),  # el perfil YA tiene python
            _PESOS,
        )
        assert "python" not in r["missing_skills"]


class TestUnReasonNoStrNoTumbaLaCorridaDelUsuario:
    """P3-3 — el AttributeError subía hasta el `except` por USUARIO."""

    @pytest.mark.parametrize(
        "reason", [["a", "b"], {"x": 1}, 42, True], ids=["lista", "dict", "int", "bool"]
    )
    def test_no_lanza_y_no_escribe_basura(self, servicio, reason):
        r = _result(explanation="TEXTO BUENO DE OTRA CORRIDA")
        servicio._apply_llm_result(
            r, {"index": 0, "score": 40, "reason": reason}, _Perfil(), _PESOS
        )
        assert r["explanation"] == "TEXTO BUENO DE OTRA CORRIDA"

    @pytest.mark.parametrize("reason", [["a"], {"x": 1}, 42, None, True])
    def test_el_borde_lo_convierte_en_cadena_vacia(self, reason):
        crudo = f'[{{"index": 0, "score": 50, "reason": {_json(reason)}}}]'
        salida = GroqService._parse_llm_response(crudo, batch_len=1)
        assert salida[0]["reason"] == ""


class TestLasSkillsDelLLMSeSanean:
    """P3-4 — un elemento no-`str` en la columna es un 500 en TODA la lista."""

    @pytest.mark.parametrize(
        "crudas",
        [[""], [None], [{"s": "x"}], ["   "], [1, 2], "no-es-lista", None],
    )
    def test_el_borde_deja_solo_cadenas_con_contenido(self, crudas):
        crudo = (
            f'[{{"index": 0, "score": 50, "reason": "ok", '
            f'"matching_skills": {_json(crudas)}, "missing_skills": {_json(crudas)}}}]'
        )
        salida = GroqService._parse_llm_response(crudo, batch_len=1)
        for clave in ("matching_skills", "missing_skills"):
            assert salida[0][clave] == []

    def test_lo_bueno_sobrevive_y_se_recorta(self):
        crudo = (
            '[{"index": 0, "score": 50, "reason": "ok", '
            '"matching_skills": ["  python ", "", null, "sql"], '
            '"missing_skills": ["go"]}]'
        )
        salida = GroqService._parse_llm_response(crudo, batch_len=1)
        assert salida[0]["matching_skills"] == ["python", "sql"]
        assert salida[0]["missing_skills"] == ["go"]

    def test_la_union_ignora_lo_que_no_sea_texto(self, servicio):
        """Aunque el borde falle, el consumidor no persiste basura."""
        r = _result(matching_skills=["python"])
        servicio._apply_llm_result(
            r,
            {
                "index": 0,
                "score": 70,
                "reason": "ok",
                "matching_skills": [None, {"s": "x"}, "  ", "fastapi"],
            },
            _Perfil(["python", "fastapi"]),
            _PESOS,
        )
        assert r["matching_skills"] == ["python", "fastapi"]

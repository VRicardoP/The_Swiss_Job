"""Matching (A-08): unit sin BD."""

import inspect
import uuid

import jobhunt_core.tasks.matching  # noqa: F401 — registra la tarea en la app
from jobhunt_core import matching
from jobhunt_core.celery_app import celery_app
from jobhunt_core.shadow import projector


def test_eval_key_deterministic_and_component_sensitive():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    k1 = matching.eval_key(a, b, c, d)
    assert k1 == matching.eval_key(a, b, c, d)  # determinista
    assert len(k1) == 64
    for other in (
        matching.eval_key(uuid.uuid4(), b, c, d),
        matching.eval_key(a, uuid.uuid4(), c, d),
        matching.eval_key(a, b, uuid.uuid4(), d),
        matching.eval_key(a, b, c, uuid.uuid4()),
    ):
        assert other != k1  # CUALQUIER componente cambia la clave


def test_matching_task_registered_on_core_queue():
    assert "jobhunt.matching.run_profile" in celery_app.tasks
    assert celery_app.conf.task_routes["jobhunt.matching.*"] == {"queue": "core.matching"}


def test_matching_task_and_projector_share_canonical_limit():
    task = celery_app.tasks["jobhunt.matching.run_profile"]
    default = inspect.signature(task.run).parameters["limit"].default
    assert default == matching.CANONICAL_EVAL_LIMIT
    assert projector.EVAL_LIMIT == matching.CANONICAL_EVAL_LIMIT


# --------------------------------------------------------------------------
# hybrid-rrf/v2 — la consulta léxica por PESO y la suficiencia POR BRAZO.
# Diseñadas desde el paquete de desarrollo DEV_RANKING_2026-09-02, que es donde
# se midió el defecto: v1 truncaba a «los primeros 32 tokens únicos» y el orden
# accidental del JSON expulsaba 'teacher' de la consulta de la persona 1.
# --------------------------------------------------------------------------
class _Fila:
    def __init__(self, semantic_rank):
        self.semantic_rank = semantic_rank


def test_un_rol_repetido_solo_en_el_cv_entra_en_la_consulta_v2():
    """La vía del caso Teacher: experiencia docente extensa en cv_text que no
    aparece ni en title ni en skills. v1 la ignoraba por diseño; v2 la incluye
    con peso por frecuencia."""
    content = {
        "title": "Bilingual Content Specialist",
        "skills": ["Localization"],
        "cv_text": "English teacher at school. Head teacher assistant. "
                   "Substitute teacher for primary levels.",
    }
    v2 = matching._lexical_query_v2(content).split(" OR ")
    assert "teacher" in v2, v2
    # …y v1 sigue SIN incluirla: v1 es inmutable, otra versión = otra fila.
    assert "teacher" not in matching._lexical_query(content).split(" OR ")


def test_el_titulo_y_los_skills_siempre_tienen_prioridad_en_v2():
    """Con el tope saturado por términos del CV, las señales explícitas no
    pueden caerse: el recorte es SOLO de la cola de menor peso."""
    relleno = " ".join(f"palabrota{i} palabrota{i}" for i in range(60))
    content = {"title": "Localization QA Lead", "skills": ["Terminology"],
               "cv_text": relleno}
    v2 = matching._lexical_query_v2(content).split(" OR ")
    assert len(v2) <= matching._LEX_CAP
    for señal in ("localization", "qa", "lead", "terminology"):
        assert señal in v2, (señal, v2)


def test_los_terminos_genericos_no_expulsan_a_los_informativos():
    """'experience' repetido cincuenta veces no entra; 'teacher' repetido dos
    veces sí. La stoplist es corta y cada palabra está aquí probada."""
    content = {"title": "Specialist", "skills": [],
               "cv_text": ("experience " * 50) + ("years " * 30)
                          + "teacher classroom teacher"}
    v2 = matching._lexical_query_v2(content).split(" OR ")
    assert "teacher" in v2
    assert "experience" not in v2
    assert "years" not in v2


def test_la_consulta_v2_no_depende_del_orden_de_serializacion():
    """Mismo contenido, otro orden del array de skills y otro orden de las
    frases del CV ⇒ MISMA consulta. El orden del JSON no es una señal."""
    a = {"title": "Content Editor", "skills": ["Zulu", "Annotation", "CRM"],
         "cv_text": "translator projects. quality review. translator tasks."}
    b = {"title": "Content Editor", "skills": ["CRM", "Zulu", "Annotation"],
         "cv_text": "quality review. translator tasks. translator projects."}
    assert matching._lexical_query_v2(a) == matching._lexical_query_v2(b)


def test_v1_permanece_inmutable():
    """El golden de v1: primeros 32 tokens únicos de title+skills, en su orden.
    Si esto cambia, alguien ha mutado v1 en vez de crear otra versión."""
    content = {"title": "Data Engineer", "skills": ["Python", "SQL"],
               "cv_text": "teacher teacher teacher"}
    assert matching._lexical_query(content) == "data OR engineer OR python OR sql"


def test_el_fts_lleno_no_oculta_el_underfill_semantico():
    """El caso adversarial de la auditoría R8 §2.2.4: la unión llega al target
    pero el brazo ANN volvió medio vacío. El control combinado (len>=target)
    lo daba por bueno; la suficiencia POR BRAZO lo detecta."""
    llenas_por_fts = [_Fila(None)] * 10          # todo vino del FTS
    mixtas = [_Fila(1), _Fila(2), _Fila(None)] + [_Fila(i) for i in range(3, 11)]
    assert matching._semantic_arm_filled(llenas_por_fts, 10, hybrid=True) is False
    assert matching._semantic_arm_filled(mixtas, 10, hybrid=True) is True
    # En modo no híbrido el control combinado ya basta: no se duplica.
    assert matching._semantic_arm_filled(llenas_por_fts, 10, hybrid=False) is True


def test_v2_registra_su_propio_algoritmo_en_los_pesos():
    assert matching.HYBRID2_POLICY_VERSION == "v2"
    assert matching.HYBRID2_POLICY_WEIGHTS == {"algorithm": "hybrid_rrf_v2"}
    # y la v1 no cambió
    assert matching.HYBRID_POLICY_WEIGHTS == {"algorithm": "hybrid_rrf_v1"}

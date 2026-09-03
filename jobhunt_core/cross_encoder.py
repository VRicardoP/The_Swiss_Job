"""Reranker LOCAL por cross-encoder (Fase 2 del cierre definitivo 2026-09-03).

- El modelo corre EN el NAS (torch CPU, sentence-transformers ya presente):
  ningún CV ni PII sale de la máquina. La consulta ni siquiera usa el CV
  completo: solo la intención declarada (roles objetivo + skills + idiomas +
  ubicaciones + preferencia de remoto), con truncados DETERMINISTAS.
- La ENTRADA está versionada (INPUT_VERSION): cambiar cualquier cota o el
  orden de composición es otra versión de entrada ⇒ otra receta ⇒ otra
  política. La activación es sigmoide FIJA (score de pareja en 0..1, absoluto:
  depende solo de consulta+documento+modelo — jamás del lote).
- El motor se carga UNA vez por proceso y por (modelo, revisión); los tests
  inyectan un stub con set_engine_factory (mismo patrón que embeddings).
"""
import logging
import math
import threading

logger = logging.getLogger(__name__)

INPUT_VERSION = "v1"
ACTIVATION = "sigmoid"
BACKEND = "torch-cpu"

# Cotas de la entrada v1 (deterministas; parte del contrato de la receta).
_Q_ROLE_LEN = 120
_Q_MAX_SKILLS = 20
_Q_SKILLS_LEN = 400
_Q_LIST_LEN = 120        # idiomas / ubicaciones serializados
_DOC_TITLE_LEN = 200
_DOC_LOC_LEN = 100
_DOC_DESC_LEN = 1200

_lock = threading.Lock()
_engines: dict = {}
_engine_factory = None


def set_engine_factory(factory) -> None:
    """Inyección para tests (None restaura el motor real). El stub debe
    exponer predict(list[tuple[str, str]]) -> list[float] (logits)."""
    global _engine_factory
    with _lock:
        _engine_factory = factory
        _engines.clear()


def _get_engine(model: str, revision: str):
    with _lock:
        clave = (model, revision)
        motor = _engines.get(clave)
        if motor is None:
            if _engine_factory is not None:
                motor = _engine_factory(model, revision)
            else:
                # Carga real: una vez por proceso. La revisión CLAVADA es
                # parte de la receta; sin red si el artefacto ya está en la
                # caché HF de la imagen/volumen.
                from sentence_transformers import CrossEncoder

                motor = CrossEncoder(
                    model, revision=revision, device="cpu", max_length=512,
                )
            _engines[clave] = motor
        return motor


def build_queries(content: dict) -> list[str]:
    """Consultas v1: UNA por rol objetivo (o [title] si no hay), con la
    intención declarada del perfil. Determinista; sin CV completo ni PII."""
    roles = [r for r in (content.get("target_roles") or []) if r and r.strip()]
    if not roles:
        titulo = (content.get("title") or "").strip()
        roles = [titulo] if titulo else [""]
    skills = ", ".join(
        s.strip() for s in (content.get("skills") or [])[:_Q_MAX_SKILLS]
        if s and s.strip()
    )[:_Q_SKILLS_LEN]
    idiomas = ", ".join(
        x.strip() for x in (content.get("languages") or []) if x and x.strip()
    )[:_Q_LIST_LEN]
    lugares = ", ".join(
        x.strip() for x in (content.get("locations") or []) if x and x.strip()
    )[:_Q_LIST_LEN]
    remoto = (content.get("remote_pref") or "").strip()
    return [
        f"{rol[:_Q_ROLE_LEN]}. Skills: {skills}. Languages: {idiomas}. "
        f"Locations: {lugares}. Remote: {remoto}"
        for rol in roles
    ]


def build_document(titulo, location, descripcion) -> str:
    """Documento v1: título + ubicación + descripción, orden y truncado
    fijos."""
    return (
        f"{(titulo or '')[:_DOC_TITLE_LEN]}. {(location or '')[:_DOC_LOC_LEN]}. "
        f"{(descripcion or '')[:_DOC_DESC_LEN]}"
    )


def score_documents(
    model: str, revision: str, queries: list[str], documents: list[str],
    batch_size: int = 16,
) -> list[float]:
    """Probabilidad (sigmoide del logit) por documento = MÁXIMO sobre las
    consultas de rol. Absoluto por pareja: ni min/max ni percentiles ni
    normalización del lote. El batch solo afecta al coste, no al score."""
    if not documents:
        return []
    motor = _get_engine(model, revision)
    pares = [(q, d) for d in documents for q in queries]
    logits = list(motor.predict(pares, batch_size=batch_size))
    if len(logits) != len(pares):
        raise ValueError(
            f"cross-encoder devolvió {len(logits)} scores para "
            f"{len(pares)} pares"
        )
    nq = len(queries)
    out = []
    for i in range(len(documents)):
        mejores = logits[i * nq:(i + 1) * nq]
        logit = max(float(x) for x in mejores)
        if not math.isfinite(logit):
            raise ValueError(f"cross-encoder devolvió un logit no finito: {logit!r}")
        out.append(1.0 / (1.0 + math.exp(-logit)))
    return out

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
import hashlib
import logging
import math
import os
import threading

logger = logging.getLogger(__name__)

INPUT_VERSION = "v1"
# Backend de inferencia (P7-b): parte de la CLAVE del motor (P1-1) y de la
# receta versionada. "torch-cpu" (por defecto) o "onnx-cpu" (NAS: paridad
# 6e-06 probada en DEV_P7_BENCH_NAS_2026-09-04; requiere onnxruntime en la
# imagen y un artefacto exportado con model.onnx[.data]).
BACKEND = os.environ.get("CE_BACKEND", "torch-cpu")

# Activaciones FIJAS y MONÓTONAS (el orden del modelo se conserva siempre;
# solo cambia la escala del score persistido). sigmoid_t4 = σ(logit/4) existe
# porque el contrato NUMERIC(6,2) redondea a 2 decimales y la sigmoide plana
# aplasta los logits 9-13 de consultas anchas en 99.99 empatados — el feed
# habría ordenado por vacancy_id (defecto medido en P2, 10/10 empatados).
ACTIVATIONS = {
    "sigmoid": lambda logit: 1.0 / (1.0 + math.exp(-logit)),
    "sigmoid_t4": lambda logit: 1.0 / (1.0 + math.exp(-logit / 4.0)),
}
ACTIVATION = "sigmoid"  # compat: la de v1

# Cotas de la entrada v1 (deterministas; parte del contrato de la receta).
_Q_ROLE_LEN = 120
_Q_MAX_SKILLS = 20
_Q_SKILLS_LEN = 400
_Q_LIST_LEN = 120        # idiomas / ubicaciones serializados
_DOC_TITLE_LEN = 200
_DOC_LOC_LEN = 100
_DOC_DESC_LEN = 1200

# Batch OPERATIVO del camino productivo (elegido por benchmark: 240s vs 252s
# a batch 16 en 1800 docs). No es parte de la receta: la invariancia del score
# al batch está probada por regresión.
CE_BATCH_SIZE = 8

# Archivos de RUNTIME que componen la identidad efectiva del modelo (P1-1
# revisión 2026-09-03): allowlist determinista de lo que CrossEncoder carga.
RUNTIME_FILES = (
    "added_tokens.json", "config.json", "merges.txt", "model.safetensors",
    # Export ONNX (P7-b): el artefacto del backend NAS lleva el grafo y sus
    # pesos externos en vez de safetensors; la MISMA huella agregada los
    # sella (allowlist ∩ archivos presentes).
    "model.onnx", "model.onnx.data",
    "sentencepiece.bpe.model", "special_tokens_map.json", "tokenizer.json",
    "tokenizer_config.json", "vocab.txt",
)

_lock = threading.Lock()
_engines: dict = {}
_engine_factory = None


def _resolve_model_dir(model: str, revision) -> str:
    """Directorio REAL de los artefactos: la ruta local tal cual, o el
    snapshot clavado resuelto OFFLINE para un modelo de hub."""
    if model.startswith("/"):
        if not os.path.isdir(model):
            raise ValueError(f"modelo local inexistente: {model}")
        return model
    from huggingface_hub import snapshot_download

    return snapshot_download(model, revision=revision, local_files_only=True)


def model_manifest(model: str, revision=None) -> dict:
    """{nombre: sha256} de los archivos de runtime PRESENTES (allowlist)."""
    d = _resolve_model_dir(model, revision)
    out = {}
    for nombre in RUNTIME_FILES:
        ruta = os.path.join(d, nombre)
        if os.path.isfile(ruta):
            h = hashlib.sha256()
            with open(ruta, "rb") as fh:
                for bloque in iter(lambda: fh.read(1 << 20), b""):
                    h.update(bloque)
            out[nombre] = h.hexdigest()
    if not out:
        raise ValueError(f"sin archivos de runtime en {d}")
    return out


def model_fingerprint(model: str, revision=None) -> str:
    """Huella agregada CANÓNICA: sha256 de las líneas «sha256  nombre\n»
    ordenadas del manifiesto de runtime. Generable por comando:
    python -m jobhunt_core.cross_encoder fingerprint <modelo> [revision]."""
    man = model_manifest(model, revision)
    canon = "".join(f"{h}  {n}\n" for n, h in sorted(man.items()))
    return hashlib.sha256(canon.encode()).hexdigest()


def verify_model_identity(model: str, revision, fingerprint: str) -> None:
    """Falla CERRADO si la huella de los archivos realmente presentes no es la
    de la receta — antes de construir motor alguno y antes de reutilizar o
    crear evaluaciones bajo esa identidad."""
    real = model_fingerprint(model, revision)
    if real != fingerprint:
        raise ValueError(
            f"huella del modelo NO coincide: receta {fingerprint[:12]}…, "
            f"artefactos {real[:12]}… en {model} — el artefacto cambió bajo "
            "la misma identidad"
        )


class _OnnxEngine:
    """Motor ONNX Runtime (CPU) con el MISMO contrato que CrossEncoder:
    predict(list[(query, doc)]) -> logits. Paridad con torch probada en el
    benchmark P7 (Δmax 6e-06)."""

    def __init__(self, ruta: str):
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self._tok = AutoTokenizer.from_pretrained(ruta)
        self._sess = ort.InferenceSession(
            os.path.join(ruta, "model.onnx"),
            providers=["CPUExecutionProvider"],
        )

    def predict(self, pares, batch_size: int = 8):
        import numpy as np

        out: list[float] = []
        for k in range(0, len(pares), batch_size):
            lote = pares[k:k + batch_size]
            enc = self._tok(
                [q for q, _ in lote], [d for _, d in lote],
                padding=True, truncation=True, max_length=512,
                return_tensors="np",
            )
            logits = self._sess.run(
                ["logits"],
                {"input_ids": enc["input_ids"].astype(np.int64),
                 "attention_mask": enc["attention_mask"].astype(np.int64)},
            )[0].reshape(-1)
            out.extend(float(x) for x in logits)
        return out


def set_engine_factory(factory) -> None:  # noqa: D401
    """Inyección para tests (None restaura el motor real). El stub debe
    exponer predict(list[tuple[str, str]]) -> list[float] (logits)."""
    global _engine_factory
    with _lock:
        _engine_factory = factory
        _engines.clear()


def _get_engine(model: str, revision, fingerprint=None):
    with _lock:
        # Clave COMPLETA de identidad (P1-1): modelo+revisión+huella+backend.
        clave = (model, revision, fingerprint, BACKEND)
        motor = _engines.get(clave)
        if motor is None:
            if _engine_factory is not None:
                # Los tests inyectan el motor y asumen la identidad; la
                # verificación pertenece a las cargas REALES.
                motor = _engine_factory(model, revision)
            else:
                if fingerprint is not None:
                    # Verificación ÚNICA al cargar (no por documento): la
                    # huella de la receta contra los archivos reales, ANTES
                    # de construir el motor.
                    verify_model_identity(model, revision, fingerprint)
                # Carga real: una vez por proceso. La revisión CLAVADA es
                # parte de la receta; sin red si el artefacto ya está en la
                # caché HF de la imagen/volumen. Un modelo FINE-TUNED es un
                # directorio local (la huella de la receta lo sella): sin
                # revisión de hub.
                if BACKEND == "onnx-cpu":
                    # Solo artefactos LOCALES exportados (la receta del
                    # backend NAS sella su propia huella ONNX).
                    if not model.startswith("/"):
                        raise ValueError(
                            "backend onnx-cpu exige un artefacto local "
                            f"exportado, no un modelo de hub: {model!r}"
                        )
                    motor = _OnnxEngine(model)
                else:
                    from sentence_transformers import CrossEncoder

                    if model.startswith("/"):
                        motor = CrossEncoder(
                            model, device="cpu", max_length=512)
                    else:
                        motor = CrossEncoder(
                            model, revision=revision, device="cpu",
                            max_length=512,
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
    model: str, revision, queries: list[str], documents: list[str],
    batch_size: int = CE_BATCH_SIZE, activation: str = "sigmoid",
    fingerprint: str | None = None,
) -> list[float]:
    """Score por documento = activación fija del MÁXIMO logit sobre las
    consultas de rol. Absoluto por pareja: ni min/max ni percentiles ni
    normalización del lote. El batch solo afecta al coste, no al score."""
    fn = ACTIVATIONS.get(activation)
    if fn is None:
        raise ValueError(
            f"activación {activation!r} desconocida "
            f"(soportadas: {sorted(ACTIVATIONS)})")
    if not documents:
        return []
    motor = _get_engine(model, revision, fingerprint)
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
        out.append(fn(logit))
    return out


if __name__ == "__main__":  # comando de manifiesto/huella (P1-1)
    import json as _json
    import sys as _sys

    if len(_sys.argv) < 3 or _sys.argv[1] != "fingerprint":
        raise SystemExit(
            "uso: python -m jobhunt_core.cross_encoder fingerprint "
            "<modelo|ruta> [revision]")
    _modelo = _sys.argv[2]
    _rev = _sys.argv[3] if len(_sys.argv) > 3 else None
    print(_json.dumps({
        "manifest": model_manifest(_modelo, _rev),
        "fingerprint": model_fingerprint(_modelo, _rev),
    }, ensure_ascii=False, sort_keys=True))

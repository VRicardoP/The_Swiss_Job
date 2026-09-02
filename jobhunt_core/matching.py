"""Matching determinista por embeddings (A-08, ADR-03 + CONTRATOS §1).

- `match_evaluations` = APPEND-ONLY con los componentes como COLUMNAS y
  `eval_key` DETERMINISTA (hash de offer_revision + profile_revision + model +
  policy): re-evaluar los MISMOS componentes no duplica —
  UNIQUE(profile_id, vacancy_id, eval_key) + DO NOTHING ("reintento no
  duplica"). Sin feedback aquí (ADR-03).
- `profile_vacancy_state` = estado ESTABLE por (perfil, vacante): el matching
  solo mueve `current_eval_id` (FK compuesta, RESTRICT) y `updated_at` —
  JAMÁS pisa feedback/dismissed_at/saved_at/notes.
- Feed (DoD): evaluación VIGENTE (current_eval_id) + no-dismissed + vacante
  ACTIVA, keyset por (score_final DESC, vacancy_id ASC).
- score_final en Fase A: similitud coseno (pgvector `<=>`, HNSW del modelo)
  escalada a 0..100 con 2 decimales (NUMERIC(6,2) del contrato). El
  multi-factor/rerank es POLÍTICA VERSIONADA de Fase B (`weights` JSONB
  reservado en scoring_policies).
"""

import hashlib
import json
import logging
import math
import re
import uuid

import sqlalchemy as sa
logger = logging.getLogger(__name__)


# Único tamaño del conjunto canónico. El feed no puede depender de si la
# evaluación la inició Celery o la recuperación del proyector.
CANONICAL_EVAL_LIMIT = 1800


# Tope de tuplas del scan ITERATIVO del HNSW (rev. A-08 #2): strict_order
# sigue escaneando hasta llenar el LIMIT tras el filtro, acotado por esto.
MAX_SCAN_TUPLES = 20000

# Namespace DETERMINISTA de los eventos de integración (ADR-05: event_id =
# uuid5(ns, type || ':' || clave-natural); para match.evaluated → eval_key).
EVENTS_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "jobhunt-core/integration-events")


def event_id_for(event_type: str, natural_key: str) -> uuid.UUID:
    return uuid.uuid5(EVENTS_NAMESPACE, f"{event_type}:{natural_key}")

# SQL de candidatos (module-level: los tests lo EXPLAINean tal cual).
CANDIDATES_SQL = (
    "SELECT v.id AS vacancy_id, "
    "v.current_offer_revision_id AS offer_revision_id, "
    "1 - (oe.vector <=> CAST(:vec AS vector)) AS sim "
    "FROM vacancies v "
    "JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "JOIN offer_embeddings oe "
    "  ON oe.text_hash = orv.text_hash AND oe.model_id = :mid "
    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
    "ORDER BY oe.vector <=> CAST(:vec AS vector) "
    "LIMIT :k"
)


HYBRID_POLICY_NAME = "hybrid-rrf"
HYBRID_POLICY_VERSION = "v1"
HYBRID_POLICY_WEIGHTS = {"algorithm": "hybrid_rrf_v1"}
# v2 (2026-09-02): misma fusión RRF, DISTINTA construcción de la consulta
# léxica. v1 tomaba «los primeros 32 tokens únicos de title+skills», así que el
# orden accidental del JSON decidía qué señales entraban: el CV de la persona 1
# acaba en 'ipgce' y expulsa 'teacher', y toda oferta docente sin descripción
# pierde su única señal léxica. v2 selecciona POR PESO (título y skills siempre;
# después términos de rol REPETIDOS del cv_text) y con orden interno
# determinista, no el de serialización. v1 no se toca: otra versión, otra fila.
HYBRID2_POLICY_VERSION = "v2"
HYBRID2_POLICY_WEIGHTS = {"algorithm": "hybrid_rrf_v2"}
# Peso léxico de v2, PROPIO — el 1.15 de arriba es de v1 y no se toca. El valor
# sale de la comparación predeclarada A/B sobre las evaluaciones persistidas
# (2026-09-02): con 1.15 y la consulta ancha de v2, una oferta mediocre en los
# DOS brazos (sr=30+lr=20) sumaba más RRF que un sr=1 sin señal léxica, y la
# marea de dobles-brazo expulsaba del top-10 un orden semántico casi perfecto
# (nDCG dev 0.0). Con 0.25 el léxico RESCATA cobertura (los 4 relevantes-2
# siguen dentro del feed) sin mandar en el orden: nDCG dev 0.557/0.617. Fue la
# alternativa ganadora frente a «semántico primero, léxico anexado» (0.545/0.617).
_RRF_K = 60
HYBRID2_LEXICAL_WEIGHT = 0.25
# v4 (P1-A de la revisión externa 2026-09-02): la RECETA COMPLETA vive en la
# fila de la política, no en el binario. v2 y v3 compartían el mismo JSON
# persistido ({"algorithm": "hybrid_rrf_v2"}) y el peso 0.25 solo existía como
# constante: una fila v2 histórica se ejecutaría hoy con 0.25 aunque nació con
# 1.15, y v2/v3 eran indistinguibles por sus datos — la misma mezcla de
# regímenes que produjo el feed_n=2380. El evaluador VALIDA la receta contra la
# implementación (rrf_k y versión de consulta soportados) y DERIVA de ella el
# peso léxico; una receta incompleta o no soportada no evalúa nada.
HYBRID4_POLICY_VERSION = "v4"
HYBRID4_POLICY_WEIGHTS = {
    "algorithm": "hybrid_rrf",
    "lexical_query": "v2",
    "lexical_weight": HYBRID2_LEXICAL_WEIGHT,
    "rrf_k": _RRF_K,
}
_LEXICAL_WEIGHT = 1.15

# Unión de recuperación semántica y léxica. Ambas ramas recorren exactamente
# el corpus elegible del modelo; la búsqueda léxica no adelanta ofertas que
# aún no tienen embedding. RRF evita comparar escalas incompatibles y el peso
# léxico, apenas superior, desempata a favor de coincidencias explícitas de
# título/skills cuando solo una rama recupera la oferta.
HYBRID_CANDIDATES_SQL = f"""
WITH ann AS MATERIALIZED (
    SELECT v.id AS vacancy_id,
           v.current_offer_revision_id AS offer_revision_id,
           1 - (oe.vector <=> CAST(:vec AS vector)) AS sim,
           row_number() OVER (
               ORDER BY oe.vector <=> CAST(:vec AS vector)
           ) AS semantic_rank
    FROM vacancies v
    JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id
    JOIN offer_embeddings oe
      ON oe.text_hash = orv.text_hash AND oe.model_id = :mid
    WHERE v.archived_at IS NULL AND v.merged_into IS NULL
    ORDER BY oe.vector <=> CAST(:vec AS vector)
    LIMIT :k
), lexical AS MATERIALIZED (
    SELECT v.id AS vacancy_id,
           v.current_offer_revision_id AS offer_revision_id,
           ts_rank_cd(orv.search_document, q.query, 32) AS lexical_score,
           row_number() OVER (
               ORDER BY ts_rank_cd(orv.search_document, q.query, 32) DESC,
                        v.id
           ) AS lexical_rank
    FROM vacancies v
    JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id
    JOIN offer_embeddings oe
      ON oe.text_hash = orv.text_hash AND oe.model_id = :mid
    CROSS JOIN websearch_to_tsquery('simple', :lex_query) AS q(query)
    WHERE v.archived_at IS NULL AND v.merged_into IS NULL
      AND orv.search_document @@ q.query
    ORDER BY lexical_score DESC, v.id
    LIMIT :k
)
SELECT COALESCE(a.vacancy_id, l.vacancy_id) AS vacancy_id,
       COALESCE(a.offer_revision_id, l.offer_revision_id) AS offer_revision_id,
       a.sim, a.semantic_rank, l.lexical_rank, l.lexical_score,
       (
         COALESCE(1.0 / ({_RRF_K} + a.semantic_rank), 0.0)
         + {_LEXICAL_WEIGHT} *
           COALESCE(1.0 / ({_RRF_K} + l.lexical_rank), 0.0)
       ) * (100.0 * ({_RRF_K} + 1) / (1.0 + {_LEXICAL_WEIGHT}))
         AS rank_score
FROM ann a
FULL OUTER JOIN lexical l
  ON l.vacancy_id = a.vacancy_id
 AND l.offer_revision_id = a.offer_revision_id
ORDER BY rank_score DESC, COALESCE(a.vacancy_id, l.vacancy_id)
LIMIT :k
"""


def _hybrid_candidates_sql(lexical_weight: float) -> str:
    """SQL híbrido con el peso léxico indicado, derivado del de v1.

    Camino ÚNICO de construcción: v3 (legacy, peso en constante) y v4 (peso en
    la receta persistida) lo comparten, con lo que la equivalencia v4↔v3 con el
    mismo peso es byte a byte por construcción, no por confianza. La derivación
    por replace no falla sola cuando el patrón cambia (misma lección que
    _EXACT_INTRA_HISTORY_SQL en dedup): se cuenta cada patrón antes de tocarlo.
    """
    patron_suma = f"+ {_LEXICAL_WEIGHT} *"
    patron_norm = f"(1.0 + {_LEXICAL_WEIGHT})"
    if (HYBRID_CANDIDATES_SQL.count(patron_suma) != 1
            or HYBRID_CANDIDATES_SQL.count(patron_norm) != 1):
        raise RuntimeError(
            "el SQL de v1 ya no contiene los patrones de peso esperados: "
            "la derivación produciría un SQL con el peso equivocado"
        )
    return HYBRID_CANDIDATES_SQL.replace(
        patron_suma, "+ %s *" % lexical_weight
    ).replace(patron_norm, "(1.0 + %s)" % lexical_weight)


# El SQL de v2/v3: idéntico en forma al de v1 pero con SU peso léxico. Compartir
# la constante habría significado que ajustar v2 MUTA v1 — la clase de
# acoplamiento que el golden de inmutabilidad existe para impedir.
HYBRID2_CANDIDATES_SQL = _hybrid_candidates_sql(HYBRID2_LEXICAL_WEIGHT)


# Versiones de consulta léxica que una receta v4+ puede nombrar. La receta
# VALIDA contra la implementación: nombrar una versión que el binario no
# implementa es error, no silencio.
_LEXICAL_QUERY_BUILDERS = {"v2": None}  # se rellena tras definir las funciones


def _validated_recipe(policy_weights: dict) -> dict:
    """Valida la receta persistida de una política ``hybrid_rrf`` (v4+).

    P1-A: el comportamiento se deriva de la fila, no del binario. Una receta
    incompleta, con campos extra o con valores que esta implementación no
    soporta no evalúa nada — mejor un error nombrable que un régimen mezclado.
    """
    esperadas = {"algorithm", "lexical_query", "lexical_weight", "rrf_k"}
    claves = set(policy_weights)
    if claves != esperadas:
        raise ValueError(
            f"receta hybrid_rrf inválida: claves {sorted(claves)}, "
            f"esperadas {sorted(esperadas)}"
        )
    if policy_weights["lexical_query"] not in _LEXICAL_QUERY_BUILDERS:
        raise ValueError(
            "receta hybrid_rrf: lexical_query "
            f"{policy_weights['lexical_query']!r} no implementada "
            f"(soportadas: {sorted(_LEXICAL_QUERY_BUILDERS)})"
        )
    if policy_weights["rrf_k"] != _RRF_K:
        raise ValueError(
            f"receta hybrid_rrf: rrf_k={policy_weights['rrf_k']!r} no "
            f"soportado por esta implementación (rrf_k={_RRF_K})"
        )
    peso = policy_weights["lexical_weight"]
    if not isinstance(peso, (int, float)) or isinstance(peso, bool)             or not math.isfinite(peso) or peso <= 0:
        raise ValueError(
            f"receta hybrid_rrf: lexical_weight={peso!r} debe ser un "
            "número finito y positivo"
        )
    return dict(policy_weights)


def _semantic_arm_filled(filas, target: int, hybrid: bool) -> bool:
    """Suficiencia del brazo SEMÁNTICO por separado (auditoría R8 §2.2.4).

    En modo híbrido, `len(filas) >= target` puede cumplirse con el FTS llenando
    la unión mientras el ANN volvió medio vacío por el scan acotado — y ese
    underfill semántico es EXACTAMENTE el que pierde a los relevantes sin señal
    léxica (ofertas sin descripción, términos fuera de la consulta). El brazo
    ANN está lleno solo si aporta por sí mismo tantas filas como el objetivo."""
    if not hybrid:
        return True
    semanticas = sum(1 for c in filas if c.semantic_rank is not None)
    return semanticas >= target


def _lexical_query(content: dict) -> str:
    """Consulta OR acotada a señales explícitas del perfil, nunca al CV entero."""
    raw = " ".join(
        [str(content.get("title") or "")]
        + [str(skill) for skill in content.get("skills") or []]
    ).casefold()
    raw = raw.replace("c++", "cplusplus").replace("c#", "csharp")
    tokens = list(dict.fromkeys(re.findall(r"[^\W\d_]\w{2,}", raw)))[:32]
    return " OR ".join(tokens)


# Palabras que aparecen repetidas en CUALQUIER CV y no nombran ningún rol.
# Lista corta y PROBADA (test_matching): añadir aquí sin su test es reabrir la
# puerta a que un término genérico expulse a uno informativo.
_CV_STOP = frozenset("""
    experience experiences work working years management support team teams
    strong excellent skills knowledge professional company companies role
    roles responsibilities con para las los del una this that with from
    and the have has been also able about
""".split())
_LEX_CAP = 48  # tope de términos de la tsquery: coste acotado y probado


def _lexical_query_v2(content: dict) -> str:
    """Señales léxicas POR PESO, no por orden de serialización (v2).

    1. Título y skills entran SIEMPRE y primero — son la declaración explícita
       de la persona. Los skills se ordenan alfabéticamente a propósito: el
       orden del array JSON no es una señal y no debe decidir empates.
    2. Después, términos de ROL del cv_text completo: palabras repetidas
       (frecuencia >= 2), no genéricas, que no estén ya arriba — la vía por la
       que «teacher» existe aunque no esté en title/skills. Orden determinista
       por (frecuencia desc, palabra asc).
    3. El tope _LEX_CAP recorta SOLO la cola de menor peso.
    """
    def toks(texto: str) -> list[str]:
        # >= 2 letras (no los 3 de v1): 'QA' o 'UX' son señales EXPLÍCITAS del
        # usuario cuando vienen de título/skills. Los términos minados del CV
        # mantienen el mínimo de 3 en su propio filtro, más abajo.
        t = texto.casefold().replace("c++", "cplusplus").replace("c#", "csharp")
        return re.findall(r"[^\W\d_]\w{1,}", t)

    titulo = toks(str(content.get("title") or ""))
    skills = sorted(
        {w for sk in (content.get("skills") or []) for w in toks(str(sk))}
    )
    prioritarios = list(dict.fromkeys(titulo + skills))
    ya = set(prioritarios)

    frec: dict[str, int] = {}
    for w in toks(str(content.get("cv_text") or "")):
        if len(w) >= 3 and w not in ya and w not in _CV_STOP:
            frec[w] = frec.get(w, 0) + 1
    del_cv = [w for w, n in sorted(frec.items(), key=lambda kv: (-kv[1], kv[0]))
              if n >= 2]

    return " OR ".join((prioritarios + del_cv)[:_LEX_CAP])


# Registro real de builders: se rellena aquí (tras definir las funciones) y no
# arriba, para que nombrar una versión inexistente falle al validar la receta.
_LEXICAL_QUERY_BUILDERS["v2"] = _lexical_query_v2


def eval_key(offer_revision_id, profile_revision_id, model_id, policy_id) -> str:
    """Clave DETERMINISTA de la evaluación: mismos componentes ⇒ misma clave
    ⇒ una sola fila append-only (idempotencia por contrato)."""
    raw = f"{offer_revision_id}|{profile_revision_id}|{model_id}|{policy_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def ensure_policy(
    session, name: str, prompt_version: str, weights: dict | None = None,
    active: bool | None = True,
) -> uuid.UUID:
    """Alta idempotente de la política (UNIQUE(name, prompt_version)). Como
    register_model: la fila existente se relee bajo lock y `active` se
    ACTUALIZA al re-declarar (declaración operativa); weights solo al crear
    (una política versionada no muta — otra versión = otra fila).

    `active=None` (P1-D): asegura la FILA sin tocar la activación — si se
    crea, nace inactiva. Es el modo del bootstrap de despliegue: un redeploy
    no puede cambiar la canonicidad en silencio; la activación solo la mueve
    declare_active_policies, explícita y atómicamente."""
    await session.execute(
        sa.text(
            "INSERT INTO scoring_policies (id, name, prompt_version, weights, active) "
            "VALUES (:id, :name, :ver, CAST(:w AS jsonb), :active) "
            "ON CONFLICT (name, prompt_version) DO NOTHING"
        ),
        {
            "id": uuid.uuid4(), "name": name, "ver": prompt_version,
            "w": json.dumps(weights or {}),
            "active": bool(active),
        },
    )
    row = (
        await session.execute(
            sa.text(
                "SELECT id, active, weights FROM scoring_policies "
                "WHERE name = :name AND prompt_version = :ver FOR UPDATE"
            ),
            {"name": name, "ver": prompt_version},
        )
    ).one()
    requested_weights = weights or {}
    if row.weights != requested_weights:
        raise ValueError(
            f"policy {name}@{prompt_version}: weights distintos para la misma versión"
        )
    if active is not None and row.active != active:
        await session.execute(
            sa.text("UPDATE scoring_policies SET active = :a WHERE id = :id"),
            {"a": active, "id": row.id},
        )
    return row.id


# Catálogo de políticas conocidas (P1-D): las FILAS que todo entorno debe
# tener, con sus recetas canónicas. El bootstrap las asegura sin tocar la
# activación; qué está activo lo decide SOLO declare_active_policies.
POLICY_CATALOG = (
    ("cosine-baseline", "v1", {}),
    (HYBRID_POLICY_NAME, HYBRID_POLICY_VERSION, HYBRID_POLICY_WEIGHTS),
    (HYBRID_POLICY_NAME, HYBRID2_POLICY_VERSION, HYBRID2_POLICY_WEIGHTS),
    (HYBRID_POLICY_NAME, "v3", HYBRID2_POLICY_WEIGHTS),
    (HYBRID_POLICY_NAME, HYBRID4_POLICY_VERSION, HYBRID4_POLICY_WEIGHTS),
)


async def bootstrap_policy_catalog(session) -> dict:
    """Asegura las filas del catálogo SIN tocar la activación (P1-D).

    Es lo único que un despliegue normal puede hacer con las políticas:
    re-ejecutarlo N veces preserva el conjunto activo que hubiera (pre o post
    promoción, o tras rollback). Devuelve {(name, version): id}."""
    ids = {}
    for name, ver, w in POLICY_CATALOG:
        ids[(name, ver)] = await ensure_policy(
            session, name, ver, weights=w, active=None
        )
    return ids


async def declare_active_policies(session, policy_ids) -> None:
    """Declara el conjunto EXACTO de políticas activas (P1-D, revisión externa
    2026-09-02): autoridad ÚNICA sobre la canonicidad.

    El bootstrap define filas inmutables; ESTA función —y solo esta— cambia la
    activación. Un despliegue normal no la invoca y por tanto no puede cambiar
    la canonicidad en silencio; promoción y rollback la invocan explícitamente
    con el conjunto completo deseado.

    Un solo UPDATE sobre TODAS las filas: (a) el flip es atómico — no existe
    ningún estado intermedio con el conjunto a medias; (b) toma lock de fila
    sobre la canónica saliente, con lo que se serializa contra la valla
    FOR SHARE de evaluate_profile (P1-C): o el movimiento de feed en vuelo
    termina antes del flip, o ve el canónico nuevo y se aborta.

    Declarar un id inexistente es error (y aborta la transacción): un conjunto
    activo que no coincide EXACTAMENTE con lo declarado no debe cometerse.
    """
    ids = sorted({str(x) for x in policy_ids})
    if not ids:
        raise ValueError("el conjunto activo declarado no puede ser vacío")
    filas = (
        await session.execute(
            sa.text(
                "UPDATE scoring_policies "
                "SET active = (id = ANY(CAST(:ids AS uuid[]))) "
                "RETURNING id, active"
            ),
            {"ids": ids},
        )
    ).all()
    activas = sorted(str(r.id) for r in filas if r.active)
    if activas != ids:
        raise ValueError(
            "declaración de políticas activas no satisfecha: "
            f"pedidas {ids}, activas {activas} — ids inexistentes o duplicados"
        )


# Corpus ELEGIBLE de un modelo: vacantes vivas cuya revisión canónica está embebida para él. El
# fragmento es la fuente ÚNICA que comparten la evaluación y la señal de recuperación del proyector.
ELIGIBLE_CORPUS_FROM = (
    "FROM vacancies v "
    "JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "JOIN offer_embeddings oe ON oe.text_hash = orv.text_hash AND oe.model_id = {model} "
    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL"
)
# VERSIÓN del corpus: contador monotónico global que los triggers de core0022 incrementan en cada
# transición de elegibilidad. Sustituye a la huella hash (colisionable, cara y dependiente del
# snapshot). Lectura O(1) por PK; se compara por DESIGUALDAD.
CORPUS_GENERATION_SQL = "SELECT generation FROM corpus_generation WHERE id = 1"


async def evaluate_profile(
    session, profile_id, model_id, policy_id, limit: int = 100,
    move_current: bool = True,
    with_corpus_generation: bool = False,
) -> dict:
    """Evalúa el perfil vigente contra el corpus embebido con la política
    versionada indicada (coseno o recuperación híbrida).

    - LOCK por perfil (FOR UPDATE — mismo protocolo que save_profile_revision;
      auditoría A-08): evaluaciones del mismo perfil se SERIALIZAN, y la que
      corre después lee la revisión vigente MÁS NUEVA — current_eval_id nunca
      retrocede a una revisión vieja por una carrera.
    - `move_current`: solo el evaluador CANÓNICO (primer (modelo, política)
      activo en orden determinista — lo decide la tarea) mueve
      current_eval_id; el resto corre en SOMBRA (append-only, sin tocar el
      estado) — con varios modelos el feed es determinista (auditoría A-08).
    Todo por lotes: 1 SELECT de candidatos + 1 INSERT append-only + 1
    re-select de ganadores + 1 UPSERT de estado (solo current_eval_id y
    updated_at)."""
    locked = (
        await session.execute(
            sa.text(
                "SELECT p.id, c.name AS consumer_name FROM profiles p "
                "JOIN consumers c ON c.id = p.consumer_id "
                "WHERE p.id = :pid FOR UPDATE OF p"
            ),
            {"pid": profile_id},
        )
    ).one_or_none()
    if locked is None:
        return {
            "status": "not_found", "evaluated": 0, "new_evals": 0,
            "moved_current": False,
        }
    policy_weights = (
        await session.execute(
            sa.text("SELECT weights FROM scoring_policies WHERE id = :id"),
            {"id": policy_id},
        )
    ).scalar_one_or_none()
    if not isinstance(policy_weights, dict):
        raise ValueError(f"política inexistente o weights inválidos: {policy_id}")
    algorithm = policy_weights.get("algorithm", "cosine")
    if algorithm == "hybrid_rrf":
        # v4+ (P1-A): la receta persistida manda; se valida ANTES de tocar nada.
        receta = _validated_recipe(policy_weights)
    elif algorithm in {"cosine", "hybrid_rrf_v1", "hybrid_rrf_v2"}:
        # Legacy congelado: el comportamiento de estas filas vive en el binario
        # (v1→1.15, v2/v3→0.25) y los goldens lo fijan. No se crean filas nuevas
        # con estos algoritmos: toda política híbrida nueva lleva receta.
        receta = None
    else:
        raise ValueError(f"algoritmo de matching no soportado: {algorithm}")

    prof = (
        await session.execute(
            sa.text(
                "SELECT cur.revision_id, pr.content, pe.vector::text AS vec "
                "FROM (SELECT DISTINCT ON (profile_id) profile_id, revision_id "
                "      FROM profile_revision_activations WHERE profile_id = :pid "
                "      ORDER BY profile_id, seq DESC) cur "
                "JOIN profile_revisions pr ON pr.id = cur.revision_id "
                "  AND pr.profile_id = cur.profile_id "
                "JOIN profile_embeddings pe "
                "  ON pe.profile_revision_id = cur.revision_id AND pe.model_id = :mid"
            ),
            {"pid": profile_id, "mid": model_id},
        )
    ).one_or_none()
    if prof is None:
        # Sin revisión vigente o sin vector para este modelo: nada que evaluar
        # (el worker de embeddings aún no pasó) — no es un error.
        return {
            "status": "sin_vector", "evaluated": 0, "new_evals": 0,
            "moved_current": False,
        }

    # ANN ROBUSTO (auditoría + rev. A-08 #2 + 2ª P2s): el filtro posterior
    # (revisión vigente + vacante activa) puede dejar el scan HNSW SIN
    # candidatos si los embeddings HISTÓRICOS/huérfanos más cercanos lo
    # consumen. Capas: ef_search en [40..1000] (rango válido del GUC; para
    # limit > 1000 cubre el fallback), iterative_scan strict_order (pgvector
    # >= 0.8: sigue escaneando hasta llenar el LIMIT tras el filtro, acotado
    # por MAX_SCAN_TUPLES) y FALLBACK EXACTO solo si el ANN devuelve menos
    # que el OBJETIVO REAL (conteo acotado de elegibles: un corpus menor que
    # limit NO dispara la segunda búsqueda en cada run — rev. 2ª P2#2).
    # Enteros validados (SET no admite binds).
    if limit < 1:
        raise ValueError(f"limit={limit}: el top-K debe ser >= 1")
    # ANTES de mirar el corpus: si algo cambia después, se registra la generación VIEJA y el
    # siguiente ciclo vuelve a coger el perfil. Al revés (registrar una posterior a lo evaluado)
    # se perdería trabajo en silencio.
    corpus_gen = (
        (await session.execute(sa.text(CORPUS_GENERATION_SQL))).scalar()
        if with_corpus_generation else None
    )
    eligible = (
        await session.execute(
            # MISMO fragmento que usa la señal de recuperación del proyector: duplicarlo permitiría
            # que "corpus elegible" significara cosas distintas en cada sitio.
            sa.text(
                "SELECT count(*) FROM (SELECT 1 "
                + ELIGIBLE_CORPUS_FROM.format(model=":mid")
                + " LIMIT :k) t"
            ),
            {"mid": model_id, "k": limit},
        )
    ).scalar_one()
    target = min(limit, int(eligible))
    if target == 0:
        return {
            "status": "ok", "evaluated": 0, "new_evals": 0, "moved_current": False,
            "profile_revision_id": prof.revision_id, "corpus_generation": corpus_gen,
        }
    if receta is not None:
        lex_query = _LEXICAL_QUERY_BUILDERS[receta["lexical_query"]](prof.content)
    elif algorithm == "hybrid_rrf_v2":
        lex_query = _lexical_query_v2(prof.content)
    elif algorithm == "hybrid_rrf_v1":
        lex_query = _lexical_query(prof.content)
    else:
        lex_query = ""
    hybrid = bool(lex_query)
    if receta is not None:
        candidate_sql = _hybrid_candidates_sql(receta["lexical_weight"])
    elif algorithm == "hybrid_rrf_v2":
        candidate_sql = HYBRID2_CANDIDATES_SQL
    elif hybrid:
        candidate_sql = HYBRID_CANDIDATES_SQL
    else:
        candidate_sql = CANDIDATES_SQL
    params = {
        "vec": prof.vec, "mid": model_id, "k": limit, "lex_query": lex_query,
    }
    ef_search = min(max(limit, 40), 1000)
    await session.execute(sa.text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
    await session.execute(sa.text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
    await session.execute(
        sa.text(f"SET LOCAL hnsw.max_scan_tuples = {int(MAX_SCAN_TUPLES)}")
    )
    candidates = (await session.execute(sa.text(candidate_sql), params)).all()

    if len(candidates) < target or not _semantic_arm_filled(
        candidates, target, hybrid
    ):
        # Inanición REAL del scan acotado: el exacto responde siempre bien.
        await session.execute(sa.text("SET LOCAL enable_indexscan = off"))
        await session.execute(sa.text("SET LOCAL enable_bitmapscan = off"))
        candidates = (await session.execute(sa.text(candidate_sql), params)).all()
        await session.execute(sa.text("SET LOCAL enable_indexscan = on"))
        await session.execute(sa.text("SET LOCAL enable_bitmapscan = on"))
    if not candidates:
        return {
            "status": "ok", "evaluated": 0, "new_evals": 0, "moved_current": False,
            "profile_revision_id": prof.revision_id, "corpus_generation": corpus_gen,
        }

    eval_rows = []
    for c in candidates:
        key = eval_key(c.offer_revision_id, prof.revision_id, model_id, policy_id)
        if hybrid:
            similarity = round(float(c.sim), 6) if c.sim is not None else None
            score = round(min(100.0, max(0.0, float(c.rank_score))), 2)
            score_parts = {
                "algorithm": algorithm,
                # Receta bajo la que se calculó ESTA fila: con ella una eval es
                # auditable sin reconstruir qué constante regía en el binario.
                **({"recipe": receta} if receta is not None else {}),
                "similarity": similarity,
                "semantic_rank": c.semantic_rank,
                "lexical_rank": c.lexical_rank,
                "lexical_score": (
                    round(float(c.lexical_score), 6)
                    if c.lexical_score is not None else None
                ),
            }
        else:
            score = round(max(0.0, float(c.sim)) * 100, 2)
            score_parts = {"similarity": round(float(c.sim), 6)}
        eval_rows.append(
            {
                "id": uuid.uuid4(), "pid": profile_id, "vid": c.vacancy_id,
                "orid": c.offer_revision_id, "prid": prof.revision_id,
                "mid": model_id, "spid": policy_id, "key": key,
                "score": score,
                "scores": json.dumps(score_parts),
            }
        )
    eval_rows.sort(key=lambda r: str(r["vid"]))  # orden determinista
    await session.execute(
        sa.text(
            "INSERT INTO match_evaluations "
            "(id, profile_id, vacancy_id, offer_revision_id, profile_revision_id, "
            " model_id, scoring_policy_id, eval_key, score_final, scores) "
            "VALUES (:id, :pid, :vid, :orid, :prid, :mid, :spid, :key, :score, "
            "CAST(:scores AS jsonb)) "
            "ON CONFLICT (profile_id, vacancy_id, eval_key) DO NOTHING"
        ),
        eval_rows,
    )
    # Ganadores REALES (idempotencia/carreras: la fila puede ser previa).
    winners = {
        (r.vacancy_id, r.eval_key): r.id
        for r in (
            await session.execute(
                sa.text(
                    "SELECT e.id, e.vacancy_id, e.eval_key FROM match_evaluations e "
                    "JOIN unnest(CAST(:vids AS uuid[]), CAST(:keys AS text[])) "
                    "  AS t(vid, k) ON e.vacancy_id = t.vid AND e.eval_key = t.k "
                    "WHERE e.profile_id = :pid"
                ),
                {
                    "pid": profile_id,
                    "vids": [str(r["vid"]) for r in eval_rows],
                    "keys": [r["key"] for r in eval_rows],
                },
            )
        ).all()
    }
    fresh = [r for r in eval_rows if winners.get((r["vid"], r["key"])) == r["id"]]
    new_evals = len(fresh)
    if fresh:
        # OUTBOX en la MISMA transacción que la escritura (A-10, ADR-05):
        # event_id determinista por eval_key + DO NOTHING = re-emisión
        # imposible; el estado de entrega va POR destino (ADR-06) — el BFF del
        # consumidor del perfil (§3). Payload = SOLO IDs (el consumidor
        # resuelve por /v1).
        events = sorted(
            (
                {
                    "eid": event_id_for("match.evaluated", r["key"]),
                    "agg": r["key"], "pid": profile_id,
                    "payload": json.dumps(
                        {
                            "eval_key": r["key"],
                            "profile_id": str(profile_id),
                            "vacancy_id": str(r["vid"]),
                        }
                    ),
                }
                for r in fresh
            ),
            key=lambda e: str(e["eid"]),
        )
        await session.execute(
            sa.text(
                "INSERT INTO integration_outbox "
                "(event_id, aggregate, aggregate_id, subject_profile_id, "
                " version, type, payload) "
                "VALUES (:eid, 'match_evaluation', :agg, :pid, 1, "
                "'match.evaluated', CAST(:payload AS jsonb)) "
                "ON CONFLICT (event_id) DO NOTHING"
            ),
            events,
        )
        await session.execute(
            sa.text(
                "INSERT INTO integration_outbox_deliveries "
                "(event_id, destination, next_attempt_at) "
                "VALUES (:eid, :dest, clock_timestamp()) "
                "ON CONFLICT (event_id, destination) DO NOTHING"
            ),
            [{"eid": e["eid"], "dest": locked.consumer_name} for e in events],
        )
    moved = False
    if move_current:
        # VALLA DE CANONICIDAD (P1-C, revisión externa 2026-09-02): la decisión
        # «soy el canónico» se tomó FUERA de esta transacción (la tarea lee las
        # políticas activas, cierra esa sesión y evalúa en transacciones
        # nuevas) y puede haber caducado: un worker pre-flip que retome aquí
        # tras una promoción restauraría el feed antiguo. Se reverifica en la
        # MISMA transacción que la escritura, con FOR SHARE sobre la fila
        # canónica: el flip actualiza TODAS las filas de scoring_policies en un
        # solo UPDATE (declare_active_policies), así que o bien espera a que
        # este movimiento termine, o bien ya cometió y esta lectura ve el
        # canónico nuevo y el movimiento se aborta. Un SELECT sin lock dejaría
        # el mismo TOCTOU con la ventana más corta.
        canonica = (
            await session.execute(
                sa.text(
                    "SELECT id FROM scoring_policies WHERE active "
                    "ORDER BY name, prompt_version LIMIT 1 FOR SHARE"
                )
            )
        ).scalar_one_or_none()
        if canonica is None or str(canonica) != str(policy_id):
            logger.warning(
                "matching: la política %s ya no es canónica (ahora %s) — la "
                "evaluación queda registrada pero el feed NO se mueve",
                policy_id, canonica,
            )
            move_current = False
    if move_current:
        state_rows = [
            {"pid": profile_id, "vid": r["vid"], "eid": winners[(r["vid"], r["key"])]}
            for r in eval_rows
            if (r["vid"], r["key"]) in winners
        ]
        if state_rows:
            # El feed representa el conjunto CANÓNICO de esta ejecución, no
            # la unión histórica de antiguos top-K. La interacción estable se
            # conserva en su fila; solo se retira el puntero de evaluación.
            await session.execute(
                sa.text(
                    "UPDATE profile_vacancy_state "
                    "SET current_eval_id = NULL, "
                    "updated_at = GREATEST(updated_at, clock_timestamp()) "
                    "WHERE profile_id = :pid AND current_eval_id IS NOT NULL "
                    "AND NOT (vacancy_id = ANY(CAST(:vids AS uuid[])))"
                ),
                {
                    "pid": profile_id,
                    "vids": [str(row["vid"]) for row in state_rows],
                },
            )
            # Estado: SOLO current_eval_id/updated_at — feedback/dismissed/
            # saved/notes se preservan SIEMPRE (ADR-03: estado estable).
            # clock_timestamp() + GREATEST (rev. A-08 #3): now() es la HORA DE
            # INICIO de la transacción — una tx vieja que escribe tarde jamás
            # debe hacer retroceder updated_at.
            await session.execute(
                sa.text(
                    "INSERT INTO profile_vacancy_state "
                    "(profile_id, vacancy_id, current_eval_id, updated_at) "
                    "VALUES (:pid, :vid, :eid, clock_timestamp()) "
                    "ON CONFLICT (profile_id, vacancy_id) DO UPDATE "
                    "SET current_eval_id = EXCLUDED.current_eval_id, "
                    "updated_at = GREATEST(profile_vacancy_state.updated_at, "
                    "clock_timestamp())"
                ),
                state_rows,
            )
            moved = True
    return {
        "status": "ok", "evaluated": len(eval_rows), "new_evals": new_evals,
        "moved_current": moved,
        # La revisión REALMENTE evaluada (la que se leyó bajo el lock del perfil): quien registre
        # el intento debe usar ESTA, no re-consultar la vigente (podría haber cambiado ya).
        "profile_revision_id": prof.revision_id,
        "corpus_generation": corpus_gen,
    }


async def feed(session, profile_id, limit: int = 20, cursor=None, consumer_id=None):
    """Feed del perfil (DoD A-08): evaluación VIGENTE + no-dismissed + vacante
    ACTIVA, keyset por (score_final DESC, vacancy_id ASC).

    `cursor` = (score_final, vacancy_id) de la última fila entregada; devuelve
    (filas, next_cursor) con next_cursor None al agotar. `consumer_id`
    (rev. A-09 #1): el OWNERSHIP multi-tenant se filtra EN LA QUERY (§2) —
    una reasignación de tenant a mitad de request jamás puede filtrar filas."""
    where_cursor = ""
    tenant_join = ""
    params = {"pid": profile_id, "lim": limit}
    if consumer_id is not None:
        tenant_join = "JOIN profiles p ON p.id = s.profile_id AND p.consumer_id = :cid "
        params["cid"] = consumer_id
    if cursor is not None:
        where_cursor = (
            "AND (e.score_final < :cs "
            "OR (e.score_final = :cs AND e.vacancy_id > :cv)) "
        )
        params["cs"], params["cv"] = cursor
    # Se pide una fila EXTRA (limit+1) para distinguir "hay página siguiente" de "esta es la
    # última": si el resto es EXACTAMENTE `limit`, devolver cursor daría una página siguiente
    # VACÍA (cursor fantasma, P3 rev. externa integral).
    params["lim"] = limit + 1
    rows = (
        await session.execute(
            sa.text(
                "SELECT e.vacancy_id, e.score_final, e.id AS eval_id, e.scores, "
                "e.offer_revision_id, s.saved_at, s.feedback, s.notes "
                "FROM profile_vacancy_state s "
                f"{tenant_join}"
                "JOIN match_evaluations e ON e.id = s.current_eval_id "
                "  AND e.profile_id = s.profile_id AND e.vacancy_id = s.vacancy_id "
                "JOIN vacancies v ON v.id = s.vacancy_id "
                "  AND v.archived_at IS NULL AND v.merged_into IS NULL "
                "WHERE s.profile_id = :pid AND s.dismissed_at IS NULL "
                f"{where_cursor}"
                "ORDER BY e.score_final DESC, e.vacancy_id ASC "
                "LIMIT :lim"
            ),
            params,
        )
    ).all()
    has_more = len(rows) > limit  # existió la fila extra → hay página siguiente REAL
    rows = rows[:limit]
    # Cursor SOLO si hay una fila más allá de las devueltas (con limit=0, rows=[] → None).
    next_cursor = (
        (rows[-1].score_final, rows[-1].vacancy_id) if has_more and rows else None
    )
    return rows, next_cursor


async def set_dismissed(session, profile_id, vacancy_id, dismissed: bool) -> None:
    """Descartar/restaurar: upsert que SOLO toca dismissed_at/updated_at.
    clock_timestamp() + GREATEST (rev. A-08 #3): la hora real de ESCRITURA,
    no la de inicio de la tx — sin retrocesos temporales entre tx solapadas."""
    await session.execute(
        sa.text(
            "INSERT INTO profile_vacancy_state "
            "(profile_id, vacancy_id, dismissed_at, updated_at) "
            "VALUES (:pid, :vid, CASE WHEN :d THEN clock_timestamp() END, "
            "clock_timestamp()) "
            "ON CONFLICT (profile_id, vacancy_id) DO UPDATE "
            "SET dismissed_at = CASE WHEN :d THEN clock_timestamp() END, "
            "updated_at = GREATEST(profile_vacancy_state.updated_at, clock_timestamp())"
        ),
        {"pid": profile_id, "vid": vacancy_id, "d": dismissed},
    )


async def set_saved(session, profile_id, vacancy_id, saved: bool) -> None:
    """Bookmark (saved = aquí, ADR-03): solo saved_at/updated_at — misma
    disciplina temporal que set_dismissed (rev. A-08 #3)."""
    await session.execute(
        sa.text(
            "INSERT INTO profile_vacancy_state "
            "(profile_id, vacancy_id, saved_at, updated_at) "
            "VALUES (:pid, :vid, CASE WHEN :sv THEN clock_timestamp() END, "
            "clock_timestamp()) "
            "ON CONFLICT (profile_id, vacancy_id) DO UPDATE "
            "SET saved_at = CASE WHEN :sv THEN clock_timestamp() END, "
            "updated_at = GREATEST(profile_vacancy_state.updated_at, clock_timestamp())"
        ),
        {"pid": profile_id, "vid": vacancy_id, "sv": saved},
    )

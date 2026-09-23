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


# ---------------------------------------------------------------- v5 rerank
# Señales deterministas de intención/compatibilidad (cierre v5, predeclaración
# 567daf7/6992d40). Léxicos ACOTADOS y VERSIONADOS: la receta los nombra y el
# evaluador valida que el binario los implementa. Dato fuera de léxico =
# NEUTRAL, jamás exclusión. Remote/Anywhere/Worldwide expresan modalidad o
# deseo, no permiso legal: no expanden el conjunto compatible.
_US_STATES = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota",
    "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
})
_EUROPE = frozenset({
    "switzerland", "spain", "france", "germany", "italy", "austria",
    "portugal", "greece", "netherlands", "belgium", "poland", "ireland",
    "united kingdom", "uk", "norway", "sweden", "denmark", "finland",
    "czech republic", "hungary", "romania", "bulgaria", "croatia",
    "slovakia", "slovenia", "estonia", "latvia", "lithuania", "luxembourg",
    "malta", "cyprus", "iceland", "ukraine", "serbia",
    "bosnia and herzegovina", "andorra", "monaco", "liechtenstein",
    "san marino", "albania", "north macedonia", "montenegro", "moldova",
    "kosovo", "belarus",
})
_COUNTRIES = _EUROPE | frozenset({
    "usa", "united states", "canada", "mexico", "brazil", "argentina",
    "colombia", "chile", "peru", "india", "philippines", "japan", "china",
    "australia", "new zealand", "turkey", "egypt", "kenya", "nigeria",
    "south africa", "israel", "singapore", "south korea", "vietnam",
    "indonesia", "thailand", "el salvador", "guatemala", "honduras",
    "costa rica", "panama", "ecuador", "uruguay", "paraguay", "bolivia",
    "venezuela", "dominican republic",
})
_GLOBAL_MARKERS = frozenset({
    "anywhere in the world", "international", "worldwide", "global",
    "anywhere", "remote",
})
# Ciudades → país SOLO para tokens del PERFIL (léxico acotado a las plazas
# que los perfiles reales declaran; una ciudad de oferta no parseada = neutral).
_CITY_TO_COUNTRY = {
    "geneva": "switzerland", "zurich": "switzerland", "basel": "switzerland",
    "bern": "switzerland", "lausanne": "switzerland",
    "valencia": "spain", "madrid": "spain", "barcelona": "spain",
}
_MODALITY_TOKENS = frozenset({"remote", "anywhere", "worldwide", "hybrid", "onsite"})
_TITLE_LANGS = frozenset({
    "english", "french", "german", "spanish", "portuguese", "italian",
    "dutch", "japanese", "chinese", "mandarin", "cantonese", "korean",
    "arabic", "russian", "polish", "turkish", "hebrew", "greek", "swedish",
    "norwegian", "danish", "finnish", "czech", "hungarian", "romanian",
    "ukrainian", "vietnamese", "thai", "indonesian", "hindi",
})
_GEO_LEXICONS = {"v1": True}
_LANG_LEXICONS = {"v1": True}


def _compatible_countries(locations) -> frozenset:
    """Países compatibles declarados por el perfil. Tokens de modalidad no
    cuentan; «Europe» expande al léxico europeo; ciudad del léxico → su país;
    lo demás se ignora (no restringe)."""
    out = set()
    for tok in locations or ():
        t = str(tok).strip().lower()
        if t in _MODALITY_TOKENS:
            continue
        if t == "europe":
            out |= _EUROPE
        elif t in _COUNTRIES:
            out.add("usa" if t == "united states" else t)
        elif t in _CITY_TO_COUNTRY:
            out.add(_CITY_TO_COUNTRY[t])
    return frozenset(out)


# Regex por entrada del léxico con frontera de palabra: «india» no dispara en
# «Indiana», y las entradas multi-palabra («bosnia and herzegovina») matchean
# en cualquier posición del texto. Compilado una vez al importar.
_PAISES_RE = {
    pais: re.compile(r"\b" + re.escape(pais) + r"\b")
    for pais in _COUNTRIES | _US_STATES
}
_USA_RE = re.compile(r"\(usa\)|\busa\b|\bunited states\b")


def _offer_countries(location) -> frozenset:
    """TODOS los países reconocidos en la restricción geográfica de la oferta
    (P2 revisión 2026-09-03: quedarse con el primero convertía
    «Germany / Switzerland» en incompatible para un perfil suizo). Conjunto
    vacío = neutral (marcador global, vacío o texto fuera de léxico)."""
    if not location:
        return frozenset()
    loc = str(location).strip().lower()
    out = set()
    if _USA_RE.search(loc):
        out.add("usa")
    for pais, patron in _PAISES_RE.items():
        if patron.search(loc):
            if pais in _US_STATES or pais == "united states":
                out.add("usa")
            else:
                out.add(pais)
    return frozenset(out)


def _title_language_requirement(titulo) -> tuple[str | None, frozenset]:
    """Requisito de idiomas del TÍTULO: (modo, idiomas) con modo ∈
    {"all", "any", None}.

    P2 revisión 2026-09-03: «English or Spanish» NO es un requisito de ambos.
    Solo se sostienen las formas explícitas: conectados por «or» ⇒ cualquiera
    («any»); por «&»/«and»/coma ⇒ todos («all»); un solo idioma ⇒ ese idioma;
    barra («English/French») o conectores mezclados ⇒ ambiguo ⇒ neutral
    (None). Solo el título: una mención en la descripción es incidental."""
    texto = (titulo or "").lower()
    hallados = []  # (posición, idioma) en orden de aparición
    for m in re.finditer(r"[a-zà-ÿ]+", texto):
        if m.group(0) in _TITLE_LANGS:
            idioma = m.group(0)
            if idioma in {"mandarin", "cantonese"}:
                idioma = "chinese"
            hallados.append((m.start(), m.end(), idioma))
    idiomas = frozenset(i for _, _, i in hallados)
    if not idiomas:
        return None, frozenset()
    if len(idiomas) == 1:
        return "all", idiomas
    # conectores ENTRE menciones consecutivas
    conectores = set()
    for (_, fin_a, _), (ini_b, _, _) in zip(hallados, hallados[1:]):
        entre = texto[fin_a:ini_b]
        if "/" in entre:
            conectores.add("/")
        elif re.search(r"\bor\b", entre):
            conectores.add("or")
        else:
            conectores.add("and")
    if conectores == {"or"}:
        return "any", idiomas
    if conectores == {"and"}:
        return "all", idiomas
    return None, idiomas  # barra o mezcla ⇒ ambiguo ⇒ neutral


def _pair_compatibility(titulo, location, remote, prefs) -> dict:
    """Compatibilidad DEMOSTRADA de una pareja (perfil, oferta) — fuente
    ÚNICA (P2-1): la usan el rerank v5 y el tier del cross-encoder.
    Desconocido/ambiguo = neutral; solo una incompatibilidad demostrable
    (remoto-only vs presencial, países conocidos y disjuntos, idioma exigido
    del título no acreditado) marca la pareja."""
    inc_loc = False
    remote_only = (prefs.get("remote_pref") == "remote_only")
    if remote_only and remote is False:
        inc_loc = True
    elif remote is True:
        paises = _offer_countries(location)
        compat = prefs.get("_compat")
        if compat is None:
            compat = _compatible_countries(prefs.get("locations"))
        if paises and compat and not (paises & compat):
            inc_loc = True
    faltan_idiomas: frozenset = frozenset()
    modo, req = _title_language_requirement(titulo)
    propios = {str(x).strip().lower() for x in prefs.get("languages") or ()}
    if req and propios and modo is not None:
        if modo == "all" and not req <= propios:
            faltan_idiomas = req - frozenset(propios)
        elif modo == "any" and not (req & propios):
            faltan_idiomas = req
    return {"inc_loc": inc_loc, "lang_missing": sorted(faltan_idiomas)}


def _rerank_score(base, role_sim, titulo, location, remote, prefs, receta):
    """Puntuación v5 de UN candidato (pura y determinista).

    base = rank_score RRF; señales según la receta (0 = señal apagada).
    Dato ausente/ambiguo ⇒ neutral. Devuelve (score, componentes)."""
    sim = float(role_sim or 0.0)
    theta = receta["role_theta"]
    exceso = max(0.0, sim - theta)
    s = float(base)
    comp = {"base_rrf": round(float(base), 4), "role_sim": round(sim, 4)}
    if receta["role_w"]:
        s *= 1 + receta["role_w"] * exceso
    compat = _pair_compatibility(titulo, location, remote, prefs)
    inc_loc = compat["inc_loc"] if receta["p_loc"] else False
    if inc_loc:
        s *= 1 - receta["p_loc"]
    faltan_idiomas = compat["lang_missing"] if receta["p_lang"] else []
    if faltan_idiomas:
        s *= 1 - receta["p_lang"]
    if receta["role_a"]:
        s += 100.0 * receta["role_a"] * exceso
    comp |= {"loc_incompatible": inc_loc,
             "lang_missing": list(faltan_idiomas)}
    return s, comp


def _rerank_scale(receta) -> float:
    """Máximo teórico de la puntuación v5 — el score persistido se normaliza
    con él a 0..100 (contrato NUMERIC(6,2)); escala monotónica: no cambia el
    orden. Sin él, la familia aditiva saturaría el clamp en 100 y empataría
    los primeros puestos en silencio."""
    return (100.0 * (1 + receta["role_w"] * (1 - receta["role_theta"]))
            + 100.0 * receta["role_a"] * (1 - receta["role_theta"]))


# Versiones de consulta léxica que una receta v4+ puede nombrar. La receta
# VALIDA contra la implementación: nombrar una versión que el binario no
# implementa es error, no silencio.
_LEXICAL_QUERY_BUILDERS = {"v2": None}  # se rellena tras definir las funciones


_RERANK_SIGNALS_SQL = (
    "SELECT o.id AS orid, o.content->>'title' AS titulo, "
    "o.content->>'location' AS location, o.content->>'remote' AS remote, "
    "(SELECT max(similarity(lower(coalesce(o.content->>'title','')), t.t)) "
    " FROM unnest(CAST(:targets AS text[])) AS t(t)) AS role_sim "
    "FROM offer_revisions o WHERE o.id = ANY(CAST(:orids AS uuid[]))"
)


def _validated_rerank_recipe(policy_weights: dict) -> dict:
    """Receta de una política ``hybrid_rrf_rerank`` (v5+): TODOS los
    parámetros que cambian el resultado viven en la fila. Señal con valor 0 =
    apagada; léxicos nombrados y validados contra el binario."""
    esperadas = {
        "algorithm", "lexical_query", "lexical_weight", "rrf_k", "rerank",
        "role_theta", "role_w", "role_a", "p_loc", "p_lang",
        "geo_lexicon", "lang_lexicon",
    }
    claves = set(policy_weights)
    if claves != esperadas:
        raise ValueError(
            f"receta hybrid_rrf_rerank inválida: claves {sorted(claves)}, "
            f"esperadas {sorted(esperadas)}"
        )
    base = _validated_recipe(
        {k: policy_weights[k]
         for k in ("lexical_query", "lexical_weight", "rrf_k")}
        | {"algorithm": "hybrid_rrf"}
    )
    if policy_weights["rerank"] != "v1":
        raise ValueError(
            f"receta: rerank {policy_weights['rerank']!r} no implementado")
    if policy_weights["geo_lexicon"] not in _GEO_LEXICONS:
        raise ValueError(
            f"receta: geo_lexicon {policy_weights['geo_lexicon']!r} desconocido")
    if policy_weights["lang_lexicon"] not in _LANG_LEXICONS:
        raise ValueError(
            f"receta: lang_lexicon {policy_weights['lang_lexicon']!r} desconocido")
    for campo, lo, hi in (("role_theta", 0.0, 1.0), ("role_w", 0.0, 100.0),
                          ("role_a", 0.0, 100.0), ("p_loc", 0.0, 0.999),
                          ("p_lang", 0.0, 0.999)):
        v = policy_weights[campo]
        if not isinstance(v, (int, float)) or isinstance(v, bool) \
                or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(
                f"receta: {campo}={v!r} fuera de rango [{lo}, {hi}]")
    return dict(policy_weights, algorithm="hybrid_rrf_rerank",
                lexical_weight=base["lexical_weight"])


def _validated_cross_encoder_recipe(policy_weights: dict) -> dict:
    """Receta de una política ``cross_encoder`` (Fase 2 cierre definitivo):
    modelo, revisión CLAVADA, huella de artefactos, versión de entrada,
    activación y backend forman parte de la receta — un cambio es otra
    versión. La recuperación de candidatos reutiliza la del híbrido v4
    (lexical_query/lexical_weight/rrf_k), validada contra el binario."""
    esperadas = {
        "algorithm", "model", "model_revision", "model_fingerprint",
        "input", "activation", "backend",
        "lexical_query", "lexical_weight", "rrf_k",
    }
    claves = set(policy_weights)
    if policy_weights.get("algorithm") == "cross_encoder_tier":
        # P2-1: el tier añade la regla de compatibilidad a la receta.
        esperadas = esperadas | {"compat_rule"}
        if policy_weights.get("compat_rule") != "tier-v1":
            raise ValueError(
                f"receta: compat_rule {policy_weights.get('compat_rule')!r} "
                "no implementada (soportada: tier-v1)")
    # Un modelo FINE-TUNED (v3+) añade la procedencia del entrenamiento; el
    # resto de la receta es idéntico. Sin esa clave, el modelo debe ser de hub.
    finetuned = "train_data_sha256" in claves
    if finetuned:
        esperadas = esperadas | {"train_data_sha256"}
    if claves != esperadas:
        raise ValueError(
            f"receta cross_encoder inválida: claves {sorted(claves)}, "
            f"esperadas {sorted(esperadas)}"
        )
    if finetuned:
        tds = policy_weights["train_data_sha256"]
        if not (isinstance(tds, str) and re.fullmatch(r"[0-9a-f]{64}", tds)):
            raise ValueError(
                f"receta: train_data_sha256 {tds!r} debe ser sha256")
        if not str(policy_weights["model"]).startswith("/"):
            raise ValueError(
                "receta fine-tuned: model debe ser la RUTA del artefacto "
                "local sellado por model_fingerprint")
    elif str(policy_weights["model"]).startswith("/"):
        raise ValueError(
            "receta: un modelo local exige train_data_sha256 (procedencia)")
    _validated_recipe(
        {k: policy_weights[k]
         for k in ("lexical_query", "lexical_weight", "rrf_k")}
        | {"algorithm": "hybrid_rrf"}
    )
    from jobhunt_core import cross_encoder as ce

    if policy_weights["input"] != ce.INPUT_VERSION:
        raise ValueError(
            f"receta: input {policy_weights['input']!r} no implementado "
            f"(binario: {ce.INPUT_VERSION})"
        )
    if policy_weights["activation"] not in ce.ACTIVATIONS:
        raise ValueError(
            f"receta: activation {policy_weights['activation']!r} no soportada")
    if policy_weights["backend"] != ce.BACKEND:
        raise ValueError(
            f"receta: backend {policy_weights['backend']!r} no soportado")
    modelo = policy_weights["model"]
    if not isinstance(modelo, str) or not modelo.strip():
        raise ValueError("receta: model vacío")
    rev = policy_weights["model_revision"]
    if not (isinstance(rev, str) and re.fullmatch(r"[0-9a-f]{40}", rev)):
        raise ValueError(
            f"receta: model_revision {rev!r} debe ser un SHA de 40 hex")
    huella = policy_weights["model_fingerprint"]
    if not (isinstance(huella, str) and re.fullmatch(r"[0-9a-f]{64}", huella)):
        raise ValueError(
            f"receta: model_fingerprint {huella!r} debe ser sha256 (64 hex)")
    return dict(policy_weights)


_CE_DOCS_SQL = (
    "SELECT o.id AS orid, o.content->>'title' AS titulo, "
    "o.content->>'location' AS location, "
    "o.content->>'remote' AS remote, "
    "o.content->>'description' AS descripcion "
    "FROM offer_revisions o WHERE o.id = ANY(CAST(:orids AS uuid[]))"
)

_CE_CACHE_SQL = (
    "SELECT offer_revision_id, score_final, scores FROM match_evaluations "
    "WHERE profile_id = :pid AND scoring_policy_id = :spid "
    "  AND profile_revision_id = :prid AND model_id = :mid "
    "  AND offer_revision_id = ANY(CAST(:orids AS uuid[]))"
)


async def _ce_prepare(
    session, candidates, prof, profile_id, model_id, policy_id, receta_ce,
):
    """FASE de preparación del cross-encoder (P1-3): TODAS las lecturas de BD
    (cache por identidad absoluta + documentos de los misses + consultas),
    SIN inferencia. El resultado es autosuficiente para puntuar sin BD."""
    from jobhunt_core import cross_encoder as ce

    orids = [str(c.offer_revision_id) for c in candidates]
    cache = {
        r.offer_revision_id: r
        for r in (
            await session.execute(
                sa.text(_CE_CACHE_SQL),
                {"pid": profile_id, "spid": policy_id,
                 "prid": prof.revision_id, "mid": model_id, "orids": orids},
            )
        ).all()
    }
    misses = [c for c in candidates if c.offer_revision_id not in cache]
    documentos = []
    docs_meta = {}
    if misses:
        docs_meta = {
            r.orid: r
            for r in (
                await session.execute(
                    sa.text(_CE_DOCS_SQL),
                    {"orids": [str(c.offer_revision_id) for c in misses]},
                )
            ).all()
        }
        for c in misses:
            m = docs_meta.get(c.offer_revision_id)
            if m is None:
                raise ValueError(
                    f"cross-encoder sin documento para offer_revision "
                    f"{c.offer_revision_id}"
                )
            documentos.append(
                ce.build_document(m.titulo, m.location, m.descripcion)
            )
    return {
        "candidates": candidates, "cache": cache, "misses": misses,
        "documentos": documentos,
        "docs_meta": docs_meta if misses else {},
        "consultas": ce.build_queries(prof.content),
        "receta_ce": receta_ce,
        # prefs para la compatibilidad del TIER (parte de la identidad de la
        # pareja: viven en la revisión del perfil)
        "prefs": {
            "languages": prof.content.get("languages"),
            "locations": prof.content.get("locations"),
            "remote_pref": prof.content.get("remote_pref"),
        },
    }


def _ce_score_misses(prep) -> dict:
    """FASE de inferencia (P1-3): CPU pura, SIN sesión de BD ni transacción —
    ejecutable fuera del event loop. Un fallo propaga y nada se persiste."""
    from jobhunt_core import cross_encoder as ce

    receta_ce = prep["receta_ce"]
    if not prep["misses"]:
        return {}
    probs = ce.score_documents(
        receta_ce["model"], receta_ce["model_revision"],
        prep["consultas"], prep["documentos"],
        activation=receta_ce["activation"],
        # Identidad EFECTIVA (P1-1): huella verificada al cargar; batch
        # OPERATIVO del benchmark (P3-1).
        fingerprint=receta_ce["model_fingerprint"],
        batch_size=ce.CE_BATCH_SIZE,
    )
    return {c.offer_revision_id: p for c, p in zip(prep["misses"], probs)}


def _ce_assemble(prep, frescos) -> list:
    """FASE de ensamblado: filas del feed desde caché + inferencias, en orden
    de feed. Pura."""
    receta_ce = prep["receta_ce"]
    cache = prep["cache"]
    rows = []
    for c in prep["candidates"]:
        cacheada = cache.get(c.offer_revision_id)
        if cacheada is not None:
            # Verbatim: mismo score y mismos componentes que ya sirvió/serviría
            # el feed — cache hit sin invocar el modelo.
            rows.append({
                "vacancy_id": c.vacancy_id,
                "offer_revision_id": c.offer_revision_id,
                "score": float(cacheada.score_final),
                "score_parts": cacheada.scores,
            })
            continue
        prob = frescos[c.offer_revision_id]
        algoritmo = receta_ce["algorithm"]
        parts = {
            "algorithm": algoritmo,
            "recipe": receta_ce,
            "ce_prob": round(prob, 6),
            "similarity": (
                round(float(c.sim), 6) if c.sim is not None else None
            ),
            "semantic_rank": c.semantic_rank,
            "lexical_rank": c.lexical_rank,
        }
        if algoritmo == "cross_encoder_tier":
            m = prep["docs_meta"][c.offer_revision_id]
            compat = _pair_compatibility(
                m.titulo, m.location,
                {"true": True, "false": False}.get(m.remote),
                prep["prefs"],
            )
            tier = 0 if (compat["inc_loc"] or compat["lang_missing"]) else 1
            score = round(50.0 * tier + 49.99 * prob, 2)
            parts |= {"tier": tier, "compat": compat}
        else:
            score = round(prob * 100.0, 2)
        rows.append({
            "vacancy_id": c.vacancy_id,
            "offer_revision_id": c.offer_revision_id,
            "score": score,
            "score_parts": parts,
        })
    rows.sort(key=lambda r: (-r["score"], str(r["vacancy_id"])))
    return rows


async def _cross_encoder_rows(
    session, candidates, prof, profile_id, model_id, policy_id, receta_ce,
):
    """Camino de UNA fase (medición/dev): preparar + puntuar + ensamblar en la
    misma sesión. La evaluación productiva usa las fases por separado."""
    prep = await _ce_prepare(
        session, candidates, prof, profile_id, model_id, policy_id, receta_ce)
    return _ce_assemble(prep, _ce_score_misses(prep))


async def _rerank_candidates(session, candidates, content, receta):
    """Aplica el rerank v5 sobre el conjunto YA recuperado: UNA consulta de
    señales para todo el lote (sin N+1) + puntuación pura por candidato.
    Devuelve filas ordenadas (score DESC, vacancy ASC) con sus componentes."""
    targets = [
        t.strip().lower()
        for t in [content.get("title") or ""] + list(content.get("skills") or ())
        if t and t.strip()
    ]
    if not targets:
        targets = [""]
    orids = [str(c.offer_revision_id) for c in candidates]
    señales = {
        r.orid: r
        for r in (
            await session.execute(
                sa.text(_RERANK_SIGNALS_SQL),
                {"targets": targets, "orids": orids},
            )
        ).all()
    }
    prefs = {
        "languages": content.get("languages"),
        "remote_pref": content.get("remote_pref"),
        "_compat": _compatible_countries(content.get("locations")),
    }
    escala = _rerank_scale(receta)
    out = []
    for c in candidates:
        sig = señales.get(c.offer_revision_id)
        if sig is None:
            raise ValueError(
                f"rerank sin señales para offer_revision {c.offer_revision_id}"
            )
        remoto = {"true": True, "false": False}.get(sig.remote)
        bruto, comp = _rerank_score(
            c.rank_score, sig.role_sim, sig.titulo, sig.location, remoto,
            prefs, receta,
        )
        out.append({
            "vacancy_id": c.vacancy_id,
            "offer_revision_id": c.offer_revision_id,
            "sim": c.sim, "semantic_rank": c.semantic_rank,
            "lexical_rank": c.lexical_rank, "lexical_score": c.lexical_score,
            "score": round(min(100.0, max(0.0, bruto / escala * 100.0)), 2),
            "rerank": comp,
        })
    out.sort(key=lambda r: (-r["score"], str(r["vacancy_id"])))
    return out


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


# Política cross-encoder (Fase 2 cierre definitivo). El NOMBRE ordena
# después de cosine-baseline y hybrid-rrf a propósito: activarla junto a la
# canónica la deja en SOMBRA (el orden productivo es (name, prompt_version)).
# La huella es sha256 del listado canónico «hash  fichero» de los artefactos
# que carga el runtime (config, safetensors, sentencepiece, tokenizer*).
XENC_POLICY_NAME = "xenc-mmarco"
# v2 (2026-09-03): misma receta que v1 salvo activation=sigmoid_t4 — la plana
# empataba el top de consultas anchas en 99.99 bajo NUMERIC(6,2) y el feed
# habría ordenado por vacancy_id. Monótona: el ranking del modelo es idéntico;
# v1 queda como historia inactiva.
XENC_POLICY_VERSION = "v1"
XENC_POLICY_WEIGHTS = {
    "algorithm": "cross_encoder",
    "model": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    "model_revision": "1427fd652930e4ba29e8149678df786c240d8825",
    "model_fingerprint": "0ef69f89417a9e60658765c534fb128b5e36cf1dde1243d1270b46f60ecc20a0",
    "input": "v1",
    "activation": "sigmoid",
    "backend": "torch-cpu",
    "lexical_query": "v2",
    "lexical_weight": 0.25,
    "rrf_k": 60,
}

# Fuente ÚNICA de metadatos por algoritmo (Fase 1 del cierre definitivo
# 2026-09-03): un score es ABSOLUTO POR PAREJA si depende solo de (revisión de
# perfil, revisión de oferta, modelo, receta) — jamás del resto del lote o del
# corpus. Los relativos (rangos RRF, normalizaciones por máximo del lote) NO
# pueden ser canónicos con la identidad append-only actual: tras un cambio de
# corpus, eval_key conserva el score viejo (ON CONFLICT DO NOTHING) y winners
# publicaría en el feed una mezcla de generaciones que ninguna ejecución
# produjo. En SOMBRA siguen permitidos (medición, historia). La propiedad vive
# AQUÍ, en código, por algoritmo — no es un booleano libre de la receta.
XENC2_POLICY_VERSION = "v2"
XENC2_POLICY_WEIGHTS = dict(XENC_POLICY_WEIGHTS, activation="sigmoid_t4")

# P2-1 (revisión 2026-09-03): el CE puro ordena por afinidad temática e
# ignora restricciones que el código YA detecta (el etiquetado ciego CE2 lo
# atribuyó a EE. UU./Canadá e idiomas no acreditados). El TIER es la regla
# PREDECLARADA, sin rejilla: nivel 1 = viable o desconocida, nivel 0 =
# incompatible DEMOSTRADA (_pair_compatibility, fuente única); dentro de cada
# nivel manda ce_prob. score = 50·tier + 49.99·ce_prob — absoluto por pareja
# (perfil-revisión + oferta-revisión + receta), estable ante el lote.
XENC_TIER_POLICY_NAME = "xtier-mmarco"
XENC_TIER_POLICY_VERSION = "v1"
XENC_TIER_POLICY_WEIGHTS = dict(
    XENC2_POLICY_WEIGHTS, algorithm="cross_encoder_tier",
    compat_rule="tier-v1",
)

# P7-b (predeclaración 3d21bb4): candidata a examen — RankNet n=436
# (dev 0.9872/0.8604 con cobertura 100 %, ronda 3) exportada a ONNX con
# paridad 6e-06 verificada por el camino real. El backend onnx-cpu es parte
# de la receta (P1-1: clave del motor) y es el MISMO en bootstrap externo,
# examen y NAS — scores idénticos por construcción. El artefacto vive en la
# ruta canónica del contenedor y su huella lo sella; la materialización va
# por watermark (materialize_misses) con presupuesto predeclarado.
XENC_RANKNET_POLICY_NAME = "xenc-ranknet"
XENC_RANKNET_POLICY_VERSION = "v1"
XENC_RANKNET_POLICY_WEIGHTS = dict(
    XENC2_POLICY_WEIGHTS,
    model="/models/xenc-ranknet-n436",
    model_fingerprint=(
        "58483c94c6f579a6219c3b5fbeef992ed85542f2d8c457bf1e5b7fc83e9eae01"),
    backend="onnx-cpu",
    train_data_sha256=(
        "25cdd3a9037c13014188ec23ad19a1d7f6929920522875c54a3b3c57e526e01b"),
)

_ALGORITHM_PAIR_ABSOLUTE = {
    "cosine": True,
    "hybrid_rrf_v1": False,
    "hybrid_rrf_v2": False,
    "hybrid_rrf": False,
    "hybrid_rrf_rerank": False,
    # cross_encoder (Fase 2): probabilidad sigmoide por pareja — depende solo
    # de (consulta del perfil, documento de la oferta, modelo, receta).
    "cross_encoder": True,
    # tier = CE (absoluto) + compatibilidad por pareja (absoluta): promovible.
    "cross_encoder_tier": True,
}


def _algorithm_of(policy_weights: dict) -> str:
    return policy_weights.get("algorithm", "cosine")


def _is_pair_absolute(policy_weights: dict) -> bool:
    algo = _algorithm_of(policy_weights)
    if algo not in _ALGORITHM_PAIR_ABSOLUTE:
        # algoritmo desconocido = NO promovible (fail closed)
        return False
    return _ALGORITHM_PAIR_ABSOLUTE[algo]


# Catálogo de políticas conocidas (P1-D): las FILAS que todo entorno debe
# tener, con sus recetas canónicas. El bootstrap las asegura sin tocar la
# activación; qué está activo lo decide SOLO declare_active_policies.
POLICY_CATALOG = (
    ("cosine-baseline", "v1", {}),
    (HYBRID_POLICY_NAME, HYBRID_POLICY_VERSION, HYBRID_POLICY_WEIGHTS),
    (HYBRID_POLICY_NAME, HYBRID2_POLICY_VERSION, HYBRID2_POLICY_WEIGHTS),
    (HYBRID_POLICY_NAME, "v3", HYBRID2_POLICY_WEIGHTS),
    (HYBRID_POLICY_NAME, HYBRID4_POLICY_VERSION, HYBRID4_POLICY_WEIGHTS),
    (XENC_POLICY_NAME, XENC_POLICY_VERSION, XENC_POLICY_WEIGHTS),
    (XENC_POLICY_NAME, XENC2_POLICY_VERSION, XENC2_POLICY_WEIGHTS),
    (XENC_TIER_POLICY_NAME, XENC_TIER_POLICY_VERSION,
     XENC_TIER_POLICY_WEIGHTS),
    (XENC_RANKNET_POLICY_NAME, XENC_RANKNET_POLICY_VERSION,
     XENC_RANKNET_POLICY_WEIGHTS),
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
    # Valla de canonicidad ABSOLUTA (Fase 1 cierre definitivo): la política
    # que resultaría canónica según el ORDEN PRODUCTIVO (la primera por
    # (name, prompt_version) — el mismo ORDER BY de tasks/matching) no puede
    # tener score relativo al lote. Las relativas solo como sombra detrás de
    # una canónica absoluta.
    canonica = (
        await session.execute(
            sa.text(
                "SELECT name, prompt_version, weights FROM scoring_policies "
                "WHERE active ORDER BY name, prompt_version LIMIT 1"
            )
        )
    ).one()
    if not _is_pair_absolute(canonica.weights):
        raise ValueError(
            f"la canónica resultante {canonica.name}:{canonica.prompt_version} "
            f"tiene score RELATIVO al lote "
            f"(algorithm={_algorithm_of(canonica.weights)!r}): tras un cambio "
            "de corpus el feed serviría una mezcla de generaciones — "
            "las relativas solo pueden ser sombra"
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
_CANDIDATE_ELIGIBILITY = "WHERE v.archived_at IS NULL AND v.merged_into IS NULL"


def _with_candidate_exclusions(sql: str, *, exclude_dismissed: bool,
                               exclude_ids: bool,
                               apply_profile_exclusions: bool = True) -> str:
    """Frontera ÚNICA de exclusión de candidatos (revisión externa
    2026-09-07): los predicados se inyectan en el WHERE de elegibilidad —
    ANTES de los LIMIT de TODOS los brazos (ANN y léxico) y de la preparación
    del cross-encoder.

    Por qué en SQL y no después: filtrando DESPUÉS de recuperar, el excluido
    consume una plaza del top-K y un candidato elegible NUNCA se recupera; el
    universo medido deja de ser el declarado. Ese fue el defecto del examen
    del 2026-09-06. Además el camino CE retornaba en `ok_prep` sin llegar
    siquiera al filtro posterior: la exclusión se ignoraba en silencio.

    Se cuenta el ancla antes de tocarla (misma disciplina que
    `_hybrid_candidates_sql`): una derivación que deja de casar no puede
    fallar en silencio."""
    extra = ""
    if exclude_dismissed:
        from jobhunt_core.feedback import effective_feedback_sql

        extra += (
            " AND COALESCE((" + effective_feedback_sql("v.id", ":pid")
            + "),'') NOT IN ('thumbs_down','dismissed')"
        )
    if exclude_ids:
        extra += " AND NOT (v.id = ANY(CAST(:excl_ids AS uuid[])))"
    if apply_profile_exclusions:
        # Exclusiones del PERFIL (core0041). Semántica idéntica a la del
        # legacy: título = subcadena LITERAL case-insensitive (los comodines
        # del patrón se escapan); tag = igualdad case-insensitive con algún
        # elemento del array (sin tags NO excluye).
        extra += (
            " AND NOT EXISTS (SELECT 1 FROM profile_exclusions pex "
            "WHERE pex.profile_id = :pid AND ("
            "  (pex.kind = 'title_contains' AND COALESCE(orv.content->>'title','') "
            "     ILIKE '%' || replace(replace(replace(pex.pattern, '\\', '\\\\'),"
            "                                  '%', '\\%'), '_', '\\_') || '%' "
            "     ESCAPE '\\')"
            "  OR (pex.kind = 'tag_contains' AND EXISTS ("
            "        SELECT 1 FROM jsonb_array_elements_text("
            "          CASE WHEN jsonb_typeof(orv.content->'tags') = 'array' "
            "               THEN orv.content->'tags' ELSE '[]'::jsonb END) tg "
            "        WHERE lower(tg) = lower(pex.pattern)))"
            "))"
        )
    if not extra:
        return sql
    if sql.count(_CANDIDATE_ELIGIBILITY) == 0:
        raise RuntimeError(
            "SQL de candidatos sin ancla de elegibilidad: la exclusión NO se "
            "aplicaría y el universo recuperado sería otro"
        )
    return sql.replace(_CANDIDATE_ELIGIBILITY,
                       _CANDIDATE_ELIGIBILITY + extra)


CORPUS_GENERATION_SQL = "SELECT generation FROM corpus_generation WHERE id = 1"

# Seam de tests para interleavings de publicación (None en producción):
# corrutina invocada en F3 entre la revalidación (locks tomados) y la
# escritura. Se inyecta con set_after_revalidation_hook.
_after_revalidation = None


def set_after_revalidation_hook(hook) -> None:
    global _after_revalidation
    _after_revalidation = hook


VALID_EXCLUSION_KINDS = ("title_contains", "tag_contains")


async def declare_profile_exclusions(session, profile_id, reglas) -> dict:
    """Declara el conjunto EXACTO de exclusiones de un perfil: autoridad
    ÚNICA de esa configuración (revisión externa 2026-09-07, hallazgo B).

    Declarativa como `declare_active_policies`: recibe el conjunto completo,
    de modo que ALTAS y BAJAS viajan por el mismo camino — un importador que
    solo inserta jamás puede proyectar una baja. Idempotente y atómica: el
    borrado y la inserción van en la misma transacción del llamador.

    `reglas`: iterable de {kind, pattern}. kind fuera del contrato o patrón
    vacío ⇒ error (falla cerrado: una regla malformada silenciada dejaría de
    excluir sin que nadie se entere).
    """
    normalizadas = []
    for r in reglas or []:
        kind = (r or {}).get("kind")
        patron = ((r or {}).get("pattern") or "").strip()
        if kind not in VALID_EXCLUSION_KINDS or not patron:
            raise ValueError(
                f"exclusión inválida {r!r}: kind ∈ {VALID_EXCLUSION_KINDS} y "
                "pattern no vacío")
        normalizadas.append({"kind": kind, "pattern": patron})
    # Same lock order as publication: profile -> generation -> writes.
    owner = (await session.execute(
        sa.text("SELECT id FROM profiles WHERE id = :p FOR UPDATE"),
        {"p": profile_id},
    )).scalar_one_or_none()
    if owner is None:
        raise ValueError("perfil no encontrado")
    actuales = (await session.execute(
        sa.text("SELECT kind, pattern FROM profile_exclusions WHERE profile_id = :p"),
        {"p": profile_id},
    )).all()
    if {(r.kind, r.pattern) for r in actuales} == {
        (r["kind"], r["pattern"]) for r in normalizadas
    }:
        return {"declaradas": len(normalizadas)}
    await session.execute(
        sa.text("DELETE FROM profile_exclusions WHERE profile_id = :p"),
        {"p": profile_id},
    )
    if normalizadas:
        await session.execute(
            sa.text(
                "INSERT INTO profile_exclusions (profile_id, kind, pattern) "
                "VALUES (:p, :k, :pat) ON CONFLICT DO NOTHING"
            ),
            [{"p": profile_id, "k": r["kind"], "pat": r["pattern"]}
             for r in normalizadas],
        )
    return {"declaradas": len(normalizadas)}


async def canonical_model_id(session, profile_id):
    """Definición ÚNICA del modelo CANÓNICO efectivo para publicar el feed de
    un perfil (revisión 2026-09-04 1B): el primer modelo ACTIVO en el orden
    productivo determinista (active_models) con dimensión compatible, la
    revisión VIGENTE del perfil embebida y corpus elegible no vacío. La tarea
    decide con esta función quién publica y la valla final de
    evaluate_profile la recomputa bajo el lock y compara el id EXACTO — «el
    modelo sigue activo» no basta: activar uno anterior en el orden cambia el
    canónico aunque el usado siga activo."""
    from jobhunt_core import embeddings as _emb

    rev = (
        await session.execute(
            sa.text(
                "SELECT revision_id FROM profile_revision_activations "
                "WHERE profile_id = :pid ORDER BY seq DESC LIMIT 1"
            ),
            {"pid": profile_id},
        )
    ).scalar_one_or_none()
    if rev is None:
        return None
    for m in await _emb.active_models(session):
        if m.dim != _emb.EMBED_DIM:
            continue
        # Corpus ELEGIBLE de verdad (revisión externa 2026-09-07, P2): un
        # `EXISTS` sobre offer_embeddings contaba embeddings de vacantes
        # archivadas o fusionadas — podía elegir un modelo sin corpus servible
        # y dejar en sombra al que sí lo tiene. Se reutiliza la fuente única.
        elegible = (
            await session.execute(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM profile_embeddings "
                    " WHERE model_id = :m AND profile_revision_id = :r) "
                    "AND EXISTS (SELECT 1 "
                    + ELIGIBLE_CORPUS_FROM.format(model=":m") + ")"
                ),
                {"m": m.id, "r": rev},
            )
        ).scalar_one()
        if elegible:
            return m.id
    return None


async def compute_policy_feed(
    session, profile_id, model_id, policy_id, limit: int = CANONICAL_EVAL_LIMIT,
    exclude_dismissed: bool = False, with_corpus_generation: bool = False,
    exclude_vacancy_ids: list | None = None,
    ce_inference: bool = True,
) -> dict:
    """Feed ACTUAL de una política: el ranking que produciría una ejecución
    completa AHORA, calculado y devuelto SIN persistir nada ni mover estado.

    P1 de la revisión externa 2026-09-03: eval_key no lleva generación del
    corpus y los algoritmos RRF/rerank persisten rangos RELATIVOS al corpus —
    tras una cosecha, el almacén append-only conserva scores viejos
    (ON CONFLICT DO NOTHING) y su unión histórica es un ranking que ninguna
    ejecución produjo (feed 1800→1814 observado). Este cálculo es la ÚNICA
    fuente de verdad del feed de una política: la evaluación productiva
    persiste su resultado y la medición de desarrollo lo mide directamente.

    - `exclude_dismissed`: aplica la MISMA semántica que el feed canónico
      (una vacante con dismissed_at no cuenta; una sin fila de estado sí).
    - Orden devuelto = el del feed: (score DESC, vacancy_id ASC) sobre el
      score final REDONDEADO — no el orden bruto del SQL.
    Devuelve {"status", "rows", "profile_revision_id", "corpus_generation"}.
    """
    policy_weights = (
        await session.execute(
            sa.text("SELECT weights FROM scoring_policies WHERE id = :id"),
            {"id": policy_id},
        )
    ).scalar_one_or_none()
    if not isinstance(policy_weights, dict):
        raise ValueError(f"política inexistente o weights inválidos: {policy_id}")
    algorithm = policy_weights.get("algorithm", "cosine")
    receta_ce = None
    if algorithm == "hybrid_rrf":
        # v4+ (P1-A): la receta persistida manda; se valida ANTES de tocar nada.
        receta = _validated_recipe(policy_weights)
    elif algorithm == "hybrid_rrf_rerank":
        # v5: candidatos de v4 + rerank determinista por señales de la receta.
        receta = _validated_rerank_recipe(policy_weights)
    elif algorithm in {"cross_encoder", "cross_encoder_tier"}:
        # Fase 2 cierre definitivo: recuperación híbrida + score ABSOLUTO por
        # pareja del cross-encoder local (receta con modelo/revisión/huella).
        # El tier (P2-1) añade la compatibilidad demostrada por pareja.
        receta_ce = _validated_cross_encoder_recipe(policy_weights)
        receta = None
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
        return {"status": "sin_vector", "rows": [],
                "profile_revision_id": None, "corpus_generation": None}

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
        return {"status": "ok", "rows": [],
                "profile_revision_id": prof.revision_id,
                "corpus_generation": None}
    recuperacion = receta_ce if receta_ce is not None else receta
    if recuperacion is not None:
        lex_query = _LEXICAL_QUERY_BUILDERS[recuperacion["lexical_query"]](
            prof.content)
    elif algorithm == "hybrid_rrf_v2":
        lex_query = _lexical_query_v2(prof.content)
    elif algorithm == "hybrid_rrf_v1":
        lex_query = _lexical_query(prof.content)
    else:
        lex_query = ""
    hybrid = bool(lex_query)
    if recuperacion is not None:
        candidate_sql = _hybrid_candidates_sql(recuperacion["lexical_weight"])
    elif algorithm == "hybrid_rrf_v2":
        candidate_sql = HYBRID2_CANDIDATES_SQL
    elif hybrid:
        candidate_sql = HYBRID_CANDIDATES_SQL
    else:
        candidate_sql = CANDIDATES_SQL
    # Frontera ÚNICA: la exclusión entra en el SQL, antes de todos los LIMIT.
    excl_ids = [str(x) for x in (exclude_vacancy_ids or [])]
    candidate_sql = _with_candidate_exclusions(
        candidate_sql, exclude_dismissed=exclude_dismissed,
        exclude_ids=bool(excl_ids),
    )
    params = {
        "vec": prof.vec, "mid": model_id, "k": limit, "lex_query": lex_query,
        "pid": profile_id, "excl_ids": excl_ids,
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
    reranked = None
    if receta is not None and receta.get("rerank"):
        # Rerank v5 SOBRE el conjunto ya recuperado (una consulta de señales
        # para el lote entero; sin N+1). El orden y el score persistidos son
        # los del rerank; los componentes van a score_parts.
        reranked = await _rerank_candidates(
            session, candidates, prof.content, receta
        )
    if receta_ce is not None and candidates:
        if not ce_inference:
            # P1-3: la evaluación productiva PREPARA aquí (todas las lecturas
            # de BD) y puntúa FUERA de la transacción; el ensamblado llega en
            # la fase de persistencia.
            prep = await _ce_prepare(
                session, candidates, prof, profile_id, model_id, policy_id,
                receta_ce,
            )
            return {"status": "ok_prep", "rows": [], "prep": prep,
                    "profile_revision_id": prof.revision_id,
                    "corpus_generation": corpus_gen}
        rows = await _cross_encoder_rows(
            session, candidates, prof, profile_id, model_id, policy_id,
            receta_ce,
        )
        return {"status": "ok", "rows": rows,
                "profile_revision_id": prof.revision_id,
                "corpus_generation": corpus_gen}
    rows = []
    if reranked is not None:
        for r in reranked:
            score_parts = {
                "algorithm": algorithm,
                "recipe": receta,
                "similarity": (
                    round(float(r["sim"]), 6) if r["sim"] is not None else None
                ),
                "semantic_rank": r["semantic_rank"],
                "lexical_rank": r["lexical_rank"],
                "lexical_score": (
                    round(float(r["lexical_score"]), 6)
                    if r["lexical_score"] is not None else None
                ),
                # componentes del rerank: suficientes para explicar el orden
                **r["rerank"],
            }
            rows.append({
                "vacancy_id": r["vacancy_id"],
                "offer_revision_id": r["offer_revision_id"],
                "score": r["score"], "score_parts": score_parts,
            })
    for c in ([] if reranked is not None else candidates):
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
        rows.append({
            "vacancy_id": c.vacancy_id, "offer_revision_id": c.offer_revision_id,
            "score": score, "score_parts": score_parts,
        })
    # Orden del FEED (no el bruto del SQL): score final redondeado DESC,
    # vacante ASC — la misma clave con la que sirve el feed canónico.
    rows.sort(key=lambda r: (-r["score"], str(r["vacancy_id"])))
    return {"status": "ok", "rows": rows,
            "profile_revision_id": prof.revision_id,
            "corpus_generation": corpus_gen}


# Margen para cerrar el lote en curso: SQL de persistencia, outbox, commit y
# cierre de sesión. Se reserva ANTES de empezar una tanda. Proporcional al
# presupuesto y acotado: con un fragmento de 1.200 s reserva 60 s; con
# presupuestos pequeños (tests, sondas) no se come el fragmento entero.
MATERIALIZE_CLOSE_MARGIN_S = 60.0
MATERIALIZE_CLOSE_MARGIN_RATIO = 0.05


def _margen_cierre(budget_seconds: float) -> float:
    return min(MATERIALIZE_CLOSE_MARGIN_S,
               max(1.0, budget_seconds * MATERIALIZE_CLOSE_MARGIN_RATIO))


async def materialize_misses(
    session_factory, profile_id, model_id, policy_id,
    budget_seconds: float = 3600.0, batch_pairs: int = 8,
) -> dict:
    """Budgeted execution; evaluation and persistence remain in this module."""
    from jobhunt_core.materialization import materialize_misses as run
    return await run(session_factory, profile_id, model_id, policy_id,
                     budget_seconds=budget_seconds, batch_pairs=batch_pairs)


async def _persist_eval_rows(
    session, profile_id, rows, prid, model_id, policy_id, consumer_name,
):
    """Persistencia CANÓNICA de evaluaciones (única frontera): filas
    idempotentes por eval_key + outbox de match.evaluated para las frescas,
    en la MISMA transacción del llamador. La usan evaluate_profile (F3) y el
    materializador incremental por watermark (P7-b) — jamás un segundo
    mecanismo de escritura. Devuelve (eval_rows, winners, new_evals)."""
    eval_rows = [
        {
            "id": uuid.uuid4(), "pid": profile_id, "vid": r["vacancy_id"],
            "orid": r["offer_revision_id"], "prid": prid,
            "mid": model_id, "spid": policy_id,
            "key": eval_key(r["offer_revision_id"], prid, model_id, policy_id),
            "score": r["score"], "scores": json.dumps(r["score_parts"]),
        }
        for r in rows
    ]
    eval_rows.sort(key=lambda r: str(r["vid"]))  # orden determinista
    if not eval_rows:
        # Fotografía VACÍA legítima (revisión externa 2026-09-07, B): no hay
        # nada que insertar ni ningún evento que emitir; el llamador retira
        # los punteros del feed bajo la misma valla.
        return [], {}, 0
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
            [{"eid": e["eid"], "dest": consumer_name} for e in events],
        )
    return eval_rows, winners, new_evals


async def evaluate_profile(
    session_factory, profile_id, model_id, policy_id, limit: int = 100,
    move_current: bool = True,
    with_corpus_generation: bool = False,
    on_evaluated=None,
    require_cache_only: bool = False,
) -> dict:
    """Evalúa el perfil vigente contra el corpus embebido — TRIFÁSICO (P1-3
    revisión 2026-09-03): la inferencia del cross-encoder tarda HORAS en el
    NAS y corría con la transacción y el FOR UPDATE del perfil abiertos.

    - FASE 1 (transacción corta, SIN lock): vallas, snapshot de identidades y
      TODAS las lecturas (candidatos, documentos, caché).
    - FASE 2 (sin BD): inferencia CPU fuera del event loop.
    - FASE 3 (transacción corta): FOR UPDATE del perfil, REVALIDACIÓN de
      identidades (revisión vigente y receta de la política); si derivaron,
      se DESCARTA sin publicar nada («descartado_por_deriva» — el ciclo
      siguiente reevalúa); si no, inserción idempotente, outbox, valla
      canónica y movimiento del feed. `on_evaluated` corre DENTRO de esta
      transacción (atómico con la persistencia). El commit es propio.
    """
    # ---------- FASE 1: preparación en transacción corta, sin lock
    async with session_factory() as session:
        existe = (
            await session.execute(
                sa.text("SELECT 1 FROM profiles WHERE id = :pid AND projection_active"),
                {"pid": profile_id},
            )
        ).scalar_one_or_none()
        if existe is None:
            return {
                "status": "not_found", "evaluated": 0, "new_evals": 0,
                "moved_current": False,
            }
        pesos_snapshot = (
            await session.execute(
                sa.text("SELECT weights FROM scoring_policies WHERE id = :id"),
                {"id": policy_id},
            )
        ).scalar_one_or_none()
        if move_current and isinstance(pesos_snapshot, dict) \
                and not _is_pair_absolute(pesos_snapshot):
            # Valla pair_absolute: falla CERRADO antes de cualquier trabajo.
            raise ValueError(
                f"política {policy_id} con score RELATIVO al lote "
                f"(algorithm={_algorithm_of(pesos_snapshot)!r}) no puede mover "
                "el feed: materializaría una mezcla de generaciones"
            )
        # Generación ANTES de leer candidatos (revisión 2026-09-04 P1): si el
        # corpus muta entre esta lectura y la de candidatos, la generación
        # fotografiada queda POR DEBAJO y la revalidación final descarta —
        # falso positivo inocuo; el orden inverso permitiría publicar
        # candidatos viejos bajo una generación nueva.
        gen_snapshot = (
            await session.execute(sa.text(CORPUS_GENERATION_SQL))
        ).scalar_one()
        computed = await compute_policy_feed(
            session, profile_id, model_id, policy_id, limit=limit,
            exclude_dismissed=False,
            with_corpus_generation=with_corpus_generation,
            ce_inference=False,
        )
    corpus_gen = (
        computed["corpus_generation"] if with_corpus_generation
        else gen_snapshot
    )
    if computed["status"] == "sin_vector":
        return {
            "status": "sin_vector", "evaluated": 0, "new_evals": 0,
            "moved_current": False,
        }
    prid = computed["profile_revision_id"]

    # ---------- FASE 2: inferencia sin BD, fuera del event loop
    if computed["status"] == "ok_prep":
        import asyncio as _asyncio

        if require_cache_only and computed["prep"]["misses"]:
            # Publicación CACHE-ONLY (revisión externa 2026-09-07, hallazgo
            # A): entre la materialización y la publicación pudo cambiar la
            # revisión del perfil o el corpus, y la preparación nueva trae
            # misses. Inferirlos aquí sería trabajo FUERA del presupuesto —
            # justo lo que P7-b existe para impedir. Se devuelve el control
            # al materializador, que los puntúa dentro de su fragmento.
            return {
                "status": "misses_pendientes", "evaluated": 0, "new_evals": 0,
                "moved_current": False,
                "misses": len(computed["prep"]["misses"]),
                "profile_revision_id": prid,
                "corpus_generation": corpus_gen,
            }
        frescos = await _asyncio.to_thread(_ce_score_misses, computed["prep"])
        rows = _ce_assemble(computed["prep"], frescos)
    else:
        rows = computed["rows"]
    # Un conjunto elegible legítimamente VACÍO (p. ej. tras añadir exclusiones
    # que descartan todo) es una FOTOGRAFÍA PUBLICABLE, no un no-op: si se
    # retornara aquí, el feed seguiría sirviendo los punteros anteriores —
    # ofertas que el usuario acaba de excluir (revisión externa 2026-09-07,
    # hallazgo B). Sigue a la fase 3, pasa la MISMA valla y limpia punteros.
    # Los caminos de error no llegan hasta aquí: `sin_vector` retorna antes y
    # un fallo de BD/modelo propaga sin publicar nada.
    computed = {"rows": rows}

    # ---------- FASE 3: lock corto, revalidación y persistencia atómica
    async with session_factory() as session:
        locked = (
            await session.execute(
                sa.text(
                    "SELECT p.id, p.projection_active, c.name AS consumer_name FROM profiles p "
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
        # REVALIDACIÓN ATÓMICA de la tupla completa (P1-3 + revisión
        # 2026-09-04): revisión del perfil + generación del corpus + (si
        # publica) modelo canónico + política canónica, TODO bajo el mismo
        # lock y en la misma transacción que la escritura. Cualquier deriva ⇒
        # descartar SIN escribir nada ni mover el feed («descartado_por_
        # deriva»); quien encoló reintenta desde la fase 1 con lo vigente.
        # Los scores por pareja siguen siendo hechos válidos (pair_absolute),
        # pero un conjunto preparado sobre una generación anterior NO puede
        # retirar punteros de un feed más nuevo.
        vigente = (
            await session.execute(
                sa.text(
                    "SELECT revision_id FROM profile_revision_activations "
                    "WHERE profile_id = :pid ORDER BY seq DESC LIMIT 1"
                ),
                {"pid": profile_id},
            )
        ).scalar_one_or_none()
        pesos_ahora = (
            await session.execute(
                sa.text("SELECT weights FROM scoring_policies WHERE id = :id"),
                {"id": policy_id},
            )
        ).scalar_one_or_none()
        # FOR SHARE mantenido hasta el commit (revisión 2026-09-04 1A): el
        # trigger bump_corpus_generation() hace UPDATE de ESTA fila única en
        # cada camino que cambia elegibilidad (embeddings y vacancies, por
        # sentencia), así que cualquier mutación concurrente ESPERA a que
        # esta publicación cometa — jamás puede quedar corpus G2 + feed G1
        # sin señal pendiente. Orden de locks: perfil (FOR UPDATE) →
        # generación (FOR SHARE) → modelo (FOR SHARE) → política (FOR SHARE)
        # → escrituras; ningún camino de cosecha/archivo/embedding toma locks
        # de perfil ni de políticas antes de tocar la generación, y
        # declare_active_policies no toca la generación: no existe orden
        # inverso que pueda producir deadlock.
        gen_ahora = (
            await session.execute(
                sa.text(CORPUS_GENERATION_SQL + " FOR SHARE")
            )
        ).scalar_one()
        deriva = (
            not locked.projection_active
            or str(vigente) != str(prid)
            or pesos_ahora != pesos_snapshot
            or int(gen_ahora) != int(gen_snapshot)
        )
        if not deriva and move_current:
            # Modelo aún ACTIVO (FOR SHARE sobre SU fila) y además CANÓNICO
            # EXACTO (revisión 2026-09-04 1B): canonical_model_id recomputada
            # bajo el lock — activar un modelo ANTERIOR en el orden cambia el
            # canónico aunque el usado siga activo. Todo cambio de activación
            # (declare_active_models o register_model declarativo) toca TODAS
            # las filas, así que conflicta con este FOR SHARE y se serializa:
            # también cubre alta+activación de OTRA fila. Política canónica
            # con FOR SHARE: el flip (declare_active_policies) actualiza
            # TODAS las filas y se serializa con esta lectura.
            modelo_activo = (
                await session.execute(
                    sa.text(
                        "SELECT active FROM embedding_models "
                        "WHERE id = :m FOR SHARE"
                    ),
                    {"m": model_id},
                )
            ).scalar_one_or_none()
            canon_modelo = await canonical_model_id(session, profile_id)
            canonica = (
                await session.execute(
                    sa.text(
                        "SELECT id FROM scoring_policies WHERE active "
                        "ORDER BY name, prompt_version LIMIT 1 FOR SHARE"
                    )
                )
            ).scalar_one_or_none()
            deriva = (
                not modelo_activo
                or str(canon_modelo) != str(model_id)
                or canonica is None
                or str(canonica) != str(policy_id)
            )
        if _after_revalidation is not None:
            # Seam SOLO para tests (como set_engine_factory): permite colocar
            # una barrera EXACTAMENTE entre la revalidación (locks tomados) y
            # la escritura, para reproducir interleavings de publicación.
            await _after_revalidation()
        if deriva:
            logger.warning(
                "matching: la tupla revalidada derivó durante la evaluación "
                "de %s (revisión %s→%s, generación %s→%s) — resultado "
                "descartado sin publicar",
                profile_id, prid, vigente, gen_snapshot, gen_ahora,
            )
            return {
                "status": "descartado_por_deriva", "evaluated": 0,
                "new_evals": 0, "moved_current": False,
                "profile_revision_id": prid,
                "corpus_generation": corpus_gen,
            }
        eval_rows, winners, new_evals = await _persist_eval_rows(
            session, profile_id, computed["rows"], prid, model_id,
            policy_id, locked.consumer_name,
        )
        moved = False
        # La valla de canonicidad P1-C (revisión 2026-09-02) vive ahora DENTRO
        # de la revalidación atómica de arriba (revisión 2026-09-04): política
        # y modelo canónicos se comprueban con FOR SHARE ANTES de escribir
        # nada, y su deriva descarta el resultado completo en vez de degradar
        # a «registrada sin mover».
        if move_current:
            state_rows = [
                {"pid": profile_id, "vid": r["vid"], "eid": winners[(r["vid"], r["key"])]}
                for r in eval_rows
                if (r["vid"], r["key"]) in winners
            ]
            if not eval_rows:
                # Fotografía vacía: se retiran TODOS los punteros del feed. El
                # estado estable (feedback/dismissed/saved/notes) se conserva
                # — solo se suelta la evaluación vigente.
                await session.execute(
                    sa.text(
                        "UPDATE profile_vacancy_state "
                        "SET current_eval_id = NULL, "
                        "updated_at = GREATEST(updated_at, clock_timestamp()) "
                        "WHERE profile_id = :pid AND current_eval_id IS NOT NULL"
                    ),
                    {"pid": profile_id},
                )
                moved = True
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
        resultado = {
            "status": "ok", "evaluated": len(eval_rows), "new_evals": new_evals,
            "moved_current": moved,
            # La revisión REALMENTE evaluada (la de la fase 1, REVALIDADA bajo
            # el lock): quien registre el intento debe usar ESTA.
            "profile_revision_id": prid,
            "corpus_generation": corpus_gen,
        }
        if on_evaluated is not None:
            # MISMA transacción que la persistencia final: o se registran
            # ambas o ninguna (atómico, como exige P1-3).
            await on_evaluated(session, resultado, model_id, policy_id)
        await session.commit()
        return resultado


async def feed(session, profile_id, limit: int = 20, cursor=None, consumer_id=None):
    """Feed del perfil (DoD A-08): evaluación VIGENTE + no-dismissed + vacante
    ACTIVA, keyset por (score_final DESC, vacancy_id ASC).

    `cursor` = (score_final, vacancy_id) de la última fila entregada; devuelve
    (filas, next_cursor) con next_cursor None al agotar. `consumer_id`
    (rev. A-09 #1): el OWNERSHIP multi-tenant se filtra EN LA QUERY (§2) —
    una reasignación de tenant a mitad de request jamás puede filtrar filas."""
    from jobhunt_core.feedback import effective_feedback_sql

    feedback_sql = effective_feedback_sql("s.vacancy_id", "s.profile_id")
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
                f"e.offer_revision_id, s.saved_at, ({feedback_sql}) AS feedback, s.notes "
                "FROM profile_vacancy_state s "
                f"{tenant_join}"
                "JOIN match_evaluations e ON e.id = s.current_eval_id "
                "  AND e.profile_id = s.profile_id AND e.vacancy_id = s.vacancy_id "
                "JOIN vacancies v ON v.id = s.vacancy_id "
                "  AND v.archived_at IS NULL AND v.merged_into IS NULL "
                # A PROPÓSITO sin `current_offer_revision_id IS NOT NULL`: este feed
                # es también el que mide el nDCG del gate (shadow/metrics.py) y su
                # semántica no se toca desde un arreglo de caché. La página omite
                # esas filas en `_vacancy_dtos`; recuento y versión las excluyen
                # (feed_total_sql / feed_version_sql) para describir lo SERVIDO.
                "WHERE s.profile_id = :pid "
                f"AND COALESCE(({feedback_sql}),'') NOT IN ('thumbs_down','dismissed') "
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


def feed_total_sql(profile_id, consumer_id=None):
    """SQL del tamaño del feed y sus parámetros. Mismas tres reglas que `feed`.

    El feedback efectivo se resuelve UNA vez por vacante (`effective_feedback_batch_sql`)
    en lugar de una subconsulta correlacionada por fila: sobre los datos de
    producción eso son 0,4-0,9 s frente a 1,1-9,3 s para el MISMO número
    (medición en PREDECLARACION_PUNTO5_2026-09-22.md §5). El plan se fija en
    `test_matches_total.py`, que falla si vuelve a aparecer un SubPlan por fila.

    Se expone el SQL además del contador para que esa regresión pueda mirar el
    plan sin duplicar la consulta que de verdad se ejecuta.
    """
    from jobhunt_core.feedback import effective_feedback_batch_sql

    params = {"pid": profile_id}
    tenant_join = ""
    if consumer_id is not None:
        tenant_join = "JOIN profiles p ON p.id = s.profile_id AND p.consumer_id = :cid "
        params["cid"] = consumer_id
    sql = (
        f"WITH fb AS ({effective_feedback_batch_sql(':pid')}) "
        "SELECT count(*) FROM profile_vacancy_state s "
        f"{tenant_join}"
        "JOIN match_evaluations e ON e.id = s.current_eval_id "
        "  AND e.profile_id = s.profile_id AND e.vacancy_id = s.vacancy_id "
        "JOIN vacancies v ON v.id = s.vacancy_id "
        "  AND v.archived_at IS NULL AND v.merged_into IS NULL "
        "  AND v.current_offer_revision_id IS NOT NULL "
        "LEFT JOIN fb ON fb.vacancy_id = s.vacancy_id "
        # `current_eval_id IS NOT NULL` es redundante con el JOIN, pero
        # explicitarlo deja que el planificador use el indice PARCIAL: sin el,
        # contar un feed recorria las 35.092 filas de la tabla para quedarse
        # con las 5.400 que pueden estar en uno.
        "WHERE s.profile_id = :pid AND s.current_eval_id IS NOT NULL "
        "AND COALESCE(fb.feedback,'') NOT IN ('thumbs_down','dismissed')"
    )
    return sql, params


async def feed_total(session, profile_id, consumer_id=None) -> int:
    """Cuántas ofertas sirve el feed de este perfil, con su mismo contrato.

    Existe para que el consumidor no tenga que recorrer el feed entero sólo
    para contarlo: servir 20 ofertas costaba 18 peticiones y ~9 s por eso.
    """
    sql, params = feed_total_sql(profile_id, consumer_id)
    return int(await session.scalar(sa.text(sql), params) or 0)


def feed_version_sql(profile_id, consumer_id=None):
    """SQL de la VERSIÓN del feed: un digest que cambia si cambia el feed.

    Existe para que el consumidor no tenga que descargar 1.800 ofertas en 18
    páginas sólo para comprobar si algo cambió. Medido en el NAS: ese recorrido
    es el 87-89 % del coste de servir la pantalla principal — 47,5 s en frío y
    11,5 s en caliente. Caliente sigue costando porque un `If-None-Match` NO
    ahorra trabajo: el ETag se deriva del payload, así que el core construye la
    página igual para responder 304.

    Qué entra en el digest, y por qué exactamente eso — cinco componentes,
    todos de filas que el JOIN ya recorre (coste cero):

    - **pertenencia** (`vacancy_id`) y **evaluación vigente** (`current_eval_id`);
    - **revisión canónica vigente** (`current_offer_revision_id`): título,
      empresa, descripción;
    - **encarnación primaria** (`v.primary_incarnation_id`): de ella salen
      `primary_listing.url/apply_url/external_id`, y es la clave con la que el
      BFF une el estado local. Reasignarla cambia lo servido sin tocar la
      canónica — la auditoría del 2026-09-23 lo encontró fuera del digest;
    - **estado de usuario** (`s.updated_at`): lo mueven `set_vacancy_feedback`
      (feedback positivo, que NO cambia la pertenencia) y `set_saved`. Sin él,
      un consumidor cacheado servía `feedback: null` después del ACK de un
      `thumbs_up` — la regresión desplegada y retirada el mismo día.

    Un contador o un `max(updated_at)` NO bastarían: una alta y una baja
    simultáneas dejarían el contador igual.

    Lo que sigue FUERA, declarado: `url`/`apply_url`/`last_seen_at` de listings
    NO primarios, y `s.notes` (hoy sin escritor en el core; si aparece uno,
    debe mover `updated_at`).

    La misma cláusula del feed y del recuento: excluye no-activas y feedback
    negativo, y explicita `current_eval_id IS NOT NULL` para que el
    planificador alcance el índice parcial `ix_pvs_feed_current_eval`.
    """
    from jobhunt_core.feedback import effective_feedback_batch_sql

    params = {"pid": profile_id}
    tenant_join = ""
    if consumer_id is not None:
        tenant_join = "JOIN profiles p ON p.id = s.profile_id AND p.consumer_id = :cid "
        params["cid"] = consumer_id
    sql = (
        f"WITH fb AS ({effective_feedback_batch_sql(':pid')}) "
        "SELECT count(*) AS total, "
        "  md5(coalesce(string_agg("
        "    s.vacancy_id::text || ':' || s.current_eval_id::text || ':' "
        "    || coalesce(v.current_offer_revision_id::text, '-') || ':' "
        "    || coalesce(v.primary_incarnation_id::text, '-') || ':' "
        "    || coalesce(s.updated_at::text, '-'), "
        "    ',' ORDER BY s.vacancy_id"
        "  ), '')) AS version "
        "FROM profile_vacancy_state s "
        f"{tenant_join}"
        "JOIN match_evaluations e ON e.id = s.current_eval_id "
        "  AND e.profile_id = s.profile_id AND e.vacancy_id = s.vacancy_id "
        "JOIN vacancies v ON v.id = s.vacancy_id "
        "  AND v.archived_at IS NULL AND v.merged_into IS NULL "
        "  AND v.current_offer_revision_id IS NOT NULL "
        "LEFT JOIN fb ON fb.vacancy_id = s.vacancy_id "
        "WHERE s.profile_id = :pid AND s.current_eval_id IS NOT NULL "
        "AND COALESCE(fb.feedback,'') NOT IN ('thumbs_down','dismissed')"
    )
    return sql, params


async def feed_version(session, profile_id, consumer_id=None) -> tuple[str, int]:
    """(version, total) del feed de este perfil. Ver `feed_version_sql`."""
    sql, params = feed_version_sql(profile_id, consumer_id)
    row = (await session.execute(sa.text(sql), params)).one()
    return row.version, int(row.total or 0)


async def set_dismissed(session, profile_id, vacancy_id, dismissed: bool) -> None:
    """Descartar/restaurar: upsert que SOLO toca dismissed_at/updated_at.
    clock_timestamp() + GREATEST (rev. A-08 #3): la hora real de ESCRITURA,
    no la de inicio de la tx — sin retrocesos temporales entre tx solapadas."""
    await session.execute(
        sa.text(
            "INSERT INTO profile_vacancy_state "
            "(profile_id, vacancy_id, dismissed_at, updated_at, feedback_recorded_at) "
            "VALUES (:pid, :vid, CASE WHEN :d THEN clock_timestamp() END, "
            "clock_timestamp(), clock_timestamp()) "
            "ON CONFLICT (profile_id, vacancy_id) DO UPDATE "
            "SET dismissed_at = CASE WHEN :d THEN clock_timestamp() END, "
            "feedback_recorded_at=clock_timestamp(), "
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

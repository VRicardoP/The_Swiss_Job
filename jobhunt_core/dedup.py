"""Dedup semántico nivel 3 — GENERADOR DE CANDIDATOS (F-5, 2026-08-22).

La capacidad que la Fase B exigía medir y nunca construyó (auditoría F-5:
`dedup_recall >= 0,90` con un techo real de 0,073 — el core solo detectaba
duplicados por URL/attach e intra-lote). Este módulo la aporta con el alcance
MÍNIMO que el gate necesita y el MÁXIMO que es seguro hoy:

- SOLO DETECCIÓN: pares en `dedup_candidates` (state 'pending'). La métrica
  cuenta "core dice duplicado" con `state <> 'rejected'` — no hace falta
  fusionar, y el AUTO-merge es exactamente donde el legacy se hizo daño
  (B-2: 664 ofertas desactivadas en falso). La fusión (ADR-04, con
  transferencia de estado) queda como paso posterior y CONTROLADO.
- ANN SOLO CROSS-SOURCE (el léxico añade además un brazo INTRA con umbral
  propio y regla de ubicación — Track R fase 2): el propósito del dedup
  semántico es la misma
  oferta publicada en portales distintos. Los pares intra-fuente fueron el
  94 % de los falsos positivos del legacy (stubs sin descripción de la misma
  empresa superando 0,95); dentro de una fuente la identidad ya la dan URL y
  external_id.

Corpus y vectores: el MISMO join probado del matching (vacante elegible →
revisión vigente → embedding del modelo activo) y el MISMO índice HNSW; la
consulta kNN es un LATERAL por vacante nueva, no un O(n²).

Incremental: cada pasada mira las vacantes cuya revisión vigente nació en la
ventana (`CORE_DEDUP_SCAN_WINDOW_H`, 48 h — un día de margen sobre el beat
diario); un par nuevo exige al menos un miembro nuevo, así que la ventana
cubre. `window_hours=None` = pasada COMPLETA (backfill inicial, una vez).
Idempotente: `uq_dedup_pair` (LEAST/GREATEST) + ON CONFLICT DO NOTHING.
"""

import hashlib
import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core import embeddings
from jobhunt_core.config import settings
from jobhunt_core.matching import MAX_SCAN_TUPLES

logger = logging.getLogger(__name__)

# Corpus elegible CON vector y fuente (la fuente sale del primary — una
# vacante sin primary no puede afirmar su procedencia: se salta).
_CORPUS_SQL = (
    "SELECT v.id, oe.vector, sl.source_id, "
    "       coalesce(orv.content->>'location', '') AS loc "
    "FROM vacancies v "
    "JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "JOIN offer_embeddings oe "
    "  ON oe.text_hash = orv.text_hash AND oe.model_id = :mid "
    "JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id "
    "JOIN source_listings sl ON sl.id = pi.source_listing_id "
    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
)


# Compatibilidad de UBICACIÓN — expresión ÚNICA compartida por ANN y léxico
# (revisión Track R, P1-3: position(short IN long) daba York~New York, y el
# remoto solo-exacto vetaba «Remote - Europe» contra «remote»).
# Semántica: vacío o código corto (<=3) no vetan; un lado es REMOTO si algún
# token es remote/global/worldwide/international/anywhere (remoto solo
# compatible con remoto); si ambos son concretos, se comparan COMPONENTES
# (split por , ; /): iguales, o prefijo en frontera de palabra
# («basel»→«basel-stadt» sí; «bern»→«bernau» NO; «york» vs «new york» NO;
# «bulgaria, romania» ~ «greece, bulgaria» sí por componente común).
# Tokens NÚCLEO de remoto: cualquier aparición ⇒ remoto («Remote - Europe»).
_REMOTO_TOKENS = "('remote','global','worldwide','international','anywhere')"
# Husos horarios (fase 3; auditoría C1 P3-1): un huso solo declara remoto si
# al retirar husos/dígitos/«hours» NO queda residuo concreto — «CET (+/- 3
# hours)» ⇒ remoto, pero «Zürich (CET)» sigue siendo Zürich (antes dejaba de
# casar consigo misma). 'est' fuera (colisiona con «Grand Est»).
_TZ_TOKENS = "('cet','cest','utc','gmt','timezone','timezones')"
# C2-P3: strip también en DE/FR (stunden/heures) — «CET (+/- 3 Stunden)»
_TZ_STRIP = "(cet|cest|utc|gmt|timezone|timezones|hours?|stunden|heures)"


def _title_norm_sql(x: str) -> str:
    """Título normalizado para el trigram — allowlist ESTRECHA (revisión
    FASE 2 rondas 1-2): lo desconocido se conserva. Se elimina SOLO:
    - el prefijo anclado «eks:» (literal probado, ronda 2 P2-1 — el umbral
      intra NO se baja);
    - marcadores de género entre paréntesis ((m/w/d) y variantes).
    Los PORCENTAJES se conservan (ronda 2 P1-2: pensums distintos son dos
    plazas — IPOS-03). Los tokens de lenguaje se CODIFICAN con límites
    ANTES de la limpieza (C++ ⇒ cplusplus, C# ⇒ csharp): pg_trgm ignora
    +/# al construir palabras y conservarlos en la cadena no distinguía
    nada (ronda 2 P1-1). El resto de puntuación pasa a espacio."""
    genero = (
        "\\((m/w/d|w/m/d|m/f/d|f/m/d|m/w/x|m/f/x|m/w|w/m|m/f|f/m"
        "|all genders?|alle)\\)"
    )
    return (
        "btrim(regexp_replace(regexp_replace(regexp_replace(regexp_replace("
        "regexp_replace(regexp_replace("
        f"lower({x}), "
        "'^eks: *', ' '), "
        "'\\mc\\+\\+', 'cplusplus', 'g'), "
        "'\\mc#', 'csharp', 'g'), "
        f"'{genero}', ' ', 'g'), "
        "'[^a-z0-9äöüéèß]+', ' ', 'g'), ' +', ' ', 'g'))"
    )


def _es_remoto_sql(x: str) -> str:
    nucleo = (
        "EXISTS (SELECT 1 FROM unnest(regexp_split_to_array("
        f"lower({x}), '[^a-z]+')) AS rt(tok) WHERE rt.tok IN {_REMOTO_TOKENS})"
    )
    tiene_tz = (
        "EXISTS (SELECT 1 FROM unnest(regexp_split_to_array("
        f"lower({x}), '[^a-z]+')) AS rt(tok) WHERE rt.tok IN {_TZ_TOKENS})"
    )
    # C2-P3: el residuo conserva letras de CUALQUIER alfabeto ([[:alpha:]])
    # — la clase latina borraba idiomas enteros y «Женева (CET)» quedaba
    # "vacía" ⇒ remota. Cirílico/griego/etc. ahora cuentan como concreto.
    residuo_vacio = (
        "btrim(regexp_replace(regexp_replace("
        f"lower({x}), '\\m{_TZ_STRIP}\\M', ' ', 'g'), "
        "'[^[:alpha:]]+', ' ', 'g')) = ''"
    )
    return f"({nucleo} OR ({tiene_tz} AND {residuo_vacio}))"


def _loc_compat_sql(a: str, b: str) -> str:
    rem_a, rem_b = _es_remoto_sql(a), _es_remoto_sql(b)
    # Revisión FASE 2 P1-2: SOLO se retira un prefijo POSTAL reconocido
    # (^[0-9]{4,5} espacio) — «9495 Triesen» ⇒ «triesen» sin fusionar
    # «District 1» con «District 2» (los dígitos de zona son semánticos).
    norm = "btrim(regexp_replace(btrim(%s), '^[0-9]{4,5} +', ''))"
    ca_n, cb_n = norm % "ca.c", norm % "cb.c"
    comp = (
        "EXISTS (SELECT 1 "
        f"FROM unnest(string_to_array(translate(lower({a}), ';/', ',,'), ',')) "
        "     WITH ORDINALITY AS ca(c, ord), "
        f"     unnest(string_to_array(translate(lower({b}), ';/', ',,'), ',')) "
        "     WITH ORDINALITY AS cb(c, ord) "
        f"WHERE {ca_n} <> '' AND {cb_n} <> '' "
        "  AND (ca.ord = 1 OR cb.ord = 1) AND ("
        f"  {ca_n} = {cb_n} "
        f"  OR {cb_n} LIKE {ca_n} || '-%' "
        f"  OR {cb_n} LIKE {ca_n} || ' %' "
        f"  OR {ca_n} LIKE {cb_n} || '-%' "
        f"  OR {ca_n} LIKE {cb_n} || ' %'))"
    )
    # Revisión FASE 2 P1-3: remoto BILATERAL restaurado — el comodín
    # unilateral se retiró (su evidencia era vacua: 0/12 pares elegibles).
    # Una excepción remoto↔concreto exigiría positivos independientes y una
    # rama explícita más fuerte; no existe hoy.
    return (
        f"(btrim({a}) = '' OR btrim({b}) = '' "
        f" OR length(btrim({a})) <= 3 OR length(btrim({b})) <= 3 "
        f" OR ({rem_a} AND {rem_b}) "
        f" OR (NOT {rem_a} AND NOT {rem_b} AND {comp}))"
    )


# kNN sobre el índice HNSW (misma forma que matching.CANDIDATES_SQL).
# B-2 auditoría externa (2026-08-23): la exclusión de la PROPIA vacante y de la
# MISMA fuente va ANTES del ORDER BY/LIMIT. Filtrarlas después, en Python,
# hacía que una concentración de anuncios muy próximos de una fuente consumiera
# el presupuesto k y OCULTARA vecinos cross-source válidos (reproducido: 6
# vacantes intra a sim 1.0 + 1 cross a 0.96 con k=5 → el par cross ni llegaba
# al filtro). Con el filtro en SQL, LIMIT :k significa "k vecinos cross-source".
#
# TRACK R.2a (2026-08-24): guard de UBICACIÓN compatible, también en SQL y
# antes del LIMIT (misma lección). El examen del holdout midió precision
# 0.636: el ANN no tenía la regla multi-ciudad que sí tiene el exacto-intra
# y proponía como duplicado el mismo texto publicado en ciudades distintas.
# Medido en DEVELOPMENT (81 pares re-adjudicados): sim>=0.95 a secas ⇒
# 17/53 distinct como FP (precision ~0.61); con este guard ⇒ 1/53 y 24/28
# dup (~0.96). Regla: compatible si alguna ubicación está VACÍA (sin dato
# no se veta) o si una contiene a la otra (case-insensitive, con btrim) —
# «Zürich» ~ «Zürich, Zürich». Ver ANALISIS_TRACK_R_2026-08-24.md.
_KNN_SQL = (
    "SELECT v.id AS vacancy_id, sl.source_id, "
    "       1 - (oe.vector <=> CAST(:vec AS vector)) AS sim "
    "FROM vacancies v "
    "JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "JOIN offer_embeddings oe "
    "  ON oe.text_hash = orv.text_hash AND oe.model_id = :mid "
    "JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id "
    "JOIN source_listings sl ON sl.id = pi.source_listing_id "
    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
    "  AND v.id <> :vid AND sl.source_id <> :src "
    "  AND " + _loc_compat_sql(
        "CAST(:loc AS text)", "coalesce(orv.content->>'location', '')"
    ) + " "
    "ORDER BY oe.vector <=> CAST(:vec AS vector) "
    "LIMIT :k"
)

# Conteo EXACTO de elegibles por fila (revisión Track R, P2-3: el objetivo
# se calculaba SIN el guard de ubicación y disparaba el fallback exacto
# cuando el 0 era el resultado correcto). Mismo predicado que _KNN_SQL,
# acotado por :k — patrón de matching.
_KNN_COUNT_SQL = (
    "SELECT count(*) FROM ("
    "SELECT 1 FROM vacancies v "
    "JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "JOIN offer_embeddings oe "
    "  ON oe.text_hash = orv.text_hash AND oe.model_id = :mid "
    "JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id "
    "JOIN source_listings sl ON sl.id = pi.source_listing_id "
    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
    "  AND v.id <> :vid AND sl.source_id <> :src "
    "  AND " + _loc_compat_sql(
        "CAST(:loc AS text)", "coalesce(orv.content->>'location', '')"
    ) + " LIMIT :k) t"
)

# ON CONFLICT (revisión Track R, P2-2): similarity = MÁXIMA fuerza de
# evidencia entre generadores (coseno del ANN, trgm del léxico, 1.000 del
# exacto — escalas distintas, no probabilidades comparables), actualizada
# SOLO mientras el par siga pending y solo si crece (así el segundo pase
# idéntico no cuenta como fila afectada). Los resueltos no se reabren.
_ON_CONFLICT = (
    "ON CONFLICT (LEAST(vacancy_a, vacancy_b), GREATEST(vacancy_a, vacancy_b)) "
    "DO UPDATE SET similarity = EXCLUDED.similarity "
    "WHERE dedup_candidates.state = 'pending' "
    "  AND dedup_candidates.similarity < EXCLUDED.similarity"
)

_INSERT_SQL = (
    "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
    "VALUES (:id, :a, :b, :sim) " + _ON_CONFLICT
)


# Duplicados EXACTOS intra-fuente (regla ratificada por el propietario el
# 2026-08-23 al curar el oráculo): mismo texto canónico (text_hash) Y misma
# location ⇒ duplicado; contenido idéntico con ciudad DISTINTA = publicación
# multi-ciudad legítima (Flix Berlín vs Múnich) y NO se marca. Determinista —
# sin umbral ni embedding — y por eso NO comparte la ambigüedad de los stubs
# que dejó lo intra-fuente fuera del ANN. Motivado además por el hallazgo del
# canario (§12.9: grupos de hasta 9 repetidas en el feed). Set-based: un pase.
_EXACT_INTRA_SQL = (
    "WITH corpus AS ("
    "  SELECT v.id, orv.text_hash, sl.source_id, "
    "         coalesce(orv.content->>'location', '') AS loc "
    "  FROM vacancies v "
    "  JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "  JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id "
    "  JOIN source_listings sl ON sl.id = pi.source_listing_id "
    "  WHERE v.archived_at IS NULL AND v.merged_into IS NULL"
    ") "
    "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
    "SELECT gen_random_uuid(), a.id, b.id, 1.000 "
    "FROM corpus a JOIN corpus b "
    "  ON a.text_hash = b.text_hash AND a.source_id = b.source_id "
    "  AND a.loc = b.loc AND a.id < b.id "
    + _ON_CONFLICT
)


# Generador LÉXICO cross-portal (TRACK R.2b, 2026-08-24). El examen del
# holdout midió recall 0.259 y el ANN a SIM_MIN=0.95 detecta 0/9 duplicados
# cross-portal reales también en development-2: entre portales el MISMO
# puesto llega con descripciones distintas o vacías (sim 0.65–0.94) y la
# EMPRESA escrita diferente («Kanton Zug» vs «Kantonale Verwaltung Zug»).
# Señal que sí funciona (dev-2: 9/9 dup, 0 FP): token SIGNIFICATIVO de
# empresa compartido (>=3 letras, sin sufijos legales/stopwords, con tope
# de frecuencia por EMPRESAS DISTINTAS — P1-2: contar vacantes silenciaba
# a los grandes empleadores; la rama de FIRMA completa cubre incluso a
# los que superan el tope) + trigram de
# título >= CORE_DEDUP_LEX_TRGM_MIN + ubicación compatible v2 (vacío o
# código corto no vetan; remoto solo con remoto; si no, contención).
# Set-based e incremental como el ANN: un miembro del par en la ventana.
_LEX_STOP = ("'ag','gmbh','mbh','est','ltd','inc','sa','kg','co','llc',"
             "'bv','as','the','and','und','de','of','für','fur','im'")
_LEX_REMOTO = ("(lower(btrim(%s)) IN ('global','remote','worldwide',"
               "'international') OR position('anywhere' IN lower(%s)) > 0)")


def _lex_sql(window: bool) -> str:
    filtro_ventana = (
        "WHERE created_at >= now() - make_interval(hours => :ventana) "
        if window else ""
    )
    loc_ok = _loc_compat_sql("p.loc_n", "p.loc_c")
    tn_n, tn_c = "p.tn_n", "p.tn_c"
    return (
        "WITH corpus AS ("
        "  SELECT v.id, sl.source_id, orv.text_hash AS th, "
        "         orv.content->>'title' AS title, "
        + "         " + _title_norm_sql("orv.content->>'title'") + " AS title_n, "
        "         lower(coalesce(orv.content->>'company','')) AS comp, "
        "         coalesce(orv.content->>'location','') AS loc, "
        "         orv.created_at "
        "  FROM vacancies v "
        "  JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
        "  JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id "
        "  JOIN source_listings sl ON sl.id = pi.source_listing_id "
        "  WHERE v.archived_at IS NULL AND v.merged_into IS NULL"
        "), tok AS ("
        "  SELECT c.id, c.source_id, c.title_n, c.loc, c.comp, c.created_at, t.tok "
        "  FROM corpus c, LATERAL unnest(regexp_split_to_array("
        "       c.comp, '[^a-zäöüéèß]+')) AS t(tok) "
        f"  WHERE length(t.tok) >= 3 AND t.tok NOT IN ({_LEX_STOP})"
        "), firma AS ("
        "  SELECT c.id, c.source_id, c.title_n, c.loc, c.th, c.created_at, "
        "         (SELECT string_agg(x.tok, ' ' ORDER BY x.tok) "
        "          FROM (SELECT DISTINCT t2.tok FROM tok t2 "
        "                WHERE t2.id = c.id) x) AS f "
        "  FROM corpus c "
        "  WHERE EXISTS (SELECT 1 FROM tok WHERE tok.id = c.id)"
        "), frec AS ("
        "  SELECT tok FROM tok GROUP BY tok "
        "  HAVING count(DISTINCT comp) <= :maxfreq"
        f"), nuevos AS (SELECT * FROM tok {filtro_ventana}"
        f"), nuevos_f AS (SELECT * FROM firma {filtro_ventana}"
        "), pares AS ("
        "  SELECT DISTINCT ON (a, b) * FROM ("
        # cross-portal por token raro compartido
        "    SELECT LEAST(n.id, c.id) AS a, GREATEST(n.id, c.id) AS b, "
        "           n.title_n AS tn_n, c.title_n AS tn_c, "
        "           n.loc AS loc_n, c.loc AS loc_c, 'x' AS via "
        "    FROM nuevos n "
        "    JOIN frec f ON f.tok = n.tok "
        "    JOIN tok c ON c.tok = n.tok AND c.source_id <> n.source_id "
        "         AND c.id <> n.id "
        "    UNION "
        # cross-portal por firma completa (grandes empleadores)
        "    SELECT LEAST(n.id, c.id), GREATEST(n.id, c.id), "
        "           n.title_n, c.title_n, n.loc, c.loc, 'x' "
        "    FROM nuevos_f n "
        "    JOIN firma c ON c.f = n.f AND c.f IS NOT NULL AND c.f <> '' "
        "         AND c.source_id <> n.source_id AND c.id <> n.id "
        "    UNION "
        # INTRA-fuente (Track R fase 2): mismo portal, misma firma, texto
        # DISTINTO (el hash idéntico es del exacto), umbral PROPIO más duro
        "    SELECT LEAST(n.id, c.id), GREATEST(n.id, c.id), "
        "           n.title_n, c.title_n, n.loc, c.loc, 'i' "
        "    FROM nuevos_f n "
        "    JOIN firma c ON c.f = n.f AND c.f IS NOT NULL AND c.f <> '' "
        "         AND c.source_id = n.source_id AND c.id <> n.id "
        "         AND c.th <> n.th"
        "  ) u"
        ") "
        "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
        f"SELECT gen_random_uuid(), p.a, p.b, "
        f"       round(similarity({tn_n}, {tn_c})::numeric, 3) "
        "FROM pares p "
        f"WHERE similarity({tn_n}, {tn_c}) >= "
        "      CASE WHEN p.via = 'i' THEN CAST(:trgm_intra AS float4) "
        "           ELSE CAST(:trgm AS float4) END "
        f"  AND {loc_ok} "
        + _ON_CONFLICT
    )


# ⚠ BUMP OBLIGATORIO en el MISMO commit que cambie _loc_compat_sql o los
# tokens de remoto (auditoría C1 P3-2): la procedencia debe identificar QUÉ
# versión de la regla resolvió cada candidato.
_REVALIDATE_RULE = "rule:track-r-location-v2"


async def revalidate_pending_candidates(
    session: AsyncSession, apply: bool = False
) -> dict:
    """Barrido one-shot por REGLA uniforme (revisión FASE 2 P2-1, auditable):
    los candidatos PENDIENTES cuya ubicación viola la regla ratificada.
    `apply=False` (preview): cuenta + hash md5 de los ids ordenados, SIN
    escribir. `apply=True`: UPDATE atómico con procedencia
    (resolved_by=_REVALIDATE_RULE, resolved_at) y RETURNING id —
    mismo resumen, comparable con el preview. Decide LA REGLA sobre toda la
    tabla; jamás la pertenencia al holdout. Los resueltos no se tocan.
    Idempotente: segunda pasada ⇒ 0."""
    loc_ok = _loc_compat_sql(
        "coalesce(oa.content->>'location','')",
        "coalesce(ob.content->>'location','')",
    )
    base = (
        "FROM vacancies va, offer_revisions oa, vacancies vb, "
        "     offer_revisions ob "
        "WHERE dc.state = 'pending' "
        "  AND va.id = dc.vacancy_a "
        "  AND oa.id = va.current_offer_revision_id "
        "  AND vb.id = dc.vacancy_b "
        "  AND ob.id = vb.current_offer_revision_id "
        f"  AND NOT {loc_ok}"
    )
    if apply:
        ids = [
            str(r.id)
            for r in (
                await session.execute(
                    sa.text(
                        "UPDATE dedup_candidates dc SET state = 'rejected', "
                        f"  resolved_by = '{_REVALIDATE_RULE}', "
                        "  resolved_at = statement_timestamp() "
                        + base + " RETURNING dc.id"
                    )
                )
            ).all()
        ]
    else:
        ids = [
            str(r.id)
            for r in (
                await session.execute(
                    sa.text("SELECT dc.id FROM dedup_candidates dc, "
                            + base.replace("FROM ", "", 1) + " ORDER BY dc.id")
                )
            ).all()
        ]
    ids.sort()
    resumen = {
        "modo": "apply" if apply else "preview",
        "n": len(ids),
        "hash_ids": hashlib.md5(",".join(ids).encode()).hexdigest(),
    }
    logger.info("revalidate_pending_candidates: %s", resumen)
    return resumen


async def lexical_backfill(session: AsyncSession) -> int:
    """One-shot POST-DEPLOY del Track R (P1-1 de la revisión): pasada
    COMPLETA del generador LÉXICO sobre todo el corpus. El beat solo mira
    la ventana de 48 h — sin esto, los pares antiguos (el holdout entero)
    jamás recibirían candidatos léxicos y el gate seguiría midiendo al
    detector viejo. No re-ejecuta el ANN (miles de kNN innecesarios).
    Idempotente (upsert). Devuelve filas insertadas/actualizadas."""
    return (
        await session.execute(
            sa.text(_lex_sql(window=False)),
            {
                "trgm": float(settings.CORE_DEDUP_LEX_TRGM_MIN),
                "trgm_intra": float(settings.CORE_DEDUP_LEX_TRGM_INTRA_MIN),
                "maxfreq": int(settings.CORE_DEDUP_LEX_TOKEN_MAX_FREQ),
            },
        )
    ).rowcount


async def scan_semantic_candidates(
    session: AsyncSession, window_hours: int | None = None
) -> dict:
    """Una pasada del generador. Devuelve conteos JSON-serializables.
    `window_hours=None` ⇒ ventana CONFIGURADA (CORE_DEDUP_SCAN_WINDOW_H,
    el camino del beat); `window_hours=0` ⇒ pasada COMPLETA (P1-1: el
    docstring anterior decía lo contrario que el código)."""
    if window_hours is None:
        window_hours = int(settings.CORE_DEDUP_SCAN_WINDOW_H)
    sim_min = float(settings.CORE_DEDUP_SIM_MIN)
    k = int(settings.CORE_DEDUP_KNN)

    models = await embeddings.active_models(session)
    if not models:
        return {"status": "sin_modelo", "escaneadas": 0, "candidatos_nuevos": 0}
    model_id = models[0].id

    sql = _CORPUS_SQL
    params: dict = {"mid": model_id}
    if window_hours > 0:
        sql += "AND orv.created_at >= now() - make_interval(hours => :ventana) "
        params["ventana"] = window_hours
    nuevos = (await session.execute(sa.text(sql), params)).all()

    # UNDERFILL del HNSW (auditoría Nº2, IMPORTANTE 1): pgvector aplica el
    # WHERE DESPUÉS de sacar candidatos del índice aproximado — sin scan
    # iterativo, "LIMIT :k" puede devolver < k vecinos cross-source aunque
    # existan (reproducido: 2 de 5 con el GUC por defecto). MISMO patrón ya
    # probado en matching: ef_search acotado + iterative_scan strict_order
    # (sigue escaneando hasta llenar el LIMIT tras el filtro, con tope
    # MAX_SCAN_TUPLES) + FALLBACK EXACTO si aun así llegan menos filas que
    # el objetivo REAL (nº de vacantes elegibles de OTRAS fuentes, acotado
    # por k — un corpus pequeño no dispara el exacto en cada fila).
    ef_search = min(max(k, 40), 1000)
    await session.execute(sa.text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
    await session.execute(sa.text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
    await session.execute(
        sa.text(f"SET LOCAL hnsw.max_scan_tuples = {int(MAX_SCAN_TUPLES)}")
    )

    inserted = 0
    for row in nuevos:
        knn_params = {"vec": row.vector, "mid": model_id, "k": k,
                      "vid": row.id, "src": row.source_id, "loc": row.loc}
        vecinos = (
            await session.execute(sa.text(_KNN_SQL), knn_params)
        ).all()
        # Objetivo = elegibles REALES bajo el MISMO predicado (incl.
        # ubicación — P2-3 de la revisión: contar sin el guard disparaba el
        # fallback exacto cuando 0 era el resultado completo correcto).
        objetivo = (
            await session.execute(sa.text(_KNN_COUNT_SQL), knn_params)
        ).scalar_one()
        if len(vecinos) < objetivo:
            # Inanición REAL del scan acotado: el exacto responde siempre.
            await session.execute(sa.text("SET LOCAL enable_indexscan = off"))
            await session.execute(sa.text("SET LOCAL enable_bitmapscan = off"))
            vecinos = (
                await session.execute(sa.text(_KNN_SQL), knn_params)
            ).all()
            await session.execute(sa.text("SET LOCAL enable_indexscan = on"))
            await session.execute(sa.text("SET LOCAL enable_bitmapscan = on"))
        for n in vecinos:
            if float(n.sim) < sim_min:
                break  # ordenados por distancia: los siguientes son peores
            r = await session.execute(
                sa.text(_INSERT_SQL),
                {
                    "id": uuid.uuid4(),
                    "a": row.id,
                    "b": n.vacancy_id,
                    # Numeric(4,3): la similitud cabe siempre (0..1)
                    "sim": round(float(n.sim), 3),
                },
            )
            inserted += r.rowcount

    # Exactos intra-fuente: pase completo siempre (barato: un join indexado;
    # la idempotencia la da uq_dedup_pair).
    exactos = (await session.execute(sa.text(_EXACT_INTRA_SQL))).rowcount

    # Léxico cross-portal (R.2b): misma ventana incremental que el ANN.
    lex_params = {
        "trgm": float(settings.CORE_DEDUP_LEX_TRGM_MIN),
        "trgm_intra": float(settings.CORE_DEDUP_LEX_TRGM_INTRA_MIN),
        "maxfreq": int(settings.CORE_DEDUP_LEX_TOKEN_MAX_FREQ),
    }
    if window_hours > 0:
        lex_params["ventana"] = window_hours
    lexicos = (
        await session.execute(
            sa.text(_lex_sql(window=window_hours > 0)), lex_params
        )
    ).rowcount

    result = {
        "status": "ok",
        "escaneadas": len(nuevos),
        "candidatos_nuevos": inserted,
        "candidatos_exactos_intra": int(exactos),
        "candidatos_lexicos": int(lexicos),
    }
    if inserted or exactos or lexicos:  # P3: el éxito SOLO-léxico también
        logger.info("dedup_scan: %s", result)
    return result

"""Métricas por ciclo de la sombra (B-04, CONTRATOS_FASE_B.md §5/§6/§7).

Un CICLO es la ventana CALENDARIO [06:00, 06:00 del día siguiente) en hora
suiza (Europe/Zurich — el "CET" del contrato y el timezone del beat legacy y
del core): corta DESPUÉS del mantenimiento legacy (cleanup 03:30, dedup
04:00) y ANTES del arranque más temprano de la cosecha (08:00). `cycle_id` =
DATE del día de ARRANQUE de la ventana (corte determinista, PK de
`shadow_cycle_metrics` junto a metric/scope). cycle_id y ventana son
INYECTABLES (`cycle_id=`, `now=`): nada depende de la hora real en tests.

Las 10 métricas de §5 se computan con sus fórmulas EXACTAS y se PERSISTEN en
`shadow_cycle_metrics` (core0008a; upsert por PK — recomputar un ciclo es
idempotente). Además se guardan `ndcg@10_legacy` (la referencia del gate de
§6: mismo set, mismo IDCG, feed VISIBLE legacy espejo de get_results) y
`no_ingeribles` (alerta de §5, contada APARTE del minuendo de `perdida`).

DECISIONES documentadas (no obvias):
- `value` es NUMERIC NOT NULL (core0008a, INMUTABLE): el "NULL con details"
  del contrato para métricas sin datos (outbox sin samples, ciclo sin lotes)
  se representa con el CENTINELA `NO_DATA_VALUE` (−1) + `details.no_data` —
  los gates leen el flag, jamás el número; para un gate "sin datos" es
  NO demostrable ⇒ ok=False (conservador: no suma al contador de §6).
- `reenlace_pct` = (attaches + recycles) / encarnaciones tocadas, TODO sobre
  fuentes `legacy:*` (el churn que mide la sombra): attach = fila de
  link_evidence method='url_normalized' creada en el ciclo (el attach
  cross-source del sink; 'url_alias' NO re-enlaza nada); recycle =
  encarnación con seq > 1 abierta (first_seen_at) en el ciclo (guard de
  reciclado o reapertura tras cierre); tocada = encarnación con
  first_seen_at, last_seen_at o ended_at dentro del ciclo. Se persiste como
  RATIO 0..1 (el umbral de §6 es REENLACE_PCT_MAX = 0.05 ≡ 5%).
- `coste` (proxy sin unidad, [alerta] informativa): embeddings de ofertas
  computados en el ciclo (offer_embeddings.created_at) + evaluaciones nuevas
  (match_evaluations.created_at) + segundos de worker aproximados por
  Σ(finished_at − started_at) de los lotes del proyector. Los vectores de
  PERFIL no tienen timestamp (profile_embeddings sin created_at, esquema
  inmutable): quedan fuera y details lo declara — proxy honesto, no exacto.
- `no_ingeribles`: se cuenta desde public.jobs read-only pasando cada url
  candidata por la MISMA ruta de cuarentena del sink en Python
  (`_sink_quarantines_url` ≡ _preprocess + _limit_violations). Causas
  reales detectadas: url NULL (no construye RawListing), url CRUDA >
  MAX_URL_LEN=1000, url NORMALIZADA > 1000 (normalize_url puede AÑADIR un
  '/': una url de exactamente 1000 con path vacío queda en 1001) y
  ValueError de normalize_url (p.ej. 'Invalid IPv6 URL' con corchete
  desbalanceado). NUL/no-UTF8 no pueden existir en columnas text de
  Postgres y quedan fuera del conteo. Cuarentenado ⇒ no_ingeribles y JAMÁS
  el minuendo de `perdida` — un desalineamiento aquí sería un falso
  perdida>0 permanente que resetearía el contador de §6 para siempre.
- nDCG con IDCG = 0 (set congelado sin ninguna etiqueta > 0): la fórmula es
  indefinida — se persiste 0 con `details.no_medible` y el gate marca
  ok=False (visible, jamás un verde silencioso).
- dedup con denominador 0 (P1-2, rev. externa): un oráculo VACÍO o sin
  pares evaluables ya NO persiste 1.0 "vacuamente cierto" — se persiste el
  CENTINELA + details.no_data y el gate queda en ROJO (sin afirmaciones no
  hay nada DEMOSTRADO). Además `labels_ready` (gate nuevo) verifica la
  precondición del oráculo (DoD B-03): >= LABELS_MIN_FROZEN_SETS sets
  CONGELADOS con >= LABELS_MIN_JUDGMENTS_PER_SET juicios cada uno, >=
  LABELS_MIN_DEDUP_PAIRS pares dedup y >= LABELS_MIN_MAPPED_DEDUP_PAIRS
  pares MAPEABLES a vacantes core — sin oráculo al DoD, el ciclo no puede
  sumar al contador de §6.
- INMUTABILIDAD de ciclos sellados (P1-4, rev. externa): compute_cycle
  sobre un ciclo con métricas SELLADAS (finished_at) no recomputa — el
  recomputo usa el estado ACTUAL y reescribiría la historia (un rojo
  sellado podría volverse verde). Recomputar exige `force=True`, que
  además estampa `details.recomputed_at` en TODAS las filas del ciclo:
  gate_status (shadow/gate.py) trata un ciclo recomputado como NO
  computable para la racha (resetea igual que un rojo) y el informe lo
  señala. Matiz documentado: un cómputo PARCIAL de un ciclo aún abierto
  también sella — completarlo al cierre exigirá force (y marcará el ciclo).
- Percentiles (p99 outbox, p95 latencia): percentile_cont de Postgres
  (interpolación lineal) — un único método, verificable a mano.
- outbox por EDAD DE EVENTO + `outbox_dead` (P2-6, rev. externa parte 2):
  el muestreador guarda la edad del evento NO entregado más viejo
  (delivery.stats: clock_timestamp() − integration_outbox.created_at,
  pending E inflight, jamás negativa) — antes medía la distancia a
  next_attempt_at y un fallo con backoff futuro APLANABA el lag justo
  cuando crecía. Cada sample lleva además `dead_total`; el gate NUEVO
  `outbox_dead` es rojo si hubo algún evento en DEAD-LETTER en el ciclo:
  value = max(dead_total muestreado en el ciclo, conteo dead ACTUAL al
  cómputo — dead es terminal: lo muerto en el ciclo sigue muerto al
  cierre, y el conteo actual cubre ciclos sin samples). Siempre
  computable; el conteo va en details.
- latencia_p95 INCLUYE los lotes `recovered` (P2-5): una intención de lote
  huérfana (crash de la invocación) se cierra en la recuperación del
  proyector con finished_at=ahora y recovered=true — cuenta como lote
  LENTO, jamás desaparece del p95; details.lotes_recuperados lo expone.

La purga del staging (§7, asignada a B-04) borra de `shadow_change_log` lo
APLICADO con received_at < (cierre del ciclo actual − 7 días) PRESERVANDO
SIEMPRE la última fila `users` (op I/U aplicada) por pk: es la fuente de la
exclusión de usuarios inactivos del proyector (NOTA de shadow/projector.py —
borrarla haría olvidar la exclusión). Lo NO aplicado jamás se toca. También
poda los arrays de samples de `outbox_lag_p99` de ciclos ya fuera de
retención SOLO si su p99 quedó SELLADO (value != centinela; el array solo
ocupa) — una fila muestreada sin computar conserva sus samples. Idempotente.

El esquema legacy es PARÁMETRO (`legacy_schema`, mismo patrón que labels.py):
tests con esquema desechable, producción 'public' (GRANTs RO de §1). Aquí
SOLO se hace SELECT sobre legacy — el core jamás escribe en `public`.
"""

import json
import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core import matching
from jobhunt_core.delivery import stats as delivery_stats
from jobhunt_core.harvest.sink import MAX_URL_LEN, normalize_url
from jobhunt_core.shadow.labels import _check_legacy_schema, map_job_refs_to_vacancies
from jobhunt_core.shadow.projector import SHADOW_CONSUMER

logger = logging.getLogger(__name__)

# --------------------------------------------------------------- ciclo (§5)

CYCLE_TZ = ZoneInfo("Europe/Zurich")
CYCLE_START_HOUR = 6  # [06:00, 06:00 +1d) hora suiza

# ------------------------------------------------- métricas y umbrales (§6)

M_NDCG = "ndcg@10"
M_NDCG_LEGACY = "ndcg@10_legacy"  # referencia del gate, mismo set/IDCG
M_OVERLAP = "overlap@10"
M_DEDUP_PRECISION = "dedup_precision"
M_DEDUP_RECALL = "dedup_recall"
M_FALSOS_NEG = "falsos_negativos"
M_PERDIDA = "perdida"
M_NO_INGERIBLES = "no_ingeribles"
M_OUTBOX_LAG = "outbox_lag_p99"
M_OUTBOX_DEAD = "outbox_dead"  # dead-letter en el ciclo ⇒ rojo (P2-6)
M_LATENCIA = "latencia_p95"
M_COSTE = "coste"
M_REENLACE = "reenlace_pct"
M_LABELS_READY = "labels_ready"  # precondición del oráculo (P1-2, DoD B-03)

SCOPE_GLOBAL = "global"
NDCG_K = 10  # top-K de ndcg@10 / overlap@10 (denominador FIJO del overlap)

# Umbrales RATIFICADOS 2026-07-24 (§6) — constantes con nombre.
NDCG_MIN = 0.60                # [gate] ndcg@10 >= 0.60 por perfil
NDCG_LEGACY_MARGIN = 0.05      # [gate] y >= ndcg legacy (mismo set) - 0.05
DEDUP_PRECISION_MIN = 0.95     # [gate]
DEDUP_RECALL_MIN = 0.90        # [gate]
FN_MIN_RELEVANCE = 2           # juicios "relevantes" del numerador/denominador
FN_STRICT_BELOW = 50           # < 50 juicios rel>=2 => 0 permitidos (§6)
FN_MAX_RATIO = 0.02            # >= 50 juicios rel>=2 => <= 2%
PERDIDA_MAX = 0                # [gate] estricto (DoD B.2 "cero pérdida")
OUTBOX_LAG_P99_MAX_S = 300.0   # [gate]
LATENCIA_P95_MAX_S = 600.0     # [gate]
REENLACE_PCT_MAX = 0.05        # [alerta] <= 5%/ciclo (ratio 0..1)
STAGING_BACKLOG_GRACE_S = 3600  # "change_log sin applied_at > 1h" (§5)
STAGING_RETENTION_DAYS = 7     # retención §2: ciclos cerrados + 7 días

# Precondición del ORÁCULO (gate `labels_ready`, P1-2 — DoD B-03/§4): sin
# esto los gates de calidad no son DEMOSTRABLES y el ciclo no puede sumar.
LABELS_MIN_FROZEN_SETS = 2         # >= 2 sets CONGELADOS…
LABELS_MIN_JUDGMENTS_PER_SET = 30  # …con >= 30 juicios cada uno (DoD B-03)
LABELS_MIN_DEDUP_PAIRS = 50        # >= 50 pares dedup etiquetados
LABELS_MIN_MAPPED_DEDUP_PAIRS = 20  # >= N pares MAPEABLES a vacantes core

# value NUMERIC NOT NULL (core0008a inmutable): centinela del "NULL con
# details" del contrato. Los gates leen details.no_data, NUNCA este número.
NO_DATA_VALUE = -1

KIND_GATE = "gate"
KIND_ALERTA = "alerta"
# Marca de cada métrica según §6 (RATIFICADAS).
METRIC_KINDS: dict[str, str] = {
    M_NDCG: KIND_GATE,
    M_OVERLAP: KIND_ALERTA,           # informativa (el set es el oráculo)
    M_DEDUP_PRECISION: KIND_GATE,
    M_DEDUP_RECALL: KIND_GATE,
    M_FALSOS_NEG: KIND_GATE,
    M_PERDIDA: KIND_GATE,
    M_NO_INGERIBLES: KIND_ALERTA,     # alerta si > 0
    M_OUTBOX_LAG: KIND_GATE,
    M_OUTBOX_DEAD: KIND_GATE,         # dead_total > 0 en el ciclo ⇒ rojo (P2-6)
    M_LATENCIA: KIND_GATE,
    M_COSTE: KIND_ALERTA,             # proxy informativo, sin umbral duro
    M_REENLACE: KIND_ALERTA,
    M_LABELS_READY: KIND_GATE,        # precondición del oráculo (P1-2)
}
# Métricas globales que TODO ciclo computado debe tener (gates sin fila =
# sin datos = ok False; alertas sin fila no inventan estado).
_EXPECTED_GLOBAL = (
    M_LABELS_READY, M_DEDUP_PRECISION, M_DEDUP_RECALL, M_PERDIDA,
    M_NO_INGERIBLES, M_OUTBOX_LAG, M_OUTBOX_DEAD, M_LATENCIA, M_COSTE,
    M_REENLACE,
)

# Espejo de backend/models/match_result.py::NEGATIVE_FEEDBACK (el feed
# VISIBLE de get_results excluye dismissed/thumbs_down) — valores constantes
# de módulo, jamás input del usuario.
LEGACY_NEGATIVE_FEEDBACK = ("dismissed", "thumbs_down")
_IN_NEGATIVE = ", ".join(f"'{f}'" for f in LEGACY_NEGATIVE_FEEDBACK)

# Método de link_evidence que ES un attach (sink._create_incarnations:
# 'url_normalized'); 'url_alias' es un alias registrado, no re-enlaza.
ATTACH_METHOD = "url_normalized"


def cycle_bounds(cycle_id: date) -> tuple[datetime, datetime]:
    """[inicio, fin) de la ventana del ciclo `cycle_id` en CYCLE_TZ."""
    start = datetime.combine(cycle_id, time(CYCLE_START_HOUR), tzinfo=CYCLE_TZ)
    end = datetime.combine(
        cycle_id + timedelta(days=1), time(CYCLE_START_HOUR), tzinfo=CYCLE_TZ
    )
    return start, end


def current_cycle_id(now: datetime | None = None) -> date:
    """Ciclo ABIERTO al que pertenece `now` (por defecto, ahora real)."""
    local = (now or datetime.now(timezone.utc)).astimezone(CYCLE_TZ)
    day = local.date()
    return day - timedelta(days=1) if local.hour < CYCLE_START_HOUR else day


def latest_closed_cycle_id(now: datetime | None = None) -> date:
    """El ciclo CERRADO más reciente: el anterior al abierto."""
    return current_cycle_id(now) - timedelta(days=1)


# ------------------------------------------------------------- persistencia


async def _upsert_metric(
    session: AsyncSession,
    cycle_id: date,
    metric: str,
    scope: str,
    value,
    details: dict,
    merge_details: bool = False,
) -> None:
    """Upsert por PK(cycle_id, metric, scope); finished_at sella el cómputo
    (patrón harvest_runs). `merge_details=True` FUSIONA sobre los details
    existentes (obligatorio en outbox_lag_p99: la fila ya lleva los samples
    del muestreador y el cómputo no debe pisarlos)."""
    details_expr = (
        "shadow_cycle_metrics.details || EXCLUDED.details"
        if merge_details
        else "EXCLUDED.details"
    )
    await session.execute(
        sa.text(
            "INSERT INTO shadow_cycle_metrics "
            "(cycle_id, metric, scope, value, details, finished_at) "
            "VALUES (:c, :m, :s, :v, CAST(:d AS jsonb), clock_timestamp()) "
            "ON CONFLICT (cycle_id, metric, scope) DO UPDATE SET "
            f"value = EXCLUDED.value, details = {details_expr}, "
            "finished_at = clock_timestamp()"
        ),
        {
            "c": cycle_id, "m": metric, "s": scope, "v": value,
            "d": json.dumps(details, default=str),
        },
    )


# ----------------------------------------------------- muestreador (outbox)


async def sample_outbox_lag(
    session: AsyncSession, now: datetime | None = None
) -> dict:
    """Muestreador LIGERO de `oldest_pending_s` (delivery.stats, §5): appendea
    {ts, oldest_pending_s, dead_total} al array details.samples de la fila del
    ciclo ABIERTO en este momento (metric=outbox_lag_p99, scope=global) vía
    upsert concatenando. P2-6: `oldest_pending_s` es la EDAD DEL EVENTO no
    entregado más viejo (pending E inflight, jamás negativa — nombre
    conservado por compatibilidad de samples) y `dead_total` alimenta el gate
    `outbox_dead`. `value` nace con el centinela (aún sin computar) y NO se
    toca en el conflict: si compute_cycle ya selló el p99, un sample tardío
    no lo pisa. La CADENCIA (5 min) la cablea B-05 — aquí solo la operación."""
    st = await delivery_stats(session)
    lag, dead_total = st["oldest_pending_s"], st["dead_total"]
    cid = current_cycle_id(now)
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sample = json.dumps(
        [{"ts": ts.isoformat(), "oldest_pending_s": lag, "dead_total": dead_total}]
    )
    n_samples = (
        await session.execute(
            sa.text(
                "INSERT INTO shadow_cycle_metrics "
                "(cycle_id, metric, scope, value, details) "
                "VALUES (:c, :m, :s, :nodata, "
                "        jsonb_build_object('samples', CAST(:j AS jsonb))) "
                "ON CONFLICT (cycle_id, metric, scope) DO UPDATE SET "
                "details = jsonb_set(shadow_cycle_metrics.details, '{samples}', "
                "  COALESCE(shadow_cycle_metrics.details->'samples', "
                "           '[]'::jsonb) || CAST(:j AS jsonb)) "
                "RETURNING jsonb_array_length(details->'samples')"
            ),
            {
                "c": cid, "m": M_OUTBOX_LAG, "s": SCOPE_GLOBAL,
                "nodata": NO_DATA_VALUE, "j": sample,
            },
        )
    ).scalar_one()
    return {
        "cycle_id": cid.isoformat(),
        "oldest_pending_s": lag,
        "dead_total": dead_total,
        "samples": int(n_samples),
    }


# ------------------------------------------------------- cómputo del ciclo


async def compute_cycle(
    session: AsyncSession,
    cycle_id: date | None = None,
    legacy_schema: str = "public",
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Computa y PERSISTE las métricas de §5 del ciclo CERRADO más reciente
    (o el `cycle_id` indicado — replay/backfill).

    INMUTABILIDAD (P1-4, rev. externa): un ciclo con métricas SELLADAS
    (alguna fila con finished_at) NO se recomputa — el recomputo usa el
    estado ACTUAL de la BD y reescribiría un veredicto histórico (un rojo
    sellado podría volverse verde). Sin `force=True` se devuelve un resumen
    con `skipped_sealed` y NO se toca nada; con `force=True` se recomputa y
    se estampa `details.recomputed_at` en TODAS las filas del ciclo —
    gate_status trata ese ciclo como NO computable para la racha.

    Matices del recomputo forzado (idempotente, upsert por PK): (a) las
    filas profile:<id> de perfiles que YA no se miden (p.ej. borrado GDPR,
    set descongelado) se ELIMINAN en la misma transacción del cómputo — no
    quedan huérfanas del cálculo anterior; (b) un outbox_lag_p99 ya SELLADO
    cuyos samples podó purge_staging se PRESERVA (no se machaca con el
    centinela sin-datos; la métrica no aparece en el resumen de ese
    recomputo). Devuelve un resumen JSON-serializable (tarea Celery)."""
    _check_legacy_schema(legacy_schema)
    cid = cycle_id or latest_closed_cycle_id(now)
    start, end = cycle_bounds(cid)
    moment = now or datetime.now(timezone.utc)
    sealed = await _cycle_sealed(session, cid)
    if sealed and not force:
        logger.warning(
            "metrics: ciclo %s ya SELLADO — INMUTABLE sin force=True (P1-4): "
            "no se recomputa", cid,
        )
        return {
            "cycle_id": cid.isoformat(),
            "window": [start.isoformat(), end.isoformat()],
            "skipped_sealed": True,
            "profiles_measured": 0,
            "metrics": {},
        }
    if end > moment:
        logger.warning(
            "metrics: ciclo %s aún ABIERTO (fin %s > ahora) — cómputo parcial",
            cid, end.isoformat(),
        )
    computed: dict[str, float | int | None] = {}
    profiles = await _measured_profiles(session)
    for prof in profiles:
        rows = await _profile_metric_rows(session, prof, legacy_schema)
        for metric, scope, value, details in rows:
            await _upsert_metric(session, cid, metric, scope, value, details)
            computed[f"{metric}::{scope}"] = value
    # Recompute con MENOS perfiles (p.ej. uno borrado por GDPR): las filas
    # profile:<id> del cálculo anterior cuyo scope ya no se mide se eliminan
    # en la MISMA transacción — sin filas huérfanas de perfiles idos.
    await session.execute(
        sa.text(
            "DELETE FROM shadow_cycle_metrics "
            "WHERE cycle_id = :c AND scope LIKE 'profile:%' "
            "  AND scope <> ALL(:scopes)"
        ),
        {"c": cid, "scopes": [f"profile:{p.id}" for p in profiles]},
    )
    for metric, value, details, merge in await _global_metric_rows(
        session, cid, start, end, legacy_schema, moment
    ):
        await _upsert_metric(
            session, cid, metric, SCOPE_GLOBAL, value, details,
            merge_details=merge,
        )
        computed[metric] = value
    summary = {
        "cycle_id": cid.isoformat(),
        "window": [start.isoformat(), end.isoformat()],
        "profiles_measured": len(profiles),
        "metrics": computed,
    }
    if sealed:
        # Recomputo FORZADO de un ciclo sellado (P1-4): trazado en TODAS las
        # filas del ciclo (también las preservadas sin upsert, p.ej. un p99
        # post-purga) — la racha de §6 lo tratará como no computable.
        ts = moment.astimezone(timezone.utc).isoformat()
        await session.execute(
            sa.text(
                "UPDATE shadow_cycle_metrics "
                "SET details = details || jsonb_build_object('recomputed_at', "
                "CAST(:ts AS text)) WHERE cycle_id = :c"
            ),
            {"ts": ts, "c": cid},
        )
        summary["recomputed_at"] = ts
        logger.warning(
            "metrics: ciclo %s RECOMPUTADO con force=True — recomputed_at=%s "
            "(no computable para la racha de §6)", cid, ts,
        )
    return summary


async def _cycle_sealed(session: AsyncSession, cid: date) -> bool:
    """True si el ciclo tiene alguna métrica SELLADA (finished_at) — el
    placeholder del muestreador de outbox no sella."""
    return bool(
        (
            await session.execute(
                sa.text(
                    "SELECT 1 FROM shadow_cycle_metrics "
                    "WHERE cycle_id = :c AND finished_at IS NOT NULL LIMIT 1"
                ),
                {"c": cid},
            )
        ).scalar_one_or_none()
    )


async def _measured_profiles(session: AsyncSession) -> list:
    """Perfiles del consumer sombra con set CONGELADO (frozen_at NOT NULL,
    §4: el oráculo no se mueve durante la medición). Si un perfil tiene
    varios sets congelados (rondas), gana el congelado MÁS RECIENTE —
    determinista con desempates fijos."""
    return (
        await session.execute(
            sa.text(
                "SELECT DISTINCT ON (p.id) p.id, p.external_ref, "
                "  ls.id AS set_id, ls.name AS set_name "
                "FROM profiles p "
                "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cn "
                "JOIN labeled_sets ls ON ls.profile_id = p.id "
                "  AND ls.frozen_at IS NOT NULL "
                "ORDER BY p.id, ls.frozen_at DESC, ls.created_at DESC, ls.id"
            ),
            {"cn": SHADOW_CONSUMER},
        )
    ).all()


# ---------------------------------------------------- métricas por perfil


def _dcg(rels) -> float:
    """DCG@k con relevancia graduada (§5): Σ (2^rel_i − 1)/log2(i+1), i=1.."""
    return sum(
        (2 ** rel - 1) / math.log2(pos + 2) for pos, rel in enumerate(rels)
    )


async def _profile_metric_rows(
    session: AsyncSession, prof, legacy_schema: str
) -> list[tuple]:
    """[(metric, scope, value, details)] del perfil: ndcg@10 core y legacy
    (MISMO set congelado y MISMO IDCG), overlap@10 y falsos_negativos."""
    scope = f"profile:{prof.id}"
    judgments = {
        r.job_ref: r.relevance
        for r in (
            await session.execute(
                sa.text(
                    "SELECT job_ref, relevance FROM labeled_judgments "
                    "WHERE set_id = :sid"
                ),
                {"sid": prof.set_id},
            )
        ).all()
    }
    legacy_top = await _legacy_visible_top(
        session, legacy_schema, prof.external_ref
    )
    # Mapeo por CUALQUIER encarnación (§4) de refs juzgados Y del feed legacy
    # (el overlap necesita llevar el top legacy al espacio de vacantes).
    mapping = await map_job_refs_to_vacancies(
        session, sorted(set(judgments) | set(legacy_top))
    )
    # Relevancia por vacante = MAX de los refs juzgados que mapean a ella
    # (dos refs sobre la misma vacante = el core los considera la MISMA
    # oferta vía attach; el juicio más alto manda).
    vac_rel: dict = {}
    for ref, rel in judgments.items():
        vid = mapping.get(ref)
        if vid is not None:
            vac_rel[vid] = max(vac_rel.get(vid, 0), rel)

    feed_rows, _cur = await matching.feed(session, prof.id, limit=NDCG_K)
    idcg = _dcg(sorted(judgments.values(), reverse=True)[:NDCG_K])
    base = {"set_id": prof.set_id, "set_name": prof.set_name}

    rows = [
        _ndcg_core_row(scope, feed_rows, vac_rel, idcg, base),
        _ndcg_legacy_row(scope, legacy_top, judgments, idcg, base),
        _overlap_row(scope, feed_rows, legacy_top, mapping, base),
        await _falsos_negativos_row(
            session, prof, scope, judgments, mapping, base
        ),
    ]
    return rows


def _ndcg_pair(dcg: float, idcg: float) -> tuple[float, dict]:
    """(value, details comunes) — IDCG=0 ⇒ no medible (documentado): 0 con
    flag, jamás un verde silencioso."""
    details = {"dcg": round(dcg, 6), "idcg": round(idcg, 6)}
    if idcg <= 0:
        details["no_medible"] = True
        return 0.0, details
    return round(dcg / idcg, 6), details


def _ndcg_core_row(scope, feed_rows, vac_rel, idcg, base) -> tuple:
    rels = [vac_rel.get(r.vacancy_id, 0) for r in feed_rows]
    value, details = _ndcg_pair(_dcg(rels), idcg)
    details |= base | {
        "feed": [
            {
                "vacancy_id": str(r.vacancy_id),
                "score": float(r.score_final),
                "rel": rel,
            }
            for r, rel in zip(feed_rows, rels)
        ],
    }
    return M_NDCG, scope, value, details


def _ndcg_legacy_row(scope, legacy_top, judgments, idcg, base) -> tuple:
    """nDCG del feed VISIBLE legacy contra el MISMO set y MISMO IDCG (§5/§6:
    no se exige superarlo, sí no degradar más de 0.05)."""
    rels = [judgments.get(ref, 0) for ref in legacy_top]
    value, details = _ndcg_pair(_dcg(rels), idcg)
    details |= base | {
        "top": [
            {"job_ref": ref, "rel": rel} for ref, rel in zip(legacy_top, rels)
        ],
    }
    return M_NDCG_LEGACY, scope, value, details


def _overlap_row(scope, feed_rows, legacy_top, mapping, base) -> tuple:
    """overlap@10 = |top10_core ∩ top10_legacy| / 10 (denominador FIJO, §5);
    la intersección se hace en el espacio de VACANTES (top legacy mapeado por
    cualquier encarnación; un ref legacy sin slot core no puede intersecar)."""
    core_vacs = {r.vacancy_id for r in feed_rows}
    legacy_vacs = {mapping[ref] for ref in legacy_top if ref in mapping}
    inter = len(core_vacs & legacy_vacs)
    details = base | {
        "core_top": len(core_vacs),
        "legacy_top": len(legacy_top),
        "legacy_mapeados": len(legacy_vacs),
        "interseccion": inter,
    }
    return M_OVERLAP, scope, round(inter / NDCG_K, 6), details


async def _falsos_negativos_row(
    session, prof, scope, judgments, mapping, base
) -> tuple:
    """§5: #{rel>=2 del set, PRESENTES en corpus core, AUSENTES del feed} /
    #{rel>=2 presentes}. Presente = mapea (cualquier encarnación) a una
    vacante VIVA (archived/merged NULL — la condición de vacante del feed);
    ausente = esa vacante no está en el feed COMPLETO del perfil (espejo del
    predicado de matching.feed sin límite). El MODO del gate (§6) se decide
    por el nº de juicios rel>=2 DEL SET: < 50 ⇒ 0 permitidos; >= 50 ⇒ <= 2%."""
    rel2 = sorted(r for r, rel in judgments.items() if rel >= FN_MIN_RELEVANCE)
    present = await _present_in_corpus(session, rel2, mapping)
    feed_vacs = await _feed_vacancies_all(session, prof.id)
    absent = sorted(ref for ref, vid in present.items() if vid not in feed_vacs)
    value = round(len(absent) / len(present), 6) if present else 0.0
    mode = "estricto_0" if len(rel2) < FN_STRICT_BELOW else "ratio_2pct"
    details = base | {
        "modo": mode,
        "rel2_en_set": len(rel2),
        "presentes_en_corpus": len(present),
        "n_ausentes": len(absent),
        "ausentes": absent[:50],  # muestra acotada, no crece sin límite
    }
    return M_FALSOS_NEG, scope, value, details


async def _legacy_visible_top(
    session: AsyncSession, legacy_schema: str, external_ref: str
) -> list[str]:
    """Top-10 del feed VISIBLE legacy — espejo READ-ONLY de get_results
    (match_result_service): JOIN jobs is_active AND duplicate_of IS NULL,
    excluye NEGATIVE_FEEDBACK (dismissed/thumbs_down), ORDER BY score_final
    DESC. Desempate por job_hash AÑADIDO (get_results no lo necesita; una
    métrica sí exige orden total determinista)."""
    return list(
        (
            await session.execute(
                sa.text(
                    f"SELECT mr.job_hash FROM {legacy_schema}.match_results mr "
                    f"JOIN {legacy_schema}.jobs j ON j.hash = mr.job_hash "
                    f"  AND j.is_active AND j.duplicate_of IS NULL "
                    f"WHERE mr.user_id::text = :ref "
                    f"  AND (mr.feedback IS NULL "
                    f"       OR mr.feedback NOT IN ({_IN_NEGATIVE})) "
                    f"ORDER BY mr.score_final DESC, mr.job_hash LIMIT :k"
                ),
                {"ref": external_ref, "k": NDCG_K},
            )
        ).scalars().all()
    )


async def _present_in_corpus(
    session: AsyncSession, refs: list[str], mapping: dict
) -> dict:
    """ref → vacancy_id de los refs "presentes en corpus core" (§5): mapean
    (cualquier encarnación) a una vacante VIVA."""
    mapped = {ref: mapping[ref] for ref in refs if ref in mapping}
    alive = await _alive_vacancies(session, set(mapped.values()))
    return {ref: vid for ref, vid in mapped.items() if vid in alive}


async def _alive_vacancies(session: AsyncSession, vids: set) -> set:
    """Vacantes VIVAS (la condición de vacante del feed de A-08/A-09)."""
    if not vids:
        return set()
    return set(
        (
            await session.execute(
                sa.text(
                    "SELECT id FROM vacancies WHERE id = ANY(:v) "
                    "AND archived_at IS NULL AND merged_into IS NULL"
                ),
                {"v": sorted(vids, key=str)},
            )
        ).scalars().all()
    )


async def _feed_vacancies_all(session: AsyncSession, profile_id) -> set:
    """Vacantes del feed COMPLETO del perfil: espejo EXACTO del predicado de
    matching.feed (evaluación vigente + no-dismissed + vacante viva) sin
    paginar — el denominador de falsos_negativos no depende del top-K."""
    return set(
        (
            await session.execute(
                sa.text(
                    "SELECT s.vacancy_id FROM profile_vacancy_state s "
                    "JOIN match_evaluations e ON e.id = s.current_eval_id "
                    "  AND e.profile_id = s.profile_id "
                    "  AND e.vacancy_id = s.vacancy_id "
                    "JOIN vacancies v ON v.id = s.vacancy_id "
                    "  AND v.archived_at IS NULL AND v.merged_into IS NULL "
                    "WHERE s.profile_id = :pid AND s.dismissed_at IS NULL"
                ),
                {"pid": profile_id},
            )
        ).scalars().all()
    )


# ------------------------------------------------------ métricas globales


async def _global_metric_rows(
    session: AsyncSession,
    cid: date,
    start: datetime,
    end: datetime,
    legacy_schema: str,
    moment: datetime,
) -> list[tuple]:
    """[(metric, value, details, merge_details)] de las métricas globales.
    outbox_lag_p99 puede devolver None (p99 sellado post-purga): se omite
    del upsert — la fila persistida se preserva tal cual."""
    rows: list[tuple] = []
    rows.append(await _labels_ready_row(session))
    rows += await _dedup_rows(session)
    rows += await _perdida_rows(session, legacy_schema, moment)
    lag_row = await _outbox_lag_row(session, cid)
    if lag_row is not None:
        rows.append(lag_row)
    rows.append(await _outbox_dead_row(session, cid))
    rows.append(await _latencia_row(session, start, end))
    rows.append(await _coste_row(session, start, end))
    rows.append(await _reenlace_row(session, start, end))
    return rows


async def _dedup_rows(session: AsyncSession) -> list[tuple]:
    """dedup_precision = TP/(TP+FP) y dedup_recall = TP/(TP+FN) sobre
    labeled_dedup_pairs (§5). "Core dice duplicate" = misma vacante (attach,
    mapeo por CUALQUIER encarnación) O par en dedup_candidates con state <>
    'rejected'. Pares con algún ref sin slot core = no evaluables (details).

    Denominador 0 (P1-2, rev. externa): SIN pares evaluables en ese
    denominador NO hay nada demostrado — se persiste el CENTINELA +
    details.no_data (jamás un 1.0 "vacuamente cierto" que pondría el gate
    en verde con el oráculo vacío); el gate queda en ROJO y `labels_ready`
    señala la precondición incumplida. details deja los conteos para
    auditarlo."""
    pairs = (
        await session.execute(
            sa.text(
                "SELECT job_ref_a, job_ref_b, verdict FROM labeled_dedup_pairs"
            )
        )
    ).all()
    refs = sorted({r for p in pairs for r in (p.job_ref_a, p.job_ref_b)})
    mapping = await map_job_refs_to_vacancies(session, refs)
    candidate_pairs = await _dedup_candidate_pairs(
        session, sorted(set(mapping.values()), key=str)
    )
    c = _dedup_confusion(pairs, mapping, candidate_pairs)
    details = c | {"pares": len(pairs)}
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    rows: list[tuple] = []
    for metric, denom in (
        (M_DEDUP_PRECISION, tp + fp),
        (M_DEDUP_RECALL, tp + fn),
    ):
        if denom:
            rows.append((metric, round(tp / denom, 6), details, False))
        else:
            rows.append((
                metric,
                NO_DATA_VALUE,
                details | {
                    "no_data": True,
                    "nota": "sin pares evaluables en el denominador: "
                            "no demostrable (P1-2)",
                },
                False,
            ))
    return rows


async def _labels_ready_row(session: AsyncSession) -> tuple:
    """Gate `labels_ready` (P1-2): precondición del ORÁCULO según el DoD de
    B-03/§4 — >= LABELS_MIN_FROZEN_SETS sets CONGELADOS con >=
    LABELS_MIN_JUDGMENTS_PER_SET juicios cada uno, >= LABELS_MIN_DEDUP_PAIRS
    pares dedup etiquetados y >= LABELS_MIN_MAPPED_DEDUP_PAIRS pares con
    AMBOS refs mapeables a vacantes core (sin mapeo, dedup no es evaluable).
    value 1 = precondición cumplida; 0 = gate ROJO (el ciclo no puede sumar
    al contador de §6 con un oráculo que no da para medir)."""
    frozen_total = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM labeled_sets WHERE frozen_at IS NOT NULL"
            )
        )
    ).scalar_one()
    frozen_ok = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM ("
                "  SELECT ls.id FROM labeled_sets ls "
                "  JOIN labeled_judgments j ON j.set_id = ls.id "
                "  WHERE ls.frozen_at IS NOT NULL "
                "  GROUP BY ls.id HAVING count(*) >= :minj) q"
            ),
            {"minj": LABELS_MIN_JUDGMENTS_PER_SET},
        )
    ).scalar_one()
    pairs = (
        await session.execute(
            sa.text("SELECT job_ref_a, job_ref_b FROM labeled_dedup_pairs")
        )
    ).all()
    refs = sorted({r for p in pairs for r in (p.job_ref_a, p.job_ref_b)})
    mapping = await map_job_refs_to_vacancies(session, refs)
    mapped_pairs = sum(
        1 for p in pairs if p.job_ref_a in mapping and p.job_ref_b in mapping
    )
    ok = (
        int(frozen_ok) >= LABELS_MIN_FROZEN_SETS
        and len(pairs) >= LABELS_MIN_DEDUP_PAIRS
        and mapped_pairs >= LABELS_MIN_MAPPED_DEDUP_PAIRS
    )
    details = {
        "sets_congelados": int(frozen_total),
        "sets_congelados_ok": int(frozen_ok),
        "pares_dedup": len(pairs),
        "pares_mapeables": mapped_pairs,
        "umbrales": {
            "min_sets_congelados": LABELS_MIN_FROZEN_SETS,
            "min_juicios_por_set": LABELS_MIN_JUDGMENTS_PER_SET,
            "min_pares_dedup": LABELS_MIN_DEDUP_PAIRS,
            "min_pares_mapeables": LABELS_MIN_MAPPED_DEDUP_PAIRS,
        },
    }
    return M_LABELS_READY, (1 if ok else 0), details, False


def _dedup_confusion(pairs, mapping: dict, candidate_pairs: set) -> dict:
    """Matriz de confusión de los pares etiquetados frente al veredicto del
    core (misma vacante O candidato no rechazado)."""
    tp = fp = fn = tn = unmapped = 0
    for p in pairs:
        va, vb = mapping.get(p.job_ref_a), mapping.get(p.job_ref_b)
        if va is None or vb is None:
            unmapped += 1
            continue
        says_dup = va == vb or frozenset((va, vb)) in candidate_pairs
        if p.verdict == "duplicate":
            tp, fn = (tp + 1, fn) if says_dup else (tp, fn + 1)
        else:
            fp, tn = (fp + 1, tn) if says_dup else (fp, tn + 1)
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "no_evaluables_sin_mapeo": unmapped,
    }


async def _dedup_candidate_pairs(session: AsyncSession, vac_ids) -> set:
    """Pares {a,b} de dedup_candidates con state != rejected que tocan las
    vacantes mapeadas (pending y confirmed cuentan como "core dice dup")."""
    if not vac_ids:
        return set()
    return {
        frozenset((r.vacancy_a, r.vacancy_b))
        for r in (
            await session.execute(
                sa.text(
                    "SELECT vacancy_a, vacancy_b FROM dedup_candidates "
                    "WHERE state <> 'rejected' AND "
                    "(vacancy_a = ANY(:v) OR vacancy_b = ANY(:v))"
                ),
                {"v": vac_ids},
            )
        ).all()
    }


def _sink_quarantines_url(url: str | None) -> bool:
    """True si el SINK cuarentenaría un listing legacy con esta url — la
    MISMA ruta que _preprocess/_limit_violations, en Python (la frontera de
    la partición vivos/no_ingeribles debe coincidir EXACTAMENTE con la del
    sink o aparece un falso perdida>0 permanente): url NULL no construye
    RawListing; normalize_url puede lanzar ValueError (p.ej. 'Invalid IPv6
    URL' con corchete desbalanceado); y el límite aplica a la longitud
    CRUDA *y* a la NORMALIZADA (normalize_url puede AÑADIR un '/': una url
    de exactamente MAX_URL_LEN con path vacío queda en MAX_URL_LEN+1)."""
    if url is None:
        return True
    try:
        url_norm = normalize_url(url)
    except ValueError:  # el mismo caso que captura _preprocess
        return True
    return len(url) > MAX_URL_LEN or len(url_norm) > MAX_URL_LEN


async def _perdida_rows(
    session: AsyncSession, legacy_schema: str, moment: datetime
) -> list[tuple]:
    """perdida (§5, [gate] = 0) = #{public.jobs is_active AND duplicate_of
    IS NULL AND no-cuarentenado} − #{slots legacy:* con encarnación ACTIVA}
    + #{change_log sin applied_at desde hace > 1h}. La partición vivos /
    no_ingeribles pasa cada url por la MISMA ruta de cuarentena del sink
    (_sink_quarantines_url): cuarentenado ⇒ no_ingeribles, JAMÁS el
    minuendo (nunca pérdida silenciosa ni falso perdida>0). COSTE: una
    única pasada sobre (hash, url) de los jobs activos no duplicados —
    dos columnas cortas, ~5k filas en producción, una vez por ciclo."""
    jobs = (
        await session.execute(
            sa.text(
                f"SELECT hash, url FROM {legacy_schema}.jobs "
                f"WHERE is_active AND duplicate_of IS NULL"
            )
        )
    ).all()
    quarantined = [j.hash for j in jobs if _sink_quarantines_url(j.url)]
    vivos = len(jobs) - len(quarantined)
    active_slots = (
        await session.execute(
            sa.text(
                "SELECT count(DISTINCT l.external_id) FROM source_listings l "
                "JOIN sources s ON s.id = l.source_id "
                "  AND s.name LIKE 'legacy:%' "
                "JOIN source_listing_incarnations i "
                "  ON i.source_listing_id = l.id AND i.ended_at IS NULL"
            )
        )
    ).scalar_one()
    backlog = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM shadow_change_log "
                "WHERE applied_at IS NULL AND received_at < :cutoff"
            ),
            {"cutoff": moment - timedelta(seconds=STAGING_BACKLOG_GRACE_S)},
        )
    ).scalar_one()
    perdida = vivos - int(active_slots) + int(backlog)
    details = {
        "legacy_activos_ingeribles": vivos,
        "slots_legacy_activos": int(active_slots),
        "staging_sin_aplicar_1h": int(backlog),
        "gracia_backlog_s": STAGING_BACKLOG_GRACE_S,
    }
    ni_details = {
        "fuente": (
            "public.jobs activos no duplicados cuya url cuarentenaría el "
            "sink — MISMA ruta que _preprocess/_limit_violations: url NULL, "
            f"cruda O normalizada > {MAX_URL_LEN} (normalize_url puede "
            "añadir un '/'), o ValueError de normalize_url; NUL/no-UTF8 no "
            "pueden existir en text de Postgres"
        ),
        "max_url_len": MAX_URL_LEN,
        "cuarentenados_muestra": quarantined[:50],  # acotada, no crece
    }
    return [
        (M_PERDIDA, perdida, details, False),
        (M_NO_INGERIBLES, len(quarantined), ni_details, False),
    ]


async def _outbox_lag_row(session: AsyncSession, cid: date) -> tuple | None:
    """p99 (percentile_cont) de los samples del muestreador guardados en la
    fila del ciclo. merge_details=True: el upsert FUSIONA sobre los samples
    existentes en vez de pisarlos. Sin samples ⇒ centinela + no_data, SALVO
    p99 ya SELLADO cuyos samples podó purge_staging (fila existente con
    value != centinela y samples_pruned o no_data=False): devuelve None y
    compute_cycle NO upserta — el valor histórico se PRESERVA (recomputar
    un ciclo purgado no lo destruye)."""
    row = (
        await session.execute(
            sa.text(
                "SELECT percentile_cont(0.99) WITHIN GROUP "
                "  (ORDER BY (s->>'oldest_pending_s')::float8) AS p99, "
                "count(*) AS n "
                "FROM shadow_cycle_metrics m "
                "CROSS JOIN LATERAL "
                "  jsonb_array_elements(m.details->'samples') AS s "
                "WHERE m.cycle_id = :c AND m.metric = :m AND m.scope = :s"
            ),
            {"c": cid, "m": M_OUTBOX_LAG, "s": SCOPE_GLOBAL},
        )
    ).one_or_none()
    if row is None or row.p99 is None:
        sealed = (
            await session.execute(
                sa.text(
                    "SELECT value, details FROM shadow_cycle_metrics "
                    "WHERE cycle_id = :c AND metric = :m AND scope = :s"
                ),
                {"c": cid, "m": M_OUTBOX_LAG, "s": SCOPE_GLOBAL},
            )
        ).one_or_none()
        if (
            sealed is not None
            and float(sealed.value) != NO_DATA_VALUE
            and (
                sealed.details.get("samples_pruned") is not None
                or sealed.details.get("no_data") is False
            )
        ):
            # p99 SELLADO sin evidencia (samples podados por purge_staging):
            # preservar — jamás machacarlo con el centinela sin-datos.
            return None
        details = {
            "no_data": True, "samples_count": 0,
            "nota": "sin samples del muestreador en el ciclo",
        }
        return M_OUTBOX_LAG, NO_DATA_VALUE, details, True
    value = round(float(row.p99), 6)
    # no_data/nota se SOBREESCRIBEN explícitamente: el upsert fusiona details
    # (para conservar samples) y un no_data=true de un cómputo previo sin
    # samples no debe sobrevivir a un recomputo con datos.
    details = {
        "samples_count": int(row.n), "no_data": False,
        "nota": "p99 sobre los samples del muestreador",
    }
    return M_OUTBOX_LAG, value, details, True


async def _outbox_dead_row(session: AsyncSession, cid: date) -> tuple:
    """Gate `outbox_dead` (P2-6): rojo si hubo DEAD-LETTER en el ciclo.

    value = max(dead_total muestreado en el ciclo, conteo dead ACTUAL):
    dead es un estado TERMINAL sin purga automática — lo que murió durante
    el ciclo sigue muerto al cómputo (06:05), y el conteo actual cubre
    también un ciclo sin samples (beat caído). Los samples dejan la traza
    intra-ciclo. Siempre computable — jamás centinela."""
    sampled = (
        await session.execute(
            sa.text(
                "SELECT max((s->>'dead_total')::int) AS dead_max, "
                "count(*) FILTER (WHERE s ? 'dead_total') AS with_data "
                "FROM shadow_cycle_metrics m "
                "CROSS JOIN LATERAL "
                "  jsonb_array_elements(m.details->'samples') AS s "
                "WHERE m.cycle_id = :c AND m.metric = :m AND m.scope = :s"
            ),
            {"c": cid, "m": M_OUTBOX_LAG, "s": SCOPE_GLOBAL},
        )
    ).one_or_none()
    current = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM integration_outbox_deliveries "
                "WHERE state = 'dead'"
            )
        )
    ).scalar_one()
    dead_max = int(sampled.dead_max) if sampled and sampled.dead_max is not None else 0
    value = max(dead_max, int(current))
    details = {
        "dead_actual": int(current),
        "dead_max_muestras": dead_max,
        "samples_con_dato": int(sampled.with_data) if sampled else 0,
        "nota": (
            "max(dead_total muestreado en el ciclo, conteo dead actual) — "
            "dead es terminal: > 0 exige intervención del operador"
        ),
    }
    return M_OUTBOX_DEAD, value, details, False


async def _latencia_row(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple:
    """p95 (percentile_cont) por LOTE de finished_at − min_received_at sobre
    shadow_projection_batches del ciclo (por finished_at), INCLUIDOS los
    lotes `recovered` (P2-5: intenciones huérfanas cerradas por la
    recuperación del proyector con finished_at=ahora — cuentan como LENTOS,
    jamás desaparecen; details.lotes_recuperados los expone). Excluye filas
    sin sellar (intenciones AÚN abiertas). Sin lotes ⇒ centinela."""
    row = (
        await session.execute(
            sa.text(
                "SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY "
                "  EXTRACT(EPOCH FROM (finished_at - min_received_at))::float8"
                ") AS p95, "
                "count(*) AS n, "
                "count(*) FILTER (WHERE recovered) AS n_rec "
                "FROM shadow_projection_batches "
                "WHERE finished_at IS NOT NULL "
                "  AND finished_at >= :s AND finished_at < :e"
            ),
            {"s": start, "e": end},
        )
    ).one()
    if row.p95 is None:
        details = {"no_data": True, "lotes": 0, "nota": "sin lotes en el ciclo"}
        return M_LATENCIA, NO_DATA_VALUE, details, False
    details = {"lotes": int(row.n), "lotes_recuperados": int(row.n_rec)}
    return M_LATENCIA, round(float(row.p95), 6), details, False


async def _coste_row(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple:
    """Proxy de coste (§5, [alerta] informativa): embeddings de OFERTAS
    computados en el ciclo + evaluaciones nuevas + segundos de worker
    aproximados por la suma de duraciones de los lotes del proyector.
    Fuentes declaradas en details; los vectores de perfil no tienen
    timestamp (profile_embeddings, esquema inmutable) y quedan FUERA."""
    emb = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM offer_embeddings "
                "WHERE created_at >= :s AND created_at < :e"
            ),
            {"s": start, "e": end},
        )
    ).scalar_one()
    evals = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM match_evaluations "
                "WHERE created_at >= :s AND created_at < :e"
            ),
            {"s": start, "e": end},
        )
    ).scalar_one()
    worker_s = float(
        (
            await session.execute(
                sa.text(
                    "SELECT COALESCE(sum(EXTRACT(EPOCH FROM "
                    "  (finished_at - started_at))), 0) "
                    "FROM shadow_projection_batches "
                    "WHERE finished_at IS NOT NULL "
                    "  AND finished_at >= :s AND finished_at < :e"
                ),
                {"s": start, "e": end},
            )
        ).scalar_one()
    )
    value = round(int(emb) + int(evals) + worker_s, 2)
    details = {
        "embeddings_ofertas": int(emb),
        "evaluaciones_nuevas": int(evals),
        "worker_s_lotes_proyector": round(worker_s, 2),
        "fuentes": (
            "offer_embeddings.created_at + match_evaluations.created_at + "
            "Σ(finished_at − started_at) de shadow_projection_batches; "
            "profile_embeddings sin timestamp: no contabilizados (proxy)"
        ),
    }
    return M_COSTE, value, details, False


async def _reenlace_row(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple:
    """reenlace_pct (§5, [alerta] <= 5%): (attaches + recycles del ciclo) /
    encarnaciones tocadas, TODO sobre fuentes legacy:* (ver docstring de
    módulo para la definición EXACTA de cada término). Sin tocadas ⇒ 0."""
    attaches = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM link_evidence le "
                "JOIN source_listings l ON l.id = le.source_listing_id "
                "JOIN sources s ON s.id = l.source_id "
                "  AND s.name LIKE 'legacy:%' "
                "WHERE le.method = :meth "
                "  AND le.created_at >= :s AND le.created_at < :e"
            ),
            {"meth": ATTACH_METHOD, "s": start, "e": end},
        )
    ).scalar_one()
    recycles = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM source_listing_incarnations i "
                "JOIN source_listings l ON l.id = i.source_listing_id "
                "JOIN sources s ON s.id = l.source_id "
                "  AND s.name LIKE 'legacy:%' "
                "WHERE i.seq > 1 "
                "  AND i.first_seen_at >= :s AND i.first_seen_at < :e"
            ),
            {"s": start, "e": end},
        )
    ).scalar_one()
    touched = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM source_listing_incarnations i "
                "JOIN source_listings l ON l.id = i.source_listing_id "
                "JOIN sources s ON s.id = l.source_id "
                "  AND s.name LIKE 'legacy:%' "
                "WHERE (i.first_seen_at >= :s AND i.first_seen_at < :e) "
                "   OR (i.last_seen_at >= :s AND i.last_seen_at < :e) "
                "   OR (i.ended_at IS NOT NULL "
                "       AND i.ended_at >= :s AND i.ended_at < :e)"
            ),
            {"s": start, "e": end},
        )
    ).scalar_one()
    n, d = int(attaches) + int(recycles), int(touched)
    details = {
        "attaches": int(attaches),
        "recycles": int(recycles),
        "encarnaciones_tocadas": d,
    }
    return M_REENLACE, (round(n / d, 6) if d else 0.0), details, False


# --------------------------------------------------------- gates e informe


def _entry(value, umbral, kind: str, ok: bool, **extra) -> dict:
    return {"value": value, "umbral": umbral, "kind": kind, "ok": ok, **extra}


def _no_data(row) -> bool:
    return row is None or bool(row.details.get("no_data"))


async def evaluate_gates(session: AsyncSession, cycle_id: date) -> dict:
    """{clave: {value, umbral, kind, ok}} con los umbrales RATIFICADOS de §6.
    Clave = metric para scope global; f"{metric}::{scope}" por perfil. Un
    gate SIN datos (fila ausente, centinela, set no medible) es ok=False —
    no demostrable no suma al contador de N ciclos. `labels_ready` (P1-2)
    es la precondición del oráculo: gate NUEVO que exige el DoD de B-03
    (sets congelados/juicios/pares dedup/pares mapeables) — en rojo si el
    oráculo no da para medir. Las alertas informativas (overlap, coste)
    siempre ok=True; las alertas con umbral (no_ingeribles, reenlace)
    marcan ok=False al dispararse (se registra, no resetea)."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT metric, scope, value, details FROM shadow_cycle_metrics "
                "WHERE cycle_id = :c ORDER BY scope, metric"
            ),
            {"c": cycle_id},
        )
    ).all()
    by = {(r.metric, r.scope): r for r in rows}
    out: dict[str, dict] = {}
    scopes = sorted({s for (_m, s) in by if s.startswith("profile:")})
    for scope in scopes:
        _profile_gates(out, by, scope)
    if not scopes:
        out[M_NDCG] = _entry(
            None, NDCG_MIN, KIND_GATE, False,
            nota="sin perfiles medidos (ningún set congelado): no demostrable",
        )
    _global_gates(out, by)
    return out


def _profile_gates(out: dict, by: dict, scope: str) -> None:
    ndcg = by.get((M_NDCG, scope))
    legacy = by.get((M_NDCG_LEGACY, scope))
    if ndcg is not None:
        umbral = NDCG_MIN
        extra: dict = {}
        if legacy is not None and not _no_data(legacy):
            extra["ndcg_legacy"] = float(legacy.value)
            umbral = max(NDCG_MIN, float(legacy.value) - NDCG_LEGACY_MARGIN)
        value = float(ndcg.value)
        ok = value >= umbral and not ndcg.details.get("no_medible")
        if ndcg.details.get("no_medible"):
            extra["nota"] = "set sin etiquetas > 0: nDCG no medible"
        out[f"{M_NDCG}::{scope}"] = _entry(
            value, round(umbral, 6), KIND_GATE, ok, **extra
        )
    overlap = by.get((M_OVERLAP, scope))
    if overlap is not None:
        out[f"{M_OVERLAP}::{scope}"] = _entry(
            float(overlap.value), None, KIND_ALERTA, True
        )
    fn = by.get((M_FALSOS_NEG, scope))
    if fn is not None:
        strict = fn.details.get("modo") == "estricto_0"
        umbral = 0.0 if strict else FN_MAX_RATIO
        out[f"{M_FALSOS_NEG}::{scope}"] = _entry(
            float(fn.value), umbral, KIND_GATE, float(fn.value) <= umbral,
            modo=fn.details.get("modo"),
        )


def _global_gates(out: dict, by: dict) -> None:
    checks = {
        M_LABELS_READY: (1, lambda v, u: v >= u),
        M_DEDUP_PRECISION: (DEDUP_PRECISION_MIN, lambda v, u: v >= u),
        M_DEDUP_RECALL: (DEDUP_RECALL_MIN, lambda v, u: v >= u),
        M_PERDIDA: (PERDIDA_MAX, lambda v, u: v == u),
        M_NO_INGERIBLES: (0, lambda v, u: v <= u),
        M_OUTBOX_LAG: (OUTBOX_LAG_P99_MAX_S, lambda v, u: v <= u),
        M_OUTBOX_DEAD: (0, lambda v, u: v <= u),  # dead-letter ⇒ rojo (P2-6)
        M_LATENCIA: (LATENCIA_P95_MAX_S, lambda v, u: v <= u),
        M_COSTE: (None, lambda v, u: True),
        M_REENLACE: (REENLACE_PCT_MAX, lambda v, u: v <= u),
    }
    # Métricas que usan el centinela NO_DATA_VALUE (fila con placeholder del
    # muestreador ANTES de compute_cycle, o dedup sin pares evaluables —
    # P1-2): el centinela es "sin datos", jamás un valor — perdida sí puede
    # ser negativa legítima y queda FUERA de este conjunto.
    sentinel_metrics = {M_OUTBOX_LAG, M_LATENCIA, M_DEDUP_PRECISION, M_DEDUP_RECALL}
    for metric in _EXPECTED_GLOBAL:
        umbral, check = checks[metric]
        kind = METRIC_KINDS[metric]
        row = by.get((metric, SCOPE_GLOBAL))
        if _no_data(row) or (
            metric in sentinel_metrics and float(row.value) == NO_DATA_VALUE
        ):
            # Gate sin datos = no demostrable (ok False); alerta sin datos
            # no puede dispararse pero queda anotada.
            out[metric] = _entry(
                None, umbral, kind, kind != KIND_GATE, nota="sin datos"
            )
            continue
        value = float(row.value)
        out[metric] = _entry(value, umbral, kind, bool(check(value, umbral)))


async def render_report(session: AsyncSession, cycle_id: date) -> str:
    """Informe de TEXTO legible por ciclo (DoD B-04): ventana, tabla de
    métricas con umbral/tipo/estado, desglose de perdida/coste y veredicto
    del ciclo para el contador de §6."""
    gates = await evaluate_gates(session, cycle_id)
    rows = (
        await session.execute(
            sa.text(
                "SELECT metric, scope, details FROM shadow_cycle_metrics "
                "WHERE cycle_id = :c"
            ),
            {"c": cycle_id},
        )
    ).all()
    details_by = {(r.metric, r.scope): r.details for r in rows}
    start, end = cycle_bounds(cycle_id)
    lines = [
        f"INFORME DE CICLO SOMBRA — {cycle_id.isoformat()}",
        f"Ventana: [{start.isoformat()} .. {end.isoformat()}) ({CYCLE_TZ.key})",
        "",
        f"{'métrica':<58} {'valor':>12} {'umbral':>18} {'tipo':<7} estado",
        "-" * 106,
    ]
    for key, g in gates.items():
        lines.append(_report_line(key, g))
    lines += ["", _report_verdict(gates)]
    lines += _report_details(details_by)
    return "\n".join(lines) + "\n"


def _report_line(key: str, g: dict) -> str:
    value = "sin datos" if g["value"] is None else f"{g['value']:.4f}"
    if g["umbral"] is None:
        umbral = "informativa"
    elif key.startswith(M_NDCG):
        umbral = f">= {g['umbral']:.4f}"
    elif g["kind"] == KIND_GATE and key.startswith(M_PERDIDA):
        umbral = f"== {g['umbral']}"
    elif key == M_LABELS_READY:
        umbral = f">= {g['umbral']}"
    else:
        cmp = ">=" if key.startswith("dedup") else "<="
        umbral = f"{cmp} {g['umbral']}"
    estado = "OK" if g["ok"] else ("FALLO" if g["kind"] == KIND_GATE else "ALERTA")
    nota = f"  ({g['nota']})" if g.get("nota") else ""
    return f"{key:<58} {value:>12} {umbral:>18} [{g['kind']}] {estado}{nota}"


def _report_verdict(gates: dict) -> str:
    gate_items = [g for g in gates.values() if g["kind"] == KIND_GATE]
    failed = sum(1 for g in gate_items if not g["ok"])
    alerts = sum(
        1 for g in gates.values() if g["kind"] == KIND_ALERTA and not g["ok"]
    )
    if failed:
        verdict = (
            f"CICLO NO APTO: {failed}/{len(gate_items)} gates fuera de umbral "
            "(el contador de N ciclos consecutivos vuelve a 0)"
        )
    else:
        verdict = f"CICLO APTO: {len(gate_items)}/{len(gate_items)} gates en verde"
    return f"{verdict} — alertas activas: {alerts}"


def _report_details(details_by: dict) -> list[str]:
    lines = ["", "Desglose:"]
    perdida = details_by.get((M_PERDIDA, SCOPE_GLOBAL))
    if perdida:
        lines.append(
            f"  perdida = {perdida['legacy_activos_ingeribles']} legacy vivos"
            f" - {perdida['slots_legacy_activos']} slots activos"
            f" + {perdida['staging_sin_aplicar_1h']} staging sin aplicar >1h"
        )
    coste = details_by.get((M_COSTE, SCOPE_GLOBAL))
    if coste:
        lines.append(
            f"  coste = {coste['embeddings_ofertas']} embeddings"
            f" + {coste['evaluaciones_nuevas']} evaluaciones"
            f" + {coste['worker_s_lotes_proyector']}s de worker (proxy)"
        )
    reenlace = details_by.get((M_REENLACE, SCOPE_GLOBAL))
    if reenlace:
        lines.append(
            f"  reenlace = ({reenlace['attaches']} attaches"
            f" + {reenlace['recycles']} recycles)"
            f" / {reenlace['encarnaciones_tocadas']} encarnaciones tocadas"
        )
    return lines


# ------------------------------------------------------- purga del staging


async def purge_staging(
    session: AsyncSession, now: datetime | None = None
) -> dict:
    """Purga del staging APLICADO (§7, retención de §2: ciclos cerrados + 7
    días): DELETE de shadow_change_log WHERE applied_at IS NOT NULL AND
    received_at < (cierre del ciclo ACTUAL − 7 días), PRESERVANDO SIEMPRE la
    última fila `users` (op I/U, aplicada) de CADA pk — es EXACTAMENTE la
    fila que lee _inactive_user_refs del proyector (su NOTA documentada):
    borrarla haría olvidar la exclusión de usuarios inactivos. Lo NO aplicado
    jamás se toca (sigue pendiente de proyectar, sea de cuando sea).

    También poda los arrays details.samples de outbox_lag_p99 en ciclos ya
    fuera de retención, SOLO en filas cuyo p99 quedó SELLADO por
    compute_cycle — GARANTIZADO por el guard `value <> NO_DATA_VALUE` del
    WHERE, no asumido: una fila muestreada pero aún sin computar (value =
    centinela — compute_cycle retrasado >= 7 días o backfill histórico)
    CONSERVA sus samples hasta que un compute los selle, evitando un
    [gate] en no_data PERMANENTE. La poda deja `samples_pruned` como
    rastro — un recomputo posterior del ciclo PRESERVA ese p99 sellado
    (_outbox_lag_row devuelve None y no hay upsert), jamás lo machaca con
    el centinela sin-datos.
    IDEMPOTENTE: el segundo pase no encuentra nada que borrar ni podar."""
    cid_now = current_cycle_id(now)
    cutoff = cycle_bounds(cid_now)[1] - timedelta(days=STAGING_RETENTION_DAYS)
    deleted = (
        await session.execute(
            sa.text(
                "DELETE FROM shadow_change_log c "
                "WHERE c.applied_at IS NOT NULL AND c.received_at < :cutoff "
                "AND NOT (c.src_table = 'users' AND (c.lsn, c.seq_in_tx) IN ("
                "  SELECT lsn, seq_in_tx FROM ("
                "    SELECT DISTINCT ON (pk) lsn, seq_in_tx "
                "    FROM shadow_change_log "
                "    WHERE src_table = 'users' AND op IN ('I', 'U') "
                "      AND applied_at IS NOT NULL "
                "    ORDER BY pk, lsn DESC, seq_in_tx DESC) last_users))"
            ),
            {"cutoff": cutoff},
        )
    ).rowcount
    pruned = (
        await session.execute(
            sa.text(
                "UPDATE shadow_cycle_metrics "
                "SET details = (details - 'samples') || jsonb_build_object("
                "  'samples_pruned', jsonb_array_length(details->'samples')) "
                "WHERE metric = :m AND cycle_id < :cid "
                "  AND value <> :nodata "
                "  AND jsonb_typeof(details->'samples') = 'array'"
            ),
            {
                "m": M_OUTBOX_LAG,
                "nodata": NO_DATA_VALUE,
                "cid": cid_now - timedelta(days=STAGING_RETENTION_DAYS),
            },
        )
    ).rowcount
    result = {
        "cutoff": cutoff.isoformat(),
        "staging_deleted": deleted,
        "sample_rows_pruned": pruned,
    }
    logger.info("metrics: purga del staging — %s", result)
    return result

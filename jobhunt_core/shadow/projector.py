"""Proyector de la sombra (B-02, CONTRATOS_FASE_B.md §3).

Consume `shadow_change_log` en orden (lsn, seq_in_tx) por LOTES (índice
parcial de lo no aplicado) y lo convierte al LENGUAJE del core — SIN rutas
nuevas de escritura:

- `jobs` → `RawListingSink.handle` (el sink REAL de A-04..A-06): una fuente
  core por fuente legacy (`legacy:<source>`, tier 0) con UN scope sombra
  `params={"shadow": true}` creado on-demand e idempotente y DESHABILITADO —
  el orquestador de cosecha (`run_all`) solo itera scopes enabled y estas
  fuentes no tienen provider: el scope existe solo para que el sink resuelva
  fuente/scope por su flujo normal. Extractor/normalizador dados de alta EN
  CALIENTE por fuente observada (harvest/providers/legacy_shadow.py; el
  registry es exact-match) y, al arrancar, para todas las `legacy:*` ya
  persistidas (la reconstrucción de canónica tras un cierre puede necesitar
  el normalizador de una fuente que el lote no trae).
- `op=D` / `is_active=false` / `duplicate_of NOT NULL` ⇒ CIERRE de la
  encarnación activa del slot REUTILIZANDO la maquinaria del sink (mismo
  advisory lock por fuente, _lock_vacancies → _close_incarnations →
  _repair_primary_pointers → _rebuild_canonical_after_repair): el DELETE
  jamás rompe la identidad compartida (DoD B-02).
- `user_profiles` → `profiles.save_profile_revision` bajo el consumer sombra
  único "swissjob-shadow"; external_ref = user_id; content = {title, cv_text,
  skills} EXACTO (PF.5: vectores comparables). El op=D de user_profiles se
  IGNORA (§2: el borrado real viaja como op=D de `users`, cuya PK sí es el
  external_ref).
- `users.is_active=false` ⇒ perfil EXCLUIDO de evaluación sin borrar;
  `users` op=D ⇒ ERASE completo del perfil sombra (GDPR §3,
  `erase_shadow_profile` reutilizable).
- Tras cada lote: embeddings `run_pending` + `evaluate_profile` SOLO de los
  perfiles sombra afectados — o de todos los activos si hubo corpus nuevo
  (revisiones de ofertas o cierres que mueven la canónica). Al ENTRAR (bajo
  el single-flight) el disparo se RE-GARANTIZA: se drenan los embeddings
  pendientes y se evalúan TODOS los perfiles sombra activos — un crash tras
  el commit de un lote pero durante `_after_batch` no deja nada sin disparar
  (re-ejecutable y barato: run_pending sin pendientes es no-op y
  evaluate_profile es dedup por eval_key).

DECISIONES no obvias (por contrato, documentadas):

MERGE TOAST (jobs). La captura NO re-lee jobs (§2): un op=U con columnas en
`_omitted` sin `_backfilled` llega SIN esas columnas (ausente ≠ NULL) y
pasarlo tal cual al sink crearía una revisión nueva con la description
perdida (el sink hashea el payload que recibe). El lote se PLIEGA por pk
(gana el último estado; una columna ausente del pliegue ≡ `_omitted` no
backfilled — la meta es redundante con la forma del payload) y:
 1. Si el pliegue contiene un op=I, el estado es completo respecto al
    esquema legacy REAL: se proyecta tal cual (las columnas de la whitelist
    que esa tabla no tiene JAMÁS se inventan).
 2. Si faltan columnas contractuales, se completan desde el último raw
    conocido del slot (source_listing_revisions, encarnación más reciente —
    también cerrada: cubre la reactivación tras cierre) o, en su defecto,
    desde el payload del último change_log YA aplicado de esa pk. Las claves
    que tampoco existían antes se OMITEN: misma forma que el payload previo
    ⇒ mismo content_hash ⇒ el DO NOTHING del sink absorbe el no-cambio (la
    columna legacy content_hash garantiza que un upsert de refresco no
    cuesta trabajo extra — no hace falta pre-chequearla).
 3. Sin valor previo conocido (op=U huérfano: imposible salvo corrupción):
    NO se proyecta contenido — solo refresh de last_seen_at de la
    encarnación activa (si existe) con ALERTA persistente (logger.error).

EXCLUSIÓN de users inactivos. Persistente y barata SIN tabla nueva: el
proyector es quien dispara evaluate_profile y decide con el ÚLTIMO estado
`users` por pk del staging YA APLICADO (op I/U, orden lsn/seq) — vive en la
BD (sobrevive reinicios) y un is_active=true posterior re-incluye por el
flujo normal en cuanto haya corpus nuevo o el perfil cambie. NOTA para
B-04: su purga del staging aplicado debe CONSERVAR la última fila `users`
de cada pk (o mover esta exclusión a estado propio) — borrarla haría
olvidar la exclusión.

IDEMPOTENCIA Y TRANSACCIONES. UNA transacción POR FUENTE — un solo
sink.handle o un solo ciclo de cierre por tx, como en Fase A: el invariante
del sink es UN único FOR UPDATE ordenado por transacción, y varios
handle/_close en la misma tx acumulan locks de vacante de órdenes distintos
(ciclo posible con cosechas concurrentes). user_profiles y users van en sus
propias txs. Cada tx sella el applied_at de SUS cambios: un crash a mitad
deja el resto en NULL y el retry continúa sobre escrituras ya idempotentes
(ON CONFLICT del sink, revisión/activación de perfiles reutilizadas,
cierres con WHERE ended_at IS NULL, erase a base de DELETEs). La fila de
`shadow_projection_batches` se escribe AL FINAL con las marcas del conjunto
realmente aplicado: un crash antes de esa fila pierde SOLO la marca del
lote — jamás datos — y B-04 debe tolerar lotes sin marca (ya tolerable:
latencia_p95 es el p95 de los lotes EXISTENTES). SINGLE-FLIGHT: un
pg_advisory_lock de SESIÓN sobre conexión dedicada cubre TODA
project_pending (incluido _after_batch); la invocación concurrente sale
limpia con pg_try_advisory_lock ("ya en curso"), sin bloquear al worker.
LOCKS DE PERFIL: protocolo A-07 — upsert de todas las pks primero y FOR
UPDATE en orden profiles.id ascendente (como
embeddings._lock_profiles_and_current); el erase bloquea el perfil ANTES
de borrar su grafo hijo (orden perfil→estado de evaluate_profile).
"""

import logging
import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from jobhunt_core import profiles as core_profiles
from jobhunt_core.database import create_core_engine, task_session_factory
from jobhunt_core.harvest import normalize
from jobhunt_core.harvest.providers import legacy_shadow
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing

# Impls ASYNC de las tareas normales del core (embeddings/matching): se
# invocan como corrutinas — un .apply() de Celery anidaría asyncio.run dentro
# del loop del proyector. Es el MISMO flujo normal, sin duplicar semántica.
from jobhunt_core.tasks.embedding import _run_pending_impl
from jobhunt_core.tasks.matching import _run_profile_impl

logger = logging.getLogger(__name__)

SHADOW_CONSUMER = "swissjob-shadow"
LEGACY_PREFIX = legacy_shadow.LEGACY_PREFIX
DEFAULT_BATCH_SIZE = 500
EVAL_LIMIT = 100  # top-K de evaluate_profile (el default de la tarea A-08)

# Clave del SINGLE-FLIGHT del proyector: pg_advisory_lock de SESIÓN sobre
# conexión dedicada (clave distinta de los locks por fuente del sink).
_PROJECTOR_LOCK = "jobhunt:shadow-projector"

# Columnas de CONTENIDO contractuales (§3): columna legacy → clave del payload
# que ve el sink (company se renombra a company_name; url/source/is_active/
# duplicate_of/content_hash NO forman parte del contenido).
JOB_PAYLOAD_MAP: dict[str, str] = {
    "title": "title",
    "company": "company_name",
    "description": "description",
    "tags": "tags",
    "location": "location",
    "canton": "canton",
    "language": "language",
    "seniority": "seniority",
    "contract_type": "contract_type",
    "remote": "remote",
    "salary_min_chf": "salary_min_chf",
    "salary_max_chf": "salary_max_chf",
    "salary_original": "salary_original",
    "salary_currency": "salary_currency",
    "salary_period": "salary_period",
}

# Contenido EXACTO del perfil sombra (§3, PF.5 — vectores comparables).
PROFILE_FIELDS = ("title", "cv_text", "skills")

# Orden FK-safe del ERASE (el de tests/dbcleanup.purge_consumer_graph, en
# código de producción): estado ANTES que evaluaciones (FK RESTRICT del
# current_eval); el outbox va aparte (sus deliveries caen por CASCADE).
_ERASE_TABLES = (
    "profile_vacancy_state",
    "profile_vacancy_events",
    "match_evaluations",
    "profile_embeddings",
    "profile_revision_activations",
    "profile_revisions",
)


@dataclass
class _JobFold:
    """Estado PLEGADO de una pk de jobs dentro del lote (el último gana)."""

    cols: dict = field(default_factory=dict)  # columnas legacy presentes
    saw_insert: bool = False  # un op=I trae el esquema completo (regla 1)
    deleted: bool = False  # el último op fue D


@dataclass
class _BatchResult:
    changes: int
    upserts: int
    closes: int
    erased: int
    revisions_new: int
    corpus_changed: bool
    affected_profiles: set


# --------------------------------------------------------------- entrada


async def project_pending(
    batch_size: int = DEFAULT_BATCH_SIZE, max_batches: int | None = None
) -> dict:
    """Drena `shadow_change_log` por lotes y dispara el flujo normal del core.

    SINGLE-FLIGHT: pg_advisory_lock de SESIÓN sobre una conexión DEDICADA
    (AUTOCOMMIT: sin transacción colgada mientras dura la proyección) tomado
    al entrar y liberado al salir — cubre también _after_batch. La invocación
    concurrente hace pg_try_advisory_lock y sale limpia con
    status="already_running" (no bloquea al worker). Robusto a errores: el
    finally libera el lock, y si la conexión murió el servidor lo libera al
    cerrarla.

    Devuelve totales JSON-serializables (resultado de la tarea Celery)."""
    totals = {
        "status": "ok", "batches": 0, "changes": 0, "upserts": 0, "closes": 0,
        "erased": 0, "revisions_new": 0, "profiles_evaluated": 0,
        "recovery_evaluated": 0,
    }
    lock_engine = create_core_engine(
        poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        lock_conn = await lock_engine.connect()
        try:
            got = (
                await lock_conn.execute(
                    sa.text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"),
                    {"k": _PROJECTOR_LOCK},
                )
            ).scalar_one()
            if not got:
                logger.info("projector: proyección ya en curso — salida limpia")
                totals["status"] = "already_running"
                return totals
            try:
                await _project_all(totals, batch_size, max_batches)
            finally:
                try:
                    await lock_conn.execute(
                        sa.text(
                            "SELECT pg_advisory_unlock(hashtextextended(:k, 0))"
                        ),
                        {"k": _PROJECTOR_LOCK},
                    )
                except Exception:  # pragma: no cover — conexión rota
                    # El lock de sesión muere con su conexión: cerrar basta.
                    logger.warning(
                        "projector: pg_advisory_unlock falló — el lock se "
                        "libera al cerrar la conexión dedicada"
                    )
        finally:
            await lock_conn.close()
    finally:
        await lock_engine.dispose()
    return totals


async def _project_all(totals, batch_size, max_batches) -> None:
    """Cuerpo de la proyección (YA bajo el single-flight): re-disparo del
    flujo post-lote + drenado del staging por lotes."""
    sink = RawListingSink()
    async with task_session_factory() as session_factory:
        async with session_factory() as session:
            await _register_known_legacy_sources(session)
        totals["recovery_evaluated"] = await _replay_after_batch(session_factory)
        while max_batches is None or totals["batches"] < max_batches:
            result = await _project_batch(session_factory, sink, batch_size)
            if result is None:
                break
            totals["batches"] += 1
            for key in ("changes", "upserts", "closes", "erased", "revisions_new"):
                totals[key] += getattr(result, key)
            totals["profiles_evaluated"] += await _after_batch(
                session_factory, result
            )


async def _register_known_legacy_sources(session) -> None:
    names = (
        await session.execute(
            sa.text("SELECT name FROM sources WHERE name LIKE 'legacy:%'")
        )
    ).scalars().all()
    for name in names:
        legacy_shadow.ensure_registered(name)


async def _project_batch(session_factory, sink, batch_size: int) -> _BatchResult | None:
    """UN lote = VARIAS transacciones: lectura+plan, una tx POR FUENTE (un
    solo sink.handle o un solo ciclo de cierre por tx — invariante del sink),
    perfiles y users en las suyas — cada una sella el applied_at de SUS
    cambios — y el registro del lote AL FINAL. Devuelve None si no queda nada
    pendiente."""
    read = await _read_batch_and_plan(session_factory, batch_size)
    if read is None:
        return None
    rows, started_at, jobs_by_pk, plans = read
    upserts, closes, job_revs = await _apply_jobs_by_source(
        session_factory, sink, plans, jobs_by_pk
    )
    affected, prof_revs = await _project_profiles_tx(session_factory, rows)
    # users DESPUÉS de user_profiles: si el mismo lote trae el perfil y el
    # borrado GDPR del usuario, el estado final es el ERASE.
    erased = await _project_users_tx(session_factory, rows)
    affected -= erased
    await _finish_batch(
        session_factory, rows, started_at, jobs_by_pk, plans, job_revs + prof_revs
    )
    return _BatchResult(
        changes=len(rows), upserts=upserts, closes=closes, erased=len(erased),
        revisions_new=job_revs + prof_revs,
        corpus_changed=(job_revs > 0 or closes > 0),
        affected_profiles=affected,
    )


async def _read_batch_and_plan(session_factory, batch_size: int) -> tuple | None:
    """Tx de LECTURA: lote pendiente + plan de jobs. El plan no queda
    obsoleto frente a las txs de aplicación que siguen: los slots/fuentes
    legacy:* solo los escribe este proyector (single-flight). Devuelve
    (rows, started_at, jobs_by_pk, plans) o None sin pendientes."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT lsn, seq_in_tx, src_table, op, pk, payload, received_at "
                    "FROM shadow_change_log WHERE applied_at IS NULL "
                    "ORDER BY lsn, seq_in_tx LIMIT :n"
                ),
                {"n": batch_size},
            )
        ).all()
        if not rows:
            return None
        started_at = (
            await session.execute(sa.text("SELECT clock_timestamp()"))
        ).scalar_one()
        jobs_rows = [r for r in rows if r.src_table == "jobs"]
        folds = _fold_jobs(jobs_rows)
        plans = await _plan_jobs(session, folds) if folds else {}
    jobs_by_pk: dict[str, list] = {}
    for r in jobs_rows:
        jobs_by_pk.setdefault(r.pk, []).append(r)
    return rows, started_at, jobs_by_pk, plans


async def _project_profiles_tx(session_factory, rows) -> tuple[set, int]:
    """user_profiles del lote en SU transacción, con su sellado dentro."""
    prof_rows = [r for r in rows if r.src_table == "user_profiles"]
    if not prof_rows:
        return set(), 0
    async with session_factory() as session:
        affected, prof_revs = await _apply_profiles(session, prof_rows)
        await _seal_rows(session, prof_rows)
        await session.commit()
    return affected, prof_revs


async def _project_users_tx(session_factory, rows) -> set:
    """users del lote (ERASEs GDPR) en SU transacción, con su sellado."""
    users_rows = [r for r in rows if r.src_table == "users"]
    if not users_rows:
        return set()
    async with session_factory() as session:
        erased = await _apply_users(session, users_rows)
        await _seal_rows(session, users_rows)
        await session.commit()
    return erased


async def _finish_batch(
    session_factory, rows, started_at, jobs_by_pk, plans, revisions_new
) -> None:
    """Cierre del lote: sella lo no cubierto por ningún plan (pks sin fuente
    resoluble, cierres de jobs jamás proyectados) y escribe la marca."""
    covered = {pk for p in plans.values() for pk, _f in p["upserts"]}
    covered |= {pk for p in plans.values() for pk in p["closes"]}
    leftover = [
        r
        for pk, pk_rows in jobs_by_pk.items()
        if pk not in covered
        for r in pk_rows
    ]
    async with session_factory() as session:
        await _seal_rows(session, leftover)
        await _record_batch(session, rows, started_at, revisions_new)
        await session.commit()


async def _seal_rows(session, rows) -> None:
    """Sella applied_at — SIEMPRE en la MISMA transacción que las escrituras
    de esas filas (idempotencia por fuente: un crash a mitad de lote deja el
    resto en NULL y el retry continúa donde quedó)."""
    if not rows:
        return
    await session.execute(
        sa.text(
            "UPDATE shadow_change_log SET applied_at = now() "
            "WHERE lsn = :l AND seq_in_tx = :s"
        ),
        [{"l": r.lsn, "s": r.seq_in_tx} for r in rows],
    )


async def _record_batch(session, rows, started_at, revisions_new) -> None:
    """Marca del lote (fuente de latencia_p95, §5) con el conjunto realmente
    aplicado — AL FINAL, tras las txs por fuente/perfiles/users. Un crash
    antes de esta fila pierde SOLO la marca del lote (los datos ya quedaron
    aplicados y sellados tx a tx y el retry no los re-lee): B-04 debe tolerar
    lotes sin marca — ya tolerable, latencia_p95 es el p95 de los lotes
    EXISTENTES en shadow_projection_batches."""
    await session.execute(
        sa.text(
            "INSERT INTO shadow_projection_batches "
            "(first_lsn, last_lsn, min_received_at, started_at, finished_at, "
            " changes, revisions_new) "
            "VALUES (:f, :l, :m, :st, clock_timestamp(), :c, :r)"
        ),
        {
            "f": rows[0].lsn, "l": rows[-1].lsn,
            "m": min(r.received_at for r in rows), "st": started_at,
            "c": len(rows), "r": revisions_new,
        },
    )


# ------------------------------------------------------------------- jobs


def _fold_jobs(rows) -> dict[str, _JobFold]:
    folds: dict[str, _JobFold] = {}
    for r in rows:
        f = folds.setdefault(r.pk, _JobFold())
        if r.op == "D":
            f.deleted = True
            continue
        f.deleted = False
        if r.op == "I":
            f.saw_insert = True
        f.cols.update({k: v for k, v in r.payload.items() if not k.startswith("_")})
    return folds


def _is_close(fold: _JobFold) -> bool:
    """§3: D, is_active=false o duplicate_of ⇒ cierre. Un backfill/I ya
    inactivo cae aquí y el cierre sin slot es un no-op: lo inactivo/duplicado
    del legacy JAMÁS entra al corpus (espejo del legacy ACTIVO)."""
    return (
        fold.deleted
        or fold.cols.get("is_active") is False
        or fold.cols.get("duplicate_of") is not None
    )


async def _apply_jobs_by_source(
    session_factory, sink, plans, jobs_by_pk
) -> tuple[int, int, int]:
    """Aplica el plan de jobs con UNA transacción POR FUENTE y por tipo de
    operación: la tx de upserts contiene UN solo sink.handle y la de cierres
    UN solo ciclo _lock_vacancies→_close→reparación (invariante del sink: un
    único FOR UPDATE ordenado por transacción — varios ciclos en la misma tx
    acumulan locks de vacante de órdenes distintos y pueden formar ciclo con
    cosechas concurrentes). Cada tx sella el applied_at de SUS pks; una pk
    cerrada en VARIAS fuentes se sella en la ÚLTIMA (orden alfabético): un
    crash intermedio la deja en NULL y el retry repite los cierres previos
    (no-op por ended_at IS NULL). → (upserts, cierres, revisiones)."""
    last_close_source: dict[str, str] = {}
    for name in sorted(plans):
        for pk in plans[name]["closes"]:
            last_close_source[pk] = name  # iteración ordenada: el último gana
    upserts = closes = revs = 0
    for name in sorted(plans):
        plan = plans[name]
        if plan["upserts"]:
            u, r = await _source_upserts_tx(
                session_factory, sink, name, plan["upserts"], jobs_by_pk
            )
            upserts += u
            revs += r
        if plan["closes"]:
            closes += await _source_closes_tx(
                session_factory, sink, name, plan["closes"],
                last_close_source, jobs_by_pk,
            )
    return upserts, closes, revs


async def _source_upserts_tx(
    session_factory, sink, name, entries, jobs_by_pk
) -> tuple[int, int]:
    """Tx de upserts de UNA fuente: alta idempotente de fuente/scope + UN
    solo sink.handle + sellado de sus pks. → (upserts, revisiones)."""
    async with session_factory() as session:
        source_id, scope_id = await _ensure_legacy_source(session, name)
        u, r = await _apply_source_upserts(
            session, sink, source_id, scope_id, name, entries
        )
        await _seal_rows(
            session, [row for pk, _f in entries for row in jobs_by_pk[pk]]
        )
        await session.commit()
    return u, r


async def _source_closes_tx(
    session_factory, sink, name, close_pks, last_close_source, jobs_by_pk
) -> int:
    """Tx de cierres de UNA fuente: UN solo ciclo de cierre + sellado de las
    pks cuya ÚLTIMA fuente de cierre es esta. Cierre sobre fuente jamás
    proyectada: no hay nada que cerrar — sus cambios se sellan igual."""
    async with session_factory() as session:
        sid = (
            await session.execute(
                sa.text("SELECT id FROM sources WHERE name = :n"), {"n": name}
            )
        ).scalar_one_or_none()
        closes = 0
        if sid is not None:
            closes = await _close_slots(session, sink, sid, close_pks)
        await _seal_rows(
            session,
            [
                row
                for pk in close_pks
                if last_close_source[pk] == name
                for row in jobs_by_pk[pk]
            ],
        )
        await session.commit()
    return closes


async def _plan_jobs(session, folds) -> dict[str, dict]:
    """Clasifica cada pk plegada en upsert/cierre y la enruta a su fuente
    `legacy:<source>` (del pliegue; si no — op=D puro — del slot ya
    proyectado; si no, del último change aplicado)."""
    known = _fold_known_sources(folds)
    pending = sorted(pk for pk in folds if pk not in known)
    slot_hits = await _legacy_slot_sources(session, pending)
    unresolved = [
        pk for pk in pending if pk not in slot_hits and not _is_close(folds[pk])
    ]
    prev = await _last_applied_changes(session, "jobs", unresolved)

    plans: dict[str, dict] = {}
    for pk in sorted(folds):
        fold = folds[pk]
        if _is_close(fold):
            # op=D de un job que nunca se proyectó (p.ej. borrado diario de
            # inactivos): caso NORMAL, sin slot no hay nada que cerrar.
            for name in _close_sources(pk, known, slot_hits):
                _plan_for(plans, name)["closes"].append(pk)
        elif (name := _upsert_source(pk, known, slot_hits, prev)) is not None:
            _plan_for(plans, name)["upserts"].append((pk, fold))
        else:
            logger.error(
                "projector: ALERTA — cambio de jobs pk=%s sin fuente "
                "resoluble: descartado", pk,
            )
    return plans


def _fold_known_sources(folds) -> dict[str, str]:
    return {
        pk: LEGACY_PREFIX + f.cols["source"]
        for pk, f in folds.items()
        if isinstance(f.cols.get("source"), str) and f.cols["source"]
    }


def _close_sources(pk, known, slot_hits) -> list[str]:
    return [known[pk]] if pk in known else slot_hits.get(pk, [])


async def _legacy_slot_sources(session, pks) -> dict[str, list[str]]:
    """pk → fuentes `legacy:*` con slot ya proyectado (orden determinista)."""
    if not pks:
        return {}
    hits: dict[str, list[str]] = {}
    for r in (
        await session.execute(
            sa.text(
                "SELECT l.external_id AS pk, s.name AS source_name "
                "FROM source_listings l JOIN sources s ON s.id = l.source_id "
                "WHERE s.name LIKE 'legacy:%' AND l.external_id = ANY(:pks) "
                "ORDER BY l.external_id, s.name"
            ),
            {"pks": list(pks)},
        )
    ).all():
        hits.setdefault(r.pk, []).append(r.source_name)
    return hits


def _upsert_source(pk, known, slot_hits, prev) -> str | None:
    name = known.get(pk) or (slot_hits.get(pk) or [None])[0]
    if name is None:
        src = (prev.get(pk) or {}).get("source")
        name = LEGACY_PREFIX + src if isinstance(src, str) and src else None
    return name


def _plan_for(plans: dict, name: str) -> dict:
    return plans.setdefault(name, {"upserts": [], "closes": []})


async def _apply_source_upserts(
    session, sink, source_id, scope_id, source_name, entries
) -> tuple[int, int]:
    """Upserts de UNA fuente: un solo sink.handle en la transacción (más los
    refrescos degradados de last_seen). → (upserts, revisiones nuevas)."""
    n_up = revs = 0
    listings, touches = await _build_source_batch(
        session, source_id, source_name, entries
    )
    if listings:
        _alert_unnormalizable(source_name, listings)
        exts = sorted(li.external_id for li in listings)
        before = await _count_job_revisions(session, source_id, exts)
        await sink.handle(session, str(scope_id), tuple(listings))
        revs = await _count_job_revisions(session, source_id, exts) - before
        n_up = len(listings)
    if touches:
        await _touch_last_seen(session, source_id, touches)
    return n_up, revs


async def _build_source_batch(
    session, source_id, source_name, entries
) -> tuple[list[RawListing], list[str]]:
    """Pliegues → RawListings con el MERGE TOAST de cabecera aplicado.
    Devuelve además las pks DEGRADADAS a refresco de last_seen (regla 3)."""
    need_prev = sorted(
        {pk for pk, f in entries if _missing_content(f) or "url" not in f.cols}
    )
    prev_raws = await _latest_slot_raws(session, source_id, need_prev)
    prev_changes = await _last_applied_changes(session, "jobs", need_prev)
    listings: list[RawListing] = []
    touches: list[str] = []
    url_pending: list[tuple[str, dict]] = []
    for pk, fold in entries:
        payload, url = _merge_job_payload(
            fold, prev_raws.get(pk), prev_changes.get(pk)
        )
        if payload is None:
            logger.error(
                "projector: ALERTA — U de jobs pk=%s (%s) con columnas "
                "omitidas y SIN valor previo conocido: degradado a refresco "
                "de last_seen (jamás una revisión con contenido perdido)",
                pk, source_name,
            )
            touches.append(pk)
        elif url is None:
            url_pending.append((pk, payload))
        else:
            listings.append(RawListing(external_id=pk, url=url, payload=payload))
    for pk, payload, url in await _resolve_pending_urls(
        session, source_id, url_pending
    ):
        if url is None:
            logger.error(
                "projector: ALERTA — jobs pk=%s (%s) sin URL resoluble: "
                "degradado a refresco de last_seen", pk, source_name,
            )
            touches.append(pk)
        else:
            listings.append(RawListing(external_id=pk, url=url, payload=payload))
    return listings, touches


def _missing_content(fold: _JobFold) -> bool:
    return bool(_missing_cols(fold))


def _missing_cols(fold: _JobFold) -> tuple:
    if fold.saw_insert:
        return ()  # regla 1: el I trae el esquema legacy completo
    return tuple(c for c in JOB_PAYLOAD_MAP if c not in fold.cols)


def _merge_job_payload(
    fold: _JobFold, prev_raw: dict | None, prev_change: dict | None
) -> tuple[dict | None, str | None]:
    """Payload contractual (§3) con las columnas omitidas completadas desde
    el último valor conocido (regla 2). (None, None) = degradación (regla 3)."""
    payload = {
        JOB_PAYLOAD_MAP[c]: fold.cols[c] for c in JOB_PAYLOAD_MAP if c in fold.cols
    }
    missing = _missing_cols(fold)
    if missing and prev_raw is None and prev_change is None:
        return None, None
    for col in missing:
        _fill_from_previous(payload, col, prev_raw, prev_change)
    url = fold.cols.get("url")
    if url is None and prev_change is not None:
        url = prev_change.get("url")
    return payload, url


def _fill_from_previous(payload, col, prev_raw, prev_change) -> None:
    """Completa UNA columna omitida desde el último valor conocido. Si
    tampoco existía antes (p.ej. columna ausente del esquema legacy) se
    OMITE la clave — misma forma que el payload previo ⇒ mismo content_hash."""
    key = JOB_PAYLOAD_MAP[col]
    if prev_raw is not None and key in prev_raw:
        payload[key] = prev_raw[key]
    elif prev_change is not None and col in prev_change:
        payload[key] = prev_change[col]


async def _resolve_pending_urls(
    session, source_id, url_pending: list[tuple[str, dict]]
) -> list[tuple[str, dict, str | None]]:
    """Última vía para una URL omitida y sin previo: la de la encarnación
    activa del slot. → [(pk, payload, url | None)]."""
    if not url_pending:
        return []
    urls = {
        r.ext: r.url
        for r in (
            await session.execute(
                sa.text(
                    "SELECT l.external_id AS ext, i.url FROM source_listings l "
                    "JOIN source_listing_incarnations i "
                    "  ON i.source_listing_id = l.id AND i.ended_at IS NULL "
                    "WHERE l.source_id = :src AND l.external_id = ANY(:exts)"
                ),
                {"src": source_id, "exts": sorted(pk for pk, _p in url_pending)},
            )
        ).all()
    }
    return [(pk, payload, urls.get(pk)) for pk, payload in url_pending]


def _alert_unnormalizable(source_name: str, listings) -> None:
    """ALERTA persistente (§3): fuente sin handler o normalize→None. La
    degradación normal de identidad (extract (None, None)) NO alerta."""
    if not normalize.has_normalizer(source_name):
        logger.error(
            "projector: ALERTA — fuente %r SIN normalizador registrado "
            "(canónica imposible para todo el lote)", source_name,
        )
        return
    for listing in listings:
        if normalize.normalize_offer(source_name, listing.payload) is None:
            logger.error(
                "projector: ALERTA — normalize_offer devolvió None para "
                "pk=%s en %r (la vacante quedará sin canónica)",
                listing.external_id, source_name,
            )


async def _ensure_legacy_source(session, source_name: str) -> tuple:
    """Alta idempotente de la fuente core + su scope sombra ({"shadow": true},
    tier 0, DESHABILITADO — sin provider registrado, el runner no debe
    tocarlo). Serializada por el single-flight del proyector."""
    legacy_shadow.ensure_registered(source_name)
    await session.execute(
        sa.text(
            "INSERT INTO sources (id, name, tier) VALUES (:i, :n, 0) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"i": uuid.uuid4(), "n": source_name},
    )
    source_id = (
        await session.execute(
            sa.text("SELECT id FROM sources WHERE name = :n"), {"n": source_name}
        )
    ).scalar_one()
    scope_id = (
        await session.execute(
            sa.text(
                "SELECT id FROM harvest_scopes WHERE source_id = :s "
                "AND params @> CAST(:p AS jsonb) ORDER BY id LIMIT 1"
            ),
            {"s": source_id, "p": '{"shadow": true}'},
        )
    ).scalar_one_or_none()
    if scope_id is None:
        scope_id = uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO harvest_scopes (id, source_id, params, tier, enabled) "
                "VALUES (:i, :s, CAST(:p AS jsonb), 0, false)"
            ),
            {"i": scope_id, "s": source_id, "p": '{"shadow": true}'},
        )
    return source_id, scope_id


async def _close_slots(session, sink, source_id, external_ids) -> int:
    """CIERRE de encarnaciones activas con la MAQUINARIA del sink (§3):
    advisory lock por fuente → _lock_vacancies → _close_incarnations →
    _repair_primary_pointers → _rebuild_canonical_after_repair. Idempotente:
    lo ya cerrado no matchea (ended_at IS NULL) y la reparación tiene su
    propio guard."""
    if not external_ids:
        return 0
    await session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:src, 0))"),
        {"src": str(source_id)},
    )
    pre = (
        await session.execute(
            sa.text(
                "SELECT i.id AS inc_id, i.vacancy_id FROM source_listings l "
                "JOIN source_listing_incarnations i "
                "  ON i.source_listing_id = l.id AND i.ended_at IS NULL "
                "WHERE l.source_id = :src AND l.external_id = ANY(:exts)"
            ),
            {"src": source_id, "exts": sorted(set(external_ids))},
        )
    ).all()
    if not pre:
        return 0
    vacancies = [r.vacancy_id for r in pre]
    await sink._lock_vacancies(session, vacancies)
    # Solo se cierran las encarnaciones PRE-seleccionadas (sus vacantes están
    # bloqueadas); el WHERE ended_at IS NULL interno absorbe carreras.
    await sink._close_incarnations(session, [r.inc_id for r in pre])
    repaired = await sink._repair_primary_pointers(session, vacancies)
    await sink._rebuild_canonical_after_repair(session, repaired)
    return len(pre)


async def _touch_last_seen(session, source_id, external_ids) -> None:
    await session.execute(
        sa.text(
            "UPDATE source_listing_incarnations i SET last_seen_at = now() "
            "FROM source_listings l WHERE l.id = i.source_listing_id "
            "AND i.ended_at IS NULL AND l.source_id = :src "
            "AND l.external_id = ANY(:exts)"
        ),
        {"src": source_id, "exts": sorted(set(external_ids))},
    )


async def _latest_slot_raws(session, source_id, external_ids) -> dict[str, dict]:
    """Último raw conocido por slot (encarnación más reciente, activa O
    cerrada — la reactivación tras cierre también debe poder completar)."""
    if not external_ids:
        return {}
    rows = (
        await session.execute(
            sa.text(
                "SELECT DISTINCT ON (l.external_id) l.external_id AS ext, r.raw "
                "FROM source_listings l "
                "JOIN source_listing_incarnations i ON i.source_listing_id = l.id "
                "JOIN source_listing_revisions r ON r.incarnation_id = i.id "
                "WHERE l.source_id = :src AND l.external_id = ANY(:exts) "
                "ORDER BY l.external_id, i.seq DESC, r.fetched_at DESC, r.id"
            ),
            {"src": source_id, "exts": list(external_ids)},
        )
    ).all()
    return {r.ext: r.raw for r in rows}


async def _last_applied_changes(session, src_table: str, pks) -> dict[str, dict]:
    """Último payload YA APLICADO por pk (op I/U, orden lsn/seq) — la vía de
    respaldo del merge y de la resolución de fuente/user_id. Sin claves meta."""
    if not pks:
        return {}
    rows = (
        await session.execute(
            sa.text(
                "SELECT DISTINCT ON (pk) pk, payload FROM shadow_change_log "
                "WHERE src_table = :t AND op IN ('I', 'U') "
                "AND applied_at IS NOT NULL AND pk = ANY(:pks) "
                "ORDER BY pk, lsn DESC, seq_in_tx DESC"
            ),
            {"t": src_table, "pks": sorted(pks)},
        )
    ).all()
    return {
        r.pk: {k: v for k, v in r.payload.items() if not k.startswith("_")}
        for r in rows
    }


async def _count_job_revisions(session, source_id, external_ids) -> int:
    if not external_ids:
        return 0
    return (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM source_listing_revisions r "
                "JOIN source_listing_incarnations i ON i.id = r.incarnation_id "
                "JOIN source_listings l ON l.id = i.source_listing_id "
                "WHERE l.source_id = :src AND l.external_id = ANY(:exts)"
            ),
            {"src": source_id, "exts": list(external_ids)},
        )
    ).scalar_one()


# --------------------------------------------------------------- perfiles


@dataclass
class _ProfileFold:
    fields: dict = field(default_factory=dict)


def _fold_profiles(rows) -> dict[str, _ProfileFold]:
    folds: dict[str, _ProfileFold] = {}
    for r in rows:
        if r.op == "D":
            # §2: el op=D de user_profiles se IGNORA — su PK (id) no es el
            # external_ref; el borrado real viaja como op=D de users.
            continue
        f = folds.setdefault(r.pk, _ProfileFold())
        f.fields.update({k: v for k, v in r.payload.items() if not k.startswith("_")})
    return folds


async def _apply_profiles(session, rows) -> tuple[set, int]:
    """Proyecta user_profiles al consumer sombra. → (perfiles afectados,
    revisiones nuevas).

    ORDEN DE LOCKS (protocolo A-07): primero el upsert de TODAS las pks (sin
    FOR UPDATE) y después bloquear/guardar revisiones en orden profiles.id
    ASCENDENTE — el MISMO orden global que embeddings._lock_profiles_and_current:
    sin ciclo posible con los workers de embeddings/matching concurrentes.
    Gracias a la captura (re-lectura RO + _backfilled) cv_text llega SIEMPRE;
    el fail-safe (_omitted sin _backfilled) PRESERVA: completa desde la
    revisión vigente o salta con alerta — jamás una revisión con CV vacío."""
    folds = _fold_profiles(rows)
    if not folds:
        return set(), 0
    cid = await core_profiles.ensure_consumer(session, SHADOW_CONSUMER)
    before = await _count_profile_revisions(session, cid)
    pid_by_pk = await _upsert_profile_pks(session, cid, folds)
    affected: set = set()
    # Orden (profiles.id, pk): dos pks del mismo usuario comparten pid y el
    # desempate por pk conserva "la última del lote deja la revisión vigente".
    for pk in sorted(pid_by_pk, key=lambda pk: (str(pid_by_pk[pk]), pk)):
        pid = pid_by_pk[pk]
        content = await _complete_profile_content(session, pid, pk, folds[pk])
        if content is None:
            continue
        rid = await core_profiles.save_profile_revision(session, pid, content)
        if rid is not None:
            affected.add(pid)
    revs = await _count_profile_revisions(session, cid) - before
    return affected, revs


async def _upsert_profile_pks(session, cid, folds) -> dict[str, uuid.UUID]:
    """Fase 1 del protocolo A-07: upsert de TODAS las pks SIN FOR UPDATE.
    → pk → profile_id (pk sin user_id resoluble se descarta con ALERTA)."""
    missing_uid = sorted(
        pk for pk, f in folds.items() if f.fields.get("user_id") is None
    )
    prev = await _last_applied_changes(session, "user_profiles", missing_uid)
    pid_by_pk: dict[str, uuid.UUID] = {}
    for pk in sorted(folds):
        user_id = (
            folds[pk].fields.get("user_id") or (prev.get(pk) or {}).get("user_id")
        )
        if user_id is None:
            logger.error(
                "projector: ALERTA — user_profiles pk=%s sin user_id "
                "resoluble: descartado", pk,
            )
            continue
        pid_by_pk[pk] = await core_profiles.upsert_profile(session, cid, str(user_id))
    return pid_by_pk


async def _complete_profile_content(session, pid, pk, fold) -> dict | None:
    """Content EXACTO {title, cv_text, skills}; los campos omitidos (fail-safe
    `_omitted` sin `_backfilled`) se PRESERVAN desde la revisión vigente —
    sin vigente, None con alerta (jamás una revisión con CV vacío)."""
    content = {k: fold.fields[k] for k in PROFILE_FIELDS if k in fold.fields}
    missing = [k for k in PROFILE_FIELDS if k not in content]
    if not missing:
        return content
    cur = await core_profiles.current_revision(session, pid)
    if cur is None:
        logger.error(
            "projector: ALERTA — user_profiles pk=%s con %s omitidos y "
            "SIN revisión previa que preserve: se salta", pk, missing,
        )
        return None
    for k in missing:
        content[k] = cur.content.get(k)
    return content


async def _count_profile_revisions(session, consumer_id) -> int:
    return (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM profile_revisions pr "
                "JOIN profiles p ON p.id = pr.profile_id "
                "WHERE p.consumer_id = :c"
            ),
            {"c": consumer_id},
        )
    ).scalar_one()


# ------------------------------------------------------------------ users


def _fold_users(rows) -> dict[str, bool]:
    """pk → 'el último op fue D' (el estado is_active se decide en la
    evaluación leyendo el staging aplicado — ver docstring de módulo)."""
    out: dict[str, bool] = {}
    for r in rows:
        out[r.pk] = r.op == "D"
    return out


async def _apply_users(session, rows) -> set:
    """users op=D ⇒ ERASE GDPR del perfil sombra. Devuelve los profile_ids
    borrados (para restarlos de los afectados del lote). Varios borrados en
    la misma tx van en orden profiles.id ASCENDENTE — el mismo orden global
    de locks de perfil que el resto del protocolo A-07."""
    folds = _fold_users(rows)
    dead = sorted(pk for pk, gone in folds.items() if gone)
    if not dead:
        return set()
    pid_by_ref = {
        r.external_ref: r.id
        for r in (
            await session.execute(
                sa.text(
                    "SELECT p.id, p.external_ref FROM profiles p "
                    "JOIN consumers c ON c.id = p.consumer_id "
                    "WHERE c.name = :cn AND p.external_ref = ANY(:refs)"
                ),
                {"cn": SHADOW_CONSUMER, "refs": dead},
            )
        ).all()
    }
    erased: set = set()
    # Sin perfil (str vacío) primero: no toman lock y el orden les da igual.
    for pk in sorted(dead, key=lambda pk: (str(pid_by_ref.get(pk, "")), pk)):
        pid = await erase_shadow_profile(session, pk)
        if pid is not None:
            erased.add(pid)
            logger.info(
                "projector: ERASE GDPR del perfil sombra %s (users pk=%s)",
                pid, pk,
            )
    return erased


async def erase_shadow_profile(
    session, external_ref: str, consumer_name: str = SHADOW_CONSUMER
) -> uuid.UUID | None:
    """ERASE completo y reutilizable del perfil sombra (GDPR §3): revisiones,
    activaciones, vectores, evaluaciones, estado y outbox del perfil, en el
    orden FK-safe de tests/dbcleanup.py — aquí en código de producción.
    Idempotente: sin perfil no hay nada que borrar (None)."""
    pid = (
        await session.execute(
            sa.text(
                "SELECT p.id FROM profiles p "
                "JOIN consumers c ON c.id = p.consumer_id "
                "WHERE c.name = :cn AND p.external_ref = :ref"
            ),
            {"cn": consumer_name, "ref": external_ref},
        )
    ).scalar_one_or_none()
    if pid is None:
        return None
    # PRIMERO el lock del perfil (orden perfil→estado del protocolo A-07 —
    # el mismo de evaluate_profile/save_profile_revision/embeddings): jamás
    # borrar el grafo hijo sin serializar con quien evalúa o embebe.
    await session.execute(
        sa.text("SELECT id FROM profiles WHERE id = :p FOR UPDATE"), {"p": pid}
    )
    # Outbox del perfil (las deliveries caen por ON DELETE CASCADE).
    await session.execute(
        sa.text("DELETE FROM integration_outbox WHERE subject_profile_id = :p"),
        {"p": pid},
    )
    for table in _ERASE_TABLES:
        await session.execute(
            sa.text(f"DELETE FROM {table} WHERE profile_id = :p"), {"p": pid}
        )
    await session.execute(
        sa.text("DELETE FROM profiles WHERE id = :p"), {"p": pid}
    )
    return pid


# ------------------------------------------------- flujo normal tras el lote


async def _replay_after_batch(session_factory) -> int:
    """Re-garantiza el disparo post-lote al ENTRAR (bajo el single-flight,
    ANTES de drenar el staging): un crash tras el commit de un lote pero
    antes o a mitad de _after_batch deja embeddings sin drenar o
    evaluaciones sin disparar y SIN rastro en el change_log (ya sellado).
    Se drenan SIEMPRE los embeddings pendientes y se evalúan TODOS los
    perfiles sombra activos — re-ejecutable y barato: run_pending sin
    pendientes es no-op y evaluate_profile es dedup por eval_key (jamás
    duplica). Devuelve los perfiles evaluados en la recuperación."""
    await _drain_embeddings()
    async with session_factory() as session:
        targets = await _active_shadow_profiles(session)
    for pid in targets:
        await _run_profile_impl(str(pid), EVAL_LIMIT)
    return len(targets)


async def _after_batch(session_factory, result: _BatchResult) -> int:
    """Embeddings pendientes + evaluate_profile del flujo NORMAL del core.
    Eficiencia (§3): solo perfiles afectados — o todos los activos del
    consumer sombra si hubo corpus nuevo. Corre FUERA de la tx del lote."""
    if not (
        result.revisions_new or result.corpus_changed or result.affected_profiles
    ):
        return 0
    await _drain_embeddings()
    async with session_factory() as session:
        targets = await _eval_targets(session, result)
    for pid in targets:
        # Mismo top-K por defecto que la tarea jobhunt.matching.run_profile.
        await _run_profile_impl(str(pid), EVAL_LIMIT)
    return len(targets)


async def _drain_embeddings(limit: int = 200, max_rounds: int = 50) -> None:
    """run_pending hasta vaciar (acotado): la evaluación necesita vectores."""
    for _ in range(max_rounds):
        r = await _run_pending_impl(limit)
        if not any(r["embedded"].values()) and not any(
            r["profiles_embedded"].values()
        ):
            return
    logger.warning("projector: embeddings sin drenar tras %d rondas", max_rounds)


async def _eval_targets(session, result: _BatchResult) -> list:
    if result.corpus_changed:
        return await _active_shadow_profiles(session)
    if not result.affected_profiles:
        return []
    cid = await _shadow_consumer_id(session)
    if cid is None:
        return []
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, external_ref FROM profiles "
                "WHERE consumer_id = :c AND id = ANY(:ids)"
            ),
            {"c": cid, "ids": sorted(result.affected_profiles, key=str)},
        )
    ).all()
    return await _filter_inactive(session, rows)


async def _active_shadow_profiles(session) -> list:
    """TODOS los perfiles del consumer sombra menos los excluidos por
    users.is_active=false (mecanismo de exclusión de cabecera) — los
    objetivos de 'corpus nuevo' y del re-disparo de entrada."""
    cid = await _shadow_consumer_id(session)
    if cid is None:
        return []
    rows = (
        await session.execute(
            sa.text("SELECT id, external_ref FROM profiles WHERE consumer_id = :c"),
            {"c": cid},
        )
    ).all()
    return await _filter_inactive(session, rows)


async def _shadow_consumer_id(session):
    return (
        await session.execute(
            sa.text("SELECT id FROM consumers WHERE name = :n"),
            {"n": SHADOW_CONSUMER},
        )
    ).scalar_one_or_none()


async def _filter_inactive(session, rows) -> list:
    inactive = await _inactive_user_refs(session, [r.external_ref for r in rows])
    return sorted((r.id for r in rows if r.external_ref not in inactive), key=str)


async def _inactive_user_refs(session, refs) -> set[str]:
    """Set de user_ids inactivos desde el ÚLTIMO estado users por pk del
    staging YA APLICADO (mecanismo de exclusión documentado en cabecera)."""
    if not refs:
        return set()
    rows = (
        await session.execute(
            sa.text(
                "SELECT pk FROM ("
                "  SELECT DISTINCT ON (pk) pk, payload->>'is_active' AS act "
                "  FROM shadow_change_log "
                "  WHERE src_table = 'users' AND op IN ('I', 'U') "
                "    AND applied_at IS NOT NULL AND pk = ANY(:refs) "
                "  ORDER BY pk, lsn DESC, seq_in_tx DESC) last "
                "WHERE last.act = 'false'"
            ),
            {"refs": sorted(refs)},
        )
    ).scalars().all()
    return set(rows)

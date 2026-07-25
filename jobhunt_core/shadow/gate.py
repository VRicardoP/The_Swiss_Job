"""Harness del GATE-SOMBRA (B-05, CONTRATOS_FASE_B.md §6/§7/§8).

Última etapa de la Fase B: orquesta el ciclo diario de la sombra
(proyección → métricas del ciclo CERRADO → purga → gates), lleva el
CONTADOR de N=7 ciclos consecutivos en verde, vigila la salud del slot
(§6: retención WAL > 2 GiB o consumidor parado > 30 min ⇒ ALERTA) y
ejecuta el rollback/replay del runbook (shadow/RUNBOOK.md) como función
con guardas de seguridad. TODO LOCAL: prod/QNAP fuera de alcance.

DECISIONES (documentadas, no obvias):

- CADENCIAS vía beat del celery_app del core (SOLO colas core.*, cableadas
  en celery_app.py): `sample_outbox_lag` y `check_slot_health` cada 5 min
  (core.default — observabilidad ligera, jamás detrás de un lote del
  proyector), `run_cycle` diario a las 06:05 Europe/Zurich (core.harvest —
  es ingesta y serializa con el proyector). Ajustables por settings
  (CORE_SHADOW_*). El beat corre EN EL core-worker LOCAL:
  `docker compose exec core-worker celery -A jobhunt_core.celery_app beat`
  (el compose NO se toca sin OK del propietario — regla del proyecto; el
  comando vive en el runbook). 06:05 y no 06:00: el ciclo cierra a las
  06:00 (§5) y el margen evita computar un ciclo aún abierto por deriva
  de reloj entre beat y BD.

- SINGLE-FLIGHT: `run_cycle` tiene lock PROPIO (advisory lock de sesión
  'jobhunt:shadow-run-cycle' sobre conexión dedicada — el MISMO patrón del
  proyector) y además REUTILIZA el del proyector tal cual: si otro worker
  está drenando, project_pending sale con status='already_running' y
  run_cycle NO espera — computa igual (idempotente: upsert por PK; el
  backlog sin aplicar > 1h lo captura el término de `perdida`, §5) y deja
  el estado del drenado anotado en el resultado. Re-entrante: repetir el
  ciclo re-proyecta nada (staging sellado), recomputa a idéntico valor y
  la purga es idempotente.

- CONTADOR (§6): un ciclo SUMA si está COMPUTADO (alguna fila de
  shadow_cycle_metrics con finished_at sellado — el placeholder del
  muestreador no sella) y TODOS los [gate] de evaluate_gates están en
  verde. Un ciclo en rojo O sin computar RESETEA (se cuenta hacia atrás
  desde el último ciclo CERRADO y se corta en el primer no-verde); las
  [alerta] NO resetean (§6: se registran y avisan). Gate sin datos = no
  demostrable = ok False en evaluate_gates ⇒ no suma (conservador).

- ROLLBACK/REPLAY (§6/§8): exige `confirm=True` EXPLÍCITO y NUNCA toca
  `public` — la conexión de escritura fija search_path SOLO al esquema
  del core y todo su SQL va sin cualificar (estructuralmente no puede
  resolver a una tabla legacy); el ÚNICO contacto con el legacy es la
  LECTURA del re-backfill, reutilizando ShadowCapture (B-01) para
  re-crear slot + snapshot exportado + backfill + frontera. El re-arranque
  del consumidor es del operador (runbook): la función deja el slot nuevo
  con la frontera registrada y core-capture reanuda desde
  last_applied_lsn. shadow_projection_batches NO se trunca (historia de
  latencia de ciclos pasados: sigue siendo válida y la purga no la toca).
  Cierre masivo de encarnaciones SIN reparación de canónica: las vacantes
  se ARCHIVAN en la misma transacción (fuera del feed y de evaluaciones
  futuras) — la reconstrucción fina del sink no aplica a un corpus que se
  retira entero.
"""

import logging
import time as time_mod
from datetime import date, datetime, timedelta, timezone

import psycopg2
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from jobhunt_core.config import settings
from jobhunt_core.database import create_core_engine, task_session_factory
from jobhunt_core.shadow.capture import (
    DEFAULT_SLOT,
    DEFAULT_TABLES,
    ShadowCapture,
    _IDENT_RE,
)
from jobhunt_core.shadow.metrics import (
    KIND_ALERTA,
    KIND_GATE,
    compute_cycle,
    evaluate_gates,
    latest_closed_cycle_id,
    purge_staging,
)
from jobhunt_core.shadow.projector import DEFAULT_BATCH_SIZE, project_pending

logger = logging.getLogger(__name__)

# ------------------------------------------------ umbrales RATIFICADOS (§6)

GATE_CYCLES_REQUIRED = 7               # N ciclos diarios CONSECUTIVOS en verde
SLOT_WAL_RETENTION_MAX_BYTES = 2 * 1024**3  # retención WAL del slot > 2 GiB
SLOT_STALLED_MAX_S = 30 * 60           # consumidor parado > 30 min

# Clave del single-flight de run_cycle (advisory lock de sesión, patrón y
# disciplina del proyector — clave DISTINTA de la suya).
_RUN_CYCLE_LOCK = "jobhunt:shadow-run-cycle"


# ------------------------------------------------------- ciclo orquestado


async def run_cycle(
    cycle_id: date | None = None,
    legacy_schema: str = "public",
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
) -> dict:
    """Orquestador del ciclo (§7 B-05): project_pending → compute_cycle del
    ciclo CERRADO (o `cycle_id` explícito — replay/backfill) → purge_staging
    → evaluate_gates + contador. Idempotente y re-entrante (ver cabecera).

    Devuelve un resumen JSON-serializable (resultado de la tarea Celery)."""
    result: dict = {"status": "ok"}
    lock_engine = create_core_engine(poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        lock_conn = await lock_engine.connect()
        try:
            got = (
                await lock_conn.execute(
                    sa.text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"),
                    {"k": _RUN_CYCLE_LOCK},
                )
            ).scalar_one()
            if not got:
                logger.info("gate: run_cycle ya en curso — salida limpia")
                result["status"] = "already_running"
                return result
            try:
                await _run_cycle_locked(
                    result, cycle_id, legacy_schema, now, batch_size, max_batches
                )
            finally:
                try:
                    await lock_conn.execute(
                        sa.text(
                            "SELECT pg_advisory_unlock(hashtextextended(:k, 0))"
                        ),
                        {"k": _RUN_CYCLE_LOCK},
                    )
                except Exception:  # pragma: no cover — conexión rota
                    # El lock de sesión muere con su conexión: cerrar basta.
                    logger.warning(
                        "gate: pg_advisory_unlock falló — el lock se libera "
                        "al cerrar la conexión dedicada"
                    )
        finally:
            await lock_conn.close()
    finally:
        await lock_engine.dispose()
    return result


async def _run_cycle_locked(
    result, cycle_id, legacy_schema, now, batch_size, max_batches
) -> None:
    """Cuerpo del ciclo (YA bajo el single-flight)."""
    cid = cycle_id or latest_closed_cycle_id(now)
    result["cycle_id"] = cid.isoformat()
    # 1) Drenado del staging (single-flight del proyector REUTILIZADO: si ya
    # hay una proyección en curso no se espera — ver decisión de cabecera).
    result["project"] = await project_pending(
        batch_size=batch_size, max_batches=max_batches
    )
    async with task_session_factory() as factory:
        # 2) + 3) Métricas del ciclo cerrado y purga en UNA transacción.
        async with factory() as session:
            result["metrics"] = await compute_cycle(
                session, cycle_id=cid, legacy_schema=legacy_schema, now=now
            )
            result["purge"] = await purge_staging(session, now=now)
            await session.commit()
        # 4) Gates del ciclo + contador de consecutivos (lectura pura).
        async with factory() as session:
            gates = await evaluate_gates(session, cid)
            status = await gate_status(session, now=now)
    failed = sorted(
        k for k, g in gates.items() if g["kind"] == KIND_GATE and not g["ok"]
    )
    alerts = sorted(
        k for k, g in gates.items() if g["kind"] == KIND_ALERTA and not g["ok"]
    )
    result["cycle_ok"] = not failed
    result["gates_failed"] = failed
    result["alertas"] = alerts
    result["consecutive_ok"] = status["consecutive_ok"]
    result["required"] = status["required"]
    if failed:
        logger.warning(
            "gate: ciclo %s NO APTO (gates en rojo: %s) — contador a 0",
            cid, ", ".join(failed),
        )
    else:
        logger.info(
            "gate: ciclo %s APTO — %d/%d consecutivos",
            cid, status["consecutive_ok"], status["required"],
        )


# ------------------------------------------------- contador de N ciclos (§6)


async def gate_status(
    session: AsyncSession,
    now: datetime | None = None,
    required: int = GATE_CYCLES_REQUIRED,
) -> dict:
    """Contador de ciclos CONSECUTIVOS en verde leyendo shadow_cycle_metrics
    vía evaluate_gates, hacia atrás desde el último ciclo CERRADO. Un ciclo
    en rojo o SIN COMPUTAR corta la cuenta (las [alerta] no, §6).

    → {consecutive_ok, required, gate_passed, last_cycle,
       per_cycle: [{cycle, computado, ok, gates_rojos, alertas}, ...]}
    (per_cycle: los `required` ciclos más recientes, del último hacia atrás).
    """
    last = latest_closed_cycle_id(now)
    first = (
        await session.execute(
            sa.text("SELECT min(cycle_id) FROM shadow_cycle_metrics")
        )
    ).scalar_one_or_none()
    per_cycle: list[dict] = []
    consecutive = 0
    counting = True
    cid = last
    while first is not None and cid >= first:
        entry = await _cycle_entry(session, cid)
        if len(per_cycle) < required:
            per_cycle.append(entry)
        if counting:
            if entry["ok"]:
                consecutive += 1
            else:
                counting = False
        # Corte anticipado (1ª rev. B-05): con la ventana del informe llena y
        # el veredicto ya decidido (racha rota O required consecutivos), los
        # ciclos más antiguos no cambian nada — sin esto, una racha verde
        # larga recorría TODO el histórico (shadow_cycle_metrics no purga
        # filas: coste diario creciente sin cota).
        if len(per_cycle) >= required and (not counting or consecutive >= required):
            break
        cid -= timedelta(days=1)
    return {
        "consecutive_ok": consecutive,
        "required": required,
        "gate_passed": consecutive >= required,
        "last_cycle": last.isoformat(),
        "per_cycle": per_cycle,
    }


async def _cycle_entry(session: AsyncSession, cid: date) -> dict:
    """Veredicto de UN ciclo para el contador. COMPUTADO = alguna fila con
    finished_at sellado (compute_cycle sella; el placeholder del muestreador
    de outbox no) — un ciclo solo muestreado NO cuenta como computado."""
    computed = bool(
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
    if not computed:
        return {
            "cycle": cid.isoformat(), "computado": False, "ok": False,
            "gates_rojos": [], "alertas": [],
        }
    gates = await evaluate_gates(session, cid)
    failed = sorted(
        k for k, g in gates.items() if g["kind"] == KIND_GATE and not g["ok"]
    )
    alerts = sorted(
        k for k, g in gates.items() if g["kind"] == KIND_ALERTA and not g["ok"]
    )
    return {
        "cycle": cid.isoformat(), "computado": True, "ok": not failed,
        "gates_rojos": failed, "alertas": alerts,
    }


async def render_gate_report(
    session: AsyncSession,
    now: datetime | None = None,
    required: int = GATE_CYCLES_REQUIRED,
) -> str:
    """Informe LEGIBLE del estado del GATE-SOMBRA (contador + últimos ciclos)."""
    st = await gate_status(session, now=now, required=required)
    verdict = (
        "GATE-SOMBRA SUPERADO"
        if st["gate_passed"]
        else f"EN CURSO (faltan {st['required'] - st['consecutive_ok']})"
    )
    lines = [
        "GATE-SOMBRA — contador de ciclos consecutivos en verde (§6)",
        f"Último ciclo cerrado: {st['last_cycle']} · consecutivos OK: "
        f"{st['consecutive_ok']}/{st['required']} · estado: {verdict}",
        "",
        f"{'ciclo':<12} {'computado':<10} {'estado':<8} detalle",
        "-" * 72,
    ]
    for e in st["per_cycle"]:
        estado = "OK" if e["ok"] else ("FALLO" if e["computado"] else "—")
        detalle = []
        if e["gates_rojos"]:
            detalle.append("gates: " + ", ".join(e["gates_rojos"]))
        if e["alertas"]:
            detalle.append("alertas: " + ", ".join(e["alertas"]))
        if not e["computado"]:
            detalle.append("SIN COMPUTAR (resetea el contador)")
        lines.append(
            f"{e['cycle']:<12} {'sí' if e['computado'] else 'no':<10} "
            f"{estado:<8} {'; '.join(detalle) or '—'}"
        )
    if not st["per_cycle"]:
        lines.append("(sin ciclos computados todavía)")
    return "\n".join(lines) + "\n"


# ------------------------------------------------- salud del slot (§6/§8)


async def check_slot_health(
    session: AsyncSession,
    slot: str = DEFAULT_SLOT,
    now: datetime | None = None,
    wal_retention_max_bytes: int = SLOT_WAL_RETENTION_MAX_BYTES,
    stalled_max_s: float = SLOT_STALLED_MAX_S,
) -> dict:
    """ALERTA (logger.error persistente) si el slot retiene WAL > 2 GiB
    (pg_wal_lsn_diff sobre restart_lsn) o el consumidor lleva parado > 30
    min (slot inactivo en pg_replication_slots + antigüedad de
    shadow_capture_state.updated_at). Umbrales de §6 como constantes,
    inyectables SOLO para tests.

    Estados sin alerta: sin slot NI estado = sombra sin bootstrap (nada
    retiene WAL: no hay riesgo que vigilar); slot inactivo con updated_at
    reciente = parada dentro de la gracia de 30 min; slot presente sin
    estado = bootstrap en curso (backfill antes de START_REPLICATION,
    mismo matiz que el healthcheck de B-01) — warning, no alerta."""
    moment = now or datetime.now(timezone.utc)
    slot_row = (
        await session.execute(
            sa.text(
                "SELECT active, "
                "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint "
                "  AS retained "
                "FROM pg_replication_slots WHERE slot_name = :s"
            ),
            {"s": slot},
        )
    ).one_or_none()
    state = (
        await session.execute(
            sa.text(
                "SELECT slot_name, updated_at FROM shadow_capture_state "
                "WHERE id = 1"
            )
        )
    ).one_or_none()
    alerts: list[dict] = []
    retained = stalled_s = None
    active = None
    if slot_row is None:
        if state is not None:
            alerts.append({
                "code": "slot_ausente",
                "msg": (
                    f"slot {slot} AUSENTE con estado registrado: continuidad "
                    "WAL perdida — ejecutar rollback/replay (RUNBOOK.md)"
                ),
            })
    else:
        active = bool(slot_row.active)
        retained = int(slot_row.retained) if slot_row.retained is not None else None
        if retained is not None and retained > wal_retention_max_bytes:
            alerts.append({
                "code": "retencion_wal",
                "msg": (
                    f"slot {slot} retiene {retained} bytes de WAL "
                    f"(> {wal_retention_max_bytes}): riesgo de disco de la BD "
                    "COMPARTIDA (§8) — diagnosticar consumidor; en emergencia, "
                    "drop-slot del RUNBOOK.md"
                ),
            })
        if not active:
            if state is None:
                logger.warning(
                    "gate: slot %s presente sin estado (bootstrap en curso o "
                    "interrumpido) — sin alerta todavía", slot,
                )
            else:
                stalled_s = (moment - state.updated_at).total_seconds()
                if stalled_s > stalled_max_s:
                    alerts.append({
                        "code": "consumidor_parado",
                        "msg": (
                            f"slot {slot} INACTIVO y sin progreso desde hace "
                            f"{stalled_s:.0f}s (> {stalled_max_s:.0f}s): "
                            "consumidor parado — reiniciar core-capture "
                            "(RTO <= 1h, RUNBOOK.md)"
                        ),
                    })
    for alert in alerts:
        logger.error("gate: ALERTA slot — %s", alert["msg"])
    return {
        "slot": slot,
        "slot_exists": slot_row is not None,
        "state_exists": state is not None,
        "active": active,
        "retained_bytes": retained,
        "stalled_s": round(stalled_s, 3) if stalled_s is not None else None,
        "alertas": alerts,
        "ok": not alerts,
        "umbrales": {
            "wal_retention_max_bytes": wal_retention_max_bytes,
            "stalled_max_s": stalled_max_s,
        },
    }


# --------------------------------------------- rollback/replay (§6/§8, runbook)


def rollback_replay(
    capture_dsn: str,
    core_dsn: str,
    slot: str = DEFAULT_SLOT,
    tables: str = DEFAULT_TABLES,
    schema: str | None = None,
    confirm: bool = False,
    slot_release_timeout_s: float = 30.0,
) -> dict:
    """La secuencia del runbook (§6, ensaya el rollback de Fase C) como
    función ejecutable: parar consumidor → DROP del slot → desactivar
    fuentes `legacy:*` y ARCHIVAR sus vacantes → truncar staging →
    re-crear slot + re-backfill por snapshot (ShadowCapture reutilizado).

    GUARDAS: exige confirm=True EXPLÍCITO (destruye slot y staging) y NUNCA
    toca `public` — la conexión de escritura fija search_path SOLO al
    esquema del core (rechazado si schema == 'public') y todo el SQL va sin
    cualificar; el legacy solo se LEE (re-backfill RO de B-01). El
    consumidor real (core-capture) debe estar PARADO: cualquier walsender
    residual del slot se termina aquí (mismo rol jobhunt_capture). Tras la
    función, re-arrancar core-capture (reanuda desde last_applied_lsn).

    Síncrona (psycopg2, como capture.py): herramienta operativa del
    runbook, no tarea Celery."""
    if confirm is not True:
        raise RuntimeError(
            "rollback_replay DESTRUYE el slot y el staging de la sombra: "
            "exige confirm=True explícito (ver shadow/RUNBOOK.md)"
        )
    schema = schema or settings.CORE_DB_SCHEMA
    if not _IDENT_RE.match(schema) or schema == "public":
        raise RuntimeError(
            f"esquema de escritura inválido para el rollback: {schema!r} "
            "(jamás 'public' — el core no escribe en el esquema legacy)"
        )
    if not _IDENT_RE.match(slot):
        raise RuntimeError(f"nombre de slot inválido: {slot!r}")
    core_dsn = core_dsn.replace("postgresql+asyncpg://", "postgresql://")
    summary: dict = {"slot": slot}

    # Pasos 1-2: parar el walsender residual y DROP del slot (rol de
    # replicación — el mismo que lo creó).
    summary["slot_dropped"] = _stop_and_drop_slot(
        capture_dsn, slot, slot_release_timeout_s
    )
    logger.info(
        "rollback_replay: paso 1-2 — slot %s %s", slot,
        "eliminado" if summary["slot_dropped"] else "ya ausente",
    )

    # Pasos 3-4: desactivar fuentes + archivar vacantes + truncar staging,
    # TODO en una transacción (search_path SOLO al esquema core: guard
    # estructural — ninguna sentencia puede resolver a public).
    core = psycopg2.connect(core_dsn, options=f"-c search_path={schema}")
    try:
        with core.cursor() as cur:
            cur.execute(
                "UPDATE harvest_scopes hs SET enabled = false "
                "FROM sources s WHERE s.id = hs.source_id "
                "AND s.name LIKE 'legacy:%' AND hs.enabled"
            )
            summary["scopes_disabled"] = cur.rowcount
            cur.execute(
                "UPDATE source_listing_incarnations i SET ended_at = now() "
                "FROM source_listings l, sources s "
                "WHERE l.id = i.source_listing_id AND s.id = l.source_id "
                "AND s.name LIKE 'legacy:%' AND i.ended_at IS NULL"
            )
            summary["incarnations_closed"] = cur.rowcount
            # Vacantes de la sombra: alguna encarnación en fuente legacy:* y
            # ninguna encarnación activa restante (una fuente NO legacy viva
            # la mantendría — en sombra no existe, pero el guard es explícito).
            cur.execute(
                "UPDATE vacancies v SET archived_at = now() "
                "WHERE v.archived_at IS NULL AND EXISTS ("
                "  SELECT 1 FROM source_listing_incarnations i "
                "  JOIN source_listings l ON l.id = i.source_listing_id "
                "  JOIN sources s ON s.id = l.source_id "
                "  WHERE i.vacancy_id = v.id AND s.name LIKE 'legacy:%') "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM source_listing_incarnations i2 "
                "  WHERE i2.vacancy_id = v.id AND i2.ended_at IS NULL)"
            )
            summary["vacancies_archived"] = cur.rowcount
            cur.execute("SELECT count(*) FROM shadow_change_log")
            summary["staging_rows_deleted"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM shadow_capture_state")
            summary["state_rows_deleted"] = cur.fetchone()[0]
            cur.execute("TRUNCATE shadow_change_log, shadow_capture_state")
        core.commit()
        logger.info(
            "rollback_replay: paso 3-4 — fuentes legacy desactivadas "
            "(%d encarnaciones cerradas, %d vacantes archivadas) y staging "
            "truncado (%d filas)",
            summary["incarnations_closed"], summary["vacancies_archived"],
            summary["staging_rows_deleted"],
        )

        # Paso 5: re-crear slot + re-backfill por snapshot (B-01 reutilizado:
        # bootstrap = CREATE_REPLICATION_SLOT ... EXPORT_SNAPSHOT + backfill
        # consistente + frontera en shadow_capture_state, atómico).
        cap = ShadowCapture(
            capture_dsn, core_dsn, slot=slot, tables=tables, schema=schema
        )
        try:
            cap.start()
        finally:
            cap.close()  # el re-arranque del consumidor es del operador
        with core.cursor() as cur:
            cur.execute(
                "SELECT snapshot_lsn, last_applied_lsn "
                "FROM shadow_capture_state WHERE id = 1"
            )
            snapshot_lsn, last_applied = cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM shadow_change_log "
                "WHERE op = 'I' AND lsn = %s",
                (snapshot_lsn,),
            )
            backfill_rows = cur.fetchone()[0]
        core.commit()
    finally:
        core.close()
    summary |= {
        "slot_recreated": True,
        "snapshot_lsn": int(snapshot_lsn),
        "last_applied_lsn": int(last_applied),
        "backfill_rows": int(backfill_rows),
    }
    logger.info(
        "rollback_replay: paso 5 — slot %s re-creado (snapshot_lsn=%d, "
        "%d filas de re-backfill); re-arrancar core-capture",
        slot, summary["snapshot_lsn"], summary["backfill_rows"],
    )
    return summary


def _stop_and_drop_slot(capture_dsn: str, slot: str, timeout_s: float) -> bool:
    """Termina el walsender residual (mismo rol) y DROPea el slot. False si
    el slot ya no existía (idempotente)."""
    conn = psycopg2.connect(capture_dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            deadline = time_mod.monotonic() + timeout_s
            while True:
                cur.execute(
                    "SELECT active, active_pid FROM pg_replication_slots "
                    "WHERE slot_name = %s",
                    (slot,),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                active, pid = row
                if not active:
                    cur.execute("SELECT pg_drop_replication_slot(%s)", (slot,))
                    return True
                if pid:
                    # Mismo rol que el walsender (jobhunt_capture): permitido
                    # sin superusuario.
                    cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
                if time_mod.monotonic() >= deadline:
                    raise RuntimeError(
                        f"slot {slot} sigue ACTIVO tras {timeout_s}s: parar "
                        "el consumidor (core-capture) antes del rollback"
                    )
                time_mod.sleep(0.25)
    finally:
        conn.close()

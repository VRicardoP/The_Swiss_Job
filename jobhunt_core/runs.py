"""Runs de cosecha idempotentes (A-11, CONTRATOS §1/§4 + ADR-05).

`harvest_runs` no tiene run_key en el esquema RATIFICADO: la idempotencia
viene del ID DETERMINISTA — id = uuid5(ns, run_key) — más ON CONFLICT:
el REINTENTO del mismo run lógico (p. ej. la ventana diaria) reutiliza la
misma fila y NO duplica (DoD). Por scope, la PK (run_id, scope_id) hace lo
propio; un scope ya TERMINADO con éxito en ese run se SALTA en el reintento
(el que quedó en error o colgado se re-ejecuta — el sink es idempotente).
"""

import asyncio
import contextlib
import logging
import uuid

import sqlalchemy as sa

logger = logging.getLogger(__name__)

RUNS_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "jobhunt-core/harvest-runs")

# Lease por scope (rev. 1ª A-11): una fila 'running' SIN finished_at solo se
# re-arma si su ÚLTIMA SEÑAL DE VIDA superó este lease — distingue "worker
# corriendo AHORA" (otro run_all solapado: se salta) de "worker muerto" (se
# re-ejecuta).
SCOPE_LEASE_S = 900

# Cadencia del heartbeat que renueva el lease durante un fetch largo. Un fetch legítimamente lento
# superaba el lease (medido desde `started_at`, inmutable) y otro run_all re-armaba el scope: el
# token de core0017 impide la corrupción de estado, pero AMBOS workers golpeaban la fuente externa
# (residual conocido de la revisión integral). A lease/3 caben dos latidos perdidos antes de que
# otro worker considere muerto a este; el primero es a los 300 s, así que una cosecha normal (que
# dura mucho menos) NO paga ninguna consulta extra.
SCOPE_HEARTBEAT_S = SCOPE_LEASE_S // 3

def _alive_at(alias: str = "") -> str:
    """Señal de vida vigente de la fila: las filas anteriores a core0018 no tienen heartbeat y
    conservan la semántica de siempre (started_at)."""
    p = f"{alias}." if alias else ""
    return f"COALESCE({p}heartbeat_at, {p}started_at)"


def run_id_for(run_key: str) -> uuid.UUID:
    """ID determinista del run lógico: mismo run_key ⇒ misma fila."""
    return uuid.uuid5(RUNS_NAMESPACE, run_key)


async def start_run(session, run_key: str) -> uuid.UUID:
    """Idempotente: crea (o RE-ABRE) el run del run_key — el reintento de un
    run cerrado vuelve a 'running' con finished_at NULL (rev. 1ª A-11:
    finished_at != NULL con scopes en vuelo mentiría al monitor)."""
    run_id = run_id_for(run_key)
    await session.execute(
        sa.text(
            "INSERT INTO harvest_runs (id) VALUES (:id) "
            "ON CONFLICT (id) DO UPDATE "
            "SET status = 'running', finished_at = NULL"
        ),
        {"id": run_id},
    )
    return run_id


async def claim_scope_run(session, run_id, scope_id) -> uuid.UUID | None:
    """Claim ATÓMICO (rev. 1ª A-11): devuelve un `claim_token` NUEVO si ESTE worker gana el scope,
    o None si no. El token FENCE (rev. externa integral): `finish_scope_run` solo cierra el scope si
    presenta el token del claim VIGENTE, así un worker desahuciado (cuyo lease venció y otro re-armó
    el scope con un token nuevo) no puede sobrescribir el estado del worker actual.
    - Fila nueva: el propio INSERT es el claim exclusivo (fija el token).
    - Terminada con éxito/decisión: nadie la gana (no duplicar).
    - 'error' terminado o colgada con LEASE vencido: se re-arma en el MISMO UPDATE condicional con
      un token NUEVO — dos workers solapados no pueden ganarla ambos."""
    token = uuid.uuid4()
    inserted = (
        await session.execute(
            sa.text(
                "INSERT INTO source_harvest_runs "
                "(run_id, scope_id, claim_token, heartbeat_at) "
                "VALUES (:rid, :sid, :tok, clock_timestamp()) "
                "ON CONFLICT (run_id, scope_id) DO NOTHING "
                "RETURNING run_id"
            ),
            {"rid": run_id, "sid": scope_id, "tok": token},
        )
    ).one_or_none()
    if inserted is not None:
        return token
    rearmed = (
        await session.execute(
            sa.text(
                "UPDATE source_harvest_runs "
                "SET status = 'running', started_at = clock_timestamp(), "
                "heartbeat_at = clock_timestamp(), "
                "finished_at = NULL, claim_token = :tok "
                "WHERE run_id = :rid AND scope_id = :sid "
                "AND ((finished_at IS NOT NULL AND status = 'error') "
                f"     OR (finished_at IS NULL AND {_alive_at()} < "
                "         clock_timestamp() - make_interval(secs => :lease))) "
                "RETURNING run_id"
            ),
            {"rid": run_id, "sid": scope_id, "tok": token, "lease": SCOPE_LEASE_S},
        )
    ).one_or_none()
    return token if rearmed is not None else None


async def beat_scope_run(session, run_id, scope_id, token) -> bool:
    """Renueva la señal de vida del claim. Devuelve True si ESTE worker sigue siendo el dueño
    (mismo fencing que finish_scope_run: token vigente y 'running'); False si fue desahuciado —
    seguir latiendo entonces resucitaría un lease ajeno. token NULL nunca matchea (fail-closed)."""
    updated = (
        await session.execute(
            sa.text(
                "UPDATE source_harvest_runs SET heartbeat_at = clock_timestamp() "
                "WHERE run_id = :rid AND scope_id = :sid "
                "AND claim_token = :tok AND status = 'running' "
                "RETURNING run_id"
            ),
            {"rid": run_id, "sid": scope_id, "tok": token},
        )
    ).one_or_none()
    return updated is not None


@contextlib.asynccontextmanager
async def scope_heartbeat(session_factory, run_id, scope_id, token):
    """Mantiene VIVO el lease del scope mientras dura el bloque (el fetch). Sin esto, un fetch más
    largo que SCOPE_LEASE_S deja que otro run_all re-arme el scope y golpee la fuente externa por
    duplicado.

    El latido va en su PROPIA sesión: la del run está en plena transacción de persistencia. Si el
    latido descubre que este worker ya fue desahuciado, PARA (no resucita un lease ajeno) y avisa;
    el fencing de core0017 se encarga de que sus escrituras no cuenten.

    CONTRAPARTIDA aceptada: mientras el proceso viva, el scope no se re-arma — un worker COLGADO
    (no muerto) lo retiene más de SCOPE_LEASE_S. Es la dirección segura (antes se duplicaba el
    tráfico externo) y está acotada por los timeouts del cliente HTTP; si el proceso muere, el
    latido cesa y el lease vence como siempre."""
    async def beat() -> None:
        while True:
            await asyncio.sleep(SCOPE_HEARTBEAT_S)
            async with session_factory() as session:
                alive = await beat_scope_run(session, run_id, scope_id, token)
                await session.commit()
            if not alive:
                logger.warning(
                    "scope %s: heartbeat detuvo — el claim ya no es de este worker", scope_id
                )
                return

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def finish_scope_run(session, run_id, scope_id, status: str, token) -> bool:
    """Cierra el scope SOLO si `token` es el del claim VIGENTE y sigue 'running' (fencing, rev.
    externa integral). Devuelve True si cerró; False si el scope fue re-armado por otro worker (el
    desahuciado no debe sobrescribirlo). token NULL nunca matchea (fail-closed)."""
    updated = (
        await session.execute(
            sa.text(
                "UPDATE source_harvest_runs "
                "SET status = :st, finished_at = clock_timestamp() "
                "WHERE run_id = :rid AND scope_id = :sid "
                "AND claim_token = :tok AND status = 'running' "
                "RETURNING run_id"
            ),
            {"st": status, "rid": run_id, "sid": scope_id, "tok": token},
        )
    ).one_or_none()
    return updated is not None


async def finish_run(session, run_id) -> str:
    """Cierra el run con el agregado de sus scopes HABILITADOS: 'error' si
    alguno falló o sigue corriendo, 'partial' si algún barrido quedó
    incompleto, 'ok' en el resto.

    Rev. 1ª A-11: un scope DESHABILITADO entre intentos no envenena el run
    para siempre — sus filas sin cerrar pasan a 'skipped' y el agregado solo
    considera scopes actualmente habilitados."""
    await session.execute(
        sa.text(
            "UPDATE source_harvest_runs shr "
            "SET status = 'skipped', finished_at = clock_timestamp() "
            "FROM harvest_scopes hs "
            "WHERE shr.run_id = :rid AND hs.id = shr.scope_id "
            "AND NOT hs.enabled AND shr.finished_at IS NULL"
        ),
        {"rid": run_id},
    )
    rows = (
        await session.execute(
            sa.text(
                "SELECT shr.status, "
                f"(shr.finished_at IS NULL AND {_alive_at('shr')} < "
                " clock_timestamp() - make_interval(secs => :lease)) AS stale "
                "FROM source_harvest_runs shr "
                "JOIN harvest_scopes hs ON hs.id = shr.scope_id AND hs.enabled "
                "WHERE shr.run_id = :rid"
            ),
            {"rid": run_id, "lease": SCOPE_LEASE_S},
        )
    ).all()
    # Rev. 2ª A-11: un 'running' con lease VIGENTE es OTRO worker del mismo
    # run_key trabajando (solapamiento legítimo) — el run NO se cierra (lo
    # cerrará el último worker); solo el 'running' con lease vencido (colgado
    # real) cuenta como error. Misma condición de lease que claim_scope_run.
    if any(r.status == "running" and not r.stale for r in rows):
        return "running"
    statuses = [r.status for r in rows]
    stale_running = any(r.status == "running" and r.stale for r in rows)
    if stale_running or any(s == "error" for s in statuses):
        overall = "error"
    elif any(s == "partial" for s in statuses):
        overall = "partial"
    else:
        overall = "ok"
    await session.execute(
        sa.text(
            "UPDATE harvest_runs "
            "SET status = :st, finished_at = clock_timestamp() WHERE id = :rid"
        ),
        {"st": overall, "rid": run_id},
    )
    return overall

# RUNBOOK — Sombra (B-05, CONTRATOS_FASE_B §6/§8)

> Operación del CDC de la sombra. **RPO = 0** (ack tras commit: el slot re-entrega
> lo no confirmado y la PK del staging absorbe duplicados). **RTO consumidor ≤ 1 h**.
> TODO LOCAL: prod/QNAP fuera de alcance.

## 1. Diagnóstico del slot

```bash
# Estado del slot (activo, WAL retenido) y frontera del consumidor
docker compose exec -T postgres psql -U swissjob -d swissjobhunter -c "
SELECT slot_name, active, active_pid,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS wal_retenido
FROM pg_replication_slots WHERE slot_name = 'jobhunt_shadow';"

docker compose exec -T postgres psql -U swissjob -d swissjobhunter -c "
SELECT slot_name, last_applied_lsn, updated_at, now() - updated_at AS sin_datos,
       heartbeat_at, now() - heartbeat_at AS sin_latido
FROM jobhunt.shadow_capture_state;"

# Salud según los umbrales de §6 (retención > 2 GiB / parado > 30 min ⇒ ALERTA)
docker compose run --rm core-migrate python -c "
import asyncio; from jobhunt_core.database import task_session_factory
from jobhunt_core.shadow.gate import check_slot_health
async def main():
    async with task_session_factory() as f:
        async with f() as s: print(await check_slot_health(s))
asyncio.run(main())"
```

Señales: `active=false` = consumidor caído (el slot **retiene WAL** mientras tanto);
`wal_retenido` creciendo = consumidor caído o atascado; `heartbeat_at` viejo = consumidor
SIN LATIDO (es lo que puntúa el healthcheck de core-capture — umbral 26 h,
`CORE_CAPTURE_HEALTH_MAX_AGE_S`; el latido avanza con cada keepalive ~10 s y cada tx
aplicada); `updated_at` viejo = sin transacciones aplicadas — **progreso de DATOS,
informativo**: con slot activo y días sin tráfico legacy es NORMAL y ya no da unhealthy
(P2-7, core0009).

## 2. Reinicio del consumidor (RTO ≤ 1 h)

El consumidor reanuda **solo** desde `last_applied_lsn` (shadow_capture_state):
no hay pérdida (RPO=0) — la tx frontera puede re-entregarse y la absorbe el
`ON CONFLICT (lsn, seq_in_tx)`.

```bash
docker compose restart core-capture          # o: up -d core-capture
docker compose logs -f core-capture          # esperar "Streaming wal2json v2 arrancado"
docker compose ps core-capture               # healthcheck → healthy
```

Verificación: `last_applied_lsn` avanza y `active=true` (consultas de §1). Si el
arranque falla con *"Estado registrado pero slot AUSENTE"* → continuidad WAL
perdida: ejecutar el rollback/replay completo (§3).

## 3. Rollback/replay completo (ensaya el de Fase C)

Secuencia de §6: **parar consumidor → DROP del slot → desactivar fuentes
`legacy:*` y archivar sus vacantes → truncar staging → re-crear slot +
re-backfill por snapshot**. Está implementada como función ejecutable con
guardas (`confirm=True` obligatorio; jamás escribe en `public`):

```bash
docker compose stop core-capture   # 1) parar el consumidor SIEMPRE primero

docker compose run --rm core-migrate python -c "
import os
from jobhunt_core.shadow.gate import rollback_replay
print(rollback_replay(
    capture_dsn=os.environ.get('CAPTURE_DSN',
        'postgresql://jobhunt_capture:jobhunt_capture_dev@postgres:5432/swissjobhunter'),
    core_dsn=os.environ['CORE_DATABASE_URL'],
    confirm=True,
))"

docker compose start core-capture  # 6) reanuda desde last_applied_lsn (= snapshot)
```

La función registra cada paso en el log y devuelve el resumen (slot dropeado,
encarnaciones cerradas, vacantes archivadas, filas truncadas, `snapshot_lsn` y
filas del re-backfill). Tras re-arrancar, verificar §1 y que el conteo del
re-backfill == conteo legacy activo.

## 4. Drop-slot de EMERGENCIA (§8)

Si el disco de la BD **compartida** peligra por WAL retenido y no hay tiempo de
diagnóstico: **perder la sombra ≪ tumbar producción**.

> ⚠️ `pg_drop_replication_slot` **FALLA** si el slot sigue `active`: terminar el
> walsender y dropear en el mismo batch es una carrera perdida (el backend tarda
> en morir). La secuencia correcta es: terminar → **esperar hasta
> `active_pid IS NULL`** → drop, con reintentos.

**Vía preferente** — la función con guardas (mismo bucle
terminate→espera→drop que usa el rollback, timeout 30 s):

```bash
docker compose stop core-capture   # parar el consumidor SIEMPRE primero

docker compose run --rm core-migrate python -c "
import os
from jobhunt_core.shadow.gate import emergency_drop_slot
print(emergency_drop_slot(
    capture_dsn=os.environ.get('CAPTURE_DSN',
        'postgresql://jobhunt_capture:jobhunt_capture_dev@postgres:5432/swissjobhunter'),
    confirm=True,
))"
```

**Alternativa manual (psql)** — el mismo bucle con la espera explícita, en un
solo `DO` (nunca terminate+drop en un único batch):

```bash
docker compose stop core-capture

docker compose exec -T postgres psql -U swissjob -d swissjobhunter -c "
DO \$\$
DECLARE pid int; i int := 0;
BEGIN
  LOOP
    SELECT active_pid INTO pid FROM pg_replication_slots
      WHERE slot_name = 'jobhunt_shadow';
    IF NOT FOUND THEN RAISE NOTICE 'slot ya ausente'; RETURN; END IF;
    IF pid IS NULL THEN
      PERFORM pg_drop_replication_slot('jobhunt_shadow');
      RAISE NOTICE 'slot dropeado — WAL liberado';
      RETURN;
    END IF;
    PERFORM pg_terminate_backend(pid);
    PERFORM pg_sleep(0.25);   -- esperar a que el walsender muera de verdad
    i := i + 1;
    IF i > 120 THEN
      RAISE EXCEPTION 'slot jobhunt_shadow sigue ACTIVO tras 30s: ¿core-capture parado?';
    END IF;
  END LOOP;
END
\$\$;"
```

El WAL se libera al instante. La sombra queda SIN continuidad: para
reconstruirla, ejecutar el rollback/replay completo (§3) cuando haya margen —
**no** re-arrancar core-capture antes (fallaría con "slot AUSENTE" a propósito).

## 5. Cadencias (beat EMBEBIDO en core-worker)

El `beat_schedule` vive en `celery_app.py` (ajustable por settings
`CORE_SHADOW_*` / `CORE_DELIVERY_DISPATCH_EVERY_S`): `sample_outbox_lag`,
`check_slot_health`, **`jobhunt.shadow.project`** (P1-1: sin cadencia, la
proyección solo al cierre del ciclo acumulaba ~20 h de latencia) y
**`jobhunt.delivery.dispatch_outbox`** cada 5 min; `run_cycle` diario 06:05
Europe/Zurich (tras el cierre del ciclo, §5). SOLO colas `core.*`.

**Entrega sombra real (P1-1b)**: al arrancar, el worker registra el transporte
`shadow/inbox.py` — INSERT síncrono e idempotente en `jobhunt.shadow_inbox`
(core0009; PK consumer_id+event_id, ON CONFLICT DO NOTHING) — SOLO si nadie
inyectó otro transporte. El transporte **real HTTP al inbox del BFF llega en
Fase C** y sustituye a este por la misma costura (`delivery.set_transport`).
Verificación: `SELECT consumer_id, count(*) FROM jobhunt.shadow_inbox GROUP BY 1;`
crece con cada despacho con eventos pendientes; re-entregas no duplican filas.

El beat va **EMBEBIDO** en el command del core-worker (`worker ... -B`,
docker-compose.yml — decisión del propietario 2026-07-25): **ya no se arranca
a mano**. Antes se lanzaba con `exec -d ... beat` y moría en silencio con cada
`up -d` (recreate) — apagando la única alerta de retención WAL de la BD
compartida (§6/§8). Embebido, cada recreate/restart del worker lo rearranca.

Verificación (tras cualquier arranque/restart del worker):

```bash
docker compose ps core-worker                                  # Up
docker compose logs core-worker | grep -m1 "beat: Starting"    # beat vivo
# En ≤ 5 min el beat despacha las cadencias de 5 min:
docker compose logs core-worker | grep -E "Sending due task (shadow-|delivery-)"
# (shadow-sample-outbox-lag, shadow-check-slot-health, shadow-project y
#  delivery-dispatch-outbox; shadow-run-cycle a las 06:05)
```

**Liveness del muestreador**: si con el worker Up no aparecen samples nuevos
durante **> 15 min** (ni "Sending due task shadow-sample-outbox-lag" en el log,
ni entradas nuevas en `details->'samples'` de la fila `outbox_lag_p99` del
ciclo abierto en `jobhunt.shadow_cycle_metrics`), el beat está caído aunque el
worker viva: revisar el log de core-worker y `docker compose restart
core-worker` (worker y beat rearrancan juntos).

Estado del gate en cualquier momento:

```bash
docker compose run --rm core-migrate python -c "
import asyncio; from jobhunt_core.database import task_session_factory
from jobhunt_core.shadow.gate import render_gate_report
async def main():
    async with task_session_factory() as f:
        async with f() as s: print(await render_gate_report(s))
asyncio.run(main())"
```

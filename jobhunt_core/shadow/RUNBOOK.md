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
SELECT slot_name, last_applied_lsn, updated_at, now() - updated_at AS sin_progreso
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
`wal_retenido` creciendo = consumidor caído o atascado; `updated_at` viejo = sin
transacciones aplicadas (ver umbral 26 h del healthcheck de core-capture).

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

```bash
docker compose stop core-capture
docker compose exec -T postgres psql -U swissjob -d swissjobhunter -c "
SELECT pg_terminate_backend(active_pid) FROM pg_replication_slots
WHERE slot_name = 'jobhunt_shadow' AND active_pid IS NOT NULL;
SELECT pg_drop_replication_slot('jobhunt_shadow');"
```

El WAL se libera al instante. La sombra queda SIN continuidad: para
reconstruirla, ejecutar el rollback/replay completo (§3) cuando haya margen —
**no** re-arrancar core-capture antes (fallaría con "slot AUSENTE" a propósito).

## 5. Cadencias (beat en el core-worker LOCAL)

El `beat_schedule` vive en `celery_app.py` (ajustable por settings
`CORE_SHADOW_*`): `sample_outbox_lag` y `check_slot_health` cada 5 min,
`run_cycle` diario 06:05 Europe/Zurich (tras el cierre del ciclo, §5). SOLO
colas `core.*`. El compose no se toca sin OK del propietario: el beat se lanza
DENTRO del core-worker existente —

```bash
docker compose exec -d core-worker celery -A jobhunt_core.celery_app beat \
  -l info -s /tmp/celerybeat-schedule
```

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

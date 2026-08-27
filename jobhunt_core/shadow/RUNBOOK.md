# RUNBOOK — Sombra (B-05, CONTRATOS_FASE_B §6/§8)

> Operación del CDC de la sombra. **RPO = 0** (ack tras commit: el slot re-entrega
> lo no confirmado y la PK del staging absorbe duplicados). **RTO consumidor ≤ 1 h**.
> TODO LOCAL: prod/QNAP fuera de alcance.

## 0. Estado operativo vigente (re-verificado 2026-08-27, tarde)

| Hecho | Estado | Cómo se comprobó |
|---|---|---|
| Release que corre el core | **`ae7fbf2`** en los tres procesos (`core-api`, `core-worker`, `core-capture`) | `printenv RELEASE_SHA` en cada contenedor, y el campo `release` de `/v1/health` y `/v1/ready` |
| `core-capture` **RECREADO hoy** | Contenedor **creado** 2026-08-27T11:35:58Z y **arrancado** 11:36:03Z. `RestartCount=0` no lo desmiente — solo cuenta reinicios de la *restart policy*, no una recreación | `docker inspect -f '{{.Created}} {{.State.StartedAt}}'`, **no** `docker compose ps` (ésa da la CREACIÓN, y ahora coinciden porque el contenedor es nuevo) |
| La avería del lazo de cola | **CERRADA** | slot con **424 bytes** retenidos (eran 2,6 GB creciendo a 2,1–3,0 MB/s), `confirmed_flush` a 368 B, `active=t` |
| Ritmo del latido | **~0/s** (techo 10/s; la avería marcaba 559–979/s) | dos muestras de `n_tup_upd` separadas en el tiempo: sin variación |
| Staging drenado | **0** filas sin aplicar sobre 16 173 | `SELECT count(*) FROM jobhunt.shadow_change_log WHERE applied_at IS NULL` |
| Código del proceso vivo (streamer **y** healthcheck) | **al día** | **Ya no hace falta razonarlo:** desde `ae7fbf2` el compose base **no monta** `./jobhunt_core` (`docker inspect` da **0 mounts** en `core-capture`), así que el contenedor corre lo que lleva la imagen y el `RELEASE_SHA` que publica **identifica el código**. Cambiar el código exige reconstruir y recrear |

> ⚠ **Corrección de la redacción anterior de esta tabla.** Decía que el healthcheck estaba
> al día «porque `jobhunt_core/` va bind-montado en local». **Esa premisa dejó de ser cierta
> el 2026-08-27**: el perfil operativo ya no monta el código. La conclusión sigue siendo la
> misma, pero por otro motivo —el contenedor se recreó con la imagen nueva—, y el motivo
> importa: era exactamente el razonamiento que hacía indistinguibles «el proceso corre el
> código nuevo» y «los ficheros del disco son nuevos». Ver §7 de este runbook y
> `docs/COTAS_Y_DECISIONES.md` §9.1.
>
> Con el **override de desarrollo** (`-f docker-compose.yml -f docker-compose.dev.yml`) el
> bind mount vuelve, y entonces el `release` que publica el proceso **deja de identificar el
> código**: por eso `/v1/ready` responde `authoritative: false` en ese perfil.

**✅ REGLA LEVANTADA (2026-08-27):** ya se puede reiniciar `worker`, `worker-ai` y `backend`.
La prohibición existía **solo** para proteger la maniobra de canonización (§7), que los paró
ella misma en su paso 1, se ejecutó y los rearrancó al terminar.

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
SIN LATIDO (umbral 26 h, `CORE_CAPTURE_HEALTH_MAX_AGE_S`; el latido avanza con cada
keepalive ~10 s y cada tx aplicada); `updated_at` viejo = sin transacciones aplicadas —
**progreso de DATOS, informativo**: con slot activo y días sin tráfico legacy es NORMAL y
ya no da unhealthy (P2-7, core0009).

### Qué puntúa HOY el healthcheck de `core-capture` (`--health`)

**Ocho** comprobaciones en cascada (la primera que falla decide y devuelve 1). Las nº 5 y 6
se añadieron el 2026-08-27 (corolario de O-1) porque **un latido fresco no es prueba de
salud**: en la avería de agosto lo fresco del latido ERA la avería, y el healthcheck estuvo
VERDE cinco días seguidos.

| # | Señal | Umbral (env) | Qué detecta |
|---|---|---|---|
| 1 | Conexión al core | `connect_timeout=5` | core caído |
| 2 | Slot presente | — | slot borrado (continuidad WAL perdida ⇒ §3) |
| 3 | Slot `active` (walsender conectado) | el `start_period` de 300 s del compose cubre el backfill del bootstrap | consumidor caído mientras el slot retiene WAL |
| 4 | WAL retenido sobre `restart_lsn` | **2 GiB** · `CORE_CAPTURE_SLOT_LAG_MAX_BYTES` (I-1) | fallo BRUTO. **Es lento para un fallo lento** |
| 5 | **RITMO** del latido: `n_tup_upd`/s de `shadow_capture_state` | **10/s** · `CORE_CAPTURE_HEARTBEAT_MAX_RATE`; ventana mín. **30 s** · `CORE_CAPTURE_HEARTBEAT_MIN_WINDOW_S` | el lazo de cola del heartbeat. **Nombra la causa** |
| 6 | **PROGRESO**: distancia a `confirmed_flush_lsn` | suelo **512 MiB** · `CORE_CAPTURE_FLUSH_STALL_MIN_BYTES`; ventana **1800 s** · `CORE_CAPTURE_FLUSH_STALL_MAX_S` | consumidor que late pero no gana terreno |
| 7 | Frontera registrada + edad del latido | **26 h** · `CORE_CAPTURE_HEALTH_MAX_AGE_S` | bootstrap incompleto · consumidor sin latido |
| 8 | Staging DRENADO (`shadow_change_log` sin aplicar) | **2 h** · `CORE_CAPTURE_STAGING_STALE_MAX_S` | proyector/worker colgado — vive AQUÍ, en OTRO contenedor, fuera de ese dominio de fallo (B-1: 22 días parado sin una alerta) |

Separación medida entre sano y averiado (mismo instrumento): latido **0,23/s sano** frente
a **559–979/s averiado**; retención **0,7–14 MB oscilando** sano frente a **2,6 GB creciendo
a 2,1–3,0 MB/s** averiado. El techo de 10/s queda 43× por encima de lo sano y 56× por debajo
de lo averiado: no hay zona gris.

La señal 5 **RE-ANCLA con cualquier mejora** — una ráfaga legítima del legacy que luego se
drena no puntúa; solo puntúa quedarse por encima del suelo SIN mejorar durante toda la
ventana. Con el déficit medido eso delata el lazo en **~35 min**, no en cinco días.

El muestreo (necesario porque 4 y 5 son *tasas*, y una tasa necesita memoria) se persiste en
`/tmp/jobhunt_capture_health.json` dentro del contenedor · `CORE_CAPTURE_HEALTH_STATE` —
**nunca en la BD**: escribir de más es justo el pecado que se vigila. El fichero es memoria,
no dependencia: ausente o corrupto = «primera vez», jamás unhealthy.

> **Trampa que costó cinco días, y no la arregla ningún healthcheck:** Python lee cada módulo
> UNA vez, al importarlo. `core-capture` arrancó el 2026-08-22T12:03:20Z y el commit que
> introduce el throttle del heartbeat (`accc10e`) es de 2 h 14 min DESPUÉS, así que el proceso
> vivo ejecutó durante cinco días una versión anterior a su propio arreglo — **con el fichero
> ya corregido dentro del contenedor**. `grep` al fichero NO responde qué código corre; solo
> lo responde la fecha de arranque del contenedor contra la del commit:
> `docker inspect -f '{{.State.StartedAt}}' swissjob-core-capture` frente a `git log -1 --format=%cI -- jobhunt_core/shadow/capture.py`.
>
> **Y no vale `docker compose ps`**: su columna de antigüedad es la de **CREACIÓN**, no la del
> proceso. El 2026-08-27 por la mañana decía «4 weeks ago» para un `core-capture` cuyo proceso
> llevaba **9 horas** — `Created=2026-07-28`, `StartedAt=2026-08-26T23:06:59Z`. (Ese contenedor
> ya no existe: la recreación de la tarde dejó `Created` y `StartedAt` a la misma hora, y por
> eso hoy la columna **sí** cuadra. La trampa no ha desaparecido, solo está dormida.) Tampoco
> vale `RestartCount`: solo cuenta los reinicios de la *restart policy*, así que un
> `stop`+`start` manual lo deja en **0**. Es la misma trampa una capa más abajo, y es la que
> hace que un proceso con código viejo parezca recién levantado.
>
> **La señal directa, desde `ae7fbf2`:** `docker compose exec -T core-capture printenv
> RELEASE_SHA` (y el campo `release` de `/v1/health` en `core-api`). Compara ese SHA con
> `git rev-parse --short HEAD`: si difieren, el proceso corre otra release, sin razonar sobre
> fechas. Es la respuesta que las dos averías de este runbook necesitaban y no tenían.
> ⚠ **Desde `ae7fbf2` (2026-08-27) esto cambió, y en la dirección segura.** Ya **no** basta
> `docker compose restart core-capture` en local: el perfil operativo **no monta**
> `./jobhunt_core`, así que local y NAS se comportan igual — el código va **horneado en la
> imagen** y tocar `shadow/capture.py` exige **reconstruir la imagen y recrear el contenedor**.
> Un `restart` a secas volvería a levantar el MISMO código.
> Con el override de desarrollo (`-f docker-compose.yml -f docker-compose.dev.yml`) el bind
> mount vuelve y `restart` sí basta para reimportar — pero entonces el `release` que publica el
> proceso deja de identificar el código y `/v1/ready` lo declara `authoritative: false`.
> En todos los casos, tras tocar `shadow/capture.py` el proceso hay que **reiniciarlo o
> recrearlo explícitamente** — nada lo hace solo.

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

El `beat_schedule` vive en `celery_app.py`. Son **9 cadencias** (verificado
enumerando `celery_app.conf.beat_schedule` en el contenedor vivo). SOLO colas `core.*`.

| Cadencia | Tarea | Cuándo | Setting |
|---|---|---|---|
| 5 min | `jobhunt.shadow.sample_outbox_lag` | — | `CORE_SHADOW_OUTBOX_SAMPLE_EVERY_S=300` |
| 5 min | `jobhunt.shadow.check_slot_health` | — | `CORE_SHADOW_SLOT_HEALTH_EVERY_S=300` |
| 5 min | `jobhunt.shadow.project` | P1-1: sin cadencia, la proyección solo al cierre del ciclo acumulaba ~20 h de latencia | `CORE_SHADOW_PROJECT_EVERY_S=300` |
| 5 min | `jobhunt.delivery.dispatch_outbox` | — | `CORE_DELIVERY_DISPATCH_EVERY_S=300` |
| **1 h** | `jobhunt.idempotency.purge_expired` | **NO son 5 min** — el valor es 3600 s | `CORE_IDEMPOTENCY_PURGE_EVERY_S=3600` |
| diaria | `jobhunt.maintenance.dedup_scan` | **05:20** — antes del barrido (un candidato sobre vacante recién archivada no estorba) | crontab fijo |
| diaria | `jobhunt.maintenance.archive_sweep` | **05:35** — ANTES del cierre de ciclo, para que las métricas del gate midan el corpus podado (F-2/ADR-07) | crontab fijo |
| diaria | `jobhunt.shadow.run_cycle` | **06:05** Europe/Zurich | `CORE_SHADOW_RUN_CYCLE_HOUR/_MINUTE` |
| diaria | `jobhunt.maintenance.purge_retention` | **06:40** — DESPUÉS del cierre de ciclo: el ciclo cuenta evaluaciones y dead-letters de la ventana que acaba de cerrar, y purgar antes le cambiaría los números por debajo de los pies (O-4) | crontab fijo |

El orden diario **05:20 → 05:35 → 06:05 → 06:40 no es casual**: cada cita está colocada
respecto al cierre de ciclo de las 06:05 por una razón distinta, anotada arriba. Moverlas
cambia lo que mide el gate.

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

## 6. Qué acreditan (y qué no) los 7 ciclos — nota de lectura (G1 H-14f)

`dedup_precision`/`dedup_recall` y el término de huecos de `perdida` son
**fotos del estado actual** al cómputo (06:05), no mediciones acotadas a la
ventana del ciclo: sobre un corpus **estable**, 7 ciclos verdes consecutivos
acreditan 7 repeticiones de la MISMA medición, no 7 jornadas independientes.
La racha demuestra estabilidad del veredicto en el tiempo; la independencia
entre jornadas la aporta el flujo real de datos (cosecha diaria + CDC), no la
métrica. Las métricas con ventana real del ciclo son `outbox_dead` (por
`dead_at`, core0030), `latencia_p95`, `coste` y `reenlace_pct`. Los umbrales
aplicados a cada ciclo quedan persistidos en su fila `gate_umbrales` (G1 H-8):
cambiar una constante NO recolorea ciclos ya sellados.

## 7. Canonización de identidad legacy: las DOS mitades (G8/P2-6)

> ## ✅ EJECUTADA el 2026-08-27 sobre `swissjobhunter` (local) — commit `2462717`
>
> Esta sección deja de ser un plan y pasa a ser el **acta** de una maniobra hecha, más
> el procedimiento por si hay que repetirla (el **NAS** todavía la necesita: ver §7.3).
> Autorizada por el propietario. Ensayo en seco y ejecución en firme dieron cifras
> **idénticas**. Copia previa: `/home/lothar/Documents/swissjob_pre_canonizacion_20260827.sql.gz`
> (117 MB, con el esquema `jobhunt`).
>
> | Script | Reescritas | Clones fusionados | `match_results` descartados | Slots reapuntados | Slots de clones |
> |---|---|---|---|---|---|
> | g3 (arbeitnow + jobgether) | 5.419 | 406 | 30, **0 con señal del usuario** | 5.263 | 371 |
> | g6 (irishjobs) | 879 | 40 | 0 | 879 | 40 |
>
> La otra mitad, `shadow/canonical_refs.py`, corrió en la **misma parada**: 6.298 filas
> canonizadas en el mapa, **10 juicios** y **162 pares** re-mapeados.
>
> **Los ficheros del repo siguen terminando en `ROLLBACK`**: son seguros por defecto y
> ejecutarlos tal cual es un ensayo, no una maniobra.

Los scripts `backend/scripts/g3_canonizacion_identidad_arbeitnow_jobgether.sql`
y `g6_canonizacion_identidad_irishjobs.sql` reescriben `jobs.hash`. Eso toca
la sombra por **dos** caminos distintos, y solo uno vive en `backend/`:

| Qué | Quién lo arregla | Si falta |
|---|---|---|
| El slot CDC de `jobhunt.source_listings` queda huérfano | **PASO 7c** del script | 6.553 slots huérfanos; la fila legacy se vuelve invisible para la sombra |
| Los `job_ref` de las ETIQUETAS se quedan con el hash viejo | **`shadow/canonical_refs.py`** | 10 de 91 juicios y 1 de 260 pares dejan de resolver, SIN error |

Las dos mitades se aplicaron, así que ninguna de esas dos columnas «si falta» ocurrió.
Lo comprobado después está en §7.2.

Las etiquetas (`labeled_judgments.job_ref`, `labeled_dedup_pairs.job_ref_a/b`)
viven en el espacio de nombres del `hash` legacy, no tienen FK y ningún PASO
las toca; `map_job_refs_to_vacancies` deja fuera del dict los refs sin slot
**sin error**. Medido contra producción el 2026-08-26 (SOLO SELECT): de los 91
juicios de los 3 sets congelados se pierden 10 — **8 del MISMO set**, y **6
con `relevance > 0`** de sus 20 relevantes.

### 7.1 Orden operativo (los dos scripts + el core)

Es el que se siguió el 2026-08-27, paso a paso. Sirve tal cual para repetirlo en el NAS.

```bash
# 1. Parar los workers (paso 2 del ORDEN de los scripts)
docker compose stop core-worker worker worker-ai

# 2. pg_dump incluyendo el esquema jobhunt, y ensayo sobre la COPIA

# 3. Los dos scripts, con COMMIT, en cualquier orden
#    (los del repo terminan en ROLLBACK: hay que cambiar esa línea en una copia)

# 4. LA OTRA MITAD, con los workers TODAVÍA parados.
#    Primero se mide, y solo después se aplica:
docker compose run --rm core-migrate \
    python -m jobhunt_core.shadow.canonical_refs --dry-run
docker compose run --rm core-migrate \
    python -m jobhunt_core.shadow.canonical_refs

# 5. Arrancar
docker compose start core-worker worker worker-ai
```

> **OJO al perfil de compose (desde `ae7fbf2`).** El compose base ya **no monta**
> `./jobhunt_core`: `core-migrate` corre el módulo **de la imagen**. Al operar eso es lo
> correcto —la maniobra debe usar código publicado, no un árbol de trabajo editado—, pero
> exige que la imagen esté reconstruida con el `canonical_refs.py` que quieres ejecutar.
> Para probarlo contra el árbol de trabajo hay que pedir el override explícito:
> `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate …`

### 7.2 Verificación posterior — lo que se midió, y lo que se volvió a medir

Con el proyector ya drenado. Re-medido el 2026-08-27 por la tarde (SOLO `SELECT`):

| Comprobación | Esperado | Medido | |
|---|---|---|---|
| Slots huérfanos en las 3 fuentes canonizadas | 924 + 371 + 40 = **1.335** | **1.335** (arbeitnow 1.274 · jobgether 21 · irishjobs 40) | ✅ |
| El número que delataría el fallo que el PASO 7c evita | **7.477** | no alcanzado | ✅ |
| Juicios que siguen resolviendo | 91 de 91 | **91 de 91** — cero etiquetas perdidas | ✅ |
| Pares con sus DOS refs resueltos | ≥ 260 | **261** de 779 (`seed_duplicate_of`); 0 con `job_ref_a = job_ref_b` | ✅ |
| `shadow_change_log` sin aplicar | 0 | **0** de 16.173 | ✅ |
| Slot `jobhunt_shadow` | activo, retención baja | activo, pocos KB retenidos | ✅ |

La consulta de huérfanos es la literal del PASO 7 del script (las tres fuentes
`legacy:arbeitnow`, `legacy:jobgether`, `legacy:irishjobs`, `LEFT JOIN jobs ON j.hash =
sl.external_id WHERE j.hash IS NULL`). La de etiquetas reproduce
`map_job_refs_to_vacancies`: `source_listings` de fuentes `legacy:%` con alguna
encarnación.

**El GATE-SOMBRA NO se invalidó**: no hubo que soltar ni recrear el slot, ni re-sembrar
el snapshot. Los `op=U` llegaron con la pk canónica y encontraron su slot ya reapuntado;
los `op=D` de los clones cerraron sus encarnaciones por el camino normal.

### 7.3 Lo que queda: el NAS

El NAS corre **imágenes anteriores** a esta maniobra y a los fixes de identidad. Cuando se
suban las imágenes nuevas hay que aplicar allí **la misma canonización, en el MISMO
despliegue**: el código nuevo emite ya la identidad canónica, y una cosecha con código
nuevo sobre datos sin canonizar es pérdida silenciosa **y** duplicación del corpus a la
vez. El razonamiento largo está en el encabezado de
`backend/scripts/g3_canonizacion_identidad_arbeitnow_jobgether.sql`.

**Por qué DESPUÉS y no antes**: el mapa `old→new` se reconstruye de la propia
`jobs` sin duplicar la lógica de canonización de URL — los scripts no tocan
`jobs.url`, así que el hash viejo es `md5(title|company|url)` y el nuevo es el
`jobs.hash` que quedó. Medido ANTES de la maniobra: de 10.805 filas, **0** no
reproducen su hash, luego tras la maniobra el conjunto «no reproduce» es
EXACTAMENTE el de las canonizadas. Y si la maniobra legacy aborta —va entera
en una transacción— este paso simplemente no se ejecuta: no hay nada que
deshacer. **Con los workers parados ningún ciclo de métricas observa el estado
intermedio**, que es lo que hace innecesario que las dos mitades compartan
transacción.

**Ventana que hay que respetar**: el re-mapeo aborta si una cohorte dedup
SELLADA tiene pares que tocar (core0025 los hace inmutables). Hoy no hay
ninguna cohorte registrada, así que la ventana está abierta. Si se sella el
holdout antes de la maniobra y sus pares llevan refs canonizables, la única
salida es cargar una cohorte NUEVA con los refs canónicos y retirar la vieja
del gate — el sello existe justo para que el acta no se reescriba.

**El estrato positivo va DESPUÉS** (G8-N-7): cargar sus 187 pares antes de la
maniobra graba refs que la maniobra invalida, y `--excluir` no sirve para eso
porque el daño no lo detecta ninguna guarda del loader.

No hace falta migración nueva: el re-mapeo es una maniobra de DATOS, sin DDL.

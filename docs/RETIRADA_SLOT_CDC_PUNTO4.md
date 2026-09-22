# Retirada del slot de captura CDC — procedimiento

Última acción del punto 4 y **la única sin vuelta atrás barata**: una vez
borrado el slot, reanudar el productor legacy exigiría un snapshot CDC nuevo.
Por eso va la última, con 48 h de observación por delante y en este orden.

## Cuándo

No antes de **23-09-2026 ~22:40 UTC** — 48 h desde el primer corte (21-09
22:40). Durante esa ventana no debe haber incidencias: cuatro rondas del
dispatcher nativo (00:10, 06:10, 12:10, 18:10 Europe/Zurich) con los 17 scopes
completando y `harvest.check_health` devolviendo `alertas: []`.

## Precondición, comprobada el mismo día

```sh
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
PGC="$D exec -i swissjob-postgres psql -U jobhunt_core -d swissjobhunter_r5_rehearsal -A"

# 1. los 17 scopes completos en las ultimas 8 h, cero fallos
ssh nas "$PGC -c \"SET search_path=jobhunt; SELECT count(*) AS scopes,
  count(*) FILTER (WHERE st.last_complete_at > now()-interval '8 hours') AS recientes,
  count(*) FILTER (WHERE st.consecutive_failures = 0) AS sin_fallos
  FROM harvest_scopes hs JOIN source_scope_state st ON st.scope_id=hs.id WHERE hs.enabled;\""
#    esperado: 17 | 17 | 17

# 2. salud de cosecha sin alertas en las ultimas 24 h
ssh nas "$D logs swissjob-core-worker-r5 --since 24h | grep check_health | grep -c \"'alertas': \[\]\""
#    esperado: >= 20 lineas, ninguna con alertas no vacias

# 3. CDC drenado y sin nada nuevo que capturar
ssh nas "$PGC -c \"SET search_path=jobhunt; SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL;\""
#    esperado: 0
ssh nas "$D ps --format '{{.Names}}' | grep -c swissjob-worker-r5"
#    esperado: 0 (el escritor de esa base sigue parado)
```

Si cualquiera de las tres falla: **parar**, anotar y no continuar.

## Secuencia

**1. Parar la captura.**

```sh
C=/share/Public/swissjob/bin-docker-compose
E=/share/CACHEDEV1_DATA/Public/unification-e15-20260914
ssh nas "$C -p swissjob-r5 -f $E/core.configured.yml stop -t 300 core-capture"
ssh nas "$D inspect swissjob-core-capture-r5 --format 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}}'"
```

**2. Quitar del beat el harness del slot.** El flag está implementado y probado
(`CORE_CAPTURE_ENABLED`): en `False` el beat deja de programar
`shadow-check-slot-health`, `shadow-preview-cycle` y `shadow-run-cycle`, **y sólo
esas tres**.

> ⚠ **El worker que corre HOY no contiene ese flag.** `swissjob-core-worker-r5`
> sigue en `swissjob-core:point4-51be757`, anterior al commit que lo añade; sólo
> `core-api` se actualizó en el punto 5. Antes de apoyarse en el interruptor hay
> que **desplegar el worker** y comprobarlo en el proceso que lo ejecuta, no en
> HEAD:
>
> ```sh
> ssh nas "$D exec swissjob-core-worker-r5 python -c \
>   'from jobhunt_core.config import settings; print(settings.CORE_CAPTURE_ENABLED)'"
> ```
>
> Si eso falla con `AttributeError`, el proceso no tiene el código y poner la
> variable en el compose no haría nada.

> **`shadow-project` se queda.** Pese al nombre, ES el postprocesado del ciclo
> nativo: drena embeddings y reevalúa perfiles tras cada cosecha. Apagar la
> familia `shadow.*` por prefijo apagaría el motor de matching con todos los
> indicadores en verde. Lo fija `test_capture_retirement.py`.

En `core.configured.yml`, servicios `core-api` y `core-worker`, añadir
`CORE_CAPTURE_ENABLED: 'false'`; recrear **uno a uno** (copia `.before` antes).
Comprobar después:

```sh
ssh nas "$D logs swissjob-core-worker-r5 --since 10m | grep -c check_slot_health"   # 0
ssh nas "$D logs swissjob-core-worker-r5 --since 10m | grep -c shadow.project"      # >= 1
```

**3. Observar una ronda más** (la siguiente ventana del dispatcher) con la
captura ya parada: los 17 scopes deben seguir completando y el proyector
seguir evaluando. Sólo entonces el paso 4.

**4. Borrar el slot.** Irreversible.

```sh
ssh nas "$PGC -c \"SELECT slot_name, active,
  pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS wal_retenido
  FROM pg_replication_slots;\""
#    'active' debe ser 'f' (la captura esta parada). Si es 't', PARAR.

ssh nas "$PGC -c \"SELECT pg_drop_replication_slot('jobhunt_shadow_r5_rehearsal');\""
```

**5. Limpiar la definición.** Quitar el servicio `core-capture` de
`core.configured.yml` y `CORE_CAPTURE_SLOT` de los otros dos; recrear `core-api`
y `core-worker`. Verificar `/v1/ready` con el mismo `release` y
`authoritative: true`.

## Qué NO se borra

- La base `swissjobhunter_r5_rehearsal`: contiene el esquema `jobhunt`, que
  **es** el core. Sólo se retira el slot de replicación.
- Las tablas `public.*` de esa base ni la base `swissjobhunter`: quedan en
  sólo lectura durante el plazo de retención ratificado.
- `swissjob-erasure-cdc`: es borrado de perfiles, otra capacidad, con una
  solicitud registrada.
- `swissjob-backend` y `swissjob-worker`: sirven el BFF, CV, documentos, la
  alerta de profesor de primaria y los 8 scrapers escolares.

## Después

Actualizar el acta `docs/audits/POINT4_CUTOVER_2026-09-22.md` §8 con la fecha
real del borrado y el `wal_retenido` que tenía el slot al retirarlo.

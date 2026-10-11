# Retirada del slot de captura CDC — procedimiento

Última acción del punto 4 y **la única sin vuelta atrás barata**: una vez
borrado el slot, reanudar el productor legacy exigiría un snapshot CDC nuevo.
Por eso va la última, con 48 h de observación por delante y en este orden.

## Cuándo

No antes de **12-10-2026 20:00 UTC**. La primera muestra conservada de la
ventana actual es del 10-10 a las 19:59:56 UTC; redondear el final hacia arriba
garantiza 48 horas completas. Durante la ventana deben completar las rondas del
dispatcher nativo (00:10, 06:10, 12:10 y 18:10 Europe/Zurich) con los 17 scopes
sanos y `harvest.check_health` devolviendo `alertas: []`.

Las muestras se guardan en
`docs/audits/2026-10-10-optimization/core-observation-48h.jsonl`. Un reinicio de
`core-api`, `core-worker`, `core-harvest-worker` o `core-ops-worker` invalida la
continuidad y obliga a abrir una ventana nueva. **Vuelve a comprobar toda la
precondición el
día de la retirada**; el histórico no sustituye el estado vivo.

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

# 2. oraculo oficial de salud, ejecutado contra el estado vivo
ssh nas "$D exec -e PYTHONPATH=/app swissjob-core-api-r5 python -c \
  'import asyncio,json; from jobhunt_core.tasks.harvest import _check_harvest_health_impl as check; print(json.dumps(asyncio.run(check()),sort_keys=True))'"
#    esperado: alertas=[], scopes=17 y censo.habilitados=17

# 3. CDC drenado; el slot sigue activo durante la ventana
ssh nas "$PGC -c \"SET search_path=jobhunt; SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL;\""
#    esperado: 0
ssh nas "$PGC -c \"SELECT slot_name,active FROM pg_replication_slots WHERE slot_name='jobhunt_shadow_r5_rehearsal';\""
#    esperado: jobhunt_shadow_r5_rehearsal | t
```

`swissjob-worker` debe seguir vivo: conserva CV, documentos, alertas y la
cosecha escolar. Su ausencia ya no demuestra que el catálogo legacy esté
quieto y no es una precondición válida.

Si cualquiera de las tres comprobaciones falla, o el JSONL contiene
`probe_failed`: **parar**, anotar y no continuar.

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

> ✅ **Precondición RESUELTA el 2026-10-10.** El interruptor existe en el
> release vivo `5a1241d`. El beat ya no está en el worker del bi-encoder: la
> topología dividida lo ejecuta únicamente en `core-ops-worker`, comprobado por
> su comando `worker ... -Q core.default,core.notifications --concurrency=1 -B`.
> Verificarlo otra vez antes de usarlo:
>
> ```sh
> ssh nas "$D exec swissjob-core-ops-worker-r5 python -c \
>   'from jobhunt_core.config import settings; print(settings.CORE_CAPTURE_ENABLED)'"
> # 2026-10-10 → True  (valor por defecto: la captura SIGUE programada)
> ```
>
> Si responde `AttributeError`, el proceso no contiene el interruptor y poner la
> variable en Compose sería inútil. No continuar.

> **`shadow-project` se queda.** Pese al nombre, ES el postprocesado del ciclo
> nativo: drena embeddings y reevalúa perfiles tras cada cosecha. Apagar la
> familia `shadow.*` por prefijo apagaría el motor de matching con todos los
> indicadores en verde. Lo fija `test_capture_retirement.py`.

La transformación está versionada en
`scripts/retire_core_capture.py`: falla ante cualquier deriva, conserva el
texto y no lee ni imprime valores. El Compose vivo contiene secretos y **no sale
del NAS**. Copiar sólo la herramienta y comparar sus huellas:

```sh
R=/share/Public/swissjob/retire_core_capture.py
sha256sum scripts/retire_core_capture.py
scp scripts/retire_core_capture.py nas:$R
ssh nas "sha256sum $R"
# las dos huellas deben coincidir
```

Crear respaldo y candidato en el NAS, ejecutar la herramienta con la propia
imagen Core sin red, validar el candidato y sólo entonces promoverlo. La fase
`disable` añade `CORE_CAPTURE_ENABLED: 'false'` exclusivamente a
`core-ops-worker` y exige que sigan presentes los cinco servicios y las cinco
referencias al slot:

```sh
IMAGE=swissjob-core:point5-5a1241d
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ssh nas "cp $E/core.configured.yml $E/core.configured.yml.before-capture-$STAMP"
ssh nas "cp $E/core.configured.yml $E/core.configured.yml.next-disable-$STAMP"
ssh nas "$D run --rm --network none --user 0:0 --entrypoint python \
  -v $E:/work -v $R:/tool.py:ro $IMAGE /tool.py \
  /work/core.configured.yml.next-disable-$STAMP disable"
ssh nas "$C -p swissjob-r5 -f $E/core.configured.yml.next-disable-$STAMP config --quiet"
ssh nas "mv $E/core.configured.yml.next-disable-$STAMP $E/core.configured.yml"
ssh nas "cd $E && $C -p swissjob-r5 -f core.configured.yml up -d --no-deps core-ops-worker"
```

Esperar seis minutos y comprobar después:

```sh
ssh nas "$D logs swissjob-core-ops-worker-r5 --since 6m | grep -c 'Sending due task shadow-check-slot-health'"  # 0
ssh nas "$D logs swissjob-core-ops-worker-r5 --since 6m | grep -c 'Sending due task shadow-project'"            # >= 1
```

**3. Observar una ronda más** (la siguiente ventana del dispatcher) con la
captura ya parada: los 17 scopes deben seguir completando y el proyector
seguir evaluando. Sólo entonces el paso 4.

Con el corte previsto después del 12-10 a las 20:00 UTC, el primer disparo
posterior es el de las **00:10 Europe/Zurich del 13-10**, equivalente a las
**22:10 UTC del 12-10**. El disparo no cierra la condición: esperar hasta que
los 17 cursores tengan `last_complete_at` posterior a esa ventana,
`core.harvest=0` y la sonda oficial siga con `alertas: []`.

La frontera se compara con el instante fijo del disparo, no con una ventana
móvil de ocho horas. La segunda consulta enumera cualquier scope que impida el
cierre; debe devolver cero filas:

```sh
POST_STOP_CUTOFF=2026-10-12T22:10:00+00:00
ssh nas "$PGC -c \"SET search_path=jobhunt; SELECT count(*) AS scopes,
  count(*) FILTER (WHERE st.last_complete_at > timestamptz '$POST_STOP_CUTOFF') AS posteriores,
  count(*) FILTER (WHERE st.consecutive_failures = 0) AS sin_fallos,
  min(st.last_complete_at) AS primera_terminacion
  FROM harvest_scopes hs JOIN source_scope_state st ON st.scope_id=hs.id
  WHERE hs.enabled;\""
# esperado: 17 | 17 | 17 | una fecha posterior al cutoff

ssh nas "$PGC -c \"SET search_path=jobhunt; SELECT s.name,hs.id,
  st.last_complete_at,st.consecutive_failures
  FROM harvest_scopes hs JOIN sources s ON s.id=hs.source_id
  LEFT JOIN source_scope_state st ON st.scope_id=hs.id
  WHERE hs.enabled AND (st.last_complete_at IS NULL
    OR st.last_complete_at <= timestamptz '$POST_STOP_CUTOFF'
    OR st.consecutive_failures <> 0)
  ORDER BY s.name,hs.id;\""
# esperado: 0 filas

ssh nas "$D exec swissjob-core-api-r5 python -c \
  'from redis import Redis; from jobhunt_core.config import settings; r=Redis.from_url(settings.CORE_BROKER_URL); print(r.llen("core.harvest")); r.close()'"
# esperado: 0

ssh nas "$D exec -e PYTHONPATH=/app swissjob-core-api-r5 python -c \
  'import asyncio,json; from jobhunt_core.tasks.harvest import _check_harvest_health_impl as check; print(json.dumps(asyncio.run(check()),sort_keys=True))'"
# esperado: alertas=[], scopes=17 y censo.habilitados=17
```

Si cualquiera de los cuatro valores difiere, esperar y repetir; no avanzar al
borrado del slot.

**4. Borrar el slot.** Irreversible.

```sh
ssh nas "$PGC -c \"SELECT slot_name, active,
  pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS wal_retenido
  FROM pg_replication_slots;\""
#    'active' debe ser 'f' (la captura esta parada). Si es 't', PARAR.

ssh nas "$PGC -c \"SELECT pg_drop_replication_slot('jobhunt_shadow_r5_rehearsal');\""
```

**5. Limpiar la definición.** Crear otro candidato desde el Compose vivo ya
desactivado. La fase `finalize` exige el flag falso sólo en operaciones,
elimina el servicio `core-capture` y las cinco claves `CORE_CAPTURE_SLOT`, y
conserva redes, volúmenes y los cuatro servicios Core:

```sh
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ssh nas "cp $E/core.configured.yml $E/core.configured.yml.next-finalize-$STAMP"
ssh nas "$D run --rm --network none --user 0:0 --entrypoint python \
  -v $E:/work -v $R:/tool.py:ro $IMAGE /tool.py \
  /work/core.configured.yml.next-finalize-$STAMP finalize"
ssh nas "$C -p swissjob-r5 -f $E/core.configured.yml.next-finalize-$STAMP config --quiet"
```

Antes de promoverlo, quitar captura de los inventarios operativos y verificar
el supervisor con su ciclo aislado. En `scripts/nas/supervisor_contenedores.sh`
se elimina sólo `swissjob-core-capture-r5` de `ESPERADOS`, conservando los dos
workers separados; después se ejecuta `sh -n`, se copia la fuente versionada al
mismo `host_path` registrado en `supervisor-production.json`, se comparan ambas
huellas SHA-256 y se repite el ciclo aislado antes de que el cron la use.

El comprobador de release ya admite ambas fases de forma explícita. Sin opción
sigue exigiendo los cinco servicios, para no ocultar una desaparición antes del
corte; `--capture-retired` exige exactamente los cuatro procesos nativos y
falla si el Compose aún declara captura. Se ejecuta desde este checkout por SSH:
el host QNAP no tiene `python3` y su Docker no incorpora el plugin Compose; el
binario correcto es el Compose independiente `$C`. La sonda ready entra en
`swissjob-core-api-r5` y consulta su loopback `:8000`, porque el API no publica
puerto en el host. Esta ruta se comprobó en vivo antes del corte y devolvió los
cinco servicios coherentes de `5a1241d`/`core0053`.

Después promover el candidato, eliminar sólo el contenedor detenido —la imagen
se conserva— y recrear los cuatro servicios uno a uno. Los nombres son
explícitos para no depender de que el Compose final ya no conozca
`core-capture`:

```sh
ssh nas "mv $E/core.configured.yml.next-finalize-$STAMP $E/core.configured.yml"
ssh nas "$D rm swissjob-core-capture-r5"
for service in core-api core-worker core-harvest-worker core-ops-worker; do
  ssh nas "$C -p swissjob-r5 -f $E/core.configured.yml up -d --no-deps $service"
done
SSH_HOST=nas DOCKER_BIN="$D" COMPOSE_BIN="$C" \
  COMPOSE_FILE="$E/core.configured.yml" COMPOSE_PROJECT_NAME=swissjob-r5 \
  python3 scripts/check_core_release.py --capture-retired
```

Debe informar cuatro servicios coherentes, la misma `release`,
`authoritative: true`; además verificar colas drenadas y cero reinicios
inesperados. El flag `CORE_CAPTURE_ENABLED=false` se conserva hasta que la
release de limpieza elimine definitivamente esas tareas.

**6. Retirar el rol y la credencial, sólo tras desplegar la limpieza de
código.** Este paso pertenece al cierre posterior de rollback, no a la primera
ventana de retirada del slot. Antes, la release viva debe haber eliminado el
bootstrap y la verificación de `jobhunt_capture`, y la configuración resuelta no
debe contener `CAPTURE_DSN` ni `CORE_CAPTURE_PASSWORD`.

Comprobar nombres de variables sin imprimir sus valores, y que no haya sesiones:

```sh
for c in swissjob-core-api-r5 swissjob-core-worker-r5 \
  swissjob-core-harvest-worker-r5 swissjob-core-ops-worker-r5; do
  if ssh nas "$D inspect $c --format '{{range .Config.Env}}{{println .}}{{end}}'" \
    | cut -d= -f1 | grep -Eq '^(CAPTURE_DSN|CORE_CAPTURE_PASSWORD)$'; then
    echo "ABORTA: $c conserva una credencial de captura" >&2
    exit 1
  fi
done
ssh nas "$PGC -c \"SELECT count(*) FROM pg_stat_activity WHERE usename='jobhunt_capture';\""
# esperado: 0
```

Conectar como administrador y ejecutar `DROP ROLE jobhunt_capture;` **sin
`CASCADE`**. Si PostgreSQL informa de una dependencia, detenerse e inventariarla;
no borrar objetos para forzar el comando. Verificar después que
`SELECT count(*) FROM pg_roles WHERE rolname='jobhunt_capture';` devuelve `0`.
Sólo entonces retirar `CORE_CAPTURE_PASSWORD` del fichero administrativo y
borrar los ficheros dedicados `.env.core.capture.prod` y
`rehearsal-r5/.env.core.capture.r5` del NAS. No registrar nunca sus contenidos
en el acta.

## Qué NO se borra

- La base `swissjobhunter_r5_rehearsal`: contiene el esquema `jobhunt`, que
  **es** el core. Sólo se retira el slot de replicación.
- Las tablas `public.*` de esa base ni la base `swissjobhunter`: quedan en
  sólo lectura durante el plazo de retención ratificado.
- `swissjob-erasure-cdc`: es borrado de perfiles, otra capacidad, con una
  solicitud registrada.
- `swissjob-backend` y `swissjob-worker`: sirven el BFF, CV, documentos, la
  alerta de profesor de primaria y los 8 colectores escolares registrados.
- `core-harvest-worker` y `core-ops-worker`: ejecutan respectivamente la cosecha
  nativa y las cadencias operativas; no son parte de la captura CDC.

## Después

Actualizar el acta `docs/audits/POINT4_CUTOVER_2026-09-22.md` §8 con la fecha
real del borrado y el `wal_retenido` que tenía el slot al retirarlo.

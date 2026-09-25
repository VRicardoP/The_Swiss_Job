# Auditoría externa R5 — guion del rehearsal NAS

---

> ## Estado de este guion (2026-08-29)
>
> Las **ocho correcciones** que fueron cabecera en las rondas 6 y 7 están
> **integradas en el paso al que afectan**, y el texto que sustituyen se ha
> retirado. El auditor R7 tenía razón en que una fe de erratas al principio y un
> cuerpo sin corregir se contradicen: el operador lee una u otra según por dónde
> entre. Este documento se lee **linealmente, como una receta**.
>
> Cambios de la R7 que afectan a la mecánica y que hay que conocer antes de
> empezar: el cerrojo **ya no vive en `BACKUP_DIR`** (§0 y §4), `flock` es
> **obligatorio** y su ausencia PARA la maniobra (§0), y `CERROJO_GRACIA`
> **desapareció** junto con el repuesto por `mkdir` que la usaba.

---


**Fecha:** 2026-08-28  
**Objeto:** demostrar en QNAP, sobre una copia desechable, que backup, cutover,
fallos intermedios, restore, replay y smoke son recuperables.  
**Estado previo:** los tres P1 de la R5 (manifiesto semántico, restore
reanudable, beat funcional), los tres de la R6 (cerrojo por base, `sync` como
precondición, señales del beat atadas) y los cinco de la R7 (durabilidad de la
unidad de copia, cerrojo independiente de `BACKUP_DIR`, `flock` obligatorio,
evidencia monotónica del beat, `VERIFICACION_FALLIDA` reanudable) están
**cerrados**. Este guion se ejecuta contra ese código, no contra `81b2ea3`.

Este rehearsal no autoriza el cutover por sí solo. Produce evidencia para un
GO/NO-GO posterior. Nunca usa `positive-stratum-v1`, `ensayo_c2` ni la base viva
como destino de escritura.

## 0. Variables, aislamiento y acta

Ejecutar desde una sesión `tmux`/SSH estable. Sustituir únicamente los valores
marcados; no reutilizar nombres de producción.

```bash
set -Eeuo pipefail
umask 077

export NAS_ROOT=/share/Public/swissjob
export EVID=/share/Public/backups/swissjob/rehearsal-$(date +%Y%m%d-%H%M%S)
export PROD_DB=swissjobhunter
export RH_DB=swissjobhunter_r5_rehearsal
# La base apartada NO se puede nombrar por adelantado: el script la llama
# `${PG_DB}_previa_<AAAAMMDDHHMMSS>`. Se resuelve por catálogo cuando exista
# (§9); fijarla aquí a `${RH_DB}_previa` dejaba la limpieza sin efecto.
export PG_CONTAINER=swissjob-postgres
export PG_USER=swissjob
export CORE_DSN_HOST=postgres
export RH_SLOT=jobhunt_shadow_r5_rehearsal
# El cerrojo es único por servidor+base y su ruta NO depende de `BACKUP_DIR`
# (R7 P1-2): dos maniobras con directorios de copia distintos tomaban cerrojos
# distintos y no se veían. Se fija explícitamente para que quede en el acta —
# y TODA invocación del rehearsal debe usar este mismo valor.
export LOCK_DIR="$EVID/cerrojos"
mkdir -p "$EVID" "$LOCK_DIR"
exec > >(tee -a "$EVID/acta.log") 2>&1
date -Ins
uname -a
docker version
```

Abortar si `RH_DB` es vacío, igual a producción o igual a `ensayo_c2`:

```bash
case "$RH_DB" in ''|"$PROD_DB"|ensayo_c2) echo 'DESTINO PROHIBIDO' >&2; exit 1;; esac
```

**Precondición de exclusión mutua.** `flock` es obligatorio desde la R7: el
repuesto por `mkdir`+PID se retiró porque su limpieza de cerrojos huérfanos no
era atómica y dos procesos podían heredar el mismo cerrojo. Sin `flock` el
script PARA, así que hay que saberlo **antes** de la ventana de mantenimiento:

```bash
command -v flock | tee "$EVID/flock.ruta" || {
  echo 'SIN flock: la maniobra no puede ejecutarse en este QNAP' >&2; exit 1; }
```

Si el QNAP no lo trae, el rehearsal se detiene aquí y el problema se resuelve
instalando `util-linux`/`busybox flock` —o apuntando `FLOCK=` a su ruta—, nunca
reactivando un repuesto sin exclusión mutua real.

**Precondición del mapa reconstruido** (SOLO `SELECT`, sobre producción). El
Paso 4 aborta antes del 4c si esto no es 0, así que se mide ahora y se registra:

```bash
$P -d "$PROD_DB" -c "SELECT count(*) FROM jobs
 WHERE md5(lower(btrim(title))||'|'||lower(btrim(company))||'|'||url) <> hash" \
 | tee "$EVID/jobs.hash.irreproducibles"
```

En la base **local** de desarrollo esta cifra es 6.754 porque la maniobra ya se
aplicó allí; **la copia fresca del NAS debe dar exactamente 0**. Cualquier otro
valor es NO-GO y hay que explicarlo antes de seguir.

Antes de cualquier escritura, guardar identidades. Todas las consultas a
producción de esta sección son `SELECT`/`SHOW`:

```bash
P="docker exec -i $PG_CONTAINER psql -U $PG_USER -X -q -A -t -v ON_ERROR_STOP=1"
$P -d "$PROD_DB" -c "SELECT current_database(),oid,pg_postmaster_start_time()
  FROM pg_database WHERE datname=current_database()" | tee "$EVID/prod.identidad.antes"
$P -d "$PROD_DB" -c "SHOW server_version; SHOW max_locks_per_transaction" \
  | tee "$EVID/postgres.parametros"
docker inspect -f '{{.HostConfig.ShmSize}}' "$PG_CONTAINER" | tee "$EVID/postgres.shm"
df -Pk "$EVID" | tee "$EVID/disco.antes"
```

Guardar metadatos de la base para compararlos tras restore:

```bash
$P -d postgres -F '|' -c "SELECT d.datname,pg_get_userbyid(d.datdba),
 d.datcollate,d.datctype,d.dattablespace,d.datconnlimit,d.datallowconn,
 coalesce(array_to_string(d.datacl,','),'')
 FROM pg_database d WHERE d.datname='$PROD_DB'" > "$EVID/dbmeta.antes"
$P -d postgres -F '|' -c "SELECT setrole,setconfig FROM pg_db_role_setting
 WHERE setdatabase=(SELECT oid FROM pg_database WHERE datname='$PROD_DB')
 ORDER BY 1,2" > "$EVID/dbsettings.antes"
$P -d "$PROD_DB" -F '|' -c "SELECT extname,extversion FROM pg_extension ORDER BY 1" \
 > "$EVID/extensions.antes"
```

**Aborto inmediato:** espacio no medible, versión/imagen desconocida, acceso al
rol `jobhunt` fallido, identidad ambigua, o cualquier escritor del stack de
ensayo conectado a producción.

## 1. Backup real del NAS y creación del destino desechable

El dump de producción es una lectura. Medir tiempo, bytes y pico de espacio:

```bash
T0=$(date +%s)
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -Fc -Z 6 "$PROD_DB" \
  > "$EVID/prod.dump.parcial"
docker exec -i "$PG_CONTAINER" pg_restore -l < "$EVID/prod.dump.parcial" \
  > "$EVID/prod.toc"
docker exec -i "$PG_CONTAINER" sha256sum < "$EVID/prod.dump.parcial" \
  > "$EVID/prod.dump.sha256"
mv "$EVID/prod.dump.parcial" "$EVID/prod.dump"
stat -c '%s bytes' "$EVID/prod.dump" | tee "$EVID/dump.bytes"
echo "$(( $(date +%s)-T0 )) s" | tee "$EVID/dump.segundos"
df -Pk "$EVID" | tee "$EVID/disco.despues_dump"
```

Crear/restaurar una base vacía desechable, con los mismos atributos medidos. No
copiar mediante `TEMPLATE $PROD_DB`: el ensayo debe probar el dump.

```bash
$P -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname='$RH_DB' AND pid<>pg_backend_pid()"
$P -d postgres -c "DROP DATABASE IF EXISTS \"$RH_DB\""
$P -d postgres -c "CREATE DATABASE \"$RH_DB\" TEMPLATE template0
 ENCODING 'UTF8' LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8' OWNER \"$PG_USER\""
$P -d postgres -c "ALTER DATABASE \"$RH_DB\" SET max_parallel_maintenance_workers=0"
T0=$(date +%s)
docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$RH_DB" \
 --exit-on-error --single-transaction < "$EVID/prod.dump"
$P -d postgres -c "ALTER DATABASE \"$RH_DB\" RESET max_parallel_maintenance_workers"
echo "$(( $(date +%s)-T0 )) s" | tee "$EVID/restore-inicial.segundos"
```

Crear una red/Redis/contenedores de rehearsal con puertos, volúmenes, slot y
env propios. Ningún servicio de rehearsal puede recibir `PROD_DB` en sus env:

```bash
docker inspect "$PG_CONTAINER" > "$EVID/postgres.inspect.json"
# Crear mediante el compose R5 de rehearsal revisado. Debe usar RH_DB/RH_SLOT,
# nombres sufijados -r5 y puertos sin publicar o distintos de producción.
docker compose -p swissjob-r5 -f "$NAS_ROOT/docker-compose.qnap.yml" \
  --env-file "$EVID/.env.r5" config > "$EVID/compose.rendered.yml"
grep -n "$PROD_DB" "$EVID/compose.rendered.yml" && {
  echo 'compose de rehearsal aún apunta a producción' >&2; exit 1; }
```

**Aborto:** el dump no pasa `pg_restore -l`, faltan `public`/`jobhunt` o
extensiones, el restore excede la ventana aprobada, el pico libre es menor de
dos veces el pico medido, o `max_locks_per_transaction` produce cualquier error.

## 2. Baseline exacto de la copia

Guardar antes del cutover:

- los manifiestos semánticos de juicios/pares **con todos sus atributos**
  (`pair_id`/`judgment_id`, cohorte, veredicto, relevancia, refs, fuente);
- **el mapa declarado** `mapa.declarado` (`IDENT|remap|<viejo>|<nuevo>`) y los
  **manifiestos esperados** `manifiesto.*.esperado` que el Paso 6 construye
  aplicándolo: sin ellos no se puede distinguir un remapeo legítimo de una
  permuta, que es justo lo que cerró R5 P1-A;
- **los cuatro sidecars** (`pares`, `juicios`, `pares.resuelven`,
  `juicios.resuelven`) y el de `guardas`, más el `SIDECARS_SHA256` que los sella
  como una unidad;
- hashes exactos de los planes G3/G6;
- conteos y checksums del script;
- triggers, publicaciones, slots y capture watermark;
- owner/ACL/settings/extensiones.

```bash
$P -d "$RH_DB" -F '|' -c "SELECT n.nspname,c.relname,t.tgname,t.tgenabled
 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE NOT t.tgisinternal AND n.nspname IN ('jobhunt','public')
 ORDER BY 1,2,3" > "$EVID/triggers.baseline"
$P -d "$RH_DB" -F '|' -c "SELECT pubname,puballtables FROM pg_publication ORDER BY 1" \
 > "$EVID/publications.baseline"
```

Ejecutar en la copia dos mordidas de las guardas de cohortes dentro de
`BEGIN; ...; ROLLBACK;`: UPDATE y DELETE/TRUNCATE prohibidos deben fallar aun con
`session_replication_role=replica`. No modificar la cohorte real; usar una
cohorte de rehearsal creada expresamente.

## 3. Preflights dirigidos

### 3.1 DSN y env-files

Probar la sonda sola con los env-files renderizados. Debe devolver la identidad
de `$RH_DB`, nunca la de producción. Repetir las 14 variantes de la regresión:
query/fragmento/percent-encoding/`dbname`/`host`/base diferente deben abortar.

```bash
export ENSAYO=1 PG_DB="$RH_DB" PG_DB_PROD="$PROD_DB"
export CORE_DSN="postgresql+asyncpg://jobhunt_core:***@postgres:5432/$RH_DB"
# Ejecutar el contenedor de identidad exactamente con los dos --env-file de R5.
# Comparar current_database|oid|postmaster_start con $P -d "$RH_DB".
```

### 3.2 Imágenes y arquitectura

```bash
for i in swissjob-core:prod swissjob-backend:prod swissjob-frontend:prod; do
  docker image inspect -f '{{.Id}} {{.Architecture}} {{.Os}}' "$i"
done | tee "$EVID/images.ids"
```

Arrancar el worker de la imagen `:prod` aislado, confirmar app/nodename y
`pong`; después confirmar **beat funcional** con el MISMO criterio que aplica el
smoke del Paso 7d — no uno más flojo, o §3 pasaría y §7 fallaría al final de la
ventana de mantenimiento:

- `beat: Starting` en el log;
- un `Sending due task` **de `shadow-sample-outbox-lag`** —la cadencia del
  muestreador, no cualquiera de las cuatro de cinco minutos—, leído con
  `--since` para que sea NUEVO;
- y una muestra **nueva** de `outbox_lag_p99`: no basta con que la más reciente
  sea posterior al sondeo. Se cuenta cuántas hay posteriores a `t0` **al
  empezar** y se exige que esa cifra crezca (R7 P1-4: una fila fechada en el
  futuro cumplía «max(ts) > t0» para siempre con el beat muerto).

```bash
t0=$(date +%s)
$P -d "$RH_DB" -c "SELECT count(*) FROM jobhunt.shadow_cycle_metrics m,
 LATERAL jsonb_array_elements(coalesce(m.details->'samples','[]'::jsonb)) s
 WHERE m.metric='outbox_lag_p99' AND m.scope='global'
   AND (s->>'ts')::timestamptz > to_timestamp($t0)" | tee "$EVID/beat.muestras.base"
```

Si la línea base no es 0, hay muestras con fecha futura: investigar el reloj del
NAS **antes** de continuar, y dejarlo en el acta.

**Inyección obligatoria:** arrancar la misma imagen sin `-B`. El ping debe ser
verde y el nuevo chequeo de beat debe ser rojo. Si el smoke queda verde, abortar.

## 4. Camino feliz de cutover sobre la copia

Con los env de rehearsal y `ENSAYO=1`, ejecutar el script corregido:

```bash
export WORK_DIR="$EVID/work-happy"
export BACKUP_DIR="$EVID/backups-happy"
export CORE_DSN="postgresql+asyncpg://jobhunt_core:***@postgres:5432/$RH_DB"
bash "$NAS_ROOT/scripts/nas_cutover.sh" cutover
```

El script imprime la ruta del cerrojo que ha tomado (`cerrojo (flock): …`).
**Anotarla en el acta**: es de `$LOCK_DIR`, no de `$BACKUP_DIR`, y tiene que ser
la MISMA en el cutover, en cada restauración y en cada fila de §5. Es lo primero
que hay que mirar junto a la precondición del hash: si el cerrojo cambia de
nombre entre dos invocaciones, no hay exclusión mutua y no debe existir Paso 4c.

Guardar exit code, informes secos/firmes, mapas old→new, manifests esperados y
posteriores. Deben ser idénticos según la transformación, no solo conservar
cardinalidad. Una segunda ejecución en una nueva copia del mismo baseline debe
ser idempotente.

## 5. Matriz de fallos inyectados

Cada fila empieza desde una restauración nueva del mismo dump. Los escritores
de rehearsal permanecen parados. Tras el fallo se ejecuta `restaurar` tantas
veces como haga falta; debe terminar `VERIFIED` sin intervención SQL manual.

| Inyección | Cómo | Resultado exigido |
|---|---|---|
| Dos cutovers simultáneos | mantener el primero tras adquirir lock y lanzar el segundo | el segundo aborta por `flock`; no toca `WORK_DIR` ni backup |
| Restauración × restauración, **copias distintas** | detener la primera dentro de `pg_restore` y lanzar la segunda con otro `.dump` de la misma base | la segunda aborta **nombrando la base**; no llega a `APARTADA` |
| Cutover × restauración | restauración detenida en `pg_restore`, lanzar `cutover` | aborta por cerrojo antes del Paso 1; no para escritores ni vuelca |
| Restauración × cutover | cutover detenido en `pg_dump`, lanzar `restaurar` | aborta por cerrojo; no aparta ninguna base |
| **`BACKUP_DIR` distinto, misma base** (R7 P1-2) | como la fila anterior pero exportando otro `BACKUP_DIR` en la segunda | aborta igual: el cerrojo sale de `$LOCK_DIR`, no del directorio de copias |
| **Sin `flock`** (R7 P1-3) | `FLOCK=ruta-que-no-existe` | `cutover` y `restaurar` abortan **antes de tocar nada**; no hay repuesto por `mkdir` |
| **`sync` falla en el checkpoint** (R6 P1-2) | volumen desechable con E/S o espacio agotado durante la marcha atrás | ni `RENAME` ni `APARTADA`; la base queda intacta |
| **`sync` falla en la unidad de copia** (R7 P1-1) | lo mismo, pero durante el sellado del manifiesto del cutover | no se alcanza el Paso 4c ni se confirma ninguna copia SQL |
| `/tmp` vacío/reinicio simulado | borrar solo `/tmp` entre fases | checkpoint durable permite diagnosticar/reanudar; no se pierde evidencia |
| dump truncado | `ulimit -f` o cortar la redirección | no aparece nombre final ni manifest; no hay Paso 3 |
| dump alterado un byte | copiar y alterar el artefacto de rehearsal | restore aborta **antes** de RENAME por SHA |
| falta concepto enclave | doble controlado de uno de los SQL | aborta antes del primer COMMIT |
| enclave >0 | cohorte de rehearsal con un ref del mapa | aborta antes del primer COMMIT y nombra la cohorte/ref |
| fallo G3 seco/G6 seco | devolver no cero | ninguna copia queda confirmada |
| muerte tras COMMIT G3 | hook de fault injection o wrapper que mata el grupo | no se arranca servicio; restore devuelve baseline exacto |
| muerte durante canonical refs | matar el grupo después de iniciar la transacción | transacción revierte o restore devuelve baseline exacto |
| permuta A/B→C/D resoluble | fixture R5 P1-A | manifest semántico da rojo |
| remapeo legítimo de juicio | old→new conservando transformación prevista | manifest da verde, no falso rojo |
| `SIGKILL` tras RENAME | matar antes de CREATE | rerun descubre `_previa`, recrea/restaura y termina VERIFIED |
| `SIGKILL` durante pg_restore | matar proceso/contenedor | destino parcial no se certifica; rerun lo recrea y termina VERIFIED |
| cliente reconector | loop que abre conexión a `$RH_DB` durante RENAME | o se bloquea por política de conexión o restore aborta reanudablemente |
| disco lleno durante restore | filesystem/volumen de rehearsal con cuota | no se marca VERIFIED; base previa intacta; rerun con espacio termina |
| sidecar alterado | cambiar manifest de pares/juicios | hash del conjunto aborta antes de tocar BD |
| trigger degradado | deshabilitar una guarda solo en copia | verificación post-restore da rojo |
| frontend ausente/unhealthy | parar o forzar health rojo | smoke rojo |
| imagen equivocada | recrear uno con otro ID | smoke rojo |
| worker sin beat | omitir `-B` | ping verde, chequeo funcional beat rojo |
| **beat desacoplado** (R6 P1-3) | que el planificador despache otra cadencia y una muestra entre por su cuenta | smoke rojo: las dos señales deben ser de la MISMA capacidad |
| **muestra fechada en el futuro** (R7 P1-4) | sembrar una muestra de `outbox_lag_p99` con `ts` en 2100 y matar el beat | smoke rojo: la del futuro entra en la línea base y no prueba nada |
| **verificación fallida tras `RESTAURADO`** (R7 P1-5) | degradar el destino después de que `pg_restore` entre entero | el checkpoint anota `VERIFICACION_FALLIDA`; el rerun **restaura de nuevo** y converge a `VERIFIED`, sin re-verificar el destino roto |
| slot inactivo | parar capture | smoke rojo |
| lag alto creciente | generar WAL de rehearsal con capture parado y reanudar | smoke rojo hasta recuperar; medir tasa y fijar umbral real |

No usar `kill` a ojo. **El script no expone hooks de fallo**: los dobles que
matan el grupo de procesos viven solo dentro de la suite. Para el NAS hace falta
un **wrapper versionado** propio del rehearsal que registre sentencia y fase y
mate el **grupo**, y que se archive en `$EVID` como parte de la evidencia.
Guardar comando, PID, instante y checkpoint antes de cada inyección.

**Presupuesto de tiempo:** cada inyección que toque el beat cuesta ~11 min
(`BEAT_ESPERA` son dos cadencias de cinco minutos más el sondeo). Con las tres
filas de beat son ~33 min solo de espera. Si se baja `BEAT_ESPERA` para acortar
el rehearsal, **hay que dejarlo escrito en el acta**: el valor probado deja de
ser el que correrá en el cutover real.

## 6. Restore, slot y replay completos

Después de al menos los fallos «tras G3», «tras RENAME» y «durante pg_restore»:

```bash
# La copia del PROPIO cutover, no el `pg_dump` pelado de §1: aquel no tiene
# `.manifiesto` ni sidecars, así que `restaurar` lo rechaza fail-closed — y hace
# bien. Sin manifiesto pre-corte no se puede certificar que la vuelta VOLVIÓ.
RH_DUMP=$(ls -1t "$EVID/backups-happy"/pre_canonizacion_*.dump | head -1)
bash "$NAS_ROOT/scripts/nas_cutover.sh" restaurar "$RH_DUMP"
```

Antes de arrancar capture, ejecutar literalmente `jobhunt_core/shadow/RUNBOOK.md
§3` sobre `$RH_DB` y `$RH_SLOT`: limpiar staging/capture state según el runbook,
crear slot nuevo, backfill y replay. Guardar:

```bash
$P -d "$RH_DB" -F '|' -c "SELECT slot_name,active,restart_lsn,confirmed_flush_lsn
 FROM pg_replication_slots WHERE slot_name='$RH_SLOT'" | tee "$EVID/slot.post"
$P -d "$RH_DB" -F '|' -c "SELECT * FROM jobhunt.shadow_capture_state" \
 | tee "$EVID/capture-state.post"
```

Comparar baseline/restaurado:

- manifests semánticos exactos;
- conteos/checksums;
- metadatos, ACL/settings/extensiones;
- publicaciones;
- todos los triggers de guarda en `A` (`ENABLE ALWAYS`);
- mordidas de inmutabilidad;
- slot activo y lag descendente;
- ausencia de hueco en WAL/staging y proyección idempotente.

La base `_previa_*` no se borra hasta que todas las comparaciones sean verdes y
el propietario firme el acta.

## 7. Recreate y smoke reales de Container Station

Recrear **solo la aplicación de rehearsal** desde la UI. Registrar antes los IDs
cargados y después los seis contenedores:

```bash
docker inspect -f '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}sin-healthcheck{{end}}|{{.Image}}|{{json .Config.Cmd}}' \
  swissjob-backend-r5 swissjob-worker-r5 swissjob-core-api-r5 \
  swissjob-core-worker-r5 swissjob-core-capture-r5 swissjob-frontend-r5 \
  | tee "$EVID/containers.post-recreate"
```

Ejecutar el smoke corregido. Esperar dos cadencias para la prueba de beat. Hacer
una escritura inocua exclusivamente en el origen **de rehearsal** para producir
WAL y exigir que `confirmed_flush_lsn` avance y el proyector refleje el evento.

## 7bis. Lo que SOLO puede comprobar este rehearsal

La suite ejercita el programa de verdad, pero con `docker` y `psql` simulados. Cerrar
los cinco invariantes «en tests» no cierra estas siete cosas, que son exactamente el
motivo por el que el rehearsal existe:

| # | Comprobación | Por qué un doble no vale | Resultado exigido |
|---|---|---|---|
| 1 | **`flock` existe en ESE QNAP** | el doble corre en Debian, donde siempre está | `command -v flock` responde; si no, la maniobra no puede ejecutarse ahí |
| 2 | **`sync` falla de verdad** por E/S o espacio agotado en el volumen desechable | el doble devuelve 1 a voluntad; un almacén real falla de otras formas (parcial, lento, con el fichero ya publicado) | ni Paso 4c ni `RENAME`, y la base intacta |
| 3 | **La precondición del hash da 0** sobre la copia fresca | el doble no reconstruye hashes de un corpus real | exactamente `0`; en local son 6.754 porque allí ya se aplicó |
| 4 | **`pg_restore` del corpus real entra en la ventana** y en `/dev/shm` de 64 MB | el doble no restaura 117 MB ni construye un índice HNSW | entra entero, `--single-transaction`, dentro del tiempo aprobado |
| 5 | **El beat despacha en el hardware real** dentro de dos cadencias | el doble contesta al primer sondeo | `Sending due task shadow-sample-outbox-lag` nuevo + muestra nueva |
| 6 | **La concurrencia real** entre dos procesos del NAS sobre la misma base | los hilos de la suite comparten planificador y reloj | el segundo aborta nombrando la base |
| 7 | **Container Station recrea con los IDs esperados** | no hay Container Station en la suite | los seis contenedores con las imágenes cargadas, `healthy` |

Cualquiera de las siete en rojo es NO-GO, y ninguna es sustituible por más tests.

## 8. Criterio vinculante de aborto

El cutover real queda **NO-GO** si ocurre cualquiera:

1. una inyección devuelve 0 cuando debía parar, o avanza a la fase siguiente;
2. un rerun de restore necesita SQL manual para encontrar/renombrar la base;
3. dump, manifests o sidecars no están sellados como una unidad;
4. una identidad semántica cambia fuera del mapa o un remapeo legítimo da rojo;
5. el estado restaurado difiere en datos, guardas, ACL/settings/extensiones o
   publicación;
6. el slot/replay no demuestra RPO 0 en la copia;
7. ping verde sin beat puede producir smoke verde, o una muestra fechada en el
   futuro basta para dar el beat por vivo;
7bis. dos maniobras sobre la misma base coexisten —por copias distintas, por
   `BACKUP_DIR` distintos o por un cerrojo heredado—, o alguna arranca sin
   `flock`;
7ter. un `sync` que falla no detiene lo irreversible que venía detrás, o una
   restauración con la verificación en rojo no converge al reintentarla;
8. Container Station ejecuta otro ID, env, comando, red o volumen;
9. el tiempo excede la ventana aprobada o el espacio libre no conserva al menos
   2× el pico medido;
10. el lag no converge dentro de la ventana medida, o el umbral de 16 MiB/5 s no
    queda sustituido por valores derivados;
11. queda cualquier conexión/escritor no controlado durante RENAME/restore;
12. no se puede explicar y reproducir cada línea roja del acta.

## 9. Evidencia de salida y limpieza

El acta debe contener: SHAs de repositorio e imágenes, compose renderizado sin
secretos, identidad de ambas BD, dump SHA/TOC, tiempos/espacio, parámetros PG,
mapas/manifests, salida de cada inyección, checkpoints, restore, triggers,
slot/replay, health/beat y firma del operador.

Tras aceptación:

```bash
docker compose -p swissjob-r5 -f "$NAS_ROOT/docker-compose.qnap.yml" \
  --env-file "$EVID/.env.r5" down
# La base apartada lleva sello temporal (`<base>_previa_<AAAAMMDDHHMMSS>`), así
# que se resuelve por catálogo: un nombre fijado a mano no la encuentra y la
# limpieza no borraría nada.
RH_PREV=$($P -d postgres -c "SELECT datname FROM pg_database
 WHERE datname LIKE '${RH_DB}_previa_%' ORDER BY datname DESC LIMIT 1")
echo "base apartada a retirar: ${RH_PREV:-(ninguna)}"
$P -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname IN ('$RH_DB','$RH_PREV') AND pid<>pg_backend_pid()"
$P -d postgres -c "DROP DATABASE IF EXISTS \"$RH_DB\""
[ -n "$RH_PREV" ] && $P -d postgres -c "DROP DATABASE IF EXISTS \"$RH_PREV\""
```

No borrar `$EVID`. El directorio es el entregable durable del rehearsal. Antes
de archivarlo, retirar/redactar `.env.r5` y cualquier secreto; conservar hashes,
no credenciales.

## Cierre

Un rehearsal aprobado demuestra la maniobra en **ese** QNAP, con **esas**
imágenes, PostgreSQL, datos y Container Station. No sustituye el holdout ciego,
la rotación de secretos/proxy ni la racha GATE-SOMBRA. Sí elimina el último tipo
de incertidumbre que otra revisión estática no puede reducir.

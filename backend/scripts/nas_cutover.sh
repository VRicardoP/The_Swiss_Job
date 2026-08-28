#!/usr/bin/env bash
#
# nas_cutover.sh — cuerpo EJECUTABLE de la maniobra de mantenimiento del NAS.
# Procedimiento y contexto: `docs/DEPLOY_NAS.md` §5.3 (siete pasos).
#
# POR QUÉ EXISTE (auditoría externa R3 P1-1). El runbook ordenaba bien los siete
# pasos y escribía «si no cuadra, se PARA», pero sus postcondiciones eran PROSA:
# `docker ps` no comprobaba nada, `pg_dump | gzip` devolvía el estado de `gzip`,
# `psql | tee` el de `tee`, y las cifras del Paso 6 se miraban a ojo. Con los
# cinco escritores vivos la postcondición del Paso 1 salía 0; con `pg_dump`
# roto quedaba un `.gz` válido y vacío; y con la SEGUNDA copia SQL abortada tras
# confirmar la primera la secuencia seguía hasta el Paso 5 — justo el estado que
# el propio documento declara IRREPARABLE para cohortes selladas (`core0025`).
# Aquí cada postcondición es una condición de EJECUCIÓN: si no cuadra, se sale
# distinto de cero y no hay paso siguiente.
#
# CIFRAS: este script NO lleva ninguna constante del corpus. Mide el estado
# ANTES, lee las cifras de los informes en seco y aserta el estado DESPUÉS
# contra lo que él mismo midió. Las del NAS serán otras que las locales.
#
#   Uso:  ./nas_cutover.sh cutover   # Pasos 1–6 (el 4 en firme es irreversible)
#         ./nas_cutover.sh smoke     # Paso 7, DESPUÉS del Recreate de la UI
#
set -Eeuo pipefail

# --------------------------------------------------------------------------
# Configuración (todo sobrescribible por entorno; sin valores del corpus)
# --------------------------------------------------------------------------
: "${DOCKER:=docker}"
: "${PG_CONTAINER:=swissjob-postgres}"
: "${PG_USER:=swissjob}"
: "${PG_DB:=swissjobhunter}"
: "${PG_DB_PROD:=swissjobhunter}"
: "${ESCRITORES:=swissjob-backend swissjob-worker swissjob-core-api swissjob-core-worker swissjob-core-capture}"
: "${BASE_DIR:=/share/Public/swissjob}"
: "${SCRIPTS_DIR:=$BASE_DIR/scripts}"
: "${BACKUP_DIR:=/share/Public/backups/swissjob}"
: "${WORK_DIR:=/tmp/nas_cutover}"
: "${CORE_IMAGE:=swissjob-core:prod}"
: "${CORE_API:=swissjob-core-api}"
: "${ENV_CORE:=$BASE_DIR/.env.core.prod}"
: "${ENV_CORE_ADMIN:=$BASE_DIR/.env.core.admin.prod}"
: "${COPIAS_SQL:=g3_canonizacion_identidad_arbeitnow_jobgether.sql g6_canonizacion_identidad_irishjobs.sql}"
: "${TARS:=swissjob-core.tar swissjob-backend.tar swissjob-frontend.tar}"
: "${IMAGENES:=swissjob-core:prod swissjob-backend:prod swissjob-frontend:prod}"
# El `status` que la API contractual devuelve en /v1/ready. Lo fija
# `jobhunt_core/api/main._READY_STATUS`, y `test_deploy_order.py` comprueba que
# esta constante y aquella no puedan divergir (auditoría externa R3 P2-1: el
# smoke exigía `ok` y la API devuelve `ready`).
: "${READY_STATUS_ESPERADO:=ready}"
# Escape ÚNICO y ruidoso: el ENSAYO sobre una restauración desechable (§5.3) no puede
# parar los escritores de producción ni recargar imágenes, así que se salta los pasos 1
# y 3. Exige una base distinta de la de producción Y un DSN propio para el core: el
# módulo del Paso 5 saca el suyo de los `--env-file`, que apuntan a producción, de modo
# que un «ensayo» sin esto escribiría en la base viva.
: "${ENSAYO:=0}"
: "${CORE_DSN:=}"
# El Paso 4 PARA si la fusión descarta match_results con señal del usuario. Es
# una decisión humana, no un umbral: por eso hay que pedirla a mano.
: "${PERMITIR_SENAL_USUARIO:=0}"

ESTADO="$WORK_DIR/estado.env"

# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
morir() { printf '\n### PARAR — %s\n' "$*" >&2; exit 1; }
titulo() { printf '\n=== %s ===\n' "$*"; }
trap 'printf "\n### PARAR — fallo no controlado en la línea %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR

# Ejecuta un fichero .sql y deja la salida en $2. SIN tubería: una tubería
# devolvería el estado del último comando y perdería el fallo de psql, que es
# exactamente el defecto que este script corrige.
psql_archivo() { # <fichero.sql> <fichero de salida>
  "$DOCKER" exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" \
    -v ON_ERROR_STOP=1 -X -q -A -t -F '|' -f - <"$1" >"$2" 2>"$2.err" \
    || { cat "$2.err" >&2; return 1; }
}

# Un escalar. La asignación va en su propia línea a propósito: `local x=$(...)`
# se traga el estado de salida de la sustitución.
psql_valor() { # <sql>
  "$DOCKER" exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" \
    -v ON_ERROR_STOP=1 -X -q -A -t -c "$1" </dev/null
}

# Las filas `concepto|filas` del informe, descartando etiquetas de comando.
informe() { grep -E '^[^|]+\|[0-9]+$' "$1" || true; }

# Valor de un concepto del informe (coincidencia por PREFIJO: los conceptos
# llevan paréntesis y sangrías que no conviene repetir aquí).
cifra() { # <fichero informe> <prefijo del concepto>
  local v
  v=$(awk -F'|' -v c="$2" 'index($1,c)==1 {print $2; exit}' "$1")
  [ -n "$v" ] || morir "el informe $1 no trae el concepto «$2»: no se puede asertar contra él"
  printf '%s' "$v"
}

entero() { # <valor> <qué es>
  case "$1" in ''|*[!0-9-]*) morir "«$2» no es un número: '$1'";; esac
}

# --------------------------------------------------------------------------
# Paso 1 — Detener todo escritor y proyector
# --------------------------------------------------------------------------
paso1_parar() {
  titulo "Paso 1 — detener escritores y proyector"
  [ -n "${ESCRITORES// /}" ] || morir "ESCRITORES vacío: la aserción sería trivialmente cierta"
  if [ "$ENSAYO" = 1 ]; then
    printf '⚠ ENSAYO contra %s: no se para nada y NO es la maniobra real.\n' "$PG_DB"
    return 0
  fi
  # `docker stop` basta: `restart: unless-stopped` NO rearranca lo parado a mano.
  # shellcheck disable=SC2086
  "$DOCKER" stop $ESCRITORES

  # Postcondición COMO CONDICIÓN: ninguno puede seguir corriendo. Se comprueba
  # contra `docker ps` (solo corriendo/reiniciando), no contra `Up`: un
  # «Restarting» tampoco está parado y también escribiría.
  local vivos vivo
  vivos=$("$DOCKER" ps --format '{{.Names}}')
  for vivo in $ESCRITORES; do
    if printf '%s\n' "$vivos" | grep -qx -- "$vivo"; then
      morir "$vivo sigue corriendo: la canonización NO puede empezar con escritores vivos"
    fi
  done
  printf 'escritores parados: %s\n' "$ESCRITORES"
}

# --------------------------------------------------------------------------
# Paso 2 — Copia de seguridad, con `public` Y `jobhunt`
# --------------------------------------------------------------------------
paso2_backup() {
  titulo "Paso 2 — copia de seguridad (public + jobhunt)"
  mkdir -p "$BACKUP_DIR"
  local sello crudo parcial final cuentas n_public n_jobhunt
  sello=$(date +%Y%m%d-%H%M%S)
  # Los dos temporales van al MISMO almacén que la copia final: `/tmp` en el NAS es un
  # ramdisk pequeño y un volcado del corpus no cabe.
  crudo="$BACKUP_DIR/.pre_canonizacion_$sello.sql.parcial"
  parcial="$BACKUP_DIR/.pre_canonizacion_$sello.sql.gz.parcial"
  final="$BACKUP_DIR/pre_canonizacion_$sello.sql.gz"

  # Sin `-t`: un TTY reescribe los saltos de línea del volcado. Y sin tubería,
  # para no perder el estado de pg_dump.
  "$DOCKER" exec "$PG_CONTAINER" \
    pg_dump -U "$PG_USER" -n public -n jobhunt "$PG_DB" >"$crudo" ||
    morir "pg_dump falló: no hay copia de seguridad y el Paso 4 es irreversible"
  gzip -c "$crudo" >"$parcial" || morir "gzip falló sobre $crudo"
  rm -f "$crudo"
  gzip -t "$parcial" || morir "el .gz no pasa gzip -t: copia corrupta"

  # Una sola pasada: descomprimir dos veces un volcado del corpus cuesta minutos.
  cuentas=$(gzip -dc "$parcial" |
    awk '/CREATE TABLE public\./ {p++} /CREATE TABLE jobhunt\./ {j++} END {print p+0, j+0}')
  n_public=${cuentas%% *}; n_jobhunt=${cuentas##* }
  printf 'tablas en el volcado: public=%s jobhunt=%s\n' "$n_public" "$n_jobhunt"
  [ "$n_public" -gt 0 ] || morir "el volcado no trae tablas de public"
  # Si el rol no puede leer `jobhunt`, la copia no sirve para ESTA maniobra:
  # hay que repetirla con la credencial de .env.core.admin.prod.
  [ "$n_jobhunt" -gt 0 ] || morir "el volcado no trae tablas de jobhunt: repítelo con la credencial admin del core"

  mv "$parcial" "$final"   # el nombre definitivo solo lo gana un volcado verificado
  printf 'BACKUP=%s\n' "$final" >>"$ESTADO"
  printf 'copia verificada: %s\n' "$final"
}

# --------------------------------------------------------------------------
# Paso 3 — Cargar las imágenes SIN arrancar servicios
# --------------------------------------------------------------------------
paso3_imagenes() {
  titulo "Paso 3 — cargar imágenes (no arranca nada)"
  if [ "$ENSAYO" = 1 ]; then
    printf '⚠ ENSAYO: no se cargan imágenes (y por eso no habrá smoke).\n'
    return 0
  fi
  local tar img release
  for tar in $TARS; do
    [ -f "$BASE_DIR/$tar" ] || morir "falta $BASE_DIR/$tar (§5.2)"
  done
  for img in $IMAGENES; do "$DOCKER" rmi "$img" >/dev/null 2>&1 || true; done
  for tar in $TARS; do "$DOCKER" load -i "$BASE_DIR/$tar" || morir "docker load falló con $tar"; done
  for img in $IMAGENES; do
    "$DOCKER" image inspect "$img" >/dev/null 2>&1 || morir "$img no quedó cargada"
  done

  # La imagen tiene que saber nombrar su release: sin el build arg hornea
  # `unknown` y las sondas responden `authoritative: false` (§1.1).
  release=$("$DOCKER" run --rm --entrypoint sh "$CORE_IMAGE" -c 'cat /opt/jobhunt-release/RELEASE')
  release=$(printf '%s' "$release" | tr -d ' \r\n')
  [ -n "$release" ] || morir "la imagen del core no publica RELEASE"
  [ "$release" != unknown ] || morir "la imagen del core hornea RELEASE=unknown: reconstruye con RELEASE_SHA (§5.1)"
  printf 'RELEASE_ESPERADA=%s\n' "$release" >>"$ESTADO"
  printf 'release horneada: %s\n' "$release"
}

# --------------------------------------------------------------------------
# Paso 6 (medición) — las cuatro consultas, ejecutables antes y después
# --------------------------------------------------------------------------
SQL_SLOTS="SELECT count(*) FROM jobhunt.source_listings sl
 JOIN jobhunt.sources s ON s.id = sl.source_id
 LEFT JOIN public.jobs j ON j.hash = sl.external_id
 WHERE s.name IN ('legacy:arbeitnow','legacy:jobgether','legacy:irishjobs')
   AND j.hash IS NULL"
SQL_JOBS="SELECT count(*) FROM public.jobs"
SQL_JUICIOS="SELECT count(*) || ' ' || count(*) FILTER (WHERE EXISTS (
   SELECT 1 FROM jobhunt.source_listings l
   JOIN jobhunt.sources src ON src.id = l.source_id
   JOIN jobhunt.source_listing_incarnations i ON i.source_listing_id = l.id
   WHERE src.name LIKE 'legacy:%' AND l.external_id = j.job_ref))
 FROM jobhunt.labeled_judgments j"
SQL_PARES="SELECT count(*) FILTER (WHERE r.a AND r.b)
 FROM jobhunt.labeled_dedup_pairs p
 CROSS JOIN LATERAL (SELECT
   EXISTS (SELECT 1 FROM jobhunt.source_listings l
           JOIN jobhunt.sources src ON src.id = l.source_id
           JOIN jobhunt.source_listing_incarnations i ON i.source_listing_id = l.id
           WHERE src.name LIKE 'legacy:%' AND l.external_id = p.job_ref_a) AS a,
   EXISTS (SELECT 1 FROM jobhunt.source_listings l
           JOIN jobhunt.sources src ON src.id = l.source_id
           JOIN jobhunt.source_listing_incarnations i ON i.source_listing_id = l.id
           WHERE src.name LIKE 'legacy:%' AND l.external_id = p.job_ref_b) AS b) r"
# Enclavamiento 4b: pares de cohortes SELLADAS que habría que re-mapear.
SQL_ENCLAVAMIENTO="SELECT count(*)
 FROM jobhunt.labeled_dedup_pairs p
 JOIN jobhunt.labeled_dedup_cohorts c
   ON c.source = p.source AND c.frozen_at IS NOT NULL
 WHERE EXISTS (
   SELECT 1 FROM jobhunt.source_listings sl
   JOIN jobhunt.sources s2 ON s2.id = sl.source_id
   WHERE sl.external_id IN (p.job_ref_a, p.job_ref_b)
     AND s2.name IN ('legacy:arbeitnow','legacy:jobgether','legacy:irishjobs'))"

medir() { # deja SLOTS/JOBS/PARES/JUICIOS_TOTAL/JUICIOS_RESUELVEN en variables con el sufijo $1
  local slots jobs pares juicios
  slots=$(psql_valor "$SQL_SLOTS")
  jobs=$(psql_valor "$SQL_JOBS")
  pares=$(psql_valor "$SQL_PARES")
  juicios=$(psql_valor "$SQL_JUICIOS")
  local juicios_total=${juicios%% *} juicios_resuelven=${juicios##* }
  entero "$slots" "slots huérfanos"; entero "$jobs" "jobs"; entero "$pares" "pares con los dos refs"
  entero "$juicios_total" "juicios"; entero "$juicios_resuelven" "juicios que resuelven"
  eval "SLOTS_$1=\$slots; JOBS_$1=\$jobs; PARES_$1=\$pares"
  eval "JUICIOS_$1=\$juicios_total; RESUELVEN_$1=\$juicios_resuelven"
  printf 'medición %s: slots_huerfanos=%s jobs=%s pares_con_los_dos_refs=%s juicios=%s resuelven=%s\n' \
    "$1" "$slots" "$jobs" "$pares" "$juicios_total" "$juicios_resuelven"
}

# --------------------------------------------------------------------------
# Paso 4 — Las dos copias SQL: primero en seco (LAS DOS), luego en firme
# --------------------------------------------------------------------------
paso4_copias() {
  titulo "Paso 4a — ensayo en seco de LAS DOS copias"
  local f base senal
  for f in $COPIAS_SQL; do
    [ -f "$SCRIPTS_DIR/$f" ] || morir "falta $SCRIPTS_DIR/$f (§5.2)"
    base=${f%.sql}
    psql_archivo "$SCRIPTS_DIR/$f" "$WORK_DIR/$base.dryrun.txt" ||
      morir "el ensayo en seco de $f falló: NADA se ha confirmado todavía"
    informe "$WORK_DIR/$base.dryrun.txt" >"$WORK_DIR/$base.dryrun.informe"
    [ -s "$WORK_DIR/$base.dryrun.informe" ] || morir "$f no produjo informe: no hay nada que asertar"
    cat "$WORK_DIR/$base.dryrun.informe"
    senal=$(cifra "$WORK_DIR/$base.dryrun.informe" "  ... de ellos CON senal del usuario")
    if [ "$senal" -ne 0 ] && [ "$PERMITIR_SENAL_USUARIO" != 1 ]; then
      morir "$f descartaría $senal match_results CON señal del usuario. Es una decisión humana: revísala y, si se acepta, repite con PERMITIR_SENAL_USUARIO=1"
    fi
  done

  titulo "Paso 4b — enclavamiento: cohortes SELLADAS afectadas"
  # Se comprueba con las DOS copias ya ensayadas y ANTES de confirmar la
  # primera: commitear la primera con la segunda condenada a abortar deja
  # `job_ref` apuntando a otras ofertas, y core0025 los hace inmutables.
  local afectados
  afectados=$(psql_valor "$SQL_ENCLAVAMIENTO")
  entero "$afectados" "pares de cohortes selladas afectados"
  printf 'pares de cohortes SELLADAS afectados: %s\n' "$afectados"
  [ "$afectados" -eq 0 ] ||
    morir "hay $afectados pares de cohortes selladas afectados: carga una cohorte NUEVA con los refs canónicos y retira la vieja del gate"

  titulo "Paso 4c — en firme (IRREVERSIBLE a partir de aquí)"
  for f in $COPIAS_SQL; do
    base=${f%.sql}
    sed 's/^ROLLBACK;$/COMMIT;/' "$SCRIPTS_DIR/$f" >"$WORK_DIR/$base.commit.sql"
    [ "$(grep -c '^COMMIT;$' "$WORK_DIR/$base.commit.sql")" -eq 1 ] ||
      morir "$base.commit.sql no tiene exactamente un COMMIT;"
    psql_archivo "$WORK_DIR/$base.commit.sql" "$WORK_DIR/$base.firme.txt" ||
      morir "$f falló EN FIRME. Si era la segunda copia, la primera ya está confirmada: RESTAURA el volcado del Paso 2 antes de arrancar nada"
    informe "$WORK_DIR/$base.firme.txt" >"$WORK_DIR/$base.firme.informe"
    cat "$WORK_DIR/$base.firme.informe"
    [ "$(cat "$WORK_DIR/$base.dryrun.informe")" = "$(cat "$WORK_DIR/$base.firme.informe")" ] ||
      morir "el informe en firme de $f DIFIERE del ensayo: algo escribió entre medias (revisa el Paso 1)"
  done
}

# --------------------------------------------------------------------------
# Paso 5 — La otra mitad: `canonical_refs` con la imagen nueva, one-shot
# --------------------------------------------------------------------------
core_run() {
  [ -n "${CORE_NET:-}" ] || {
    CORE_NET=$("$DOCKER" inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
      swissjob-core-migrate 2>/dev/null | awk '{print $1}')
    [ -n "$CORE_NET" ] || morir "no se pudo descubrir la red del core: pásala en CORE_NET"
  }
  # CORE_DSN pisa el DSN de los env-file. Vacío en la maniobra real; obligatorio en el
  # ensayo, donde apuntar a producción sería escribir en la base viva.
  # shellcheck disable=SC2086
  "$DOCKER" run --rm --network "$CORE_NET" \
    --env-file "$ENV_CORE" --env-file "$ENV_CORE_ADMIN" -e CORE_ENV=prod \
    ${CORE_DSN:+-e CORE_DATABASE_URL=$CORE_DSN} "$CORE_IMAGE" "$@"
}

paso5_canonical_refs() {
  titulo "Paso 5 — canonical_refs (one-shot, escritores aún parados)"
  local modulo=jobhunt_core.shadow.canonical_refs
  core_run python -m "$modulo" --dry-run >"$WORK_DIR/canonical_refs.dryrun.json" ||
    morir "canonical_refs --dry-run falló"
  cat "$WORK_DIR/canonical_refs.dryrun.json"
  core_run python -m "$modulo" >"$WORK_DIR/canonical_refs.firme.json" ||
    morir "canonical_refs falló al aplicar"
  cat "$WORK_DIR/canonical_refs.firme.json"

  # El JSON de la aplicación tiene que cuadrar con el del ensayo salvo la
  # bandera `dry_run`. Se comparan los ficheros sin esa línea: un `jq` que el
  # NAS puede no tener no es una dependencia que valga la pena.
  local seco firme
  seco=$(grep -v '"dry_run"' "$WORK_DIR/canonical_refs.dryrun.json")
  firme=$(grep -v '"dry_run"' "$WORK_DIR/canonical_refs.firme.json")
  [ "$seco" = "$firme" ] ||
    morir "el JSON de canonical_refs en firme no cuadra con el del ensayo: revisa el Paso 1 y RESTAURA el volcado"

  # Idempotencia declarada por el runbook: re-ejecutarlo devuelve ceros.
  core_run python -m "$modulo" --dry-run >"$WORK_DIR/canonical_refs.idem.json" ||
    morir "la re-ejecución en seco de canonical_refs falló"
  grep -q '"filas_canonizadas_en_legacy": 0' "$WORK_DIR/canonical_refs.idem.json" ||
    morir "canonical_refs NO quedó idempotente (queda mapa por canonizar): RESTAURA el volcado del Paso 2"
}

# --------------------------------------------------------------------------
# Paso 6 — Verificar ANTES de dejar entrar a nadie (aserciones, no vistazo)
# --------------------------------------------------------------------------
paso6_verificar() {
  titulo "Paso 6 — verificación con aserciones"
  medir despues
  local f base clones=0 slots_clones=0 v
  for f in $COPIAS_SQL; do
    base=${f%.sql}
    v=$(cifra "$WORK_DIR/$base.firme.informe" "clones fusionados"); clones=$((clones + v))
    v=$(cifra "$WORK_DIR/$base.firme.informe" "sombra: slots de clones"); slots_clones=$((slots_clones + v))
  done
  printf 'informes en firme: clones fusionados=%s · sombra: slots de clones=%s\n' "$clones" "$slots_clones"

  # (a) Los slots de los clones quedan huérfanos (su fila de `jobs` desaparece)
  #     y los de los supervivientes se reapuntan al hash canónico: la subida es
  #     EXACTAMENTE la suma de «sombra: slots de clones». Si sube en miles, el
  #     PASO 7c de los scripts no hizo su trabajo.
  [ "$SLOTS_despues" -eq "$((SLOTS_antes + slots_clones))" ] ||
    morir "(a) slots huérfanos: $SLOTS_antes → $SLOTS_despues, esperado $((SLOTS_antes + slots_clones))"
  # (b) Ningún juicio puede dejar de resolver.
  [ "$RESUELVEN_despues" -eq "$JUICIOS_despues" ] ||
    morir "(b) juicios que no resuelven: $JUICIOS_despues juicios, $RESUELVEN_despues resuelven"
  # (c) Los pares con sus DOS refs resueltos no pueden BAJAR.
  [ "$PARES_despues" -ge "$PARES_antes" ] ||
    morir "(c) pares con los dos refs: $PARES_antes → $PARES_despues (BAJA)"
  # (d) `jobs` baja exactamente en los clones fusionados.
  [ "$JOBS_despues" -eq "$((JOBS_antes - clones))" ] ||
    morir "(d) jobs: $JOBS_antes → $JOBS_despues, esperado $((JOBS_antes - clones))"
  printf 'las cuatro invariantes del Paso 6 cuadran.\n'
}

# --------------------------------------------------------------------------
# Paso 7 — Smoke, DESPUÉS del Recreate de Container Station
# --------------------------------------------------------------------------
smoke() {
  titulo "Paso 7 — smoke"
  [ -f "$ESTADO" ] || morir "no hay $ESTADO: el smoke necesita la release medida en el Paso 3"
  # shellcheck disable=SC1090
  . "$ESTADO"
  [ -n "${RELEASE_ESPERADA:-}" ] || morir "$ESTADO no trae RELEASE_ESPERADA (Paso 3)"

  local vivos vivo
  vivos=$("$DOCKER" ps --format '{{.Names}}\t{{.Status}}')
  printf '%s\n' "$vivos"
  for vivo in $ESCRITORES; do
    printf '%s\n' "$vivos" | awk -F'\t' -v n="$vivo" '$1==n && $2 ~ /^Up/ {ok=1} END {exit !ok}' ||
      morir "$vivo no está Up tras el Recreate"
  done

  # UN solo parser, dentro del contenedor que responde: compara /v1/ready con
  # /v1/health (de donde sale el head esperado, medido allí y no copiado aquí)
  # y con la release horneada del Paso 3.
  if ! "$DOCKER" exec -i "$CORE_API" python - "$READY_STATUS_ESPERADO" "$RELEASE_ESPERADA" <<'PY'
import json, sys, urllib.request

estado_ok, release_esperada = sys.argv[1], sys.argv[2]
leer = lambda ruta: json.load(urllib.request.urlopen("http://127.0.0.1:8000" + ruta))
try:
    health, ready = leer("/v1/health"), leer("/v1/ready")
except Exception as exc:  # 503 incluido: readiness que no responde 200 es rojo
    sys.exit(f"/v1/ready no responde 200: {exc}")
print(json.dumps({"health": health, "ready": ready}, ensure_ascii=False))
fallos = [
    m
    for cond, m in (
        (ready.get("status") == estado_ok, f"status={ready.get('status')!r}, esperado {estado_ok!r}"),
        (ready.get("authoritative") is True, f"authoritative={ready.get('authoritative')!r}"),
        (ready.get("alembic") == health.get("alembic_expected"),
         f"alembic={ready.get('alembic')!r} != esperado {health.get('alembic_expected')!r}"),
        (ready.get("release") == release_esperada,
         f"release={ready.get('release')!r} != la del Paso 3 {release_esperada!r}"),
    )
    if not cond
]
if fallos:
    sys.exit("/v1/ready no cumple el contrato: " + "; ".join(fallos))
PY
  then
    morir "el smoke de /v1/ready no pasa: NO se abre a nadie"
  fi

  "$DOCKER" logs --tail 50 swissjob-core-capture || true
  "$DOCKER" logs --tail 50 swissjob-worker || true
  printf '\nsmoke OK — release %s, %s autoritativo.\n' "$RELEASE_ESPERADA" "$READY_STATUS_ESPERADO"
}

# --------------------------------------------------------------------------
main() {
  mkdir -p "$WORK_DIR"
  case "${1:-}" in
    cutover)
      if [ "$ENSAYO" = 1 ]; then
        [ "$PG_DB" != "$PG_DB_PROD" ] || morir "ENSAYO=1 con PG_DB en la base de producción ($PG_DB_PROD)"
        [ -n "$CORE_DSN" ] || morir "ENSAYO=1 exige CORE_DSN: sin él el Paso 5 escribiría en la base viva"
        case "$CORE_DSN" in
          *"/$PG_DB_PROD") morir "ENSAYO=1 con CORE_DSN apuntando a la base de producción" ;;
        esac
      fi
      : >"$ESTADO"
      paso1_parar
      paso2_backup
      paso3_imagenes
      medir antes          # la referencia del Paso 6 se MIDE aquí, no se copia
      paso4_copias
      paso5_canonical_refs
      paso6_verificar
      printf '\nevidencia (informes, JSON y stderr de cada psql): %s\n' "$WORK_DIR"
      printf 'Pasos 1–6 OK. Ahora el Recreate de Container Station y después: %s smoke\n' "$0"
      ;;
    smoke) smoke ;;
    *) morir "uso: $0 cutover|smoke" ;;
  esac
}

main "$@"

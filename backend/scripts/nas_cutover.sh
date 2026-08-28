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
# LO QUE CORRIGIÓ LA AUDITORÍA R4 (cinco P1, todos sobre este fichero):
#   P1-1  el aislamiento del ENSAYO comparaba el DSN por SUFIJO, y una URL con
#         `?query`, `#fragmento` o percent-encoding lo esquivaba y llegaba al
#         Paso 5 EN FIRME sobre la base viva. Ahora hay UNA forma de DSN
#         admitida (parseada, no comparada) y, antes de cualquier escritura, una
#         SONDA que exige que el core y `psql` vean la MISMA base del MISMO
#         servidor.
#   P1-2  el enclavamiento 4b era un SEGUNDO oráculo aproximado («¿algún lado
#         del par aparece en alguna de las tres fuentes?») y en local contaba 67
#         pares cuando los mapas reales de G3/G6 remapean 0: el procedimiento
#         habría parado sin motivo, quizá para siempre. Ahora lo declara CADA
#         ensayo en seco desde su PROPIA tabla de supervivientes.
#   P1-3  el Paso 6 comparaba CANTIDADES: una pérdida se compensaba con una
#         ganancia distinta y las cuatro fórmulas pasaban mientras el par
#         positivo conocido dejaba de resolver. Ahora se compara por IDENTIDAD
#         (manifiestos ordenados de `pair_id` y `set_id|job_ref`, y los hashes
#         exactos que declaran los dry-runs).
#   P1-4  la copia era SQL plano y el restore documentado (`psql < volcado`)
#         devolvía 0 dejando una MEZCLA pre/post. Ahora la copia es `-Fc`,
#         verificada con el MISMO `pg_restore` que la restaurará, sellada con
#         sha256 + manifiesto, y hay un subcomando `restaurar` todo-o-nada.
#   P1-5  el smoke aceptaba `Up 3 minutes (unhealthy)` porque miraba si el texto
#         empezaba por `Up`, no exigía frontend y solo sondaba la API del core.
#         Ahora el estado es estructurado (`docker inspect`), `healthy` es
#         obligatorio para todo contenedor que tenga healthcheck, se exige el
#         frontend, se contrastan los IDs de las tres imágenes y hay sondas
#         acotadas de Celery y del slot de la sombra.
#
# CIFRAS: este script NO lleva ninguna constante del corpus. Mide el estado
# ANTES, lee las cifras de los informes en seco y aserta el estado DESPUÉS
# contra lo que él mismo midió. Las del NAS serán otras que las locales.
#
#   Uso:  ./nas_cutover.sh cutover           # Pasos 1–6 (el 4 en firme es irreversible)
#         ./nas_cutover.sh smoke             # Paso 7, DESPUÉS del Recreate de la UI
#         ./nas_cutover.sh restaurar [dump]  # marcha atrás: todo-o-nada, verificada
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
# Qué contenedor corre QUÉ imagen. El smoke exige presencia, estado y —para los
# seis— que el ID de imagen sea EXACTAMENTE el que cargó el Paso 3: una imagen
# equivocada pero saludable pasaba antes sin atestación (R4 P1-5). El frontend
# está aquí y NO en ESCRITORES: no se para (no escribe), pero sin él no hay UI y
# el smoke no puede decir que el servicio está abierto.
: "${SERVICIOS_IMAGEN:=swissjob-backend=swissjob-backend:prod swissjob-worker=swissjob-backend:prod swissjob-core-api=swissjob-core:prod swissjob-core-worker=swissjob-core:prod swissjob-core-capture=swissjob-core:prod swissjob-frontend=swissjob-frontend:prod}"
# Sondas ACOTADAS para los procesos sin healthcheck: `contenedor=app_celery`.
# `ps` no existe en estas imágenes (comprobado), así que la sonda de proceso es
# el ping dirigido de Celery — que además es funcional.
: "${SONDAS_CELERY:=swissjob-core-worker=jobhunt_core.celery_app swissjob-worker=celery_app}"
: "${SONDA_CELERY_TIMEOUT:=15}"
# Sonda de PROGRESO del CDC: el slot lógico de la sombra tiene que estar ACTIVO
# (hay un consumidor conectado) y no acumular WAL sin parar. Vacío = no medirlo
# (despliegue sin Fase B).
: "${SLOT_SOMBRA:=jobhunt_shadow}"
: "${SLOT_ESPERA:=5}"
: "${SLOT_LAG_MAX:=16777216}"
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
# Host que el CORE_DSN del ensayo puede nombrar. Es el alias del contenedor de
# Postgres dentro de la red del core, no el del host.
: "${CORE_DSN_HOST:=postgres}"
# Base de MANTENIMIENTO desde la que se renombra/crea la de producción: no se
# puede hacer ninguna de las dos cosas estando conectado a ella.
: "${PG_MAINT_DB:=postgres}"
# Módulo que imprime la identidad de la base a la que el core se conecta DE
# VERDAD (resolviendo el DSN igual que el one-shot del Paso 5).
: "${MODULO_IDENTIDAD:=jobhunt_core.shadow.identidad_destino}"
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

# Contra la base de MANTENIMIENTO: renombrar o crear la base de producción no se
# puede hacer estando conectado a ella.
psql_maint() { # <sql>
  "$DOCKER" exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_MAINT_DB" \
    -v ON_ERROR_STOP=1 -X -q -A -t -c "$1" </dev/null
}

# Un conjunto de IDENTIDADES, ordenado con la colación de C para que `comm` y
# `cmp` sean deterministas entre invocaciones y entre servidores.
psql_lineas() { # <sql> <fichero>
  psql_valor "$1" >"$2.crudo" || return 1
  LC_ALL=C sort "$2.crudo" >"$2"
  rm -f "$2.crudo"
}

# Las filas `concepto|filas` del informe, descartando etiquetas de comando y las
# líneas `IDENT|clase|hash` (tres campos, el último no numérico).
informe() { grep -E '^[^|]+\|[0-9]+$' "$1" || true; }

# Las identidades que un informe DECLARA: `IDENT|<clase>|<hash>`.
identidades() { # <fichero crudo> <clase>
  awk -F'|' -v c="$2" '$1=="IDENT" && $2==c {print $3}' "$1" | LC_ALL=C sort -u
}

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

# Nombre de variable a partir de un tag de imagen (`swissjob-core:prod` →
# `swissjob_core_prod`): lo que sigue se pasa a `eval`, así que solo alfanumérico.
identificador() { printf '%s' "$1" | tr -c 'A-Za-z0-9' '_'; }

# --------------------------------------------------------------------------
# Aislamiento del ENSAYO (auditoría R4 P1-1)
#
# La guarda anterior rechazaba las cadenas que TERMINABAN en `/<base de prod>`.
# Una URL de PostgreSQL válida admite `?parámetros`, y `…/swissjobhunter?ssl=
# require` pasaba y llegaba al Paso 5 EN FIRME sobre la base viva. Lo mismo con
# `#fragmento` y con percent-encoding (`swissjobhunte%72`).
#
# Aquí no se compara: se PARSEA, y solo hay UNA forma admitida
#
#   <esquema>://[usuario[:clave]@]<host>[:puerto]/<base>[?clave=valor&…]
#
# Cualquier otra cosa —fragmentos, rutas con más de un segmento, parámetros que
# puedan REDEFINIR el destino (`dbname`, `host`, `service`…)— se rechaza. Lo no
# parseable también: fallar cerrado no cuesta nada, un ensayo que escribe en
# producción sí.
# --------------------------------------------------------------------------
_PARAMS_DSN_PERMITIDOS=" ssl sslmode sslrootcert sslcert sslkey connect_timeout application_name target_session_attrs "

# Percent-decoding de un segmento ya validado como `[A-Za-z0-9._~-]` o `%HH`.
decodificar_dsn() { printf '%b' "${1//%/\\x}"; }

# Imprime «host|base» del DSN; devuelve 1 si no es la forma admitida.
partes_dsn() { # <dsn>
  local dsn=$1 resto host puerto base consulta par clave
  case "$dsn" in *'#'*) return 1 ;; esac        # libpq no usa fragmentos
  case "$dsn" in *'\'*|*' '*) return 1 ;; esac  # ni barras invertidas ni espacios
  case "$dsn" in
    postgresql://*|postgres://*|postgresql+asyncpg://*|postgresql+psycopg://*) ;;
    *) return 1 ;;
  esac
  resto=${dsn#*://}
  case "$resto" in *@*) resto=${resto##*@} ;; esac   # userinfo fuera (clave con `@` incluida)
  case "$resto" in */*) ;; *) return 1 ;; esac       # sin `/base` no hay base que validar
  host=${resto%%/*}
  base=${resto#*/}
  puerto=""
  case "$host" in *:*) puerto=${host##*:}; host=${host%%:*} ;; esac
  [ -z "$puerto" ] || case "$puerto" in ''|*[!0-9]*) return 1 ;; esac
  case "$host" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
  consulta=""
  case "$base" in *'?'*) consulta=${base#*\?}; base=${base%%\?*} ;; esac
  case "$base" in *'/'*) return 1 ;; esac            # `/a/b` no es un nombre de base
  [ -n "$base" ] || return 1
  # El nombre de base solo puede traer caracteres «no reservados» o %HH bien
  # formado: así el decodificado no puede fabricar separadores.
  [[ $base =~ ^([A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+$ ]] || return 1
  base=$(decodificar_dsn "$base")
  case "$base" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
  # Parámetros: solo los que NO pueden cambiar el destino.
  if [ -n "$consulta" ]; then
    local IFS='&'
    for par in $consulta; do
      clave=${par%%=*}
      case "$par" in *=*) ;; *) return 1 ;; esac
      case "$_PARAMS_DSN_PERMITIDOS" in *" $clave "*) ;; *) return 1 ;; esac
    done
  fi
  printf '%s|%s' "$host" "$base"
}

guarda_ensayo() {
  [ "$ENSAYO" = 1 ] || return 0
  [ "$PG_DB" != "$PG_DB_PROD" ] || morir "ENSAYO=1 con PG_DB en la base de producción ($PG_DB_PROD)"
  [ -n "$CORE_DSN" ] || morir "ENSAYO=1 exige CORE_DSN: sin él el Paso 5 escribiría en la base viva"
  local partes host base
  partes=$(partes_dsn "$CORE_DSN") ||
    morir "ENSAYO=1 con un CORE_DSN que no es la ÚNICA forma admitida (esquema://[usuario[:clave]@]host[:puerto]/base[?parámetros seguros]): '$CORE_DSN'"
  host=${partes%%|*}; base=${partes#*|}
  [ "$base" != "$PG_DB_PROD" ] ||
    morir "ENSAYO=1 con CORE_DSN apuntando a la base de producción ($PG_DB_PROD): '$CORE_DSN'"
  [ "$base" = "$PG_DB" ] ||
    morir "ENSAYO=1 con CORE_DSN en la base '$base' y psql en '$PG_DB': el Paso 5 y los Pasos 4/6 medirían bases DISTINTAS"
  [ "$host" = "$CORE_DSN_HOST" ] ||
    morir "ENSAYO=1 con CORE_DSN en el host '$host' y no en el esperado '$CORE_DSN_HOST' (ajústalo con CORE_DSN_HOST=)"
  printf 'ENSAYO validado: el core irá a %s@%s y psql a %s\n' "$base" "$host" "$PG_DB"
}

# --------------------------------------------------------------------------
# Sonda de destino (auditoría R4 P1-1) — ANTES de cualquier escritura
#
# La guarda de arriba mira una CADENA. Esto mira la BASE: el módulo del Paso 5
# resuelve su DSN igual que el one-shot y publica la identidad de la base a la
# que se conecta de verdad; `psql` publica la suya. Si no coinciden, el Paso 4
# escribiría en una base y el Paso 5 en otra — y el Paso 6 verificaría la
# equivocada.
#
# La identidad es «base|oid|arranque del postmaster en UTC»: no necesita
# privilegios, distingue dos bases del mismo servidor y dos servidores con la
# misma base, y `to_char` la deja libre de DateStyle/TimeZone (dos clientes
# distintos formatean el timestamptz de forma distinta).
# --------------------------------------------------------------------------
SQL_IDENTIDAD="SELECT current_database()
 || '|' || (SELECT oid::text FROM pg_database WHERE datname = current_database())
 || '|' || to_char(pg_postmaster_start_time() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')"

sonda_destino() {
  titulo "Sonda de destino — el core y psql tienen que ver la MISMA base"
  local por_psql por_core
  por_psql=$(psql_valor "$SQL_IDENTIDAD") ||
    morir "no se pudo leer la identidad de la base por psql"
  por_core=$(core_run python -m "$MODULO_IDENTIDAD" "$SQL_IDENTIDAD") ||
    morir "no se pudo leer la identidad de la base por el DSN del core ($MODULO_IDENTIDAD)"
  por_core=$(printf '%s' "$por_core" | tr -d '\r' | grep -v '^$' | tail -n 1)
  printf 'psql: %s\ncore: %s\n' "$por_psql" "$por_core"
  case "$por_psql" in
    "$PG_DB|"*) ;;
    *) morir "psql no está en la base que dice PG_DB ($PG_DB): '$por_psql'" ;;
  esac
  [ "$por_psql" = "$por_core" ] ||
    morir "el core NO apunta a la base de psql: core='$por_core' psql='$por_psql'. Con ENSAYO=1 revisa CORE_DSN; sin él, los --env-file de $ENV_CORE"
}

# --------------------------------------------------------------------------
# Paso 1 — Detener todo escritor y proyector
# --------------------------------------------------------------------------
parar_escritores() {
  [ -n "${ESCRITORES// /}" ] || morir "ESCRITORES vacío: la aserción sería trivialmente cierta"
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
      morir "$vivo sigue corriendo: no se puede escribir en la base con escritores vivos"
    fi
  done
  printf 'escritores parados: %s\n' "$ESCRITORES"
}

paso1_parar() {
  titulo "Paso 1 — detener escritores y proyector"
  if [ "$ENSAYO" = 1 ]; then
    printf '⚠ ENSAYO contra %s: no se para nada y NO es la maniobra real.\n' "$PG_DB"
    return 0
  fi
  parar_escritores
}

# --------------------------------------------------------------------------
# Paso 2 — Copia de seguridad RESTAURABLE, con `public` Y `jobhunt`
#
# Auditoría R4 P1-4. La copia era SQL plano y §7 la restauraba canalizándola a
# `psql` sobre la base poblada: la tubería devolvía 0, escupía «ya existe» y
# dejaba datos de los DOS estados mezclados. Una copia que no restaura no es una
# copia, y el Paso 4c es irreversible.
#
# Ahora: formato `custom` (-Fc) —el único que `pg_restore` aplica en una sola
# transacción—, verificado con el MISMO `pg_restore` que lo restaurará, sellado
# con sha256 y con un manifiesto que el subcomando `restaurar` usa para
# comprobar que la vuelta atrás VOLVIÓ.
#
# Y del volcado se quitó el `-n public -n jobhunt`: comprobado el 2026-08-28
# contra el corpus real (SOLO LECTURA), un volcado por esquemas NO lleva
# `CREATE EXTENSION vector` —las extensiones no son objetos de esquema— y sí
# lleva un `DROP SCHEMA public` que la propia extensión bloquea. Con la base
# entera el archivo es autosuficiente: extensiones incluidas.
# --------------------------------------------------------------------------
paso2_backup() {
  titulo "Paso 2 — copia de seguridad restaurable (public + jobhunt)"
  mkdir -p "$BACKUP_DIR"
  local sello parcial final toc n_public n_jobhunt suma
  sello=$(date +%Y%m%d-%H%M%S)
  # El temporal va al MISMO almacén que la copia final: `/tmp` en el NAS es un
  # ramdisk pequeño y un volcado del corpus no cabe.
  parcial="$BACKUP_DIR/.pre_canonizacion_$sello.dump.parcial"
  final="$BACKUP_DIR/pre_canonizacion_$sello.dump"
  toc="$BACKUP_DIR/.pre_canonizacion_$sello.toc"

  # Sin `-t`: un TTY reescribe el volcado. Y sin tubería, para no perder el
  # estado de pg_dump.
  "$DOCKER" exec "$PG_CONTAINER" \
    pg_dump -U "$PG_USER" -Fc -Z 6 "$PG_DB" >"$parcial" ||
    morir "pg_dump falló: no hay copia de seguridad y el Paso 4 es irreversible"

  # La verificación se hace con la herramienta que va a RESTAURAR: si el archivo
  # no se puede leer entero, no hay copia. (`gzip -t` verificaba el envoltorio
  # de un formato que además no era restaurable de una pieza.)
  "$DOCKER" exec -i "$PG_CONTAINER" pg_restore -l <"$parcial" >"$toc" ||
    morir "pg_restore -l no puede leer la copia: está corrupta o truncada"
  n_public=$(grep -cE '^[0-9]+; [0-9]+ [0-9]+ TABLE public ' "$toc" || true)
  n_jobhunt=$(grep -cE '^[0-9]+; [0-9]+ [0-9]+ TABLE jobhunt ' "$toc" || true)
  printf 'tablas en el volcado: public=%s jobhunt=%s\n' "$n_public" "$n_jobhunt"
  [ "$n_public" -gt 0 ] || morir "el volcado no trae tablas de public"
  # Si el rol no puede leer `jobhunt`, la copia no sirve para ESTA maniobra:
  # hay que repetirla con la credencial de .env.core.admin.prod.
  [ "$n_jobhunt" -gt 0 ] || morir "el volcado no trae tablas de jobhunt: repítelo con la credencial admin del core"

  # sha256 con el contenedor de Postgres (Debian: `sha256sum` seguro; el NAS
  # puede no tenerlo) — es el sello que `restaurar` comprueba antes de tocar nada.
  suma=$("$DOCKER" exec -i "$PG_CONTAINER" sha256sum <"$parcial" | awk '{print $1}')
  [ -n "$suma" ] || morir "no se pudo calcular el sha256 de la copia"

  mv "$parcial" "$final"   # el nombre definitivo solo lo gana un volcado verificado
  rm -f "$toc"
  {
    printf 'DUMP_SHA256=%s\n' "$suma"
    printf 'DUMP_TABLAS_PUBLIC=%s\n' "$n_public"
    printf 'DUMP_TABLAS_JOBHUNT=%s\n' "$n_jobhunt"
    printf 'DUMP_PG_DB=%s\n' "$PG_DB"
  } >"$final.manifiesto"
  printf 'BACKUP=%s\n' "$final" >>"$ESTADO"
  printf 'copia verificada: %s (sha256 %s)\n' "$final" "$suma"
}

# El manifiesto pre-corte se cierra con la medición ANTES: es contra estas
# identidades contra lo que `restaurar` comprueba que la vuelta atrás VOLVIÓ.
sellar_manifiesto() {
  local final
  # shellcheck disable=SC1090
  . "$ESTADO"
  final=${BACKUP:-}
  [ -n "$final" ] || morir "no hay BACKUP en $ESTADO: el Paso 2 no dejó copia"
  {
    printf 'MEDIDA_SLOTS=%s\n' "$SLOTS_antes"
    printf 'MEDIDA_JOBS=%s\n' "$JOBS_antes"
    printf 'MEDIDA_PARES=%s\n' "$PARES_antes"
    printf 'MEDIDA_JUICIOS=%s\n' "$JUICIOS_antes"
    printf 'MEDIDA_RESUELVEN=%s\n' "$RESUELVEN_antes"
  } >>"$final.manifiesto"
  cp "$WORK_DIR/manifiesto.pares.antes" "$final.pares"
  cp "$WORK_DIR/manifiesto.juicios.antes" "$final.juicios"
  printf 'manifiesto pre-corte sellado junto a la copia: %s.manifiesto\n' "$final"
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
  local tar img release id
  for tar in $TARS; do
    [ -f "$BASE_DIR/$tar" ] || morir "falta $BASE_DIR/$tar (§5.2)"
  done
  for img in $IMAGENES; do "$DOCKER" rmi "$img" >/dev/null 2>&1 || true; done
  for tar in $TARS; do "$DOCKER" load -i "$BASE_DIR/$tar" || morir "docker load falló con $tar"; done
  for img in $IMAGENES; do
    "$DOCKER" image inspect "$img" >/dev/null 2>&1 || morir "$img no quedó cargada"
    # El ID exacto de cada imagen: el smoke exige que los contenedores recreados
    # corran ESTAS y no otras (R4 P1-5).
    id=$("$DOCKER" image inspect -f '{{.Id}}' "$img") || morir "no se pudo leer el ID de $img"
    [ -n "$id" ] || morir "$img no publica ID"
    printf 'IMAGEN_ID_%s=%s\n' "$(identificador "$img")" "$id" >>"$ESTADO"
    printf 'imagen cargada: %s → %s\n' "$img" "$id"
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
# Paso 6 (medición) — cantidades E IDENTIDADES, ejecutables antes y después
#
# Auditoría R4 P1-3: las cuatro fórmulas comparaban CANTIDADES. El auditor mutó
# las source listings de un par positivo conocido a otro par distinto: el par
# que importaba dejó de resolver, otro empezó a resolver, y las cuatro pasaron.
# Por eso además de las cantidades se guardan MANIFIESTOS ordenados por
# identidad (`pair_id`, `set_id|job_ref`) y se exige que ninguna identidad
# resoluble ANTES deje de resolver DESPUÉS.
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
# IDENTIDADES, no cardinalidades: QUÉ pares resuelven sus dos refs y QUÉ juicios
# resuelven. `p.id::text` y `j.set_id::text` son además los discriminantes que
# usan los dobles para no confundir estas consultas con las de arriba.
SQL_MANIFIESTO_PARES="SELECT p.id::text
 FROM jobhunt.labeled_dedup_pairs p
 CROSS JOIN LATERAL (SELECT
   EXISTS (SELECT 1 FROM jobhunt.source_listings l
           JOIN jobhunt.sources src ON src.id = l.source_id
           JOIN jobhunt.source_listing_incarnations i ON i.source_listing_id = l.id
           WHERE src.name LIKE 'legacy:%' AND l.external_id = p.job_ref_a) AS a,
   EXISTS (SELECT 1 FROM jobhunt.source_listings l
           JOIN jobhunt.sources src ON src.id = l.source_id
           JOIN jobhunt.source_listing_incarnations i ON i.source_listing_id = l.id
           WHERE src.name LIKE 'legacy:%' AND l.external_id = p.job_ref_b) AS b) r
 WHERE r.a AND r.b"
SQL_MANIFIESTO_JUICIOS="SELECT j.set_id::text || '|' || j.job_ref
 FROM jobhunt.labeled_judgments j
 WHERE EXISTS (
   SELECT 1 FROM jobhunt.source_listings l
   JOIN jobhunt.sources src ON src.id = l.source_id
   JOIN jobhunt.source_listing_incarnations i ON i.source_listing_id = l.id
   WHERE src.name LIKE 'legacy:%' AND l.external_id = j.job_ref)"

medir() { # deja SLOTS/JOBS/PARES/JUICIOS_TOTAL/JUICIOS_RESUELVEN con el sufijo $1
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
  psql_lineas "$SQL_MANIFIESTO_PARES" "$WORK_DIR/manifiesto.pares.$1" ||
    morir "no se pudo medir el manifiesto de pares ($1)"
  psql_lineas "$SQL_MANIFIESTO_JUICIOS" "$WORK_DIR/manifiesto.juicios.$1" ||
    morir "no se pudo medir el manifiesto de juicios ($1)"
  printf 'medición %s: slots_huerfanos=%s jobs=%s pares_con_los_dos_refs=%s juicios=%s resuelven=%s (manifiestos: %s pares · %s juicios)\n' \
    "$1" "$slots" "$jobs" "$pares" "$juicios_total" "$juicios_resuelven" \
    "$(wc -l <"$WORK_DIR/manifiesto.pares.$1")" "$(wc -l <"$WORK_DIR/manifiesto.juicios.$1")"
}

# Ninguna identidad que estaba puede faltar. `comm -23` sobre dos ficheros ya
# ordenados con la colación de C: lo que sobra en el primero es lo que se perdió.
sin_perdidas() { # <fichero antes> <fichero despues> <qué son>
  local perdidas
  perdidas=$(LC_ALL=C comm -23 "$1" "$2")
  [ -z "$perdidas" ] ||
    morir "IDENTIDAD: $(printf '%s\n' "$perdidas" | wc -l) $3 resolvían ANTES y ya no. Los primeros: $(printf '%s\n' "$perdidas" | head -n 5 | tr '\n' ' ')"
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

  titulo "Paso 4b — enclavamiento: lo que declara CADA ensayo en seco"
  # Se comprueba con las DOS copias ya ensayadas y ANTES de confirmar la
  # primera: commitear la primera con la segunda condenada a abortar deja
  # `job_ref` apuntando a otras ofertas, y core0025 los hace inmutables.
  #
  # Auditoría R4 P1-2. Antes esto era una consulta PROPIA del script: contaba
  # cualquier par de cohorte sellada con un lado en cualquiera de las tres
  # fuentes, sin preguntar si G3/G6 iba a cambiar ese ref. Era un SEGUNDO
  # oráculo, distinto del mapa que los propios SQL calculan: en local contaba 67
  # pares cuando los mapas remapean 0 — un rojo falso que podía bloquear la
  # maniobra para siempre. Ahora la cifra la DECLARA cada script desde su propia
  # tabla de supervivientes, y aquí solo se exige cero. Si un script no la
  # declara, `cifra` para: no se confirma nada sin ese dato.
  local afectados total=0
  for f in $COPIAS_SQL; do
    base=${f%.sql}
    afectados=$(cifra "$WORK_DIR/$base.dryrun.informe" "enclavamiento: refs de cohortes SELLADAS")
    entero "$afectados" "refs de cohortes selladas que remapea $f"
    printf '%s declara %s refs de cohortes SELLADAS a remapear\n' "$f" "$afectados"
    total=$((total + afectados))
  done
  [ "$total" -eq 0 ] ||
    morir "los ensayos en seco declaran $total refs de cohortes SELLADAS que remapearían: carga una cohorte NUEVA con los refs canónicos y retira la vieja del gate"

  titulo "Paso 4c — en firme (IRREVERSIBLE a partir de aquí)"
  for f in $COPIAS_SQL; do
    base=${f%.sql}
    sed 's/^ROLLBACK;$/COMMIT;/' "$SCRIPTS_DIR/$f" >"$WORK_DIR/$base.commit.sql"
    [ "$(grep -c '^COMMIT;$' "$WORK_DIR/$base.commit.sql")" -eq 1 ] ||
      morir "$base.commit.sql no tiene exactamente un COMMIT;"
    psql_archivo "$WORK_DIR/$base.commit.sql" "$WORK_DIR/$base.firme.txt" ||
      morir "$f falló EN FIRME. Si era la segunda copia, la primera ya está confirmada: RESTAURA la copia del Paso 2 ($0 restaurar) antes de arrancar nada"
    informe "$WORK_DIR/$base.firme.txt" >"$WORK_DIR/$base.firme.informe"
    cat "$WORK_DIR/$base.firme.informe"
    [ "$(cat "$WORK_DIR/$base.dryrun.informe")" = "$(cat "$WORK_DIR/$base.firme.informe")" ] ||
      morir "el informe en firme de $f DIFIERE del ensayo: algo escribió entre medias (revisa el Paso 1)"
    # Y las IDENTIDADES declaradas también: si el conjunto de hashes cambia
    # entre el ensayo y la aplicación, el Paso 6 estaría verificando otra cosa.
    local clase
    for clase in desaparece canonico; do
      [ "$(identidades "$WORK_DIR/$base.dryrun.txt" "$clase")" = "$(identidades "$WORK_DIR/$base.firme.txt" "$clase")" ] ||
        morir "las identidades «$clase» que declara $f en firme DIFIEREN de las del ensayo"
    done
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
    morir "el JSON de canonical_refs en firme no cuadra con el del ensayo: revisa el Paso 1 y RESTAURA la copia ($0 restaurar)"

  # Idempotencia declarada por el runbook: re-ejecutarlo devuelve ceros.
  core_run python -m "$modulo" --dry-run >"$WORK_DIR/canonical_refs.idem.json" ||
    morir "la re-ejecución en seco de canonical_refs falló"
  grep -q '"filas_canonizadas_en_legacy": 0' "$WORK_DIR/canonical_refs.idem.json" ||
    morir "canonical_refs NO quedó idempotente (queda mapa por canonizar): RESTAURA la copia del Paso 2 ($0 restaurar)"
}

# --------------------------------------------------------------------------
# Paso 6 — Verificar ANTES de dejar entrar a nadie (identidades, no cantidades)
# --------------------------------------------------------------------------

# Construye y ejecuta la verificación por HASH: las identidades que los dry-runs
# DECLARARON que desaparecen tienen que haber desaparecido de `public.jobs`, y
# las canónicas que declararon, estar. No se recalcula nada aquí — se comprueba
# lo que los propios SQL dijeron que iban a hacer.
verificar_identidad_de_vacantes() {
  local f base fichero sql salida hash n_desaparece=0 n_canonico=0
  fichero="$WORK_DIR/identidad.hashes"
  : >"$fichero.desaparece"; : >"$fichero.canonico"
  for f in $COPIAS_SQL; do
    base=${f%.sql}
    identidades "$WORK_DIR/$base.firme.txt" desaparece >>"$fichero.desaparece"
    identidades "$WORK_DIR/$base.firme.txt" canonico >>"$fichero.canonico"
  done
  # Nada que no sea un md5 hexadecimal entra en la SQL que se va a ejecutar.
  local clase
  for clase in desaparece canonico; do
    while read -r hash; do
      [ -n "$hash" ] || continue
      [[ $hash =~ ^[0-9a-f]{32}$ ]] ||
        morir "un dry-run declaró una identidad «$clase» que no es un md5: '$hash'"
    done <"$fichero.$clase"
  done
  n_desaparece=$(grep -c . "$fichero.desaparece" || true)
  n_canonico=$(grep -c . "$fichero.canonico" || true)

  sql="$WORK_DIR/identidad.sql"
  salida="$WORK_DIR/identidad.txt"
  {
    printf 'BEGIN;\n'
    printf 'CREATE TEMP TABLE ident_desaparece(h text) ON COMMIT DROP;\n'
    printf 'CREATE TEMP TABLE ident_canonico(h text) ON COMMIT DROP;\n'
    if [ "$n_desaparece" -gt 0 ]; then
      printf 'INSERT INTO ident_desaparece VALUES\n'
      sed "s/^/('/; s/\$/'),/" "$fichero.desaparece" | sed '$ s/,$/;/'
    fi
    if [ "$n_canonico" -gt 0 ]; then
      printf 'INSERT INTO ident_canonico VALUES\n'
      sed "s/^/('/; s/\$/'),/" "$fichero.canonico" | sed '$ s/,$/;/'
    fi
    printf "SELECT 'identidad: viejas que NO desaparecieron' AS concepto, count(*) AS filas\n"
    printf '  FROM ident_desaparece d JOIN public.jobs j ON j.hash = d.h\n'
    printf 'UNION ALL\n'
    printf "SELECT 'identidad: canonicas que NO aparecieron', count(*)\n"
    printf '  FROM ident_canonico c LEFT JOIN public.jobs j ON j.hash = c.h WHERE j.hash IS NULL;\n'
    printf 'ROLLBACK;\n'
  } >"$sql"

  psql_archivo "$sql" "$salida" || morir "la verificación por identidad de vacantes falló"
  informe "$salida" >"$salida.informe"
  local viejas canonicas
  viejas=$(cifra "$salida.informe" "identidad: viejas que NO desaparecieron")
  canonicas=$(cifra "$salida.informe" "identidad: canonicas que NO aparecieron")
  entero "$viejas" "viejas que no desaparecieron"; entero "$canonicas" "canonicas que no aparecieron"
  printf 'identidad de vacantes: %s declaradas a desaparecer (%s siguen) · %s canónicas declaradas (%s faltan)\n' \
    "$n_desaparece" "$viejas" "$n_canonico" "$canonicas"
  [ "$viejas" -eq 0 ] ||
    morir "(e) IDENTIDAD: $viejas hashes que los dry-runs declararon fusionados siguen en public.jobs. RESTAURA la copia ($0 restaurar)"
  [ "$canonicas" -eq 0 ] ||
    morir "(f) IDENTIDAD: $canonicas hashes canónicos declarados por los dry-runs NO existen en public.jobs. RESTAURA la copia ($0 restaurar)"
}

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

  # (b') y (c') por IDENTIDAD: (b) y (c) son cantidades y una pérdida se
  # compensa con una ganancia distinta (R4 P1-3). Aquí no: los `pair_id` y los
  # `set_id|job_ref` que resolvían ANTES tienen que seguir resolviendo.
  sin_perdidas "$WORK_DIR/manifiesto.pares.antes" "$WORK_DIR/manifiesto.pares.despues" "pares"
  sin_perdidas "$WORK_DIR/manifiesto.juicios.antes" "$WORK_DIR/manifiesto.juicios.despues" "juicios"
  # (e) y (f): los hashes exactos que declararon los dry-runs.
  verificar_identidad_de_vacantes
  printf 'las cuatro invariantes del Paso 6 cuadran, y también las identidades.\n'
}

# --------------------------------------------------------------------------
# Marcha atrás — la ÚNICA salida de emergencia, y tiene que funcionar
#
# Auditoría R4 P1-4. La restauración documentada (`gunzip | psql`) devolvía 0,
# escupía «ya existe» y dejaba datos de los dos estados MEZCLADOS. Aquí:
#   · se paran los CINCO escritores (no dos) y se comprueba que están parados;
#   · se verifica el sello sha256 y que `pg_restore -l` lee la copia entera;
#   · la base se RENOMBRA (no se borra) y se crea una nueva vacía con la misma
#     codificación, colación y propietario;
#   · `pg_restore --exit-on-error --single-transaction` sobre esa base vacía: o
#     entra entera, o no entra nada;
#   · y DESPUÉS se comprueba contra el manifiesto pre-corte que volvió.
#
# POR QUÉ NO `--clean --if-exists` (probado contra el corpus REAL el 2026-08-28,
# restaurando en una base DESECHABLE): sobre una base ya poblada muere en
#
#   ERROR: cannot drop inherited constraint "offer_embeddings_…_pkey"
#
# — `jobhunt.offer_embeddings_*` son PARTICIONES y `--clean` no sabe soltar sus
# constraints heredadas. Es decir: la marcha atrás con `--clean` funciona sobre
# una base vacía y falla justo en el caso que importa. Por eso se recrea la base.
# El RENOMBRE, y no un DROP, deja el estado roto recuperable mientras el operador
# no lo borre a mano: si la restauración fallara, no se ha perdido nada.
#
# Y la base nueva lleva `max_parallel_maintenance_workers = 0` mientras dura la
# restauración: el índice HNSW de `offer_embeddings` se construye en paralelo y
# pide un segmento de memoria compartida de ~64 MB que NO cabe en el `/dev/shm`
# por defecto de Docker (64 MB). Medido: con el valor por defecto la restauración
# del corpus real aborta con «could not resize shared memory segment»; con 0,
# entra entera en 13 s.
# --------------------------------------------------------------------------
restaurar() { # [fichero .dump]
  titulo "RESTAURAR — vuelta al estado previo al corte"
  local dump=${1:-} suma toc n_public n_jobhunt
  if [ -z "$dump" ] && [ -f "$ESTADO" ]; then
    # shellcheck disable=SC1090
    . "$ESTADO"
    dump=${BACKUP:-}
  fi
  [ -n "$dump" ] || morir "no sé qué restaurar: pasa el .dump, o deja el $ESTADO del cutover"
  [ -f "$dump" ] || morir "no existe la copia $dump"
  [ -f "$dump.manifiesto" ] || morir "falta $dump.manifiesto: sin manifiesto pre-corte no se puede comprobar que la vuelta VOLVIÓ"
  # shellcheck disable=SC1090
  . "$dump.manifiesto"
  [ "${DUMP_PG_DB:-}" = "$PG_DB" ] ||
    morir "el manifiesto dice que la copia es de '${DUMP_PG_DB:-}' y PG_DB es '$PG_DB': no se restaura una base en otra"

  suma=$("$DOCKER" exec -i "$PG_CONTAINER" sha256sum <"$dump" | awk '{print $1}')
  [ "$suma" = "${DUMP_SHA256:-}" ] ||
    morir "la copia NO cuadra con su sello: sha256 $suma, manifiesto ${DUMP_SHA256:-}"
  toc="$WORK_DIR/restaurar.toc"
  "$DOCKER" exec -i "$PG_CONTAINER" pg_restore -l <"$dump" >"$toc" ||
    morir "pg_restore -l no puede leer $dump: la copia está corrupta y NO hay marcha atrás por aquí"
  n_public=$(grep -cE '^[0-9]+; [0-9]+ [0-9]+ TABLE public ' "$toc" || true)
  n_jobhunt=$(grep -cE '^[0-9]+; [0-9]+ [0-9]+ TABLE jobhunt ' "$toc" || true)
  [ "$n_public" = "${DUMP_TABLAS_PUBLIC:-}" ] && [ "$n_jobhunt" = "${DUMP_TABLAS_JOBHUNT:-}" ] ||
    morir "el índice de la copia no cuadra con el manifiesto: public $n_public/${DUMP_TABLAS_PUBLIC:-}, jobhunt $n_jobhunt/${DUMP_TABLAS_JOBHUNT:-}"

  # Restaurar con escritores vivos deja el estado a medias en cuanto uno escriba.
  parar_escritores

  [ "$PG_DB" != "$PG_MAINT_DB" ] ||
    morir "PG_DB y PG_MAINT_DB son la misma base ($PG_DB): no se puede renombrar la base a la que hay que conectarse"
  local atributos enc coll ctype propietario previa
  atributos=$(psql_maint "SELECT pg_encoding_to_char(encoding) || '|' || datcollate || '|' || datctype
                          || '|' || pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$PG_DB'")
  [ -n "$atributos" ] || morir "no existe la base $PG_DB en el servidor"
  enc=${atributos%%|*}; atributos=${atributos#*|}
  coll=${atributos%%|*}; atributos=${atributos#*|}
  ctype=${atributos%%|*}; propietario=${atributos#*|}
  previa="${PG_DB}_previa_$(date +%Y%m%d%H%M%S)"

  psql_maint "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
              WHERE datname = '$PG_DB' AND pid <> pg_backend_pid()" >/dev/null ||
    morir "no se pudieron cortar las conexiones a $PG_DB"
  psql_maint "ALTER DATABASE \"$PG_DB\" RENAME TO \"$previa\"" >/dev/null ||
    morir "no se pudo apartar $PG_DB (¿queda alguna conexión abierta?): NADA se ha tocado"
  printf 'el estado roto queda APARTADO en la base %s (bórrala a mano cuando estés conforme)\n' "$previa"
  psql_maint "CREATE DATABASE \"$PG_DB\" TEMPLATE template0 ENCODING '$enc'
              LC_COLLATE '$coll' LC_CTYPE '$ctype' OWNER \"$propietario\"" >/dev/null ||
    morir "no se pudo crear $PG_DB vacía. El estado anterior sigue ENTERO en $previa: devuélvelo con ALTER DATABASE \"$previa\" RENAME TO \"$PG_DB\""
  # El índice HNSW en paralelo pide ~64 MB de memoria compartida y el /dev/shm
  # por defecto de Docker mide justo 64 MB: la restauración del corpus real
  # aborta con «could not resize shared memory segment». Sin paralelismo entra.
  psql_maint "ALTER DATABASE \"$PG_DB\" SET max_parallel_maintenance_workers = 0" >/dev/null ||
    morir "no se pudo desactivar el paralelismo de mantenimiento en $PG_DB"

  # `--single-transaction` implica `--exit-on-error`: o entra entera o no entra
  # nada. Sin `--clean`: la base está recién creada y vacía.
  "$DOCKER" exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$PG_DB" \
    --exit-on-error --single-transaction <"$dump" \
    >"$WORK_DIR/restaurar.out" 2>"$WORK_DIR/restaurar.err" || {
      cat "$WORK_DIR/restaurar.err" >&2
      morir "pg_restore FALLÓ y la base $PG_DB quedó vacía. El estado anterior sigue ENTERO en $previa: devuélvelo con ALTER DATABASE \"$PG_DB\" ... DROP y ALTER DATABASE \"$previa\" RENAME TO \"$PG_DB\". Revisa $WORK_DIR/restaurar.err"
    }
  psql_maint "ALTER DATABASE \"$PG_DB\" RESET max_parallel_maintenance_workers" >/dev/null ||
    morir "no se pudo devolver max_parallel_maintenance_workers a su valor por defecto en $PG_DB"

  titulo "RESTAURAR — comprobación contra el manifiesto pre-corte"
  medir restaurado
  local fallos=""
  [ "$SLOTS_restaurado" = "${MEDIDA_SLOTS:-}" ] || fallos="$fallos slots($SLOTS_restaurado≠${MEDIDA_SLOTS:-})"
  [ "$JOBS_restaurado" = "${MEDIDA_JOBS:-}" ] || fallos="$fallos jobs($JOBS_restaurado≠${MEDIDA_JOBS:-})"
  [ "$PARES_restaurado" = "${MEDIDA_PARES:-}" ] || fallos="$fallos pares($PARES_restaurado≠${MEDIDA_PARES:-})"
  [ "$JUICIOS_restaurado" = "${MEDIDA_JUICIOS:-}" ] || fallos="$fallos juicios($JUICIOS_restaurado≠${MEDIDA_JUICIOS:-})"
  [ "$RESUELVEN_restaurado" = "${MEDIDA_RESUELVEN:-}" ] || fallos="$fallos resuelven($RESUELVEN_restaurado≠${MEDIDA_RESUELVEN:-})"
  [ -z "$fallos" ] ||
    morir "la restauración terminó pero el estado NO es el del manifiesto pre-corte:$fallos"
  cmp -s "$dump.pares" "$WORK_DIR/manifiesto.pares.restaurado" ||
    morir "los pares que resuelven tras restaurar NO son los del manifiesto pre-corte ($dump.pares)"
  cmp -s "$dump.juicios" "$WORK_DIR/manifiesto.juicios.restaurado" ||
    morir "los juicios que resuelven tras restaurar NO son los del manifiesto pre-corte ($dump.juicios)"
  printf '\nrestauración VERIFICADA contra %s.manifiesto: cantidades e identidades coinciden.\n' "$dump"
  printf 'El estado roto sigue APARTADO en %s: bórralo a mano cuando estés conforme.\n' "$previa"
  # La base es NUEVA (otro OID), así que el slot lógico de la sombra se quedó con
  # la base apartada: `core-capture` arrancaría contra un `shadow_capture_state`
  # restaurado y un slot AUSENTE, y aborta con «continuidad WAL perdida». Es
  # correcto que aborte, pero hay que re-bootstrapearlo a mano.
  printf 'ANTES del Recreate: el slot lógico %s NO existe en la base nueva. Ejecuta el\n' "$SLOT_SOMBRA"
  printf 'rollback/replay de la sombra (jobhunt_core/shadow/RUNBOOK.md §3: truncar staging\n'
  printf '+ re-crear slot + re-backfill) o core-capture abortará con «Estado registrado\n'
  printf 'pero slot AUSENTE».\n'
  printf 'Los escritores siguen PARADOS a propósito. Arráncalos con el Recreate de Container Station.\n'
}

# --------------------------------------------------------------------------
# Paso 7 — Smoke, DESPUÉS del Recreate de Container Station
#
# Auditoría R4 P1-5. El filtro miraba si el texto de `docker ps` empezaba por
# `Up`, y `Up 3 minutes (unhealthy)` empieza por `Up`: el smoke devolvía 0 con
# el capturador enfermo. Además el frontend no estaba en la lista, los `docker
# logs … || true` no eran postcondición de nada y solo la API del core tenía
# sonda funcional. Aquí el estado es ESTRUCTURADO y todo lo de abajo es una
# condición de ejecución.
# --------------------------------------------------------------------------
estado_contenedor() { # <nombre> -> «estado|salud|imagen»
  "$DOCKER" inspect \
    -f '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}sin-healthcheck{{end}}|{{.Image}}' \
    "$1" 2>/dev/null
}

smoke_contenedores() {
  titulo "Paso 7a — estado ESTRUCTURADO de los seis contenedores"
  local par nombre imagen linea estado salud id esperado
  for par in $SERVICIOS_IMAGEN; do
    nombre=${par%%=*}; imagen=${par#*=}
    linea=$(estado_contenedor "$nombre") ||
      morir "$nombre no existe tras el Recreate (docker inspect no lo encuentra)"
    [ -n "$linea" ] || morir "$nombre no existe tras el Recreate"
    estado=${linea%%|*}; linea=${linea#*|}
    salud=${linea%%|*}; id=${linea#*|}
    printf '%-26s estado=%-11s salud=%-16s imagen=%s\n' "$nombre" "$estado" "$salud" "$id"
    [ "$estado" = running ] ||
      morir "$nombre está en estado '$estado' (se exige 'running'): NO se abre a nadie"
    case "$salud" in
      healthy|sin-healthcheck) ;;
      *) morir "$nombre tiene healthcheck y está '$salud': 'Up (unhealthy)' NO es verde (R4 P1-5)" ;;
    esac
    eval "esperado=\${IMAGEN_ID_$(identificador "$imagen"):-}"
    [ -n "$esperado" ] ||
      morir "$ESTADO no trae el ID de $imagen: repite el Paso 3, el smoke no puede atestiguar qué imagen corre"
    [ "$id" = "$esperado" ] ||
      morir "$nombre corre la imagen $id y el Paso 3 cargó $esperado para $imagen"
  done
}

smoke_sondas_acotadas() {
  titulo "Paso 7b — sondas acotadas de los procesos SIN healthcheck"
  local par nombre app salida
  for par in $SONDAS_CELERY; do
    nombre=${par%%=*}; app=${par#*=}
    salida="$WORK_DIR/sonda.$nombre.txt"
    # Ping DIRIGIDO (`-d celery@$(hostname)`): un `inspect ping` a secas lo
    # contesta CUALQUIER worker del broker, así que pasaría con este muerto.
    "$DOCKER" exec "$nombre" sh -c \
      "celery -A $app inspect ping -d celery@\$(hostname) -t $SONDA_CELERY_TIMEOUT" \
      >"$salida" 2>&1 ||
      { cat "$salida" >&2; morir "$nombre no contesta al ping dirigido de Celery"; }
    grep -q pong "$salida" || { cat "$salida" >&2; morir "$nombre respondió al ping sin «pong»"; }
    printf '%s: pong\n' "$nombre"
  done

  [ -n "$SLOT_SOMBRA" ] || { printf 'SLOT_SOMBRA vacío: no se mide el progreso del CDC.\n'; return 0; }
  local activo antes despues
  activo=$(psql_valor "SELECT active FROM pg_replication_slots WHERE slot_name = '$SLOT_SOMBRA'")
  [ "$activo" = t ] ||
    morir "el slot $SLOT_SOMBRA no está ACTIVO (active='$activo'): swissjob-core-capture no está consumiendo el WAL y la sombra se queda atrás en silencio"
  antes=$(psql_valor "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint FROM pg_replication_slots WHERE slot_name = '$SLOT_SOMBRA'")
  sleep "$SLOT_ESPERA"
  despues=$(psql_valor "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint FROM pg_replication_slots WHERE slot_name = '$SLOT_SOMBRA'")
  entero "$antes" "retraso del slot"; entero "$despues" "retraso del slot"
  printf 'slot %s: activo, retraso %s → %s bytes en %ss\n' "$SLOT_SOMBRA" "$antes" "$despues" "$SLOT_ESPERA"
  # Progreso: o el capturador está recuperando terreno, o ya está al día.
  [ "$despues" -le "$antes" ] || [ "$despues" -le "$SLOT_LAG_MAX" ] ||
    morir "el slot $SLOT_SOMBRA acumula WAL y CRECE: $antes → $despues bytes (techo $SLOT_LAG_MAX)"
}

smoke() {
  titulo "Paso 7 — smoke"
  [ -f "$ESTADO" ] || morir "no hay $ESTADO: el smoke necesita la release y los IDs de imagen medidos en el Paso 3"
  # shellcheck disable=SC1090
  . "$ESTADO"
  [ -n "${RELEASE_ESPERADA:-}" ] || morir "$ESTADO no trae RELEASE_ESPERADA (Paso 3)"

  smoke_contenedores
  smoke_sondas_acotadas

  titulo "Paso 7c — contrato de /v1/ready"
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

  # Evidencia para el acta. NO es postcondición de nada: las postcondiciones son
  # las de 7a y 7b, que sí paran.
  "$DOCKER" logs --tail 50 swissjob-core-capture || true
  "$DOCKER" logs --tail 50 swissjob-worker || true
  printf '\nsmoke OK — release %s, %s autoritativo, seis contenedores sanos con las imágenes del Paso 3.\n' \
    "$RELEASE_ESPERADA" "$READY_STATUS_ESPERADO"
}

# --------------------------------------------------------------------------
main() {
  mkdir -p "$WORK_DIR"
  case "${1:-}" in
    cutover)
      guarda_ensayo
      : >"$ESTADO"
      paso1_parar
      paso2_backup
      paso3_imagenes
      medir antes          # la referencia del Paso 6 se MIDE aquí, no se copia
      sellar_manifiesto    # …y viaja junto a la copia, para poder verificar la vuelta
      sonda_destino        # ANTES de la primera escritura: misma base para psql y para el core
      paso4_copias
      paso5_canonical_refs
      paso6_verificar
      printf '\nevidencia (informes, JSON, manifiestos y stderr de cada psql): %s\n' "$WORK_DIR"
      printf 'Pasos 1–6 OK. Ahora el Recreate de Container Station y después: %s smoke\n' "$0"
      ;;
    smoke) smoke ;;
    restaurar) restaurar "${2:-}" ;;
    *) morir "uso: $0 cutover|smoke|restaurar [fichero.dump]" ;;
  esac
}

main "$@"

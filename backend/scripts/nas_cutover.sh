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
# LO QUE CORRIGIÓ LA AUDITORÍA R5 (ronda dirigida SOLO al cutover, porque es lo
# único del proyecto que se ejecuta UNA vez sobre datos irreemplazables):
#   P1-A  los manifiestos etiquetaban la FILA, no la ENTIDAD ni la
#         TRANSFORMACIÓN, y fallaban en las DOS direcciones: el de pares
#         guardaba solo `p.id` (un par que sigue resolviendo con sus dos lados
#         en OTRAS vacantes no cambiaba el fichero → falso VERDE) y el de
#         juicios guardaba `set_id|job_ref` (un remapeo CORRECTO cambia el ref
#         → falso ROJO, y el procedimiento pararía una transformación buena).
#         Ahora los ensayos en seco DECLARAN el mapa exacto `old_hash ->
#         new_hash` (`IDENT|remap|…`), el manifiesto lleva la etiqueta entera
#         y se compara por IGUALDAD EXACTA contra el manifiesto ESPERADO que
#         se construye aplicando ese mapa al de ANTES.
#   P1-B  un `SIGKILL` entre el `RENAME` y el `CREATE DATABASE` dejaba el
#         restore sin destino y sin reanudación: el nombre de la base apartada
#         vivía SOLO en una variable del proceso, y la siguiente invocación
#         abortaba con «no existe la base». Ahora `restaurar` es una máquina de
#         estados reanudable, con exclusión mutua y checkpoint DURABLE junto a
#         la copia, que resuelve su estado POR CATÁLOGO. Los traps no bastan:
#         `SIGKILL` y un reinicio no los ejecutan.
#   P1-C  el smoke daba verde con el worker vivo y el beat AUSENTE: un worker
#         sin `-B` está `running`, corre la imagen del Paso 3 y contesta al
#         ping dirigido, porque el ping prueba el CONSUMIDOR y no el
#         PLANIFICADOR de las nueve cadencias. Ahora hay postcondición
#         FUNCIONAL del beat (Paso 7d).
#
# LO QUE CORRIGIÓ LA AUDITORÍA R6 (tres P1: el cerrojo, la durabilidad y una
# señal de salud — las tres clases que deciden si esto se puede deshacer):
#   P1-1  el cerrojo protegía el FICHERO DE COPIA y no el recurso destructivo.
#         `cutover` tomaba `$BACKUP_DIR/nas_cutover.cerrojo` y `restaurar`
#         tomaba `$dump.cerrojo`: dos restauraciones con nombres de copia
#         distintos adquirían cerrojos distintos y las dos llegaban a
#         `VERIFIED` sobre la MISMA base, calculando además el mismo nombre
#         `_previa_<segundo>`. Ahora hay UN cerrojo por servidor + `PG_DB`,
#         compartido por los dos subcomandos y tomado ANTES de cualquier
#         acción operacional; los checkpoints siguen siendo por copia. En el
#         repuesto sin `flock` se cerró además la ventana «`mkdir` con éxito →
#         PID aún no escrito», donde el `rm -rf` le quitaba el cerrojo a un
#         proceso VIVO.
#   P1-2  el checkpoint DECLARADO durable seguía adelante aunque `sync`
#         fallase: `sync 2>/dev/null || true` descartaba justo su fallo y el
#         `RENAME` destructivo ocurría igual, dejando el estado físico previo
#         en una base cuyo nombre la máquina de estados desconoce. La
#         sincronización es ahora PRECONDICIÓN: si `sync` no existe o no
#         termina 0, se PARA antes del `RENAME`.
#   P1-3  el smoke del beat combinaba dos señales DESACOPLADAS: aceptaba
#         cualquiera de cuatro cadencias en el log y medía solo
#         `outbox_lag_p99` detrás, así que un `shadow-project` despachado más
#         una muestra entrada por su cuenta daba verde con el muestreador
#         nunca planificado. Ahora las dos señales son de la MISMA capacidad:
#         despacho NUEVO de `$BEAT_CADENCIA` Y muestra con marca de tiempo
#         posterior al inicio del sondeo.
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
# Postcondición FUNCIONAL del beat EMBEBIDO (auditoría R5 P1-C). El planificador
# de las nueve cadencias (RUNBOOK §5) puede morir con el worker vivo, y el ping
# dirigido no lo nota. `BEAT_ESPERA` cubre DOS cadencias de cinco minutos: el
# smoke puede esperar; abrir con el planificador muerto no.
: "${BEAT_CONTENEDOR:=swissjob-core-worker}"
# UNA cadencia, no cuatro (R6 P1-3): la que se mide detrás es la del muestreador
# de `outbox_lag_p99`, así que es la que hay que exigir en el log. Aceptar
# «cualquiera de las cuatro» desataba las dos señales.
: "${BEAT_CADENCIA:=shadow-sample-outbox-lag}"
: "${BEAT_ESPERA:=660}"
: "${BEAT_SONDEO:=15}"
: "${BEAT_LOG_LINEAS:=5000}"
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
# El cerrojo de la maniobra (R5 P1-B, corregido en R6 P1-1: protege la BASE, no
# el archivo de copia). `flock` es OBLIGATORIO: el núcleo suelta el cerrojo
# aunque el proceso muera de `SIGKILL`, así que no hay huérfanos que heredar ni
# ventana entre ganar el cerrojo y poder nombrar al dueño. El repuesto por
# `mkdir`+PID se RETIRÓ en R7 P1-3 (ver `tomar_cerrojo`).
: "${FLOCK:=flock}"
# Dónde vive el cerrojo. NO puede derivarse de `BACKUP_DIR` (R7 P1-2): dos
# maniobras sobre la MISMA base con directorios de copia distintos tomaban
# cerrojos distintos y no se veían. La escalera es FIJA y se evalúa igual en
# todo proceso del host, que es lo que hace que dos invocaciones cualesquiera
# resuelvan el MISMO fichero. La durabilidad da igual aquí: `flock` no deja
# estado que sobreviva al proceso, y un cerrojo que se pierde en un reinicio es
# justo lo correcto —tras el reinicio no hay ninguna maniobra en curso.
: "${LOCK_DIR:=}"
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

# La ÚNICA barrera entre «escrito» y «escrito y sobrevive al corte». Es
# PRECONDICIÓN de todo paso irreversible, no cortesía.
#
# R6 P1-2 la puso en el checkpoint de la marcha atrás. R7 P1-1: la unidad de
# copia del cutover —volcado, manifiesto y los cinco sidecars— NO la tenía, y
# el Paso 4c es igual de irreversible que el `RENAME`. Reproducido: con un
# `sync` que falla, el cutover llegaba a 4c e imprimía «Pasos 1–6 OK» con
# código 0. Una copia que no ha llegado al disco no es una copia, y es la ÚNICA
# marcha atrás que tiene esta maniobra.
sincronizar_o_morir() { # <qué se está persistiendo> <dónde vive>
  command -v sync >/dev/null 2>&1 ||
    morir "este host no trae el comando 'sync': $1 no se puede persistir, y lo que viene detrás es IRREVERSIBLE. NO se continúa"
  sync ||
    morir "'sync' falló al persistir $1: puede no haber llegado al disco y lo que viene detrás es IRREVERSIBLE. Se PARA aquí a propósito, ANTES de esa acción: corrige el almacén de $2 (espacio, E/S) y vuelve a lanzar el MISMO comando"
}
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

SIDECARS="pares juicios pares.resuelven juicios.resuelven guardas"

# sha256 del CONJUNTO de sidecars, en orden fijo. `sha256sum` va dentro del
# contenedor de Postgres, como el de la copia: el NAS puede no tenerlo.
suma_sidecars() { # <copia>
  local s
  # shellcheck disable=SC2086
  for s in $SIDECARS; do cat "$1.$s"; done |
    "$DOCKER" exec -i "$PG_CONTAINER" sha256sum | awk '{print $1}'
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
  cp "$WORK_DIR/resuelven.pares.antes" "$final.pares.resuelven"
  cp "$WORK_DIR/resuelven.juicios.antes" "$final.juicios.resuelven"
  # Las guardas de inmutabilidad tal y como estaban: `restaurar` no marca
  # VERIFIED si la vuelta atrás trae los datos y los triggers degradados.
  psql_lineas "$SQL_GUARDAS" "$final.guardas" ||
    morir "no se pudieron medir las guardas de inmutabilidad para el manifiesto pre-corte"
  # Los sidecars son lo que decide qué significa «VERIFIED», así que se sellan
  # como UNA unidad junto a la copia: alterar uno a mano (o una escritura a
  # medias) ya no cambia en silencio el criterio de la marcha atrás. Es
  # integridad, no autenticidad: el sello vive en el mismo manifiesto.
  printf 'SIDECARS_SHA256=%s\n' "$(suma_sidecars "$final")" >>"$final.manifiesto"
  # La copia entera —volcado, manifiesto y sidecars— tiene que estar EN EL
  # DISCO antes del Paso 4c, que es irreversible (R7 P1-1). Si esto falla, la
  # maniobra para aquí y todavía no ha tocado nada.
  sincronizar_o_morir "la unidad de copia $final (volcado + manifiesto + sidecars)" "$BACKUP_DIR"
  printf 'manifiesto pre-corte sellado y sincronizado junto a la copia: %s.manifiesto (+ pares/juicios/resuelven/guardas)\n' "$final"
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
# --------------------------------------------------------------------------
# MANIFIESTOS SEMÁNTICOS (auditoría R5 P1-A)
#
# Los manifiestos de R4 etiquetaban la FILA, no la entidad ni la transformación,
# y fallaban en las DOS direcciones:
#
#   · falso VERDE — el de pares guardaba solo `p.id`. Si el par seguía
#     resolviendo pero sus DOS lados pasaban a vacantes distintas, el fichero no
#     cambiaba y `comm -23` salía vacío: cambió justo la materia etiquetada y
#     las cantidades, la resolubilidad y el manifiesto pasaban.
#   · falso ROJO — el de juicios guardaba `set_id|job_ref`. Un remapeo CORRECTO
#     (el Paso 5 reapunta `job_ref` al hash canónico) cambia el ref, y `comm -23`
#     declaraba PERDIDO el valor antiguo aunque la etiqueta siguiera unida a la
#     MISMA vacante: el procedimiento pararía una transformación correcta.
#
# La corrección NO es otro contador. El manifiesto lleva ahora la etiqueta
# ENTERA —identidad, refs y atributos— y se compara por IGUALDAD EXACTA contra
# un manifiesto ESPERADO, que se construye aplicando al de ANTES el mapa
# `old_hash -> new_hash` que los propios ensayos en seco DECLARAN
# (`IDENT|remap|viejo|nuevo`, §9 de cada SQL). Así el remapeo previsto se ACEPTA
# y cualquier permuta NO declarada se RECHAZA — incluida la que conserva
# cardinalidad, resolubilidad y `pair_id`.
#
# La resolubilidad se sigue midiendo aparte y en una sola dirección (lo que
# resolvía no puede dejar de resolver), porque NO es simétrica: un juicio que
# apuntaba al hash de un clon puede EMPEZAR a resolver tras el remapeo, y exigir
# igualdad ahí sería otro falso rojo.
#
# Los comentarios `/* … */` del principio de cada consulta son su nombre: son lo
# que distingue estas cuatro consultas entre sí y de las de cantidades.
# --------------------------------------------------------------------------
SQL_MANIFIESTO_PARES="/* manifiesto-pares */
 SELECT p.id::text || '|' || p.source || '|' || p.verdict
        || '|' || p.job_ref_a || '|' || p.job_ref_b
 FROM jobhunt.labeled_dedup_pairs p"
SQL_MANIFIESTO_JUICIOS="/* manifiesto-juicios */
 SELECT j.set_id::text || '|' || j.job_ref || '|' || j.relevance::text
        || '|' || j.source
 FROM jobhunt.labeled_judgments j"
SQL_RESUELVEN_PARES="/* resuelven-pares */
 SELECT p.id::text
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
SQL_RESUELVEN_JUICIOS="/* resuelven-juicios */
 SELECT j.set_id::text || '|' || j.job_ref
 FROM jobhunt.labeled_judgments j
 WHERE EXISTS (
   SELECT 1 FROM jobhunt.source_listings l
   JOIN jobhunt.sources src ON src.id = l.source_id
   JOIN jobhunt.source_listing_incarnations i ON i.source_listing_id = l.id
   WHERE src.name LIKE 'legacy:%' AND l.external_id = j.job_ref)"
# Las guardas de inmutabilidad de las cohortes: `restaurar` no marca VERIFIED si
# la vuelta atrás trae los datos y deja los triggers degradados.
SQL_GUARDAS="/* guardas-inmutabilidad */
 SELECT n.nspname || '|' || c.relname || '|' || t.tgname || '|' || t.tgenabled
 FROM pg_trigger t
 JOIN pg_class c ON c.oid = t.tgrelid
 JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE NOT t.tgisinternal AND n.nspname IN ('jobhunt', 'public')"

# El par se guarda con los dos refs NORMALIZADOS (menor|mayor) por la misma
# comparación de bytes que usa el manifiesto esperado: así el ORDEN de los dos
# lados no puede ser jamás la diferencia entre los ficheros que se comparan.
psql_lineas_pares() { # <sql> <fichero>
  psql_valor "$1" >"$2.crudo" || return 1
  LC_ALL=C awk -F'|' -v OFS='|' '{ if ($4 > $5) { t = $4; $4 = $5; $5 = t } print }' \
    "$2.crudo" | LC_ALL=C sort >"$2"
  rm -f "$2.crudo"
}

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
  psql_lineas_pares "$SQL_MANIFIESTO_PARES" "$WORK_DIR/manifiesto.pares.$1" ||
    morir "no se pudo medir el manifiesto de pares ($1)"
  psql_lineas "$SQL_MANIFIESTO_JUICIOS" "$WORK_DIR/manifiesto.juicios.$1" ||
    morir "no se pudo medir el manifiesto de juicios ($1)"
  psql_lineas "$SQL_RESUELVEN_PARES" "$WORK_DIR/resuelven.pares.$1" ||
    morir "no se pudo medir qué pares resuelven ($1)"
  psql_lineas "$SQL_RESUELVEN_JUICIOS" "$WORK_DIR/resuelven.juicios.$1" ||
    morir "no se pudo medir qué juicios resuelven ($1)"
  printf 'medición %s: slots_huerfanos=%s jobs=%s pares_con_los_dos_refs=%s juicios=%s resuelven=%s (manifiestos: %s pares · %s juicios)\n' \
    "$1" "$slots" "$jobs" "$pares" "$juicios_total" "$juicios_resuelven" \
    "$(wc -l <"$WORK_DIR/manifiesto.pares.$1")" "$(wc -l <"$WORK_DIR/manifiesto.juicios.$1")"
}

# Ninguna identidad que resolvía puede dejar de resolver. `comm -23` sobre dos
# ficheros ya ordenados con la colación de C: lo que sobra en el primero es lo
# que se perdió.
sin_perdidas() { # <fichero esperado> <fichero despues> <qué son>
  local perdidas
  perdidas=$(LC_ALL=C comm -23 "$1" "$2")
  [ -z "$perdidas" ] ||
    morir "IDENTIDAD: $(printf '%s\n' "$perdidas" | wc -l) $3 resolvían ANTES y ya no. Los primeros: $(printf '%s\n' "$perdidas" | head -n 5 | tr '\n' ' ')"
}

# Igualdad EXACTA, y el rojo NOMBRA las dos direcciones: lo que falta (se
# perdió) y lo que sobra (apareció sin que nadie lo declarara).
identicos_o_morir() { # <fichero esperado> <fichero real> <qué son>
  cmp -s "$1" "$2" && return 0
  local faltan sobran
  faltan=$(LC_ALL=C comm -23 "$1" "$2" | head -n 3 | tr '\n' ' ')
  sobran=$(LC_ALL=C comm -13 "$1" "$2" | head -n 3 | tr '\n' ' ')
  morir "IDENTIDAD: $3 DESPUÉS no son la transformación que declararon los ensayos en seco. Faltan: ${faltan:-(nada)} · Sobran sin declarar: ${sobran:-(nada)}"
}

# --------------------------------------------------------------------------
# El mapa `old_hash -> new_hash` que DECLARAN los ensayos en seco: es la
# transformación prevista, y con ella se construye el manifiesto esperado.
# Cada SQL lo emite desde su PROPIA tabla de supervivientes (`IDENT|remap|…`),
# igual que el enclavamiento de 4b: aquí no hay un segundo oráculo.
# --------------------------------------------------------------------------
mapa_de() { # <fichero crudo del informe>
  awk -F'|' '$1 == "IDENT" && $2 == "remap" && NF == 4 { print $3 "|" $4 }' "$1" |
    LC_ALL=C sort -u
}

leer_mapa_declarado() {
  local f base viejo nuevo mapa="$WORK_DIR/mapa.declarado"
  : >"$mapa.crudo"
  for f in $COPIAS_SQL; do
    base=${f%.sql}
    mapa_de "$WORK_DIR/$base.dryrun.txt" >>"$mapa.crudo"
  done
  LC_ALL=C sort -u "$mapa.crudo" >"$mapa"
  rm -f "$mapa.crudo"
  while IFS='|' read -r viejo nuevo; do
    [ -n "$viejo" ] || continue
    { [[ $viejo =~ ^[0-9a-f]{32}$ ]] && [[ $nuevo =~ ^[0-9a-f]{32}$ ]]; } ||
      morir "un ensayo en seco declara un remapeo que no son dos md5: '$viejo' -> '$nuevo'"
    [ "$viejo" != "$nuevo" ] ||
      morir "un ensayo en seco declara un remapeo de un hash a sí mismo: $viejo"
  done <"$mapa"
  # Un `old_hash` con DOS destinos haría indeterminable el manifiesto esperado.
  local ambiguos encadenados
  ambiguos=$(cut -d'|' -f1 "$mapa" | LC_ALL=C sort | uniq -d)
  [ -z "$ambiguos" ] ||
    morir "el mapa declarado es AMBIGUO (un hash viejo con varios destinos): $(printf '%s' "$ambiguos" | tr '\n' ' ')"
  # Y un destino que es a la vez origen encadenaría el remapeo: el resultado
  # dependería del orden en que se aplicara, así que no se adivina.
  encadenados=$(LC_ALL=C comm -12 \
    <(cut -d'|' -f1 "$mapa" | LC_ALL=C sort -u) \
    <(cut -d'|' -f2 "$mapa" | LC_ALL=C sort -u))
  [ -z "$encadenados" ] ||
    morir "el mapa declarado ENCADENA remapeos (un destino que es también origen): $(printf '%s' "$encadenados" | tr '\n' ' ')"
  printf 'transformación declarada por los ensayos en seco: %s remapeos old→new\n' \
    "$(grep -c . "$mapa" || true)"
}

# El manifiesto esperado vale SOLO si el mapa declarado por G3/G6 es el MISMO
# que el Paso 5 aplicará a las etiquetas. `canonical_refs` no lee ese mapa: lo
# RECONSTRUYE de `jobs` (`md5(titulo|empresa|url) <> hash`), y esa
# reconstrucción coincide exactamente con los supervivientes SOLO si hoy no hay
# ya filas que no reproducen su hash — cada una de ellas metería en `canon_map`
# un remapeo FANTASMA que ningún ensayo declaró, y el Paso 5 podría reapuntar
# etiquetas que nadie vio venir. Medido contra producción el 2026-08-26 (SOLO
# SELECT): 0 de 10.805. Se vuelve a medir aquí, ANTES de escribir nada, porque
# es más barato parar antes del Paso 4c que después.
SQL_HASHES_NO_REPRODUCIBLES="/* hashes-no-reproducibles */
 SELECT count(*) FROM public.jobs j
 WHERE md5(lower(btrim(j.title)) || '|' || lower(btrim(j.company)) || '|' || j.url) <> j.hash"

exigir_mapa_reconstruible() {
  local fantasmas
  fantasmas=$(psql_valor "$SQL_HASHES_NO_REPRODUCIBLES") ||
    morir "no se pudo medir cuántas filas de jobs no reproducen su hash"
  entero "$fantasmas" "filas de jobs que no reproducen su hash"
  printf 'filas de jobs que no reproducen su hash: %s (tienen que ser 0)\n' "$fantasmas"
  [ "$fantasmas" -eq 0 ] ||
    morir "$fantasmas filas de public.jobs NO reproducen su hash: el mapa que el Paso 5 reconstruye de jobs traería $fantasmas remapeos que NINGÚN ensayo en seco declara, y el Paso 6 no podría distinguirlos de una permuta. NADA se ha escrito todavía: averigua por qué esas filas no reproducen su hash (¿una canonización anterior sin re-mapear sus etiquetas? ¿títulos truncados?) antes de seguir"
}

# El estado que las etiquetas TIENEN que tener después: el de antes con el mapa
# aplicado. Nada más se mueve.
construir_esperados() {
  local mapa="$WORK_DIR/mapa.declarado"
  local remapeo='FILENAME == mapa { m[$1] = $2; next }'
  LC_ALL=C awk -F'|' -v OFS='|' -v mapa="$mapa" \
    "$remapeo"' { if ($2 in m) $2 = m[$2]; print }' \
    "$mapa" "$WORK_DIR/manifiesto.juicios.antes" |
    LC_ALL=C sort >"$WORK_DIR/manifiesto.juicios.esperado"
  LC_ALL=C awk -F'|' -v OFS='|' -v mapa="$mapa" \
    "$remapeo"' { a = $4; b = $5
        if (a in m) a = m[a]
        if (b in m) b = m[b]
        if (a > b) { t = a; a = b; b = t }
        $4 = a; $5 = b; print }' \
    "$mapa" "$WORK_DIR/manifiesto.pares.antes" |
    LC_ALL=C sort >"$WORK_DIR/manifiesto.pares.esperado"
  LC_ALL=C awk -F'|' -v OFS='|' -v mapa="$mapa" \
    "$remapeo"' { if ($2 in m) $2 = m[$2]; print }' \
    "$mapa" "$WORK_DIR/resuelven.juicios.antes" |
    LC_ALL=C sort >"$WORK_DIR/resuelven.juicios.esperado"
  printf 'manifiesto ESPERADO tras la transformación: %s juicios · %s pares\n' \
    "$(wc -l <"$WORK_DIR/manifiesto.juicios.esperado")" \
    "$(wc -l <"$WORK_DIR/manifiesto.pares.esperado")"
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

  # LA TRANSFORMACIÓN, no solo los conjuntos: con este mapa el Paso 6 construye
  # el manifiesto ESPERADO de las etiquetas (R5 P1-A).
  leer_mapa_declarado
  exigir_mapa_reconstruible

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
    # Y el MAPA: el manifiesto esperado del Paso 6 se construyó con el del
    # ensayo, así que si en firme es otro estaríamos verificando otra cosa.
    [ "$(mapa_de "$WORK_DIR/$base.dryrun.txt")" = "$(mapa_de "$WORK_DIR/$base.firme.txt")" ] ||
      morir "el mapa old→new que declara $f EN FIRME difiere del que declaró el ensayo: el manifiesto esperado del Paso 6 se construyó con el del ensayo"
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

  # El mapa que este módulo va a aplicar tiene que ser EXACTAMENTE el que
  # declararon los ensayos en seco: con ese mapa se construye el manifiesto
  # esperado del Paso 6, y un remapeo de más aquí sería indistinguible de una
  # permuta allí. El módulo no lee el mapa —lo reconstruye de `jobs`—, así que
  # esto es lo que ata las dos mitades. Todavía no ha escrito nada.
  local declarados aplicara
  declarados=$(grep -c . "$WORK_DIR/mapa.declarado" || true)
  aplicara=$(sed -n 's/.*"filas_canonizadas_en_legacy": *\([0-9][0-9]*\).*/\1/p' \
    "$WORK_DIR/canonical_refs.dryrun.json" | head -n 1)
  entero "${aplicara:-x}" "filas que canonical_refs reconstruye"
  [ "$aplicara" = "$declarados" ] ||
    morir "canonical_refs reconstruye un mapa de $aplicara filas y los ensayos en seco declararon $declarados remapeos: el Paso 5 movería etiquetas que el Paso 6 no puede verificar. NO se ha aplicado nada del Paso 5: RESTAURA la copia del Paso 2 ($0 restaurar)"
  printf 'el mapa de canonical_refs (%s filas) coincide con el declarado por los ensayos\n' "$aplicara"

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

  # (b') y (c') por IDENTIDAD SEMÁNTICA (R4 P1-3 + R5 P1-A): (b) y (c) son
  # cantidades y una pérdida se compensa con una ganancia distinta; y el
  # manifiesto de R4 etiquetaba la FILA, así que una permuta de los dos lados de
  # un par pasaba y un remapeo correcto de un juicio paraba. Aquí se compara el
  # estado ENTERO de las etiquetas contra el que declara la transformación.
  construir_esperados
  identicos_o_morir "$WORK_DIR/manifiesto.juicios.esperado" \
    "$WORK_DIR/manifiesto.juicios.despues" "los juicios"
  identicos_o_morir "$WORK_DIR/manifiesto.pares.esperado" \
    "$WORK_DIR/manifiesto.pares.despues" "los pares"
  # Y en una sola dirección, la resolubilidad: lo que resolvía no puede dejar de
  # resolver (con la clave que la transformación le dará).
  sin_perdidas "$WORK_DIR/resuelven.juicios.esperado" "$WORK_DIR/resuelven.juicios.despues" "juicios"
  sin_perdidas "$WORK_DIR/resuelven.pares.antes" "$WORK_DIR/resuelven.pares.despues" "pares"
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
# --------------------------------------------------------------------------
# Auditoría R5 P1-B — y por qué esto es una MÁQUINA DE ESTADOS.
#
# La versión anterior conservaba la base rota (bien: no había pérdida física),
# pero el nombre de la base apartada vivía SOLO en una variable del proceso. Un
# `SIGKILL` entre el `RENAME` y el `CREATE DATABASE` —un corte de corriente, un
# `docker kill`, el OOM killer— borraba a la vez el destino esperado y el
# conocimiento operativo para continuar: la siguiente invocación abortaba con
# «no existe la base $PG_DB en el servidor» y la única salida de emergencia
# quedaba fuera de servicio. Reproducido: `EXIT_CODE=137` en la primera
# invocación, `EXIT_CODE=1` en la segunda.
#
# Los traps NO valen: `SIGKILL` y un reinicio no los ejecutan. Lo que vale es
# que el estado esté FUERA del proceso y se pueda leer del catálogo:
#
#   1. exclusión mutua sobre un fichero DURABLE junto a la copia (no en /tmp,
#      que en el NAS es un ramdisk que un reinicio vacía);
#   2. checkpoint escrito ATÓMICAMENTE (`.tmp` + `mv`) ANTES del `RENAME`, con
#      copia, sello, base destino, nombre de la base previa, atributos y fase;
#   3. al arrancar, resolución POR CATÁLOGO: solo destino ⇒ empezar; solo
#      previa ⇒ continuar creando y restaurando; las dos ⇒ el destino está a
#      medias, se recrea antes de repetir `pg_restore`; ninguna ⇒ abortar;
#   4. la fase se escribe DESPUÉS de cada postcondición, nunca antes;
#   5. la base previa NO se borra sola, y `VERIFIED` solo se marca después de
#      manifiestos, metadatos Y guardas.
#
# Con esto, el mismo comando repetido tantas veces como haga falta termina en
# `VERIFIED` sin una sola sentencia SQL a mano.
# --------------------------------------------------------------------------
CK_DUMP=""; CK_SHA256=""; CK_DESTINO=""; CK_PREVIA=""; CK_SETTINGS=""
CK_ENC=""; CK_COLL=""; CK_CTYPE=""; CK_OWNER=""; CK_CONNLIMIT=""; CK_ACL=""
CK_FASE=INICIO
CHECKPOINT=""
SUMA_DUMP=""

sql_atributos() { # <base>
  printf "/* atributos-base */ SELECT pg_encoding_to_char(encoding) || '|' || datcollate
   || '|' || datctype || '|' || pg_get_userbyid(datdba) || '|' || datconnlimit::text
   || '|' || coalesce(array_to_string(datacl, ','), '')
   FROM pg_database WHERE datname = '%s'" "$1"
}

sql_settings() { # <base>
  printf "/* settings-base */ SELECT setrole::text || '=' || array_to_string(setconfig, ',')
   FROM pg_db_role_setting
   WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname = '%s') ORDER BY 1" "$1"
}

base_existe() { # <base>
  local v
  v=$(psql_maint "/* existe-base */ SELECT 1 FROM pg_database WHERE datname = '$1'") ||
    morir "no se pudo consultar el catálogo de bases del servidor: sin catálogo no se puede resolver el estado de la marcha atrás"
  [ "$v" = 1 ]
}

atributos_de() { # <base> -> «enc|coll|ctype|owner|connlimit|acl»
  local a
  a=$(psql_maint "$(sql_atributos "$1")") ||
    morir "no se pudieron leer los atributos de la base $1"
  [ -n "$a" ] || morir "no existe la base $1 en el servidor"
  printf '%s' "$a"
}

settings_de() { # <base> -> los `ALTER DATABASE … SET` en una línea
  local s
  s=$(psql_maint "$(sql_settings "$1")") ||
    morir "no se pudieron leer los settings por base de $1"
  printf '%s' "$s" | tr '\n' ';'
}

# El fichero de estado nunca se ve a medias: `mv` es `rename(2)`, atómico. Y el
# `sync` es lo que separa «escrito» de «escrito y sobrevive al corte» — por eso
# es PRECONDICIÓN y no cortesía (auditoría R6 P1-2): `sync … || true` se tragaba
# justo su fallo y el `RENAME` DESTRUCTIVO ocurría igual, de modo que un corte
# dejaba el estado físico previo en una base cuyo nombre la máquina de estados
# no conoce. Que es, exactamente, el defecto que R5 cerró.
checkpoint_escribir() { # <fase>
  CK_FASE=$1
  {
    printf "CK_DUMP='%s'\n" "$CK_DUMP"
    printf "CK_SHA256='%s'\n" "$CK_SHA256"
    printf "CK_DESTINO='%s'\n" "$CK_DESTINO"
    printf "CK_PREVIA='%s'\n" "$CK_PREVIA"
    printf "CK_ENC='%s'\n" "$CK_ENC"
    printf "CK_COLL='%s'\n" "$CK_COLL"
    printf "CK_CTYPE='%s'\n" "$CK_CTYPE"
    printf "CK_OWNER='%s'\n" "$CK_OWNER"
    printf "CK_CONNLIMIT='%s'\n" "$CK_CONNLIMIT"
    printf "CK_ACL='%s'\n" "$CK_ACL"
    printf "CK_SETTINGS='%s'\n" "$CK_SETTINGS"
    printf "CK_FASE='%s'\n" "$CK_FASE"
  } >"$CHECKPOINT.tmp" || morir "no se pudo escribir el checkpoint $CHECKPOINT.tmp"
  mv "$CHECKPOINT.tmp" "$CHECKPOINT" || morir "no se pudo publicar el checkpoint $CHECKPOINT"
  sincronizar_o_morir "el checkpoint $CHECKPOINT (fase $CK_FASE)" "$BACKUP_DIR"
  printf 'checkpoint %s → %s\n' "$CK_FASE" "$CHECKPOINT"
}

checkpoint_cargar() { # <dump> <sha256>
  CHECKPOINT="$1.restauracion"
  CK_DUMP=$1; CK_SHA256=$2; CK_DESTINO=$PG_DB
  [ -f "$CHECKPOINT" ] || { printf 'sin checkpoint previo: la marcha atrás empieza de cero.\n'; return 0; }
  # shellcheck disable=SC1090
  . "$CHECKPOINT"
  [ "${CK_DUMP:-}" = "$1" ] ||
    morir "el checkpoint $CHECKPOINT es de otra copia (${CK_DUMP:-}): no se reanuda una restauración con un archivo distinto"
  [ "${CK_SHA256:-}" = "$2" ] ||
    morir "el checkpoint $CHECKPOINT sella otro contenido (${CK_SHA256:-}) que el de la copia ($2)"
  [ "${CK_DESTINO:-}" = "$PG_DB" ] ||
    morir "el checkpoint $CHECKPOINT restaura sobre '${CK_DESTINO:-}' y PG_DB es '$PG_DB': no se restaura una base en otra"
  printf 'checkpoint encontrado: fase=%s · base previa=%s\n' "$CK_FASE" "${CK_PREVIA:-(ninguna)}"
}

# El cerrojo protege el RECURSO DESTRUCTIVO, que es la BASE `$PG_DB` de ESTE
# servidor — no el fichero de copia (auditoría R6 P1-1). `cutover` tomaba
# `$BACKUP_DIR/nas_cutover.cerrojo` y `restaurar` tomaba `$dump.cerrojo`: dos
# restauraciones con nombres de copia DISTINTOS adquirían cerrojos distintos, y
# un cutover y una restauración no se veían entre sí, aunque los tres ejecutan
# `RENAME`, `DROP`, `CREATE` y `pg_restore` sobre la MISMA base. Reproducido:
# las dos restauraciones llegaron a `VERIFIED` y las dos calcularon el mismo
# nombre `_previa_<segundo>`. Ahora hay UN cerrojo por servidor+base, que toman
# los dos subcomandos ANTES de cualquier acción operacional; los checkpoints
# siguen siendo por copia, que es lo que sí distingue una copia de otra.
# El directorio del cerrojo: el primero de una escalera FIJA que sea un
# directorio escribible. Deliberadamente NO mira `BACKUP_DIR` ni ningún otro
# artefacto de la maniobra (R7 P1-2). Si el operador fija `LOCK_DIR` a mano,
# tiene que fijarlo IGUAL en las dos invocaciones — y se le avisa.
directorio_del_cerrojo() {
  local d
  if [ -n "$LOCK_DIR" ]; then
    mkdir -p "$LOCK_DIR" 2>/dev/null || true
    [ -d "$LOCK_DIR" ] && [ -w "$LOCK_DIR" ] ||
      morir "LOCK_DIR='$LOCK_DIR' no es un directorio escribible: sin cerrojo no se toca la base $PG_DB"
    printf '⚠ LOCK_DIR viene del entorno (%s): TODA maniobra sobre %s debe usar el MISMO valor, o dos no se verán entre sí.\n' "$LOCK_DIR" "$PG_DB" >&2
    printf '%s' "$LOCK_DIR"; return 0
  fi
  for d in /var/lock /var/tmp /tmp; do
    [ -d "$d" ] && [ -w "$d" ] && { printf '%s' "$d"; return 0; }
  done
  morir "no hay ningún directorio escribible para el cerrojo (/var/lock, /var/tmp, /tmp): sin exclusión mutua no se toca la base $PG_DB"
}

cerrojo_de_la_base() { # -> ruta del cerrojo ÚNICO por servidor + PG_DB
  printf '%s/nas_cutover.%s.cerrojo' "$(directorio_del_cerrojo)" \
    "$(printf '%s@%s' "$PG_DB" "$PG_CONTAINER" | tr -c 'A-Za-z0-9._@-' '_')"
}

# Exclusión mutua sobre un fichero de ruta ESTABLE. `flock` es obligatorio y su
# ausencia PARA la maniobra (R7 P1-3).
#
# El repuesto por `mkdir`+PID se retiró. Era atómico para ganar el cerrojo, pero
# la LIMPIEZA de un cerrojo huérfano no lo es: dos procesos podían leer el mismo
# PID muerto, hacer `rm -rf` uno detrás de otro y `mkdir` con éxito los dos, y
# ambos se declaraban dueños de la misma base. Reproducido. Cualquier remiendo
# —reintentar, comparar mtime, un segundo cerrojo— vuelve a apoyarse en «leer,
# decidir y borrar» sin atomicidad, así que se quita la clase entera: sobre
# datos irreemplazables, no arrancar es barato y dos maniobras a la vez no
# tienen marcha atrás. Si un QNAP mínimo no trae `flock`, el procedimiento se
# para y el operador comprueba A MANO que no hay maniobra en curso; la limpieza
# de un cerrojo obsoleto es manual y auditada, nunca automática.
tomar_cerrojo() { # <fichero>
  local cerrojo=$1
  command -v "$FLOCK" >/dev/null 2>&1 ||
    morir "este host no trae '$FLOCK' y la exclusión mutua es OBLIGATORIA sobre $PG_DB: dos maniobras a la vez (dos restauraciones, o un cutover y una restauración) corrompen la base sin marcha atrás. Instala util-linux/busybox flock, o apunta FLOCK= a su ruta. NO se continúa sin cerrojo"
  exec 9>"$cerrojo" || morir "no se pudo abrir el cerrojo $cerrojo"
  "$FLOCK" -n 9 ||
    morir "otra maniobra tiene el cerrojo $cerrojo: NO se lanzan dos a la vez sobre la base $PG_DB (ni dos restauraciones con copias distintas, ni un cutover y una restauración). Si estás seguro de que ninguna sigue viva, comprueba a mano qué proceso lo retiene antes de tocar nada"
  printf 'cerrojo (flock): %s\n' "$cerrojo"
}

localizar_dump() { # [fichero]
  local dump=${1:-}
  if [ -z "$dump" ] && [ -f "$ESTADO" ]; then
    # shellcheck disable=SC1090
    . "$ESTADO"
    dump=${BACKUP:-}
  fi
  [ -n "$dump" ] || morir "no sé qué restaurar: pasa el .dump, o deja el $ESTADO del cutover"
  printf '%s' "$dump"
}

# El sello y el índice de la copia, ANTES de tocar nada: una copia que no se
# puede leer entera no es una marcha atrás. Deja $SUMA_DUMP y las MEDIDA_*.
verificar_copia() { # <dump>
  local dump=$1 toc n_public n_jobhunt sidecar
  [ -f "$dump" ] || morir "no existe la copia $dump"
  [ -f "$dump.manifiesto" ] || morir "falta $dump.manifiesto: sin manifiesto pre-corte no se puede comprobar que la vuelta VOLVIÓ"
  # shellcheck disable=SC2086
  for sidecar in $SIDECARS; do
    [ -f "$dump.$sidecar" ] ||
      morir "falta $dump.$sidecar: el manifiesto pre-corte está incompleto y la vuelta atrás no se podría certificar"
  done
  # shellcheck disable=SC1090
  . "$dump.manifiesto"
  local suma_lateral
  suma_lateral=$(suma_sidecars "$dump")
  [ "$suma_lateral" = "${SIDECARS_SHA256:-}" ] ||
    morir "los sidecars del manifiesto pre-corte NO cuadran con su sello (sha256 $suma_lateral, manifiesto ${SIDECARS_SHA256:-}): alguien los cambió, y son lo que decide qué significa que la vuelta atrás VOLVIÓ"
  [ "${DUMP_PG_DB:-}" = "$PG_DB" ] ||
    morir "el manifiesto dice que la copia es de '${DUMP_PG_DB:-}' y PG_DB es '$PG_DB': no se restaura una base en otra"
  SUMA_DUMP=$("$DOCKER" exec -i "$PG_CONTAINER" sha256sum <"$dump" | awk '{print $1}')
  [ "$SUMA_DUMP" = "${DUMP_SHA256:-}" ] ||
    morir "la copia NO cuadra con su sello: sha256 $SUMA_DUMP, manifiesto ${DUMP_SHA256:-}"
  toc="$WORK_DIR/restaurar.toc"
  "$DOCKER" exec -i "$PG_CONTAINER" pg_restore -l <"$dump" >"$toc" ||
    morir "pg_restore -l no puede leer $dump: la copia está corrupta y NO hay marcha atrás por aquí"
  n_public=$(grep -cE '^[0-9]+; [0-9]+ [0-9]+ TABLE public ' "$toc" || true)
  n_jobhunt=$(grep -cE '^[0-9]+; [0-9]+ [0-9]+ TABLE jobhunt ' "$toc" || true)
  [ "$n_public" = "${DUMP_TABLAS_PUBLIC:-}" ] && [ "$n_jobhunt" = "${DUMP_TABLAS_JOBHUNT:-}" ] ||
    morir "el índice de la copia no cuadra con el manifiesto: public $n_public/${DUMP_TABLAS_PUBLIC:-}, jobhunt $n_jobhunt/${DUMP_TABLAS_JOBHUNT:-}"
}

# El RENAME, con el checkpoint escrito ANTES: si el proceso muere entre las dos
# sentencias, el nombre de la base apartada NO se va con él.
apartar_destino() {
  local a
  a=$(atributos_de "$PG_DB")
  CK_ENC=${a%%|*}; a=${a#*|}
  CK_COLL=${a%%|*}; a=${a#*|}
  CK_CTYPE=${a%%|*}; a=${a#*|}
  CK_OWNER=${a%%|*}; a=${a#*|}
  CK_CONNLIMIT=${a%%|*}; CK_ACL=${a#*|}
  entero "$CK_CONNLIMIT" "límite de conexiones de $PG_DB"
  CK_SETTINGS=$(settings_de "$PG_DB")
  CK_PREVIA="${PG_DB}_previa_$(date +%Y%m%d%H%M%S)"
  checkpoint_escribir INICIO
  psql_maint "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
              WHERE datname = '$PG_DB' AND pid <> pg_backend_pid()" >/dev/null ||
    morir "no se pudieron cortar las conexiones a $PG_DB"
  psql_maint "ALTER DATABASE \"$PG_DB\" RENAME TO \"$CK_PREVIA\"" >/dev/null ||
    morir "no se pudo apartar $PG_DB (¿queda alguna conexión abierta?): NADA se ha tocado"
  checkpoint_escribir APARTADA
  printf 'el estado roto queda APARTADO en la base %s (no se borra sola)\n' "$CK_PREVIA"
}

# La base nueva lleva `max_parallel_maintenance_workers = 0` mientras dura la
# restauración: el índice HNSW de `offer_embeddings` se construye en paralelo y
# pide un segmento de memoria compartida de ~64 MB que NO cabe en el `/dev/shm`
# por defecto de Docker (64 MB). Medido: con el valor por defecto la
# restauración del corpus real aborta con «could not resize shared memory
# segment»; con 0, entra entera en 13 s.
recrear_destino() {
  if base_existe "$PG_DB"; then
    # Un destino sin certificar es basura de una invocación muerta: el estado
    # bueno vive ENTERO en la base apartada, así que aquí no se pierde nada.
    printf 'el destino %s existe pero NO está certificado (fase %s): se recrea vacío.\n' "$PG_DB" "$CK_FASE"
    psql_maint "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                WHERE datname = '$PG_DB' AND pid <> pg_backend_pid()" >/dev/null ||
      morir "no se pudieron cortar las conexiones a $PG_DB"
    psql_maint "DROP DATABASE \"$PG_DB\"" >/dev/null ||
      morir "no se pudo borrar el destino incompleto $PG_DB. El estado anterior sigue ENTERO en $CK_PREVIA"
  fi
  psql_maint "CREATE DATABASE \"$PG_DB\" TEMPLATE template0 ENCODING '$CK_ENC'
              LC_COLLATE '$CK_COLL' LC_CTYPE '$CK_CTYPE' OWNER \"$CK_OWNER\"" >/dev/null ||
    morir "no se pudo crear $PG_DB vacía. El estado anterior sigue ENTERO en $CK_PREVIA: vuelve a lanzar el MISMO comando (se reanuda solo) o devuélvelo con ALTER DATABASE \"$CK_PREVIA\" RENAME TO \"$PG_DB\""
  checkpoint_escribir DESTINO_CREADO
  psql_maint "ALTER DATABASE \"$PG_DB\" SET max_parallel_maintenance_workers = 0" >/dev/null ||
    morir "no se pudo desactivar el paralelismo de mantenimiento en $PG_DB"
}

# `--single-transaction` implica `--exit-on-error`: o entra entera o no entra
# nada. Sin `--clean`: la base está recién creada y vacía.
restaurar_dump() { # <dump>
  "$DOCKER" exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$PG_DB" \
    --exit-on-error --single-transaction <"$1" \
    >"$WORK_DIR/restaurar.out" 2>"$WORK_DIR/restaurar.err" || {
      cat "$WORK_DIR/restaurar.err" >&2
      morir "pg_restore FALLÓ y la base $PG_DB quedó vacía. El estado anterior sigue ENTERO en $CK_PREVIA. Corrige la causa (espacio, locks, versión) y vuelve a lanzar el MISMO comando: se reanuda desde aquí. Revisa $WORK_DIR/restaurar.err"
    }
  psql_maint "ALTER DATABASE \"$PG_DB\" RESET max_parallel_maintenance_workers" >/dev/null ||
    morir "no se pudo devolver max_parallel_maintenance_workers a su valor por defecto en $PG_DB"
  # Los metadatos por base NO viajan en el volcado: el límite de conexiones se
  # devuelve aquí, y los `ALTER DATABASE … SET` se nombran para que el operador
  # los reponga (la verificación de abajo no los da por buenos).
  [ "$CK_CONNLIMIT" = "-1" ] ||
    psql_maint "ALTER DATABASE \"$PG_DB\" CONNECTION LIMIT $CK_CONNLIMIT" >/dev/null ||
    morir "no se pudo devolver el límite de conexiones ($CK_CONNLIMIT) a $PG_DB"
  checkpoint_escribir RESTAURADO
}

# VERIFIED solo si cuadran las tres cosas: manifiestos, metadatos y guardas.
# NINGÚN modo para aquí dentro: los dos devuelven 1 y dejan el detalle en
# `FALLOS_VUELTA`. Quien llama es el que sabe qué significa el fallo —el modo
# `duro` tiene que dejarlo ANOTADO en el checkpoint antes de parar (R7 P1-5),
# y el `blando` solo re-comprueba una restauración ya certificada.
FALLOS_VUELTA=""
verificar_vuelta() { # <dump> <duro|blando>
  local dump=$1 modo=$2 fallos="" atributos esperados settings
  titulo "RESTAURAR — comprobación contra el manifiesto pre-corte"
  medir restaurado
  [ "$SLOTS_restaurado" = "${MEDIDA_SLOTS:-}" ] || fallos="$fallos slots($SLOTS_restaurado≠${MEDIDA_SLOTS:-})"
  [ "$JOBS_restaurado" = "${MEDIDA_JOBS:-}" ] || fallos="$fallos jobs($JOBS_restaurado≠${MEDIDA_JOBS:-})"
  [ "$PARES_restaurado" = "${MEDIDA_PARES:-}" ] || fallos="$fallos pares($PARES_restaurado≠${MEDIDA_PARES:-})"
  [ "$JUICIOS_restaurado" = "${MEDIDA_JUICIOS:-}" ] || fallos="$fallos juicios($JUICIOS_restaurado≠${MEDIDA_JUICIOS:-})"
  [ "$RESUELVEN_restaurado" = "${MEDIDA_RESUELVEN:-}" ] || fallos="$fallos resuelven($RESUELVEN_restaurado≠${MEDIDA_RESUELVEN:-})"
  cmp -s "$dump.pares" "$WORK_DIR/manifiesto.pares.restaurado" || fallos="$fallos pares(identidad)"
  cmp -s "$dump.juicios" "$WORK_DIR/manifiesto.juicios.restaurado" || fallos="$fallos juicios(identidad)"
  cmp -s "$dump.pares.resuelven" "$WORK_DIR/resuelven.pares.restaurado" || fallos="$fallos pares(resuelven)"
  cmp -s "$dump.juicios.resuelven" "$WORK_DIR/resuelven.juicios.restaurado" || fallos="$fallos juicios(resuelven)"
  atributos=$(atributos_de "$PG_DB")
  esperados="$CK_ENC|$CK_COLL|$CK_CTYPE|$CK_OWNER|$CK_CONNLIMIT|$CK_ACL"
  [ "$atributos" = "$esperados" ] || fallos="$fallos metadatos($atributos≠$esperados)"
  settings=$(settings_de "$PG_DB")
  [ "$settings" = "$CK_SETTINGS" ] ||
    fallos="$fallos settings-por-base(hay '$settings' y el estado previo tenía '$CK_SETTINGS': reponlos con ALTER DATABASE … SET)"
  psql_lineas "$SQL_GUARDAS" "$WORK_DIR/guardas.restaurado" ||
    morir "no se pudieron medir las guardas de inmutabilidad tras restaurar"
  cmp -s "$dump.guardas" "$WORK_DIR/guardas.restaurado" ||
    fallos="$fallos guardas(los triggers de inmutabilidad no son los del manifiesto pre-corte)"
  FALLOS_VUELTA=""
  [ -n "$fallos" ] || { printf 'manifiestos, metadatos y guardas cuadran con %s.manifiesto\n' "$dump"; return 0; }
  FALLOS_VUELTA=$fallos
  [ "$modo" != blando ] ||
    { printf '⚠ la re-comprobación NO cuadra:%s\n' "$fallos"; return 1; }
  printf '⚠ la restauración NO cuadra con el manifiesto pre-corte:%s\n' "$fallos"
  return 1
}

cierre_restauracion() { # <dump>
  printf '\nrestauración VERIFIED contra %s.manifiesto: cantidades, identidades, metadatos y guardas coinciden.\n' "$1"
  printf 'El estado roto sigue APARTADO en %s: NO se borra solo; bórralo a mano cuando estés conforme.\n' "$CK_PREVIA"
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

restaurar() { # [fichero .dump]
  titulo "RESTAURAR — vuelta al estado previo al corte (reanudable)"
  local dump previa
  # El cerrojo va ANTES de cualquier acción operacional y es el de la BASE, no
  # el de la copia (R6 P1-1): dos restauraciones con copias distintas, o una
  # restauración y un cutover, no pueden coexistir sobre $PG_DB.
  mkdir -p "$BACKUP_DIR"
  tomar_cerrojo "$(cerrojo_de_la_base)"
  dump=$(localizar_dump "${1:-}")
  verificar_copia "$dump"
  checkpoint_cargar "$dump" "$SUMA_DUMP"
  # Restaurar con escritores vivos deja el estado a medias en cuanto uno escriba.
  parar_escritores
  [ "$PG_DB" != "$PG_MAINT_DB" ] ||
    morir "PG_DB y PG_MAINT_DB son la misma base ($PG_DB): no se puede renombrar la base a la que hay que conectarse"

  previa=${CK_PREVIA:-}
  if [ -n "$previa" ] && ! base_existe "$previa"; then
    printf '⚠ el checkpoint nombra la base apartada %s y ya NO existe: se empieza de cero.\n' "$previa"
    previa=""
  fi

  # Ya certificada: si el estado SIGUE cuadrando no hay nada que hacer; si se
  # volvió a romper, empieza un ciclo NUEVO que apartará el destino actual (no
  # lo borra: se conservan las dos bases).
  if [ "$CK_FASE" = VERIFIED ] && [ -n "$previa" ] && base_existe "$PG_DB"; then
    if verificar_vuelta "$dump" blando; then
      printf '\nla marcha atrás ya estaba VERIFIED y el estado SIGUE cuadrando: nada que hacer.\n'
      return 0
    fi
    printf '⚠ el estado volvió a romperse: ciclo NUEVO (la base %s se conserva).\n' "$previa"
    previa=""; CK_FASE=INICIO
  fi

  # Resolución POR CATÁLOGO. Sin base previa, el estado roto sigue siendo el
  # destino y hay que apartarlo; sin destino y sin previa no hay nada que hacer.
  if [ -z "$previa" ]; then
    base_existe "$PG_DB" ||
      morir "ni existe la base $PG_DB ni queda una base apartada que reanudar: alguien la borró a mano. La copia $dump sigue siendo válida — crea la base ($0 no lo hace a ciegas) y vuelve a lanzar $0 restaurar $dump"
    apartar_destino
    previa=$CK_PREVIA
  fi

  # `RESTAURADO` es la única fase que permite saltarse `pg_restore`: dice que la
  # copia entró ENTERA y que solo falta certificarla. `VERIFICACION_FALLIDA` NO
  # la habilita a propósito (R7 P1-5): ese destino ya se comprobó y no cuadra,
  # así que volver a verificarlo daría el mismo rojo para siempre. Cae al
  # `else`, que recrea el destino y restaura otra vez — el estado bueno sigue
  # ENTERO en la base apartada, que no se toca.
  if [ "$CK_FASE" = RESTAURADO ] && base_existe "$PG_DB"; then
    printf 'el checkpoint dice que pg_restore ya entró entero: se pasa a verificar.\n'
  else
    [ "$CK_FASE" != VERIFICACION_FALLIDA ] ||
      printf 'el checkpoint dice que la verificación anterior NO cuadró: se recrea el destino y se restaura de nuevo (la base apartada %s no se toca).\n' "$previa"
    recrear_destino
    restaurar_dump "$dump"
  fi

  if ! verificar_vuelta "$dump" duro; then
    # Se ANOTA antes de parar: sin esto el checkpoint se quedaba en
    # `RESTAURADO`, la siguiente ejecución se saltaba `pg_restore` y volvía a
    # verificar exactamente el mismo destino defectuoso. No convergía nunca.
    checkpoint_escribir VERIFICACION_FALLIDA
    morir "la restauración terminó pero el estado NO es el del manifiesto pre-corte:$FALLOS_VUELTA. Queda anotado en $CHECKPOINT: vuelve a lanzar el MISMO comando y se restaurará DE NUEVO desde la copia (no se re-verifica el destino roto). El estado anterior sigue ENTERO en $CK_PREVIA"
  fi
  checkpoint_escribir VERIFIED
  cierre_restauracion "$dump"
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

# --------------------------------------------------------------------------
# Paso 7d — Celery BEAT vivo (auditoría R5 P1-C)
#
# El smoke daba VERDE con el worker vivo y el beat AUSENTE. Comprobaba estado,
# imagen y un ping dirigido, y un worker arrancado SIN `-B` cumple las tres
# cosas: el ping prueba el CONSUMIDOR, no el PLANIFICADOR. Y beat es quien manda
# las NUEVE cadencias —proyector, despacho del outbox, salud del slot, purga de
# idempotencia y cierre de ciclo (RUNBOOK §5)—, que el propio runbook documenta
# que pueden morir con el worker vivo.
#
# La postcondición es FUNCIONAL y las dos señales tienen que ser de la MISMA
# capacidad (auditoría R6 P1-3). La versión anterior admitía CUALQUIERA de las
# cuatro cadencias de cinco minutos en el log y medía SOLO `outbox_lag_p99`
# detrás: un `Sending due task shadow-project` más una muestra entrada por una
# ejecución independiente —una cola anterior, una mano, otro planificador—
# daba VERDE aunque el beat actual no despachara el muestreador nunca.
# Reproducido. Ahora se exige, dentro de DOS cadencias de cinco minutos:
#   · `beat: Starting` en el log del worker —arrancó alguna vez—,
#   · un `Sending due task` NUEVO de `$BEAT_CADENCIA` (el muestreador), y
#   · una muestra de `outbox_lag_p99` con marca de tiempo POSTERIOR al inicio
#     del sondeo, no un simple «hay más que antes».
# El planificador MANDA esa cadencia y el worker la EJECUTA: es una capacidad,
# no dos coincidencias. `--since` mira solo lo nuevo: una traza de hace horas no
# vale como prueba de vida.
#
# Mirar si `Config.Cmd` trae `-B` mejora el DIAGNÓSTICO pero no prueba que beat
# siga vivo, así que se imprime y no decide nada. Y el smoke puede esperar:
# abrir con el planificador muerto es peor que unos minutos más de mantenimiento.
# --------------------------------------------------------------------------
# CUÁNTAS muestras de `outbox_lag_p99` hay con marca de tiempo posterior a un
# instante dado. No `max(ts)` (R7 P1-4): una fila fechada en el FUTURO —reloj
# desviado, dato inyectado, sembrado de pruebas— cumple `max(ts) > t0` para
# siempre, así que el smoke daba «beat VIVO» sin que entrara ni una muestra
# nueva. Reproducido: la misma epoch de 2100 antes y después, verde. Contando
# se compara contra la línea base tomada en el propio `t0`: la fila del futuro
# ya está dentro de esa base y deja de probar nada, y solo una muestra NUEVA
# hace crecer la cifra.
#
# Y se cuenta solo lo posterior a `t0` a propósito: la poda de `purge_staging`
# retira samples de ciclos ya fuera de retención —todos anteriores a `t0`—, de
# modo que no puede encoger este contador y provocar un rojo falso.
sql_muestras_posteriores() { # <epoch>
  printf "/* muestras-outbox-posteriores */
 SELECT count(*)::bigint
 FROM jobhunt.shadow_cycle_metrics m,
      LATERAL jsonb_array_elements(coalesce(m.details->'samples', '[]'::jsonb)) s
 WHERE m.metric = 'outbox_lag_p99' AND m.scope = 'global'
   AND (s->>'ts')::timestamptz > to_timestamp(%s)" "$1"
}

smoke_beat() {
  titulo "Paso 7d — Celery beat VIVO (el ping del worker NO lo prueba)"
  local cmd muestras base t0 ahora transcurrido
  cmd=$("$DOCKER" inspect -f '{{json .Config.Cmd}}' "$BEAT_CONTENEDOR" 2>/dev/null || true)
  case "$cmd" in
    *'"-B"'*) printf 'diagnóstico: %s arranca con -B (beat embebido, RUNBOOK §5)\n' "$BEAT_CONTENEDOR" ;;
    *) printf '⚠ diagnóstico: el command de %s NO trae -B (%s). El beat va EMBEBIDO en el worker; esto explicaría el rojo que viene.\n' "$BEAT_CONTENEDOR" "${cmd:-desconocido}" ;;
  esac

  "$DOCKER" logs --tail "$BEAT_LOG_LINEAS" "$BEAT_CONTENEDOR" >"$WORK_DIR/beat.log" 2>&1 ||
    morir "no se pudo leer el log de $BEAT_CONTENEDOR"
  grep -q 'beat: Starting' "$WORK_DIR/beat.log" ||
    morir "«beat: Starting» no aparece en las últimas $BEAT_LOG_LINEAS líneas de $BEAT_CONTENEDOR: el planificador de las nueve cadencias NO arrancó. El ping del worker NO lo prueba (R5 P1-C)"

  # El instante en que empieza el sondeo y la LÍNEA BASE en ese mismo instante:
  # lo que ya hubiera ahí —incluida una muestra fechada en el futuro— entra en
  # la base y no prueba nada. Solo cuenta el CRECIMIENTO (R7 P1-4).
  t0=$(date +%s)
  base=$(psql_valor "$(sql_muestras_posteriores "$t0")")
  entero "$base" "muestras de outbox_lag_p99 posteriores al inicio del sondeo"
  [ "$base" -eq 0 ] ||
    printf '⚠ ya hay %s muestra(s) de outbox_lag_p99 fechadas DESPUÉS de %s (¿reloj desviado, o datos sembrados?). Entran en la línea base: harán falta %s para dar verde.\n' \
      "$base" "$t0" "$((base + 1))"
  printf 'esperando hasta %ss (dos cadencias de 5 min) a que el beat despache «%s» Y el número de muestras posteriores a %s pase de %s\n' \
    "$BEAT_ESPERA" "$BEAT_CADENCIA" "$t0" "$base"
  while :; do
    sleep "$BEAT_SONDEO"
    ahora=$(date +%s); transcurrido=$((ahora - t0 + 2))
    "$DOCKER" logs --since "${transcurrido}s" "$BEAT_CONTENEDOR" >"$WORK_DIR/beat.nuevo.log" 2>&1 || true
    muestras=$(psql_valor "$(sql_muestras_posteriores "$t0")") || muestras=$base
    entero "$muestras" "muestras de outbox_lag_p99 posteriores al inicio del sondeo"
    if grep -qE "Sending due task \[?$BEAT_CADENCIA" "$WORK_DIR/beat.nuevo.log" &&
       [ "$muestras" -gt "$base" ]; then
      printf 'beat VIVO: despachó «%s» y esa cadencia dejó una muestra NUEVA de outbox_lag_p99 (%s > %s posteriores a %s)\n' \
        "$BEAT_CADENCIA" "$muestras" "$base" "$t0"
      return 0
    fi
    [ $((ahora - t0)) -lt "$BEAT_ESPERA" ] || break
  done
  morir "el beat de $BEAT_CONTENEDOR no probó la cadencia del muestreador en ${BEAT_ESPERA}s: hacen falta LAS DOS señales de la MISMA capacidad —un «Sending due task $BEAT_CADENCIA» nuevo Y una muestra de outbox_lag_p99 NUEVA (hay $muestras posteriores a $t0 y la línea base era $base)—. Otra cadencia en el log, una muestra que entra por su cuenta, o una fila fechada en el futuro, NO prueban que el planificador siga despachando (R6 P1-3, R7 P1-4): NO se abre a nadie"
}

# `smoke` NO toma el cerrojo de la base a propósito: es el único subcomando que
# no ejecuta ni una escritura —solo `SELECT`, `docker inspect/logs/exec` y una
# petición a /v1/ready—, y retenerlo hasta once minutos bloquearía justo la
# salida de emergencia. Todo lo que sí escribe (Pasos 1–6 y la marcha atrás)
# entra por `cutover` o por `restaurar`, y los dos comparten ese cerrojo.
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

  # El beat va el ÚLTIMO porque es el único que ESPERA: los rojos baratos
  # (contenedores, ping, slot, /v1/ready) salen antes de gastar dos cadencias.
  smoke_beat

  # Evidencia para el acta. NO es postcondición de nada: las postcondiciones son
  # las de 7a, 7b y 7d, que sí paran.
  "$DOCKER" logs --tail 50 swissjob-core-capture || true
  "$DOCKER" logs --tail 50 swissjob-worker || true
  printf '\nsmoke OK — release %s, %s autoritativo, seis contenedores sanos con las imágenes del Paso 3 y beat VIVO.\n' \
    "$RELEASE_ESPERADA" "$READY_STATUS_ESPERADO"
}

# --------------------------------------------------------------------------
main() {
  mkdir -p "$WORK_DIR"
  case "${1:-}" in
    cutover)
      guarda_ensayo
      # Dos cutovers a la vez confirmarían las copias SQL dos veces y se
      # pisarían el WORK_DIR y el backup; y un cutover con una restauración en
      # curso reescribe la base que la otra está reconstruyendo. Es el MISMO
      # cerrojo que toma `restaurar` —el de la base—, va ANTES de tocar nada y
      # su ruta NO depende de BACKUP_DIR (R7 P1-2).
      mkdir -p "$BACKUP_DIR"
      tomar_cerrojo "$(cerrojo_de_la_base)"
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

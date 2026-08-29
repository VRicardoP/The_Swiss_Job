"""El cutover del NAS FALLA CERRADO: cada etapa rota tiene que salir distinto de cero.

REGRESIÓN auditoría externa R3 P1-1. El runbook decía «si no cuadra, se PARA» y sus
postcondiciones eran prosa: `docker ps` no comprobaba nada, `pg_dump | gzip` devolvía el
estado de `gzip` y `psql | tee` el de `tee`. El auditor lo ejecutó literalmente con los
cinco escritores vivos y obtuvo `POSTCOND_EXIT=0`; con la SEGUNDA copia SQL abortada tras
confirmar la primera, la secuencia seguía hasta el Paso 5 — el estado que el propio
documento declara IRREPARABLE (`core0025` sella las cohortes).

REGRESIÓN auditoría externa R4 (los cinco P1 viven en este script). Los dobles de R3
respetaban precisamente los agregados y las cadenas que había que poner en duda, así que
aquí se rompen a propósito:

  P1-1  un `CORE_DSN` de PRODUCCIÓN con `?query`, `#fragmento` o percent-encoding
        esquivaba la guarda del ensayo (comparaba por SUFIJO) y llegaba al Paso 5 en
        firme. Además el DSN podía ser sintácticamente inocente y apuntar a otra base:
        ahora hay una SONDA que compara la identidad de la base por psql y por el core.
  P1-2  el enclavamiento 4b era un oráculo APROXIMADO propio del script: paraba por pares
        que G3/G6 no remapean (67 contra 0 en local). El caso `enclavamiento_falso_rojo`
        exige que eso ya NO pare; `paso4b_enclavamiento` exige que lo que los ensayos SÍ
        declaran siga parando.
  P1-3  las cuatro invariantes eran cantidades: `paso6_identidad_*` mueve la identidad sin
        mover ni una cifra, que es exactamente lo que el auditor reprodujo.
  P1-4  la copia era SQL plano y la restauración documentada devolvía 0 dejando una mezcla:
        aquí la copia tiene que ser un archivo que `pg_restore` lea, y `restaurar` es un
        subcomando con verificación contra el manifiesto pre-corte.
  P1-5  `Up 3 minutes (unhealthy)` empieza por `Up` y pasaba: ahora el estado se lee con
        `docker inspect` y hay casos para ausente/exited/restarting/unhealthy/healthy,
        frontend, IDs de imagen y sondas acotadas de Celery y del slot.

La guarda de orden (`test_deploy_order.py`) protege la SECUENCIA; no puede demostrar que
una etapa rota la detenga. Esto sí: ejecuta `backend/scripts/nas_cutover.sh` con dobles de
`docker` y `psql` en el PATH (el doble de `docker` delega en el de `psql`, igual que el
runbook, que invoca `psql` a través de `docker exec`), inyecta un fallo por etapa y exige
salida no cero. Los caminos felices salen 0: sin eso, «todo rojo» no demostraría nada.

`sha256sum` NO se dobla —es el real, dentro del doble de `docker exec`— así que el sello
de la copia y su comprobación en `restaurar` se ejercitan de verdad.

Lo que estos dobles NO pueden decidir es si `pg_restore` traga el esquema REAL: eso se
probó aparte, restaurando un volcado del corpus de producción en una base DESECHABLE
(2026-08-28). Allí salieron los dos modos de fallo que el diseño inicial de la marcha
atrás no veía —`--clean` no sabe soltar las constraints heredadas de las particiones de
`offer_embeddings`, y el índice HNSW en paralelo no cabe en el `/dev/shm` de 64 MB de
Docker—, y por eso la base se RECREA y se restaura sin paralelismo de mantenimiento.
"""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_REPO = Path("/app")
_SCRIPT = _REPO / "backend" / "scripts" / "nas_cutover.sh"

# Cifras del corpus SIMULADO. No son las del NAS ni las locales: el script no lleva
# ninguna constante de corpus, mide antes y aserta contra lo que él mismo midió, y estas
# cifras solo tienen que ser COHERENTES entre sí:
#   slots después = 100 + 2 informes × 4 slots de clones = 108
#   jobs  después = 1000 − 2 informes × 3 clones fusionados = 994
_ANTES = {"slots": 100, "jobs": 1000, "pares": 10, "juicios": "5 5"}
_DESPUES = {"slots": 108, "jobs": 994, "pares": 10, "juicios": "5 5"}
_RELEASE = "deadbee"
_HEAD = "core0035"
# Las tres imágenes del Paso 3 y el ID que el doble les asigna: el smoke exige que los
# contenedores recreados corran EXACTAMENTE estas.
_IMAGENES = ("swissjob-core:prod", "swissjob-backend:prod", "swissjob-frontend:prod")

# Hashes del corpus SIMULADO (md5 = 32 hex). Cada copia declara UN remapeo de
# superviviente (`…01` → `…0a`) y un clon que desaparece (`…02`). `C`/`D` son
# vacantes que NINGÚN ensayo en seco declara: la permuta hacia ellas es la
# reproducción del falso verde de R5 P1-A.
_HA, _HB = "a" * 30, "b" * 30
_HC, _HD = "c" * 30, "d" * 30


def _id_imagen(tag: str) -> str:
    limpio = "".join(c if c.isalnum() else "_" for c in tag)
    return f"sha256:id-de-{limpio}"


_DOBLE_DOCKER = r"""#!/usr/bin/env bash
# DOBLE de `docker`. $ROMPER nombra la etapa que se rompe; $MATAR_EN, el borde
# de la marcha atrás donde el proceso muere de golpe (auditoría R5 P1-B).
set -u
d=$DOBLES_DIR
r=${ROMPER:-}
beat=${BEAT_CONTENEDOR:-swissjob-core-worker}

# `SIGKILL` al GRUPO de procesos: no ejecuta traps y es lo que hace un corte de
# corriente o un `docker kill`. El script corre en su propia sesión
# (`start_new_session`), así que esto no alcanza a pytest.
matar_grupo() {
  [ "${MATAR_EN:-}" = "$1" ] || return 0
  printf '%s\n' "$1" >>"$d/muertes"
  kill -9 -"$(awk '{print $5}' /proc/self/stat)"
  sleep 30
}
# La identidad que ve psql y la que declara el core tienen que coincidir salvo
# cuando se rompe la sonda a propósito.
IDENTIDAD="${PG_DB:-swissjobhunter}|16384|2026-08-28 00:00:00.000000"

id_imagen() { printf 'sha256:id-de-%s' "$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_')"; }
imagen_de() {
  case "$1" in
    swissjob-backend|swissjob-worker) printf 'swissjob-backend:prod' ;;
    swissjob-frontend)                printf 'swissjob-frontend:prod' ;;
    *)                                printf 'swissjob-core:prod' ;;
  esac
}

case "$1" in
  stop) : >"$d/parados"; exit 0 ;;
  ps)
    if [ ! -f "$d/parados" ] || [ "$r" = paso1 ] || [ "$r" = restaurar_escritor_vivo ]; then
      vivos="swissjob-backend swissjob-worker swissjob-core-api swissjob-core-worker swissjob-core-capture"
    else
      vivos="swissjob-postgres swissjob-redis"
    fi
    for v in $vivos; do
      case "$*" in *Status*) printf '%s\t%s\n' "$v" "Up 3 minutes (healthy)" ;;
                   *)        printf '%s\n' "$v" ;; esac
    done
    exit 0 ;;
  rmi) exit 0 ;;
  logs)
    # Señales del beat EMBEBIDO (R5 P1-C). `--since` = solo lo NUEVO: un
    # `Sending due task` ahí prueba que el planificador sigue vivo AHORA.
    for a in "$@"; do nombre=$a; done
    [ "$nombre" = "$beat" ] || exit 0
    case "$*" in
      *--since*)
        # R6 P1-3: `beat_desacoplado` despacha OTRA cadencia (el proyector) y
        # NO el muestreador. Es el falso verde: el smoke miraba «alguna de las
        # cuatro» y una muestra que sube por su cuenta.
        case "$r" in
          # Ni una traza nueva: el planificador no despacha nada.
          smoke_sin_beat|smoke_beat_muerto|muestra_sin_sampler) ;;
          beat_desacoplado)
            echo "[INFO/Beat] Scheduler: Sending due task shadow-project (jobhunt.shadow.project)" ;;
          *)
            echo "[INFO/Beat] Scheduler: Sending due task shadow-sample-outbox-lag (jobhunt.shadow.sample_outbox_lag)"
            echo "[INFO/Beat] Scheduler: Sending due task shadow-project (jobhunt.shadow.project)" ;;
        esac
        exit 0 ;;
    esac
    [ "$r" = smoke_sin_beat ] || echo "[INFO/Beat] beat: Starting..."
    echo "[INFO/MainProcess] celery@simulado ready."
    exit 0 ;;
  load) [ "$r" = paso3_load ] && exit 1; exit 0 ;;
  image)
    # `docker image inspect <img>` (existe) y `docker image inspect -f '{{.Id}}' <img>`.
    if [ "${3:-}" = "-f" ]; then id_imagen "${5:-}"; printf '\n'; fi
    exit 0 ;;
  inspect)
    # `docker inspect -f <tmpl> <nombre>`: la red del core, el command (para el
    # diagnóstico del beat) o el estado de un contenedor.
    nombre=${4:-}
    if [ "$nombre" = swissjob-core-migrate ]; then echo "swissjob_core-net "; exit 0; fi
    case "${3:-}" in
      *Config.Cmd*)
        # El worker SIN `-B` es el falso verde de R5 P1-C: contesta al ping
        # igual, porque el ping prueba el consumidor y no el planificador.
        if [ "$r" = smoke_sin_beat ]; then
          echo '["celery","-A","jobhunt_core.celery_app","worker","-Q","core.default"]'
        else
          echo '["celery","-A","jobhunt_core.celery_app","worker","-B","-Q","core.default"]'
        fi
        exit 0 ;;
    esac
    [ "$r" = smoke_ausente ] && [ "$nombre" = swissjob-core-worker ] && exit 1
    [ "$r" = smoke_frontend_ausente ] && [ "$nombre" = swissjob-frontend ] && exit 1
    estado=running
    salud=sin-healthcheck
    case "$nombre" in
      swissjob-backend|swissjob-core-api|swissjob-core-capture|swissjob-frontend) salud=healthy ;;
    esac
    [ "$r" = smoke_unhealthy ] && [ "$nombre" = swissjob-core-capture ] && salud=unhealthy
    [ "$r" = smoke_frontend_unhealthy ] && [ "$nombre" = swissjob-frontend ] && salud=unhealthy
    [ "$r" = smoke_exited ] && [ "$nombre" = swissjob-core-worker ] && estado=exited
    [ "$r" = smoke_restarting ] && [ "$nombre" = swissjob-backend ] && estado=restarting
    img=$(id_imagen "$(imagen_de "$nombre")")
    [ "$r" = smoke_imagen ] && [ "$nombre" = swissjob-frontend ] && img=sha256:otra-imagen
    printf '%s|%s|%s\n' "$estado" "$salud" "$img"
    exit 0 ;;
  exec)
    shift
    while [ "${1#-}" != "$1" ]; do shift; done
    contenedor=$1; shift
    case "$1" in
      pg_dump)
        [ "$r" = paso2_pg_dump ] && { echo "pg_dump: error: connection to server failed" >&2; exit 1; }
        [ "${LENTO_EN:-}" = pg_dump ] && { : >"$d/lento"; sleep 20; }
        printf 'PGDMP-SIMULADO-%s\n' "${PG_DB:-swissjobhunter}"
        exit 0 ;;
      pg_restore)
        if [ "${2:-}" = "-l" ]; then          # índice del archivo
          cat >/dev/null
          [ "$r" = paso2_toc ] && { echo "pg_restore: error: could not read from input file" >&2; exit 1; }
          [ "$r" = restaurar_toc ] && { echo "pg_restore: error: could not read from input file" >&2; exit 1; }
          [ "$r" != paso2_sin_public ]  && echo "216; 1259 51066562 TABLE public jobs swissjob"
          [ "$r" != paso2_sin_jobhunt ] && echo "217; 1259 51066567 TABLE jobhunt sources swissjob"
          exit 0
        fi
        cat >/dev/null                        # la restauración de verdad
        matar_grupo en_restore
        [ "$r" = restaurar_pgrestore ] && { echo "pg_restore: error: relation does not exist" >&2; exit 1; }
        [ "${LENTO_EN:-}" = pg_restore ] && { : >"$d/lento"; sleep 20; }
        # La base vuelve al estado ANTES: es lo que el doble de psql lee de `$d/firme`.
        [ "$r" = restaurar_verificacion ] || rm -f "$d/firme"
        : >"$d/restaurado"
        matar_grupo tras_restore
        exit 0 ;;
      sha256sum)
        # El REAL: el sello de la copia y su comprobación se ejercitan de verdad.
        if [ "$r" = restaurar_sha ]; then
          cat >/dev/null
          echo "0000000000000000000000000000000000000000000000000000000000000000  -"
          exit 0
        fi
        exec sha256sum ;;
      psql) shift; exec psql "$@" ;;
      sh)
        # La sonda ACOTADA de los workers sin healthcheck (ping dirigido de Celery).
        [ "$r" = smoke_celery ] && { echo "Error: No nodes replied within time constraint" >&2; exit 1; }
        printf -- '->  celery@simulado: OK\n        pong\n\n1 node online.\n'
        exit 0 ;;
      python)
        # El smoke: el programa embebido en el script se ejecuta DE VERDAD contra el
        # servidor HTTP que levanta el test en 127.0.0.1:8000.
        shift; [ "$1" = "-" ] && shift
        cat >"$d/smoke.py"; exec python3 "$d/smoke.py" "$@" ;;
    esac
    exit 0 ;;
  run)
    case "$*" in
      *"/opt/jobhunt-release/RELEASE"*)
        [ "$r" = paso3_release_unknown ] && { echo unknown; exit 0; }
        echo "RELEASE_SIMULADA"; exit 0 ;;
      *identidad_destino*)
        # La sonda de destino (R4 P1-1): la identidad que ve el DSN del core.
        if [ "$r" = sonda_destino ]; then echo "otra_base|99999|2000-01-01 00:00:00.000000"
        else echo "$IDENTIDAD"; fi
        exit 0 ;;
      *canonical_refs*)
        n=$(cat "$d/canon" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" >"$d/canon"
        juicios=2
        case "$*" in *--dry-run*) seco=true ;; *) seco=false ;; esac
        # `filas_canonizadas_en_legacy` = tamaño del mapa que el módulo
        # RECONSTRUYE de `jobs`: tiene que ser el que declararon los ensayos
        # (un remapeo por copia = 2), o el Paso 5 movería etiquetas que el
        # Paso 6 no puede verificar (R5 P1-A).
        filas=2
        [ "$r" = paso5_mapa_ajeno ] && filas=5
        [ "$n" = 3 ] && filas=0                                  # idempotente: ceros
        [ "$n" = 3 ] && [ "$r" = paso5_idempotencia ] && filas=2
        [ "$n" = 2 ] && [ "$r" = paso5_json ] && juicios=3
        printf '{\n  "filas_canonizadas_en_legacy": %s,\n  "juicios_remapeados": %s,\n  "pares_remapeados": 1,\n  "dry_run": %s\n}\n' \
          "$filas" "$juicios" "$seco"
        exit 0 ;;
    esac
    exit 0 ;;
esac
exit 0
"""

_DOBLE_PSQL = r"""#!/usr/bin/env bash
# DOBLE de `psql`. El de `docker` delega aquí, igual que el runbook via `docker exec`.
set -u
d=$DOBLES_DIR
r=${ROMPER:-}
A=__HA__; B=__HB__; C=__HC__; D=__HD__

matar_grupo() {
  [ "${MATAR_EN:-}" = "$1" ] || return 0
  printf '%s\n' "$1" >>"$d/muertes"
  kill -9 -"$(awk '{print $5}' /proc/self/stat)"
  sleep 30
}

# CATÁLOGO simulado de `pg_database`: sin él la marcha atrás reanudable no se
# puede probar, porque su resolución de estado es «qué bases existen».
catalogo="$d/bases"
[ -f "$catalogo" ] || printf '%s\n' "${PG_DB:-swissjobhunter}" >"$catalogo"
existe_base() { grep -qx -- "$1" "$catalogo"; }
alta_base() { existe_base "$1" || printf '%s\n' "$1" >>"$catalogo"; }
baja_base() { grep -vx -- "$1" "$catalogo" >"$catalogo.tmp" || true; mv "$catalogo.tmp" "$catalogo"; }
plano() { printf '%s' "$1" | tr '\n' ' '; }
nombre_de() { plano "$sql" | sed -n "s/.*$1 \"\([^\"]*\)\".*/\1/p"; }
nombre_datname() { plano "$sql" | sed -n "s/.*datname = '\([^']*\)'.*/\1/p"; }

# MANIFIESTOS SEMÁNTICOS (R5 P1-A). La transformación declarada por los ensayos
# en seco es `…01 -> …0a` en las dos copias; todo lo demás no se mueve.
manifiesto_juicios() {
  if [ "$1" = antes ]; then printf 'j-1|%s01|1|seed\nj-2|%s02|0|seed\n' "$A" "$B"; return; fi
  case "$r" in
    paso6_juicio_permutado)  printf 'j-1|%s0a|1|seed\nj-2|%s02|0|seed\n' "$C" "$B" ;;
    paso6_relevancia)        printf 'j-1|%s0a|0|seed\nj-2|%s02|0|seed\n' "$A" "$B" ;;
    paso6_identidad_juicios) printf 'j-3|%s0a|1|seed\nj-2|%s02|0|seed\n' "$A" "$B" ;;
    *)                       printf 'j-1|%s0a|1|seed\nj-2|%s02|0|seed\n' "$A" "$B" ;;
  esac
}
manifiesto_pares() {
  if [ "$1" = antes ]; then
    printf 'p-1|cohorte-1|duplicate|%s01|%s01\np-2|cohorte-1|distinct|%s03|%s04\n' "$A" "$B" "$A" "$A"
    return
  fi
  case "$r" in
    # LA reproducción del falso verde: el par sigue existiendo, resolviendo y
    # con el MISMO `pair_id`, pero sus dos lados apuntan a otras vacantes.
    paso6_permuta_par)     printf 'p-1|cohorte-1|duplicate|%s0a|%s0a\np-2|cohorte-1|distinct|%s03|%s04\n' "$C" "$D" "$A" "$A" ;;
    paso6_veredicto)       printf 'p-1|cohorte-1|distinct|%s0a|%s0a\np-2|cohorte-1|distinct|%s03|%s04\n' "$A" "$B" "$A" "$A" ;;
    paso6_identidad_pares) printf 'p-1|cohorte-1|duplicate|%s0a|%s0a\np-3|cohorte-1|distinct|%s03|%s04\n' "$A" "$B" "$A" "$A" ;;
    *)                     printf 'p-1|cohorte-1|duplicate|%s0a|%s0a\np-2|cohorte-1|distinct|%s03|%s04\n' "$A" "$B" "$A" "$A" ;;
  esac
}
resuelven_juicios() {
  if [ "$1" = antes ]; then printf 'j-1|%s01\nj-2|%s02\n' "$A" "$B"; return; fi
  case "$r" in
    paso6_juicio_no_resuelve) printf 'j-2|%s02\n' "$B" ;;
    paso6_juicio_permutado)   printf 'j-1|%s0a\nj-2|%s02\n' "$C" "$B" ;;
    paso6_identidad_juicios)  printf 'j-3|%s0a\nj-2|%s02\n' "$A" "$B" ;;
    *)                        printf 'j-1|%s0a\nj-2|%s02\n' "$A" "$B" ;;
  esac
}
resuelven_pares() {
  if [ "$1" = antes ]; then printf 'p-1\np-2\n'; return; fi
  case "$r" in
    paso6_par_no_resuelve) printf 'p-1\n' ;;
    paso6_identidad_pares) printf 'p-1\np-3\n' ;;
    *) printf 'p-1\np-2\n' ;;
  esac
}
guardas() {
  # Las guardas de inmutabilidad, todas en `ENABLE ALWAYS` ('A'). Degradarlas
  # SOLO despues de restaurar es el caso «la vuelta atras trajo los datos pero
  # no las guardas».
  estado=A
  [ "$r" = restaurar_guarda_degradada ] && [ -f "$d/restaurado" ] && estado=D
  for n in 1 2 3 4 5 6 7; do
    printf 'jobhunt|labeled_dedup_pairs|trg_inmutable_%s|%s\n' "$n" "$estado"
  done
}
# Epoch de la muestra MÁS NUEVA de `outbox_lag_p99` — el instrumento VIEJO
# (R6 P1-3). Se conserva para que la mordida funcione: contra el script padre,
# que preguntaba por el máximo, `muestra_futura` devuelve una fecha de 2100 y
# el smoke daba VERDE. Contra el script corregido nadie hace esta pregunta.
muestra_outbox() {
  case "$r" in
    smoke_sin_beat|smoke_beat_muerto|sampler_sin_muestra) echo 1000000000 ;;
    muestra_pasada) echo 1000000000 ;;          # 2001: anterior al sondeo
    muestra_futura) echo 4102444800 ;;          # 2100-01-01: siempre > t0
    *) date +%s ;;
  esac
}
# CUÁNTAS muestras hay posteriores al instante que pregunta el smoke (R7 P1-4).
# La primera llamada es la LÍNEA BASE, en el propio `t0`; las siguientes ven lo
# que haya entrado desde entonces.
#   · sin beat / beat muerto: no entra ninguna, nunca ⇒ 0 siempre.
#   · `muestra_futura`: hay UNA fechada en 2100 que ya estaba ahí. Cuenta en la
#     línea base y no crece jamás ⇒ 1 siempre. Es el falso verde de R7 P1-4.
#   · el resto: 0 en la base y 1 en cuanto el muestreador deja una nueva.
muestras_posteriores() {
  local n
  case "$r" in
    # Ninguna muestra nueva: sin beat, con el beat muerto, o con el muestreador
    # despachado pero sin que su ejecución deje nada (`sampler_sin_muestra`), y
    # el caso de una muestra ANTERIOR al sondeo, que tampoco prueba nada.
    smoke_sin_beat|smoke_beat_muerto|sampler_sin_muestra|muestra_pasada) echo 0; return ;;
    # UNA fechada en 2100 que ya estaba: cuenta en la línea base y no crece.
    muestra_futura) echo 1; return ;;
  esac
  n=$(cat "$d/muestras" 2>/dev/null || echo 0)
  echo "$n"
  echo 1 >"$d/muestras"
}

sql=""
esperando=0
for a in "$@"; do
  [ "$esperando" = 1 ] && { sql=$a; esperando=0; }
  [ "$a" = "-c" ] && esperando=1
done

if [ -n "$sql" ]; then                      # consultas escalares y manifiestos
  fase=antes; [ -f "$d/firme" ] && fase=despues
  case "$sql" in
    *manifiesto-juicios*) manifiesto_juicios "$fase"; exit 0 ;;
    *manifiesto-pares*)   manifiesto_pares "$fase"; exit 0 ;;
    *resuelven-juicios*)  resuelven_juicios "$fase"; exit 0 ;;
    *resuelven-pares*)    resuelven_pares "$fase"; exit 0 ;;
    *guardas-inmutabilidad*) guardas; exit 0 ;;
    *muestras-outbox-posteriores*) muestras_posteriores; exit 0 ;;
    *muestra-outbox*)     muestra_outbox; exit 0 ;;
  esac
  case "$sql" in
    *atributos-base*)                       # atributos de la base a recrear
      base=$(nombre_datname)
      existe_base "$base" && echo "UTF8|en_US.utf8|en_US.utf8|swissjob|-1|" ;;
    *existe-base*)
      base=$(nombre_datname); existe_base "$base" && echo 1 ;;
    *settings-base*)
      [ "$r" = restaurar_settings ] && echo "0=search_path=jobhunt, public" ;;
    *hashes-no-reproducibles*)
      if [ "$r" = paso4_hashes_fantasma ]; then echo 3; else echo 0; fi ;;
    *pg_terminate_backend*) : ;;
    *"RENAME TO"*)
      matar_grupo antes_rename
      [ "$r" = restaurar_rename ] && { echo "ERROR: database is being accessed by other users" >&2; exit 1; }
      viejo=$(nombre_de "ALTER DATABASE"); nuevo=$(nombre_de "RENAME TO")
      baja_base "$viejo"; alta_base "$nuevo"
      matar_grupo tras_rename ;;
    *"DROP DATABASE"*)
      baja_base "$(nombre_de "DROP DATABASE")" ;;
    *"CREATE DATABASE"*)
      matar_grupo en_create
      [ "$r" = restaurar_create ] && { echo "ERROR: permission denied to create database" >&2; exit 1; }
      alta_base "$(nombre_de "CREATE DATABASE")"
      matar_grupo tras_create ;;
    *max_parallel_maintenance_workers*|*"CONNECTION LIMIT"*) : ;;
    *pg_postmaster_start_time*)             # sonda de destino (R4 P1-1)
      echo "${PG_DB:-swissjobhunter}|16384|2026-08-28 00:00:00.000000" ;;
    *pg_replication_slots*)                 # sonda de progreso del CDC (R4 P1-5)
      case "$sql" in
        *"SELECT active"*)
          if [ "$r" = smoke_slot_inactivo ]; then echo f; else echo t; fi ;;
        *)
          n=$(cat "$d/lag" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" >"$d/lag"
          if [ "$r" = smoke_slot_atrasado ]; then
            if [ "$n" = 1 ]; then echo 1000; else echo 999999999; fi
          else
            if [ "$n" = 1 ]; then echo 500; else echo 100; fi
          fi ;;
      esac ;;
    *labeled_dedup_cohorts*)
      # El oráculo APROXIMADO que el script tenía en Bash (R4 P1-2). Ya no se
      # consulta; sigue aquí para demostrar que un 67 no vuelve a parar nada.
      if [ "$r" = paso4b_enclavamiento_legacy ] || [ "$r" = enclavamiento_falso_rojo ]; then
        echo 67
      else echo 0; fi ;;
    *"j.hash IS NULL"*)
      if [ "$fase" = antes ]; then echo __SLOTS_ANTES__
      elif [ "$r" = paso6_slots ]; then echo 999
      else echo __SLOTS_DESPUES__; fi ;;
    *"FROM public.jobs"*)
      if [ "$fase" = antes ]; then echo __JOBS_ANTES__
      elif [ "$r" = paso6_jobs ]; then echo __JOBS_ANTES__
      else echo __JOBS_DESPUES__; fi ;;
    *labeled_judgments*)
      if [ "$fase" = antes ]; then echo "__JUICIOS_ANTES__"
      elif [ "$r" = paso6_juicios ]; then echo "5 4"
      else echo "__JUICIOS_DESPUES__"; fi ;;
    *labeled_dedup_pairs*)
      if [ "$fase" = antes ]; then echo __PARES_ANTES__
      elif [ "$r" = paso6_pares ]; then echo 9
      else echo __PARES_DESPUES__; fi ;;
  esac
  exit 0
fi

cuerpo=$(cat)                               # las dos copias y la verificación por hash, por `-f -`

# Verificación por IDENTIDAD de las vacantes (R4 P1-3): el script la GENERA con los
# hashes que los dry-runs declararon.
case "$cuerpo" in
  *ident_desaparece*)
    viejas=0; canonicas=0
    [ "$r" = paso6_identidad_jobs ] && viejas=2
    [ "$r" = paso6_identidad_canonicas ] && canonicas=1
    printf 'identidad: viejas que NO desaparecieron|%s\n' "$viejas"
    printf 'identidad: canonicas que NO aparecieron|%s\n' "$canonicas"
    exit 0 ;;
esac

case "$cuerpo" in *"COMMIT;"*) modo=firme ;; *) modo=seco ;; esac
case "$cuerpo" in *"-- G6"*) copia=segunda ;; *) copia=primera ;; esac
[ "$modo" = firme ] && : >"$d/firme"

if [ "$r" = "paso4a_$copia" ] && [ "$modo" = seco ]; then
  echo "psql:-:215: ERROR:  no se pudo bloquear la fila" >&2; exit 3
fi
if [ "$r" = "paso4c_$copia" ] && [ "$modo" = firme ]; then
  echo "psql:-:930: ERROR:  cohorte sellada: el par es inmutable" >&2; exit 3
fi

senal=0; [ "$r" = paso4b_senal ] && senal=2
clones=3; [ "$r" = paso4c_informe ] && [ "$modo" = firme ] && clones=4
# El enclavamiento lo DECLARA el propio ensayo desde su tabla de supervivientes (R4 P1-2).
enclave=0; [ "$r" = paso4b_enclavamiento ] && enclave=1
cat <<EOF
BEGIN
reescritas|12
clones fusionados|$clones
match_results descartados por la fusion|$senal
  ... de ellos CON senal del usuario|$senal
sombra: slots reapuntados al hash canonico|9
sombra: slots de clones (los cierra el op=D del PASO 6)|4
EOF
[ "$r" != enclavamiento_sin_concepto ] &&
  printf 'enclavamiento: refs de cohortes SELLADAS que ESTE script remapea|%s\n' "$enclave"

# IDENTIDADES declaradas: dos hashes que desaparecen y uno canónico por copia.
if [ "$copia" = primera ]; then p=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; else p=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; fi
sufijo=01
[ "$r" = paso4c_ident ] && [ "$modo" = firme ] && sufijo=0f
printf 'IDENT|desaparece|%s%s\n' "$p" "$sufijo"
printf 'IDENT|desaparece|%s02\n' "$p"
printf 'IDENT|canonico|%s0a\n' "$p"
# LA TRANSFORMACIÓN declarada (R5 P1-A): solo el superviviente, que es el mapa
# que `canonical_refs` reconstruye de `jobs` y aplica a las etiquetas.
remap_viejo="${p}01"; remap_nuevo="${p}0a"
[ "$r" = paso4c_remap ] && [ "$modo" = firme ] && remap_nuevo="${p}0b"
[ "$r" = remap_no_md5 ] && remap_nuevo="no-es-un-md5"
[ "$r" = remap_ambiguo ] && printf 'IDENT|remap|%s|%s0c\n' "$remap_viejo" "$p"
[ "$r" = remap_encadenado ] && printf 'IDENT|remap|%s0a|%s0d\n' "$p" "$p"
printf 'IDENT|remap|%s|%s\n' "$remap_viejo" "$remap_nuevo"
exit 0
"""


_DOBLE_SYNC = r"""#!/usr/bin/env bash
# DOBLE de `sync`. Dos modos:
#   · $ROMPER=sync_falla — falla SIEMPRE (R6 P1-2, R7 P1-1).
#   · $SYNC_FALLA_EN=N   — falla solo en la N-ésima barrera de esta invocación,
#     que es lo que permite recorrer las barreras UNA A UNA en vez de probar
#     solo la primera. El orden de las barreras es el del programa.
# En cualquier otro caso no hace falta sincronizar un tmpdir.
d=${DOBLES_DIR:-/tmp}
[ "${ROMPER:-}" = sync_falla ] && { echo "sync: error writing: Input/output error" >&2; exit 1; }
if [ -n "${SYNC_FALLA_EN:-}" ]; then
  n=$(( $(cat "$d/sync_n" 2>/dev/null || echo 0) + 1 ))
  echo "$n" >"$d/sync_n"
  [ "$n" = "$SYNC_FALLA_EN" ] && {
    echo "sync: error writing: No space left on device" >&2; exit 1; }
fi
exit 0
"""


def _plantar_dobles(raiz: Path) -> Path:
    """Escribe los dos dobles y devuelve el directorio que va al frente del PATH."""
    binarios = raiz / "bin"
    binarios.mkdir(parents=True, exist_ok=True)
    psql = _DOBLE_PSQL
    for clave, valor in (
        ("__HA__", _HA), ("__HB__", _HB), ("__HC__", _HC), ("__HD__", _HD),
        ("__SLOTS_ANTES__", _ANTES["slots"]), ("__SLOTS_DESPUES__", _DESPUES["slots"]),
        ("__JOBS_ANTES__", _ANTES["jobs"]), ("__JOBS_DESPUES__", _DESPUES["jobs"]),
        ("__PARES_ANTES__", _ANTES["pares"]), ("__PARES_DESPUES__", _DESPUES["pares"]),
        ("__JUICIOS_ANTES__", _ANTES["juicios"]), ("__JUICIOS_DESPUES__", _DESPUES["juicios"]),
    ):
        psql = psql.replace(clave, str(valor))
    for nombre, cuerpo in (("docker", _DOBLE_DOCKER), ("psql", psql), ("sync", _DOBLE_SYNC)):
        destino = binarios / nombre
        destino.write_text(cuerpo, encoding="utf-8")
        destino.chmod(0o755)
    return binarios


def _montar_nas(raiz: Path) -> dict[str, str]:
    """Un NAS de mentira: los tres tar, las dos copias SQL y los directorios."""
    base, scripts = raiz / "nas", raiz / "nas" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for tar in ("swissjob-core.tar", "swissjob-backend.tar", "swissjob-frontend.tar"):
        (base / tar).write_text("tar", encoding="utf-8")
    for marca, fichero in (("G3", "g3.sql"), ("G6", "g6.sql")):
        (scripts / fichero).write_text(f"-- {marca}\nBEGIN;\nROLLBACK;\n", encoding="utf-8")
    return {
        "BASE_DIR": str(base),
        "SCRIPTS_DIR": str(scripts),
        "BACKUP_DIR": str(raiz / "backups"),
        "WORK_DIR": str(raiz / "trabajo"),
        "COPIAS_SQL": "g3.sql g6.sql",
        "DOBLES_DIR": str(raiz / "estado"),
        # El cerrojo NO se deriva de BACKUP_DIR (R7 P1-2). Aquí se fija a un
        # directorio propio de la prueba por dos razones: para que dos suites
        # a la vez en el mismo host no se bloqueen entre sí en /var/lock, y
        # para poder mover BACKUP_DIR y comprobar que el cerrojo NO se mueve.
        "LOCK_DIR": str(raiz / "cerrojos"),
        "SLOT_ESPERA": "0",          # la sonda de progreso no tiene que dormir en tests
        # La postcondición del beat espera DOS cadencias de cinco minutos en el
        # NAS; aquí el doble contesta al primer sondeo.
        "BEAT_ESPERA": "3",
        "BEAT_SONDEO": "1",
    }


def _ejecutar(
    raiz: Path,
    subcomando: str,
    romper: str | None,
    *extra: str,
    matar_en: str | None = None,
    lento_en: str | None = None,
    timeout: int = 120,
    override: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    assert _SCRIPT.is_file(), (
        f"{_SCRIPT} no está montado: esta guarda NO puede saltarse. Ejecuta la suite con "
        "el perfil de dev (docker-compose.yml + docker-compose.dev.yml)."
    )
    (raiz / "estado").mkdir(parents=True, exist_ok=True)
    entorno = dict(os.environ)
    entorno.update(_montar_nas(raiz))
    entorno["PATH"] = f"{_plantar_dobles(raiz)}:{entorno['PATH']}"
    entorno["CORE_NET"] = "red-de-mentira"
    for clave, valor in (("ROMPER", romper), ("MATAR_EN", matar_en), ("LENTO_EN", lento_en)):
        if valor:
            entorno[clave] = valor
        else:
            entorno.pop(clave, None)
    # LO ÚLTIMO, a propósito: `_montar_nas` fija BACKUP_DIR/WORK_DIR/LOCK_DIR y
    # machacaría cualquier valor que la prueba quisiera cambiar. Sin esto, el caso
    # «mismo recurso, distinto BACKUP_DIR» se ejecutaba con el MISMO directorio y
    # pasaba en el padre: la fixture anulaba el caso que decía probar.
    entorno.update(override or {})
    return subprocess.run(
        ["bash", str(_SCRIPT), subcomando, *extra],
        env=entorno, capture_output=True, text=True, timeout=timeout,
        # SESIÓN PROPIA: los dobles matan el GRUPO de procesos para reproducir
        # un `SIGKILL` (R5 P1-B). Sin esto se llevarían por delante a pytest.
        start_new_session=True,
    )


# --------------------------------------------------------------------------
# Servidor de /v1/health y /v1/ready para el Paso 7 (el programa del smoke se
# ejecuta DE VERDAD: no hay un segundo parser en el test).
# --------------------------------------------------------------------------
_RESPUESTAS: dict[str, dict] = {}


class _Sonda(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        cuerpo = json.dumps(_RESPUESTAS.get(self.path, {})).encode()
        self.send_response(200 if self.path in _RESPUESTAS else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_):  # silencio
        pass


@pytest.fixture(scope="module")
def sonda():
    servidor = HTTPServer(("127.0.0.1", 8000), _Sonda)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    yield
    servidor.shutdown()


def _preparar_smoke(raiz: Path, romper: str | None) -> None:
    ready = {"status": "ready", "alembic": _HEAD, "release": _RELEASE, "authoritative": True}
    if romper == "smoke_status":
        ready["status"] = "ok"          # LO QUE EL RUNBOOK EXIGÍA (R3 P2-1)
    elif romper == "smoke_release":
        ready["release"] = "0tra1mg"
    elif romper == "smoke_authoritative":
        ready["authoritative"] = False
    elif romper == "smoke_alembic":
        ready["alembic"] = "core0030"
    _RESPUESTAS.clear()
    _RESPUESTAS["/v1/ready"] = ready
    _RESPUESTAS["/v1/health"] = {
        "status": "ok", "release": _RELEASE, "alembic_expected": _HEAD, "authoritative": True,
    }
    trabajo = raiz / "trabajo"
    trabajo.mkdir(parents=True, exist_ok=True)
    if romper != "smoke_sin_paso3":
        lineas = [f"RELEASE_ESPERADA={_RELEASE}"]
        if romper != "smoke_sin_ids":
            lineas += [
                f"IMAGEN_ID_{''.join(c if c.isalnum() else '_' for c in tag)}={_id_imagen(tag)}"
                for tag in _IMAGENES
            ]
        (trabajo / "estado.env").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    (raiz / "estado").mkdir(parents=True, exist_ok=True)
    (raiz / "estado" / "parados").touch()


# --------------------------------------------------------------------------
# Camino feliz: sin él, «todo rojo» no demostraría nada
# --------------------------------------------------------------------------
def test_el_camino_feliz_de_los_pasos_1_a_6_sale_cero(tmp_path):
    p = _ejecutar(tmp_path, "cutover", None)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "las cuatro invariantes del Paso 6 cuadran" in p.stdout
    assert "identidad de vacantes" in p.stdout


def test_el_camino_feliz_del_smoke_sale_cero(tmp_path, sonda):
    _preparar_smoke(tmp_path, None)
    p = _ejecutar(tmp_path, "smoke", None)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "smoke OK" in p.stdout


# --------------------------------------------------------------------------
# P1-4 — la copia tiene que ser RESTAURABLE, y la restauración, todo o nada
# --------------------------------------------------------------------------
def test_la_copia_es_un_archivo_que_pg_restore_lee_y_queda_sellada(tmp_path):
    """R4 P1-4: `.sql.gz` + `psql <` devolvía 0 dejando una MEZCLA pre/post. La copia es
    ahora un archivo `-Fc`, verificado con el mismo `pg_restore` que la restaurará, con
    sha256 y manifiesto pre-corte al lado."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    copias = sorted((tmp_path / "backups").glob("pre_canonizacion_*.dump"))
    assert len(copias) == 1, list((tmp_path / "backups").iterdir())
    assert not list((tmp_path / "backups").glob("*.parcial"))
    assert not list((tmp_path / "backups").glob("*.sql.gz")), "sigue siendo SQL plano"
    manifiesto = (copias[0].parent / (copias[0].name + ".manifiesto")).read_text()
    for clave in ("DUMP_SHA256=", "DUMP_TABLAS_PUBLIC=", "DUMP_TABLAS_JOBHUNT=",
                  "DUMP_PG_DB=", "MEDIDA_JOBS=", "MEDIDA_PARES=", "MEDIDA_JUICIOS="):
        assert clave in manifiesto, manifiesto
    # Y las identidades pre-corte viajan con la copia, no solo en /tmp.
    assert (copias[0].parent / (copias[0].name + ".pares")).is_file()
    assert (copias[0].parent / (copias[0].name + ".juicios")).is_file()


def test_la_restauracion_verifica_sello_manifiesto_e_identidades(tmp_path):
    """La ÚNICA salida de emergencia del procedimiento: tiene que existir, parar a los
    CINCO escritores y comprobar que la vuelta atrás VOLVIÓ."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    p = _ejecutar(tmp_path, "restaurar", None)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "restauración VERIFIED" in p.stdout, p.stdout
    assert "escritores parados" in p.stdout, p.stdout
    # El estado roto se APARTA (rename), no se borra: si la restauración fallara,
    # no se habría perdido nada.
    assert "APARTADO en" in p.stdout, p.stdout
    # Y el slot lógico se queda con la base apartada: hay que re-bootstrapear.
    assert "RUNBOOK.md §3" in p.stdout, p.stdout


@pytest.mark.parametrize(
    "etapa",
    [
        "restaurar_sha",            # el sello no cuadra con la copia
        "restaurar_toc",            # pg_restore no puede leer el archivo
        "restaurar_rename",         # no se puede apartar la base rota
        "restaurar_create",         # no se puede crear la base nueva
        "restaurar_pgrestore",      # la restauración aborta (single-transaction)
        "restaurar_verificacion",   # termina pero el estado NO es el del manifiesto
        "restaurar_escritor_vivo",  # restaurar con escritores vivos deja mezcla
    ],
)
def test_la_restauracion_falla_cerrada(tmp_path, etapa):
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    p = _ejecutar(tmp_path, "restaurar", etapa)
    assert p.returncode != 0, f"la restauración aceptó {etapa}:\n{p.stdout}"
    assert "PARAR" in p.stdout + p.stderr


def test_restaurar_con_un_sidecar_alterado_no_toca_nada(tmp_path):
    """Los sidecars deciden qué significa «VERIFIED», así que van sellados como UNA
    unidad con la copia: alterar uno cambiaría el criterio de la marcha atrás en
    silencio."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    copia = next((tmp_path / "backups").glob("pre_canonizacion_*.dump"))
    sidecar = copia.parent / (copia.name + ".pares")
    sidecar.write_text(sidecar.read_text() + "p-9|cohorte-1|distinct|x|y\n")
    p = _ejecutar(tmp_path, "restaurar", None)
    assert p.returncode != 0
    assert "sidecars" in p.stdout + p.stderr
    assert "APARTADO" not in p.stdout, "tocó la base antes de comprobar el sello"


def test_dos_cutovers_a_la_vez_no_se_pisan(tmp_path):
    """Dos cutovers simultáneos confirmarían las dos copias SQL dos veces y se pisarían
    el WORK_DIR y el backup. El segundo aborta por el cerrojo."""
    import threading
    import time

    resultados: dict[str, subprocess.CompletedProcess] = {}

    def primero():
        resultados["a"] = _ejecutar(tmp_path, "cutover", None, lento_en="pg_dump")

    hilo = threading.Thread(target=primero)
    hilo.start()
    try:
        testigo = tmp_path / "estado" / "lento"
        for _ in range(200):
            if testigo.exists():
                break
            time.sleep(0.1)
        assert testigo.exists(), "el primer cutover no llegó a pg_dump"
        segundo = _ejecutar(tmp_path, "cutover", None)
        assert segundo.returncode != 0, segundo.stdout
        assert "cerrojo" in segundo.stdout + segundo.stderr
        assert "Paso 4c" not in segundo.stdout, segundo.stdout
    finally:
        hilo.join(timeout=180)
    assert resultados["a"].returncode == 0, resultados["a"].stdout + resultados["a"].stderr
    assert len(list((tmp_path / "backups").glob("pre_canonizacion_*.dump"))) == 1


def test_restaurar_sin_manifiesto_no_toca_nada(tmp_path):
    """Sin manifiesto pre-corte no se puede demostrar que la vuelta atrás volvió."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    copia = next((tmp_path / "backups").glob("pre_canonizacion_*.dump"))
    (copia.parent / (copia.name + ".manifiesto")).unlink()
    p = _ejecutar(tmp_path, "restaurar", None)
    assert p.returncode != 0
    assert "manifiesto" in (p.stdout + p.stderr)


# --------------------------------------------------------------------------
# Una etapa rota, una salida no cero
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "etapa",
    [
        "paso1",                 # los cinco escritores siguen vivos tras el `stop`
        "paso2_pg_dump",         # pg_dump falla y el archivo saldría vacío
        "paso2_toc",             # el archivo no se puede leer entero: no hay copia
        "paso2_sin_public",
        "paso2_sin_jobhunt",     # el rol no lee `jobhunt`: la copia no sirve
        "paso3_load",
        "paso3_release_unknown", # imagen sin RELEASE_SHA → authoritative: false
        "sonda_destino",         # el core apunta a OTRA base que psql (R4 P1-1)
        "paso4a_primera",
        "paso4a_segunda",        # preflight de LAS DOS antes de confirmar la primera
        "paso4b_enclavamiento",  # el ensayo DECLARA refs de cohortes selladas
        "enclavamiento_sin_concepto",  # y si no lo declara, tampoco se sigue
        "paso4b_senal",          # descartaría match_results con señal del usuario
        "paso4c_primera",
        "paso4c_segunda",        # EL caso irreparable: la primera ya confirmada
        "paso4c_informe",        # el informe en firme difiere del ensayo
        "paso4c_ident",          # …y las identidades declaradas, también
        "paso5_json",            # la aplicación no cuadra con su --dry-run
        "paso5_idempotencia",
        "paso6_slots",
        "paso6_jobs",
        "paso6_juicios",
        "paso6_pares",
        "paso6_identidad_pares",     # R4 P1-3: cifras iguales, par distinto
        "paso6_identidad_juicios",   # R4 P1-3: cifras iguales, juicio distinto
        "paso6_identidad_jobs",      # un hash declarado fusionado sigue vivo
        "paso6_identidad_canonicas", # un hash canónico declarado no existe
    ],
)
def test_cada_etapa_rota_detiene_la_secuencia(tmp_path, etapa):
    p = _ejecutar(tmp_path, "cutover", etapa)
    assert p.returncode != 0, f"la etapa {etapa} falló y la secuencia SIGUIÓ:\n{p.stdout}"
    assert "PARAR" in p.stdout + p.stderr, p.stdout + p.stderr


def test_la_segunda_copia_abortada_no_deja_pasar_al_paso_5(tmp_path):
    """El caso peor que nombra el runbook, aislado: la primera copia CONFIRMADA y la
    segunda abortada. La secuencia no puede llegar a `canonical_refs`."""
    p = _ejecutar(tmp_path, "cutover", "paso4c_segunda")
    assert p.returncode != 0
    assert "Paso 5" not in p.stdout, p.stdout
    assert "restaurar" in p.stdout + p.stderr


# --------------------------------------------------------------------------
# R4 P1-2 — el enclavamiento no puede parar por pares que G3/G6 no remapean
# --------------------------------------------------------------------------
def test_el_enclavamiento_no_para_por_pares_que_los_scripts_no_remapean(tmp_path):
    """El oráculo aproximado contaba 67 pares en local mientras los mapas de G3/G6
    remapeaban 0: el procedimiento paraba sin motivo y el remedio que sugería (cargar una
    cohorte nueva) podía dejarlo bloqueado para siempre. Hoy la cifra la declara cada
    ensayo desde su PROPIA tabla de supervivientes, y un 67 en la consulta vieja no para
    nada."""
    p = _ejecutar(tmp_path, "cutover", "enclavamiento_falso_rojo")
    assert p.returncode == 0, (
        "el enclavamiento sigue parando por pares que los scripts no remapean:\n"
        + p.stdout + p.stderr
    )
    assert "declara 0 refs de cohortes SELLADAS" in p.stdout, p.stdout


def test_el_enclavamiento_para_por_lo_que_el_ensayo_si_declara(tmp_path):
    """El lado opuesto: si un ensayo declara que SÍ remapearía un ref congelado, no hay
    Paso 4c. Es el enclavamiento sin marcha atrás que `core0025` hace irreparable."""
    p = _ejecutar(tmp_path, "cutover", "paso4b_enclavamiento")
    assert p.returncode != 0
    assert "cohorte NUEVA" in p.stdout + p.stderr
    assert "Paso 4c" not in p.stdout, p.stdout


# --------------------------------------------------------------------------
# R4 P1-3 — identidades, no cardinalidades
# --------------------------------------------------------------------------
def test_las_cifras_pueden_cuadrar_y_aun_asi_perderse_un_par_conocido(tmp_path):
    """La reproducción del auditor: mover las source listings de un par positivo a otro
    par deja las cuatro fórmulas intactas (PARES antes = PARES después) y pierde
    exactamente el par que importaba. Con manifiestos por identidad eso es rojo, y el rojo
    NOMBRA el par perdido."""
    p = _ejecutar(tmp_path, "cutover", "paso6_identidad_pares")
    assert p.returncode != 0, p.stdout
    salida = p.stdout + p.stderr
    assert "IDENTIDAD" in salida and "p-2" in salida, salida


# --------------------------------------------------------------------------
# R4 P1-5 — el smoke
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "etapa",
    ["smoke_status", "smoke_release", "smoke_authoritative", "smoke_alembic",
     "smoke_sin_paso3", "smoke_sin_ids",
     "smoke_ausente", "smoke_exited", "smoke_restarting", "smoke_unhealthy",
     "smoke_frontend_ausente", "smoke_frontend_unhealthy", "smoke_imagen",
     "smoke_celery", "smoke_slot_inactivo", "smoke_slot_atrasado"],
)
def test_el_smoke_falla_cerrado(tmp_path, sonda, etapa):
    _preparar_smoke(tmp_path, etapa)
    p = _ejecutar(tmp_path, "smoke", etapa)
    assert p.returncode != 0, f"el smoke aceptó {etapa}:\n{p.stdout}"
    assert "PARAR" in p.stdout + p.stderr


def test_el_smoke_rechaza_up_unhealthy(tmp_path, sonda):
    """R4 P1-5, el hallazgo literal: `Up 3 minutes (unhealthy)` empieza por `Up`. El smoke
    devolvía 0 con el capturador enfermo, es decir, con el CDC caído."""
    _preparar_smoke(tmp_path, "smoke_unhealthy")
    p = _ejecutar(tmp_path, "smoke", "smoke_unhealthy")
    assert p.returncode != 0
    assert "unhealthy" in p.stdout + p.stderr


def test_el_smoke_exige_el_frontend(tmp_path, sonda):
    """`ESCRITORES` no incluía el frontend: se podía reabrir el servicio sin UI."""
    _preparar_smoke(tmp_path, "smoke_frontend_ausente")
    p = _ejecutar(tmp_path, "smoke", "smoke_frontend_ausente")
    assert p.returncode != 0
    assert "swissjob-frontend" in p.stdout + p.stderr


def test_el_smoke_rechaza_el_status_ok_que_exigia_el_runbook(tmp_path, sonda):
    """R3 P2-1, invertido: `ok` era lo que pedía la postcondición del Paso 7 y NUNCA lo
    devuelve la API. Hoy eso es rojo, y el rojo dice por qué."""
    _preparar_smoke(tmp_path, "smoke_status")
    p = _ejecutar(tmp_path, "smoke", "smoke_status")
    assert p.returncode != 0
    assert "'ok'" in p.stdout + p.stderr and "'ready'" in p.stdout + p.stderr


# --------------------------------------------------------------------------
# R5 P1-A — el manifiesto etiqueta la ENTIDAD y la TRANSFORMACIÓN, no la fila
#
# Los manifiestos de R4 fallaban en las DOS direcciones y las dos están aquí:
#   · falso VERDE: el de pares guardaba solo `p.id`, así que un par que sigue
#     resolviendo con sus dos lados en OTRAS vacantes no cambiaba el fichero;
#   · falso ROJO: el de juicios guardaba `set_id|job_ref`, y el remapeo
#     CORRECTO del Paso 5 cambia el ref: `comm -23` lo declaraba perdido.
# --------------------------------------------------------------------------
def test_una_permuta_de_los_dos_lados_del_par_no_puede_pasar(tmp_path):
    """Reproducción 1 del auditor (A/B → C/D): el par `p-1` sigue existiendo, sigue
    resolviendo y conserva su `pair_id`, pero sus DOS lados pasan a vacantes que ningún
    ensayo en seco declaró. Cardinalidad, resolubilidad y manifiesto de `p.id` pasan;
    cambió justo la materia etiquetada."""
    p = _ejecutar(tmp_path, "cutover", "paso6_permuta_par")
    assert p.returncode != 0, f"la permuta de los dos lados pasó:\n{p.stdout}"
    salida = p.stdout + p.stderr
    assert "IDENTIDAD" in salida, salida
    assert _HC in salida or _HD in salida, salida


def test_el_mapa_declarado_y_el_que_aplica_el_paso_5_son_el_mismo(tmp_path):
    """El manifiesto esperado se construye con el mapa que declaran G3/G6, pero quien
    mueve los `job_ref` es `canonical_refs`, que NO lee ese mapa: lo reconstruye de
    `jobs`. Si las dos mitades no coinciden, el Paso 5 movería etiquetas que el Paso 6
    no puede distinguir de una permuta — así que se atan, y antes de escribir."""
    p = _ejecutar(tmp_path, "cutover", None)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "coincide con el declarado por los ensayos" in p.stdout, p.stdout
    assert "no reproducen su hash: 0" in p.stdout, p.stdout


def test_un_remapeo_declarado_por_los_ensayos_no_puede_dar_rojo(tmp_path):
    """Reproducción 2 del auditor, invertida: el Paso 5 remapea `job_ref` al hash
    canónico —la etiqueta sigue unida a la MISMA vacante— y eso es exactamente lo que
    los ensayos en seco declararon. El procedimiento tiene que ACEPTARLO; pararlo es
    parar una transformación correcta."""
    p = _ejecutar(tmp_path, "cutover", None)
    assert p.returncode == 0, (
        "el manifiesto declara perdido un juicio que solo cambió de clave:\n"
        + p.stdout + p.stderr
    )
    assert "transformación declarada" in p.stdout, p.stdout


@pytest.mark.parametrize(
    ("etapa", "porque"),
    [
        ("paso6_permuta_par", "los dos lados del par a vacantes no declaradas"),
        ("paso6_juicio_permutado", "el ref de un juicio a una vacante no declarada"),
        ("paso6_veredicto", "el veredicto del par editado por el camino"),
        ("paso6_relevancia", "la relevancia del juicio editada por el camino"),
        ("paso6_juicio_no_resuelve", "un juicio deja de resolver"),
        ("paso6_par_no_resuelve", "un par deja de resolver"),
        ("paso4c_remap", "el mapa en firme no es el que declaró el ensayo"),
        ("remap_no_md5", "un remapeo declarado que no es un md5"),
        ("remap_ambiguo", "un `old_hash` con dos destinos"),
        ("remap_encadenado", "un destino que es a la vez origen"),
        ("paso4_hashes_fantasma", "filas de jobs que ya no reproducen su hash"),
        ("paso5_mapa_ajeno", "canonical_refs reconstruye un mapa mayor que el declarado"),
    ],
)
def test_el_manifiesto_semantico_rechaza(tmp_path, etapa, porque):
    p = _ejecutar(tmp_path, "cutover", etapa)
    assert p.returncode != 0, f"pasó {porque}:\n{p.stdout}"
    assert "PARAR" in p.stdout + p.stderr


# --------------------------------------------------------------------------
# R5 P1-B — la marcha atrás es una máquina de estados REANUDABLE
#
# La base rota se conservaba, pero el nombre de la base apartada vivía solo en
# una variable del proceso: un `SIGKILL` entre el `RENAME` y el `CREATE
# DATABASE` borraba a la vez el destino esperado y el conocimiento para
# continuar, y la siguiente invocación abortaba con «no existe la base». Los
# traps no valen: `SIGKILL` y un reinicio no los ejecutan.
# --------------------------------------------------------------------------
_BORDES = ["antes_rename", "tras_rename", "en_create", "tras_create",
           "en_restore", "tras_restore"]


def _dump_de(tmp_path: Path) -> str:
    return str(next((tmp_path / "backups").glob("pre_canonizacion_*.dump")))


@pytest.mark.parametrize("borde", _BORDES)
def test_la_restauracion_reanuda_tras_un_sigkill_en_cada_borde(tmp_path, borde):
    """Se mata el GRUPO de procesos en cada transición y se re-ejecuta EL MISMO
    comando: tiene que llegar a `VERIFIED` sin una sola sentencia SQL a mano."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    muerto = _ejecutar(tmp_path, "restaurar", None, dump, matar_en=borde)
    assert muerto.returncode != 0, f"el borde {borde} no mató nada:\n{muerto.stdout}"
    assert (tmp_path / "estado" / "muertes").is_file(), muerto.stdout + muerto.stderr

    reanudado = _ejecutar(tmp_path, "restaurar", None, dump)
    assert reanudado.returncode == 0, (
        f"tras morir en {borde} la marcha atrás NO se reanuda sola:\n"
        + reanudado.stdout + reanudado.stderr
    )
    assert "VERIFIED" in reanudado.stdout, reanudado.stdout


def test_la_restauracion_reanuda_aunque_se_pierda_el_directorio_de_trabajo(tmp_path):
    """`WORK_DIR` está en `/tmp`, que en el NAS es un ramdisk: un reinicio lo vacía. El
    checkpoint vive JUNTO a la copia, así que la reanudación no depende de él."""
    import shutil

    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    assert _ejecutar(tmp_path, "restaurar", None, dump, matar_en="tras_rename").returncode != 0
    shutil.rmtree(tmp_path / "trabajo")
    p = _ejecutar(tmp_path, "restaurar", None, dump)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "VERIFIED" in p.stdout


def test_dos_restauraciones_a_la_vez_no_se_pisan(tmp_path):
    """Exclusión mutua sobre un fichero DURABLE junto a la copia: la segunda invocación
    aborta, no restaura sobre la primera."""
    import threading
    import time

    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    resultados: dict[str, subprocess.CompletedProcess] = {}

    def primera():
        resultados["a"] = _ejecutar(tmp_path, "restaurar", None, dump, lento_en="pg_restore")

    hilo = threading.Thread(target=primera)
    hilo.start()
    try:
        # El doble avisa cuando la primera está DENTRO de `pg_restore`, con el
        # cerrojo tomado: así la prueba no depende de ganar una carrera.
        testigo = tmp_path / "estado" / "lento"
        for _ in range(200):
            if testigo.exists():
                break
            time.sleep(0.1)
        assert testigo.exists(), "la primera restauración no llegó a pg_restore"
        segunda = _ejecutar(tmp_path, "restaurar", None, dump)
        assert segunda.returncode != 0, (
            "la segunda restauración simultánea NO fue rechazada:\n" + segunda.stdout
        )
        assert "cerrojo" in segunda.stdout + segunda.stderr
    finally:
        hilo.join(timeout=120)
    assert resultados["a"].returncode == 0, resultados["a"].stdout + resultados["a"].stderr


def test_sin_flock_la_maniobra_no_arranca(tmp_path):
    """R7 P1-3. El repuesto por `mkdir`+PID se RETIRÓ. Ganar el cerrojo con `mkdir`
    era atómico, pero LIMPIAR uno huérfano no lo era: dos procesos leían el mismo PID
    muerto, hacían `rm -rf` uno detrás de otro y `mkdir` con éxito los dos, y ambos se
    creían dueños de la misma base. Sin `flock` no hay exclusión mutua que valga, así
    que la maniobra PARA — y para antes de tocar nada."""
    entorno = {"FLOCK": "flock-que-no-existe-en-este-host"}
    p = _con_entorno(tmp_path, entorno)
    salida = p.stdout + p.stderr
    assert p.returncode != 0, "arrancó un cutover sin exclusión mutua:\n" + p.stdout
    assert "flock" in salida
    assert "Paso 1" not in p.stdout, p.stdout
    assert "Paso 2" not in p.stdout, p.stdout


def test_sin_flock_tampoco_arranca_la_marcha_atras(tmp_path):
    """Y la restauración igual: es la que ejecuta `RENAME`, `DROP` y `CREATE`."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    entorno = {"FLOCK": "flock-que-no-existe-en-este-host"}
    p = _con_entorno(tmp_path, entorno, subcomando="restaurar", extra=(dump,))
    salida = p.stdout + p.stderr
    assert p.returncode != 0, "restauró sin exclusión mutua:\n" + p.stdout
    assert "flock" in salida
    assert "APARTADO" not in p.stdout, p.stdout
    bases = (tmp_path / "estado" / "bases").read_text().split()
    assert bases == ["swissjobhunter"], f"tocó el catálogo sin cerrojo: {bases}"


def test_un_cerrojo_huerfano_no_se_limpia_solo(tmp_path):
    """El corolario de retirar el repuesto: ya no hay ninguna vía por la que el script
    borre un cerrojo que no es suyo. La limpieza de un cerrojo obsoleto es MANUAL y
    auditada — que es justo lo que impide la carrera de dos herederos."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    cerrojo = _cerrojo_de_la_base(tmp_path)
    assert cerrojo.is_file(), "el cerrojo de flock no llegó a existir"
    assert not Path(str(cerrojo) + ".d").exists(), (
        "quedó el mutex por mkdir: el repuesto retirado sigue vivo"
    )


def test_la_base_apartada_nunca_se_borra_sola(tmp_path):
    """La salida de emergencia de la salida de emergencia: el estado roto se conserva
    hasta que el operador lo borre a mano."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    p = _ejecutar(tmp_path, "restaurar", None, dump)
    assert p.returncode == 0, p.stdout + p.stderr
    bases = (tmp_path / "estado" / "bases").read_text().split()
    previas = [b for b in bases if "_previa_" in b]
    assert previas, bases
    assert "swissjobhunter" in bases


def test_la_vuelta_atras_verifica_tambien_las_guardas(tmp_path):
    """`VERIFIED` solo después de manifiestos, metadatos Y guardas: una restauración que
    trae los datos pero deja los triggers de inmutabilidad degradados no es una vuelta
    atrás."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    p = _ejecutar(tmp_path, "restaurar", "restaurar_guarda_degradada", _dump_de(tmp_path))
    assert p.returncode != 0, p.stdout
    assert "guarda" in (p.stdout + p.stderr).lower()
    assert "VERIFIED" not in p.stdout, p.stdout


# --------------------------------------------------------------------------
# R5 P1-C — el smoke no puede dar verde con el worker vivo y el beat ausente
# --------------------------------------------------------------------------
def test_el_smoke_no_da_verde_con_el_worker_vivo_y_el_beat_ausente(tmp_path, sonda):
    """La reproducción del auditor: un worker SIN `-B` está `running`, corre la imagen
    del Paso 3 y contesta `pong` al ping dirigido — el ping prueba el CONSUMIDOR, no el
    PLANIFICADOR. Beat manda las nueve cadencias (proyector, despacho, salud del slot,
    cierre de ciclo) y el runbook documenta que puede morir con el worker vivo."""
    _preparar_smoke(tmp_path, "smoke_sin_beat")
    p = _ejecutar(tmp_path, "smoke", "smoke_sin_beat")
    salida = p.stdout + p.stderr
    assert "pong" in salida, "el doble tiene que seguir contestando al ping: " + salida
    assert p.returncode != 0, f"el smoke dio verde sin beat:\n{p.stdout}"
    assert "beat" in salida.lower()


def test_el_smoke_para_si_el_beat_arranco_y_esta_muerto(tmp_path, sonda):
    """El caso insidioso del runbook: `beat: Starting` está en el log de hace horas y el
    planificador ya no despacha nada. La postcondición es FUNCIONAL —el despacho NUEVO
    del muestreador y una muestra posterior al sondeo—, no la traza de arranque."""
    _preparar_smoke(tmp_path, "smoke_beat_muerto")
    p = _ejecutar(tmp_path, "smoke", "smoke_beat_muerto")
    assert p.returncode != 0, f"el smoke dio verde con el beat muerto:\n{p.stdout}"
    assert "no probó la cadencia del muestreador" in p.stdout + p.stderr


# --------------------------------------------------------------------------
# R4 P1-1 — el aislamiento del ensayo
# --------------------------------------------------------------------------
_ENSAYO_OK = {
    "ENSAYO": "1",
    "PG_DB": "swissjob_ensayo",
    "CORE_DSN": "postgresql+asyncpg://u:p@postgres:5432/swissjob_ensayo",
}


def _con_entorno(tmp_path, entorno, subcomando="cutover", romper=None, extra=(), **kw):
    guardado = {k: os.environ.get(k) for k in entorno}
    os.environ.update(entorno)
    try:
        return _ejecutar(tmp_path, subcomando, romper, *extra, **kw)
    finally:
        for k, v in guardado.items():
            os.environ.pop(k, None) if v is None else os.environ.update({k: v})


@pytest.mark.parametrize(
    ("dsn", "porque"),
    [
        (None, "sin CORE_DSN el Paso 5 escribiría en la base viva"),
        ("postgresql://u:p@postgres:5432/swissjobhunter", "es la base de producción"),
        ("postgresql://u:p@postgres:5432/swissjobhunter?ssl=require",
         "query string: la guarda comparaba por SUFIJO (R4 P1-1)"),
        ("postgresql://u:p@postgres:5432/swissjobhunter?ssl=require&application_name=x",
         "varios parámetros tras la base de producción"),
        ("postgresql://u:p@postgres:5432/swissjobhunter#/swissjob_ensayo",
         "fragmento: el sufijo visible no es la base"),
        ("postgresql://u:p@postgres:5432/swissjobhunte%72",
         "percent-encoding: decodifica a la base de producción"),
        ("postgresql://u:p@postgres:5432/%73wissjobhunter",
         "percent-encoding en la primera letra"),
        ("postgresql://u:p@postgres:5432/swissjob_ensayo?dbname=swissjobhunter",
         "un parámetro que puede REDEFINIR la base de destino"),
        ("postgresql://u:p@postgres:5432/swissjob_ensayo?host=otro",
         "un parámetro que puede redefinir el host"),
        ("postgresql://u:p@otro-servidor:5432/swissjob_ensayo",
         "la base se llama bien pero el servidor no es el esperado"),
        ("postgresql://u:p@postgres:5432/otra_base",
         "el core mediría una base distinta de la de psql"),
        ("esto no es una url", "no es parseable"),
        ("postgresql://u:p@postgres:5432/", "no nombra ninguna base"),
        ("mysql://u:p@postgres:3306/swissjob_ensayo", "no es un DSN de PostgreSQL"),
    ],
)
def test_el_unico_escape_del_ensayo_no_puede_apuntar_a_produccion(tmp_path, dsn, porque):
    """`ENSAYO=1` es la única salida del fallo cerrado y se salta dos pasos, así que no
    puede convertirse en la maniobra real por descuido — ni tocar la base viva. La guarda
    vieja comparaba por sufijo y `…/swissjobhunter?ssl=require` llegaba al Paso 5 EN
    FIRME."""
    entorno = {"ENSAYO": "1", "PG_DB": "swissjob_ensayo", "CORE_DSN": dsn or ""}
    p = _con_entorno(tmp_path, entorno)
    assert p.returncode != 0, f"el ensayo se aceptó aunque {porque}:\n{p.stdout}"
    assert "Paso 4c" not in p.stdout, f"llegó a escribir con un DSN que {porque}:\n{p.stdout}"
    assert "Paso 5" not in p.stdout, f"llegó al Paso 5 con un DSN que {porque}:\n{p.stdout}"


def test_el_ensayo_con_pg_db_de_produccion_no_arranca(tmp_path):
    p = _con_entorno(
        tmp_path,
        {"ENSAYO": "1", "PG_DB": "swissjobhunter",
         "CORE_DSN": "postgresql://u:p@postgres:5432/swissjob_ensayo"},
    )
    assert p.returncode != 0
    assert "producción" in p.stdout + p.stderr


def test_un_ensayo_bien_aislado_si_recorre_la_maniobra(tmp_path):
    """Sin esto, «todo rojo» solo demostraría que la guarda rechaza cualquier cosa."""
    p = _con_entorno(tmp_path, dict(_ENSAYO_OK))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ENSAYO validado" in p.stdout
    assert "Pasos 1–6 OK" in p.stdout


def test_la_sonda_de_destino_para_si_el_core_ve_otra_base(tmp_path):
    """La guarda mira una CADENA; la sonda mira la BASE. Un DSN sintácticamente perfecto
    puede resolver a otro servidor (env-file, DNS, pooler): entonces el Paso 4 escribiría
    en una base y el Paso 5 en otra."""
    p = _con_entorno(tmp_path, dict(_ENSAYO_OK), romper="sonda_destino")
    assert p.returncode != 0
    assert "Paso 4c" not in p.stdout, p.stdout
    assert "NO apunta a la base de psql" in p.stdout + p.stderr


# --------------------------------------------------------------------------
# R6 P1-1 — el cerrojo protege la BASE, no el fichero de copia
#
# `cutover` tomaba `$BACKUP_DIR/nas_cutover.cerrojo` y `restaurar` tomaba
# `$dump.cerrojo`: dos restauraciones con copias DISTINTAS adquirían cerrojos
# distintos, y un cutover y una restauración no se veían entre sí. Los tres
# ejecutan `RENAME`, `DROP`, `CREATE` y `pg_restore` sobre el MISMO `PG_DB`, así
# que el recurso a proteger nunca fue el archivo: es la base.
# --------------------------------------------------------------------------
_ESTADO_DOBLES = ("firme", "canon", "parados", "bases", "restaurado",
                  "lento", "muertes", "muestras", "sync_n")


def _cerrojo_de_la_base(tmp_path: Path) -> Path:
    """El cerrojo ÚNICO por servidor+base: ni por copia, ni por subcomando, ni por
    directorio de copias (R7 P1-2)."""
    return tmp_path / "cerrojos" / "nas_cutover.swissjobhunter@swissjob-postgres.cerrojo"


def _reiniciar_dobles(tmp_path: Path) -> None:
    """Devuelve los dobles al estado ANTES para poder encadenar un cutover más."""
    for nombre in _ESTADO_DOBLES:
        (tmp_path / "estado" / nombre).unlink(missing_ok=True)


def _mientras_retiene(tmp_path: Path, arranque, *, timeout=180):
    """Lanza `arranque` en un hilo y espera al testigo que deja el doble cuando el
    proceso está DENTRO de la etapa lenta, con el cerrojo ya tomado: así la prueba
    no depende de ganar una carrera."""
    import threading
    import time

    (tmp_path / "estado").mkdir(parents=True, exist_ok=True)
    (tmp_path / "estado" / "lento").unlink(missing_ok=True)
    resultados: dict[str, subprocess.CompletedProcess] = {}

    def correr():
        resultados["a"] = arranque()

    hilo = threading.Thread(target=correr)
    hilo.start()
    testigo = tmp_path / "estado" / "lento"
    for _ in range(300):
        if testigo.exists():
            break
        time.sleep(0.1)
    assert testigo.exists(), "el primer proceso no llegó a la etapa lenta"
    return hilo, resultados, timeout


def test_dos_restauraciones_con_copias_distintas_no_se_pisan(tmp_path):
    """LA reproducción del auditor: misma base, dos nombres de copia. Con el cerrojo
    por copia las dos llegaban a VERIFIED tocando `swissjobhunter`, y las dos podían
    generar el mismo `_previa_<segundo>`."""
    import shutil

    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    primera = Path(_dump_de(tmp_path))
    segunda = primera.parent / "segunda_copia.dump"
    shutil.copy(primera, segunda)
    for sufijo in ("manifiesto", "pares", "juicios", "pares.resuelven",
                   "juicios.resuelven", "guardas"):
        shutil.copy(f"{primera}.{sufijo}", f"{segunda}.{sufijo}")

    hilo, resultados, timeout = _mientras_retiene(
        tmp_path,
        lambda: _ejecutar(tmp_path, "restaurar", None, str(primera), lento_en="pg_restore"),
    )
    try:
        b = _ejecutar(tmp_path, "restaurar", None, str(segunda))
        assert b.returncode != 0, (
            "la segunda restauración, con OTRA copia de la MISMA base, no fue "
            "rechazada:\n" + b.stdout
        )
        assert "otra maniobra tiene el cerrojo" in b.stdout + b.stderr, b.stdout
        assert "APARTADO" not in b.stdout, b.stdout
    finally:
        hilo.join(timeout=timeout)
    assert resultados["a"].returncode == 0, resultados["a"].stdout + resultados["a"].stderr


def test_un_cutover_no_entra_mientras_hay_una_restauracion(tmp_path):
    """El cutover empieza parando escritores y volcando la base que la restauración
    está reescribiendo. Tiene que rebotar en el MISMO cerrojo."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)

    hilo, resultados, timeout = _mientras_retiene(
        tmp_path,
        lambda: _ejecutar(tmp_path, "restaurar", None, dump, lento_en="pg_restore"),
    )
    try:
        p = _ejecutar(tmp_path, "cutover", None)
        assert p.returncode != 0, "el cutover entró con una restauración en curso:\n" + p.stdout
        assert "otra maniobra tiene el cerrojo" in p.stdout + p.stderr, p.stdout
        assert "Paso 2" not in p.stdout, p.stdout
    finally:
        hilo.join(timeout=timeout)
    assert resultados["a"].returncode == 0, resultados["a"].stdout + resultados["a"].stderr


def test_una_restauracion_no_entra_mientras_hay_un_cutover(tmp_path):
    """Y al revés: la marcha atrás no puede apartar la base que el cutover está
    volcando y reescribiendo."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    _reiniciar_dobles(tmp_path)

    hilo, resultados, timeout = _mientras_retiene(
        tmp_path, lambda: _ejecutar(tmp_path, "cutover", None, lento_en="pg_dump")
    )
    try:
        p = _ejecutar(tmp_path, "restaurar", None, dump)
        assert p.returncode != 0, "la restauración entró con un cutover en curso:\n" + p.stdout
        assert "otra maniobra tiene el cerrojo" in p.stdout + p.stderr, p.stdout
        assert "APARTADO" not in p.stdout, p.stdout
    finally:
        hilo.join(timeout=timeout)
    assert resultados["a"].returncode == 0, resultados["a"].stdout + resultados["a"].stderr


# --------------------------------------------------------------------------
# R6 P1-2 — el checkpoint DURABLE no lo es si `sync` falla y se sigue igual
# --------------------------------------------------------------------------
def test_un_sync_que_falla_para_antes_del_rename(tmp_path):
    """`sync` es la frontera entre «publicado» y «sobrevive al corte», y detrás va el
    `RENAME` destructivo. Si no se puede sincronizar, no puede haber `RENAME`: el
    estado físico previo quedaría en una base cuyo nombre la máquina de estados
    desconoce, que es exactamente el defecto que R5 cerró."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    p = _ejecutar(tmp_path, "restaurar", "sync_falla", dump)
    salida = p.stdout + p.stderr
    assert p.returncode != 0, "siguió adelante con la sincronización rota:\n" + p.stdout
    assert "sync" in salida
    assert "APARTADA" not in p.stdout, p.stdout
    bases = (tmp_path / "estado" / "bases").read_text().split()
    assert bases == ["swissjobhunter"], f"hubo RENAME con el sync roto: {bases}"


# --------------------------------------------------------------------------
# R6 P1-3 — el smoke ataba dos señales que no prueban la misma capacidad
# --------------------------------------------------------------------------
def test_el_smoke_no_da_verde_si_beat_despacha_otra_cadencia(tmp_path, sonda):
    """El log admitía CUALQUIERA de cuatro cadencias y la segunda señal medía SOLO
    `outbox_lag_p99`: un `Sending due task shadow-project` más una muestra entrada por
    una ejecución independiente (una cola anterior, una mano, otro planificador) daba
    verde aunque el beat actual no despachara el muestreador nunca."""
    _preparar_smoke(tmp_path, "beat_desacoplado")
    p = _ejecutar(tmp_path, "smoke", "beat_desacoplado")
    salida = p.stdout + p.stderr
    assert "Paso 7d" in salida, salida
    assert p.returncode != 0, "verde con el proyector despachado y una muestra ajena:\n" + p.stdout
    assert "shadow-sample-outbox-lag" in salida


# --------------------------------------------------------------------------
# R7 — la ronda 7 de auditoría externa
# --------------------------------------------------------------------------
def test_un_sync_que_falla_para_antes_del_paso_4c(tmp_path):
    """R7 P1-1. La barrera de durabilidad estaba SOLO en el checkpoint de la marcha
    atrás. La unidad de copia del cutover —volcado, manifiesto y los cinco sidecars—
    no la tenía, y el Paso 4c es igual de irreversible que el `RENAME`: con `sync`
    fallando, el cutover llegaba a 4c y salía con código 0 diciendo «Pasos 1–6 OK».
    Una copia que no ha llegado al disco es la única marcha atrás que hay."""
    p = _ejecutar(tmp_path, "cutover", "sync_falla")
    salida = p.stdout + p.stderr
    assert p.returncode != 0, "confirmó la canonización con la copia sin sincronizar:\n" + p.stdout
    assert "sync" in salida
    assert "Paso 4c" not in p.stdout, p.stdout
    assert "Pasos 1–6 OK" not in p.stdout, p.stdout
    assert not (tmp_path / "estado" / "firme").exists(), "hubo COMMIT con el sync roto"


def test_el_cerrojo_no_cambia_al_cambiar_el_directorio_de_copias(tmp_path):
    """R7 P1-2. El cerrojo vivía bajo `$BACKUP_DIR`: dos maniobras sobre la MISMA
    base con directorios de copia distintos tomaban cerrojos distintos y no se veían.
    Reproducido por el auditor con un restore detenido dentro de `pg_restore` y otro
    con otro BACKUP_DIR, que llegó hasta VERIFIED."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)

    hilo, resultados, timeout = _mientras_retiene(
        tmp_path,
        lambda: _ejecutar(tmp_path, "restaurar", None, dump, lento_en="pg_restore"),
    )
    try:
        otro = tmp_path / "otras_copias"
        otro.mkdir(exist_ok=True)
        b = _ejecutar(
            tmp_path, "restaurar", None, dump, override={"BACKUP_DIR": str(otro)}
        )
        assert b.returncode != 0, (
            "la segunda maniobra, con OTRO BACKUP_DIR sobre la MISMA base, no fue "
            "rechazada:\n" + b.stdout
        )
        # CAUSAL: no basta con que muera ni con que la palabra «cerrojo» aparezca
        # —el padre tomaba su PROPIO cerrojo en el otro BACKUP_DIR y lo imprimía
        # al arrancar—. Tiene que morir POR el cerrojo ajeno, y nombrando la base.
        salida = b.stdout + b.stderr
        assert "otra maniobra tiene el cerrojo" in salida, (
            "murió por otra causa, no por el cerrojo compartido:\n" + salida
        )
        assert "swissjobhunter" in salida, salida
        assert "APARTADO" not in b.stdout, b.stdout
    finally:
        hilo.join(timeout=timeout)
    assert resultados["a"].returncode == 0, resultados["a"].stdout + resultados["a"].stderr


def test_el_smoke_no_da_verde_por_una_muestra_fechada_en_el_futuro(tmp_path, sonda):
    """R7 P1-4. El smoke comparaba `max(ts)` con el inicio del sondeo, así que UNA
    fila fechada en 2100 —reloj desviado, dato sembrado— cumplía la condición para
    siempre y el beat podía estar muerto. Ahora se cuenta cuántas muestras posteriores
    al sondeo hay AL EMPEZAR y se exige que esa cifra CREZCA: la del futuro entra en
    la línea base y deja de probar nada."""
    _preparar_smoke(tmp_path, "muestra_futura")
    p = _ejecutar(tmp_path, "smoke", "muestra_futura")
    salida = p.stdout + p.stderr
    assert "Paso 7d" in salida, salida
    assert p.returncode != 0, (
        "verde con una muestra preexistente del futuro y ninguna nueva:\n" + p.stdout
    )
    assert "outbox_lag_p99" in salida


def test_una_verificacion_fallida_se_anota_y_la_siguiente_restaura_de_nuevo(tmp_path):
    """R7 P1-5. Si `pg_restore` entraba entero y la verificación NO cuadraba, el
    checkpoint se quedaba en `RESTAURADO`: la siguiente ejecución se saltaba
    `pg_restore` y volvía a verificar el MISMO destino defectuoso, para siempre. No
    convergía ni fallaba cerrada — se quedaba dando vueltas."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)

    primera = _ejecutar(tmp_path, "restaurar", "restaurar_verificacion", dump)
    assert primera.returncode != 0, "certificó una vuelta que no cuadra:\n" + primera.stdout
    checkpoint = Path(f"{dump}.restauracion").read_text(encoding="utf-8")
    assert "VERIFICACION_FALLIDA" in checkpoint, (
        "la verificación fallida no quedó anotada; el checkpoint dice:\n" + checkpoint
    )

    # Y la segunda, ya sin el fallo inducido, tiene que RESTAURAR otra vez —no
    # limitarse a re-verificar— y llegar a VERIFIED.
    segunda = _ejecutar(tmp_path, "restaurar", None, dump)
    assert segunda.returncode == 0, segunda.stdout + segunda.stderr
    assert "se restaura de nuevo" in segunda.stdout, segunda.stdout
    assert "se pasa a verificar" not in segunda.stdout, (
        "se saltó pg_restore y volvió a verificar el destino roto:\n" + segunda.stdout
    )
    assert "VERIFIED" in Path(f"{dump}.restauracion").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Clases de equivalencia de los cinco invariantes (§5.3 de docs/DEPLOY_NAS.md).
# Los cinco P1 de la R7 se reprodujeron con un ejemplo cada uno; esto cubre la
# CLASE, que es lo que impide que el mismo invariante se reabra por una variante.
# --------------------------------------------------------------------------

# --- Invariante 1: un solo cerrojo por recurso -----------------------------
def test_el_cerrojo_se_suelta_solo_si_su_dueno_muere(tmp_path):
    """Clase (f): dueño muerto. Es la contracara de exigir `flock` — si el cerrojo
    sobreviviera al proceso, un `SIGKILL` dejaría bloqueada para siempre la única
    salida de emergencia, y esa fue la razón por la que existía el repuesto que se
    retiró. El núcleo lo suelta; no hace falta limpieza automática ninguna."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    muerta = _ejecutar(tmp_path, "restaurar", None, dump, matar_en="en_restore")
    assert muerta.returncode != 0, "no murió donde se le pidió"
    # Sin intervención manual y sin esperar: el rerun inmediato NO rebota.
    p = _ejecutar(tmp_path, "restaurar", None, dump)
    assert "otra maniobra tiene el cerrojo" not in p.stdout + p.stderr, (
        "el cerrojo de un proceso MUERTO bloqueó la marcha atrás:\n" + p.stdout
    )
    assert p.returncode == 0, p.stdout + p.stderr


# --- Invariante 2: durabilidad antes que irreversibilidad ------------------
@pytest.mark.parametrize("barrera", [1, 2, 3, 4])
def test_cada_barrera_de_persistencia_de_la_marcha_atras_para_lo_irreversible(tmp_path, barrera):
    """Clase completa del invariante 2 en la marcha atrás: no basta con probar que la
    PRIMERA barrera para. Se recorre una a una —`INICIO`, `APARTADA`,
    `DESTINO_CREADO`, `RESTAURADO`— y en todas el fallo de `sync` tiene que detener la
    maniobra sin dejar el estado en una fase que el checkpoint no conozca."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    p = _con_entorno(
        tmp_path, {"SYNC_FALLA_EN": str(barrera)}, subcomando="restaurar", extra=(dump,)
    )
    salida = p.stdout + p.stderr
    assert p.returncode != 0, f"la barrera {barrera} no detuvo nada:\n{p.stdout}"
    assert "sync" in salida, salida
    assert "VERIFIED" not in p.stdout, p.stdout
    # Y lo que se haya hecho hasta ahí tiene que ser reanudable: el estado nunca
    # queda en una base cuyo nombre la máquina de estados desconozca.
    reanudada = _ejecutar(tmp_path, "restaurar", None, dump)
    assert reanudada.returncode == 0, (
        f"tras parar en la barrera {barrera} la marcha atrás no converge:\n"
        + reanudada.stdout + reanudada.stderr
    )


def test_la_barrera_del_sellado_para_el_cutover_antes_del_4c(tmp_path):
    """La otra frontera del invariante 2: la unidad de copia del cutover. Es la
    primera barrera de esa invocación, y detrás va el Paso 4c."""
    p = _con_entorno(tmp_path, {"SYNC_FALLA_EN": "1"}, subcomando="cutover")
    salida = p.stdout + p.stderr
    assert p.returncode != 0, "llegó al 4c con la copia sin sincronizar:\n" + p.stdout
    assert "sync" in salida, salida
    assert "Paso 4c" not in p.stdout, p.stdout
    assert not (tmp_path / "estado" / "firme").exists(), "hubo COMMIT sin copia durable"


# --- Invariante 3: reanudable desde todo estado ----------------------------
def test_la_marcha_atras_converge_tras_reanudaciones_consecutivas(tmp_path):
    """Clase «reanudación repetida hasta estado terminal». Una reanudación que funciona
    una vez puede no componer: se encadenan cuatro muertes en bordes distintos, cada
    una sobre el estado que dejó la anterior, y solo al final se deja converger."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    for borde in ("antes_rename", "tras_rename", "en_create", "en_restore"):
        muerta = _ejecutar(tmp_path, "restaurar", None, dump, matar_en=borde)
        assert muerta.returncode != 0, f"no murió en {borde}"
    p = _ejecutar(tmp_path, "restaurar", None, dump)
    assert p.returncode == 0, (
        "tras cuatro muertes encadenadas la marcha atrás no converge:\n"
        + p.stdout + p.stderr
    )
    assert "restauración VERIFIED" in p.stdout, p.stdout


def test_una_muerte_tras_anotar_la_verificacion_fallida_sigue_convergiendo(tmp_path):
    """La transición nueva también tiene su borde: morir DESPUÉS de anotar
    `VERIFICACION_FALLIDA` no puede dejar el estado en un sitio del que no se salga."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    dump = _dump_de(tmp_path)
    primera = _ejecutar(tmp_path, "restaurar", "restaurar_verificacion", dump)
    assert primera.returncode != 0
    assert "VERIFICACION_FALLIDA" in Path(f"{dump}.restauracion").read_text(encoding="utf-8")
    segunda = _ejecutar(tmp_path, "restaurar", None, dump, matar_en="en_restore")
    assert segunda.returncode != 0, "no murió donde se le pidió"
    tercera = _ejecutar(tmp_path, "restaurar", None, dump)
    assert tercera.returncode == 0, tercera.stdout + tercera.stderr
    assert "restauración VERIFIED" in tercera.stdout, tercera.stdout


# --- Invariante 4: evidencia causal y nueva del planificador ---------------
@pytest.mark.parametrize(
    ("clase", "porque"),
    [
        ("smoke_sin_beat", "beat ausente: ni siquiera arrancó"),
        ("smoke_beat_muerto", "worker vivo, `beat: Starting` viejo y nada nuevo"),
        ("beat_desacoplado", "otra cadencia despachada + muestra que sube sola"),
        ("muestra_sin_sampler", "muestra independiente, sin ningún despacho"),
        ("muestra_futura", "muestra preexistente fechada en el futuro"),
        ("sampler_sin_muestra", "sampler despachado pero su ejecución no dejó muestra"),
        ("muestra_pasada", "la única muestra es anterior al sondeo"),
    ],
)
def test_el_beat_solo_da_verde_con_evidencia_causal_y_nueva(tmp_path, sonda, clase, porque):
    """Las siete clases del invariante 4. Verde exige LAS DOS señales de la MISMA
    capacidad: el despacho exacto del muestreador durante el sondeo y una muestra
    nueva causada por él. Cualquier otra combinación es roja."""
    _preparar_smoke(tmp_path, clase)
    p = _ejecutar(tmp_path, "smoke", clase)
    salida = p.stdout + p.stderr
    assert p.returncode != 0, f"el smoke dio verde aunque {porque}:\n{p.stdout}"
    assert "beat" in salida.lower(), salida

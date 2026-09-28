#!/bin/sh
# Supervisor de contenedores del NAS — A19-17 / T13 §2.
#
# El 2026-09-23 los 42 contenedores del NAS se pararon por un reinicio del
# daemon de Docker y `restart: unless-stopped` no levantó ninguno. Nadie se
# enteró hasta desplegar. Un healthcheck de compose no cubre eso: mide un
# contenedor que CORRE; uno parado no tiene salud que medir.
#
# Este script corre desde el crontab del host (no desde Docker: si el daemon
# está caído, es precisamente cuando tiene que hablar). Cada 5 minutos:
#   1. si `docker ps` no responde → alerta;
#   2. si falta alguno de los contenedores esperados, o no está `running`,
#      o está `unhealthy` → alerta con la lista;
#   3. si todo vuelve a estar bien tras una alerta → aviso de recuperación.
# Anti-ruido: sólo escribe correo en los CAMBIOS de estado, y un recordatorio
# cada 6 h mientras siga mal. El estado vive en $ESTADO.
#
# Correo: /usr/sbin/sendmail si existe (ssmtp del QTS, probado el 2026-09-28
# con un correo real); si no —dentro del contenedor supervisor—, SMTP directo
# con curl y las variables SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD.
#
# Dónde corre: el crontab del QTS exige suid y sin root no se puede armar, así
# que el script vive en un contenedor mínimo con `restart: always` y el socket
# de Docker en solo lectura (scripts/nas/Dockerfile.supervisor). `always`, no
# `unless-stopped`: el 23-09 los contenedores quedaron «parados» tras el
# reinicio del daemon y `unless-stopped` no los levantó; `always` sí vuelve.
# La entrada del crontab queda además en /etc/config/crontab por si algún día
# se arma; ambos caminos comparten el fichero de estado y no duplican correos.
#
# EXTRA_ESPERADOS: nombres adicionales (para el control negativo sin editar).
#
# Deliberadamente sin healthchecks nuevos por contenedor (A19-25): esta máquina
# ya arranca ~60 procesos por minuto sólo para comprobarse a sí misma.

D="${DOCKER_BIN:-/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker}"
[ -x "$D" ] || D=docker
DESTINO="${SUPERVISOR_EMAIL:-lotharsan@gmail.com}"
DIR="${SUPERVISOR_DIR:-/share/Public/swissjob/supervisor}"
ESTADO="$DIR/estado"
LOG="$DIR/supervisor.log"
RECORDATORIO_S=21600
ESPERADOS="swissjob-backend swissjob-worker swissjob-core-api-r5 swissjob-core-worker-r5 swissjob-core-capture-r5 swissjob-postgres swissjob-redis swissjob-redis-core-r5 swissjob-frontend swissjob-erasure-cdc portfolio_backend portfolio_db portfolio_redis portfolio_ngrok ${EXTRA_ESPERADOS:-}"

mkdir -p "$(dirname "$ESTADO")"
ahora=$(date -u +%s)
fecha=$(date -u +%FT%TZ)

problemas=""
if ! salida=$($D ps -a --format '{{.Names}}|{{.State}}|{{.Status}}' 2>&1); then
  problemas="docker no responde: $salida"
else
  for c in $ESPERADOS; do
    linea=$(printf '%s\n' "$salida" | grep "^$c|" || true)
    if [ -z "$linea" ]; then
      problemas="$problemas
  - $c: NO EXISTE"
    else
      estado=$(printf '%s' "$linea" | cut -d'|' -f2)
      status=$(printf '%s' "$linea" | cut -d'|' -f3)
      if [ "$estado" != "running" ]; then
        problemas="$problemas
  - $c: $estado ($status)"
      elif printf '%s' "$status" | grep -q unhealthy; then
        problemas="$problemas
  - $c: unhealthy ($status)"
      fi
    fi
  done
fi

previo_estado=$(sed -n '1p' "$ESTADO" 2>/dev/null || echo ok)
previo_ts=$(sed -n '2p' "$ESTADO" 2>/dev/null || echo 0)

enviar() {
  asunto="$1"; cuerpo="$2"
  mensaje=$(printf 'To: %s\nFrom: %s\nSubject: %s\n\n%s\n\n-- supervisor_contenedores.sh, %s\n' \
    "$DESTINO" "${SMTP_FROM:-$DESTINO}" "$asunto" "$cuerpo" "$fecha")
  if [ -x /usr/sbin/ssmtp ]; then  # el sendmail del QTS es ssmtp; el de busybox (alpine) NO sirve
    printf '%s\n' "$mensaje" | /usr/sbin/sendmail -t && echo "$fecha ENVIADO (sendmail): $asunto" >> "$LOG" && return
  fi
  if [ -n "${SMTP_HOST:-}" ]; then
    # El motivo del fallo va al log: «sin SMTP_HOST» y «Gmail rechazó la
    # credencial» son problemas distintos con dueños distintos (A19-31), y el
    # mensaje anterior los confundía.
    error_smtp=$(printf '%s\n' "$mensaje" | curl -sS --ssl-reqd --url "smtp://${SMTP_HOST}:${SMTP_PORT:-587}" \
      --user "${SMTP_USER}:${SMTP_PASSWORD}" --mail-from "${SMTP_FROM:-$DESTINO}" --mail-rcpt "$DESTINO" -T - 2>&1) \
      && echo "$fecha ENVIADO (smtp): $asunto" >> "$LOG" && return
    echo "$fecha NO ENVIADO (smtp ${SMTP_HOST}: ${error_smtp:-curl falló}): $asunto" >> "$LOG"
    return
  fi
  echo "$fecha NO ENVIADO (sin sendmail ni SMTP_HOST): $asunto" >> "$LOG"
}

if [ -n "$problemas" ]; then
  echo "$fecha MAL:$problemas" >> "$LOG"
  if [ "$previo_estado" != "mal" ]; then
    enviar "[NAS] contenedores caídos o insanos" "Detectado a las $fecha:$problemas"
    printf 'mal\n%s\n' "$ahora" > "$ESTADO"
  elif [ $((ahora - previo_ts)) -ge $RECORDATORIO_S ]; then
    enviar "[NAS] SIGUE mal (recordatorio)" "Desde hace $(( (ahora - previo_ts) / 3600 )) h:$problemas"
    printf 'mal\n%s\n' "$ahora" > "$ESTADO"
  fi
else
  if [ "$previo_estado" = "mal" ]; then
    enviar "[NAS] recuperado: todos los contenedores esperados corren" "Comprobado a las $fecha."
  fi
  printf 'ok\n%s\n' "$ahora" > "$ESTADO"
fi

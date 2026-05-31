#!/usr/bin/env bash
# Entrypoint del contenedor backend en producción.
# - Aplica migraciones Alembic pendientes antes de arrancar el server.
# - El worker (mismo image) sobrescribe entrypoint=[] en docker-compose para
#   saltarse las migraciones; solo el backend las corre.
set -euo pipefail

echo "[entrypoint] Aplicando migraciones Alembic..."
alembic upgrade head

echo "[entrypoint] Arrancando: $*"
exec "$@"

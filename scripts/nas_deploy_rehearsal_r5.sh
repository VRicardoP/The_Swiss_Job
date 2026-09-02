#!/usr/bin/env sh
# Prepara los env aislados y despliega la pila desechable R5 en QNAP.

set -eu
umask 077

NAS_ROOT=/share/Public/swissjob
R5_ROOT="$NAS_ROOT/rehearsal-r5"
COMPOSE="$NAS_ROOT/docker-compose.rehearsal.qnap.yml"
COMPOSE_BIN="$NAS_ROOT/bin-docker-compose"
DOCKER_BIN=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
PROJECT=swissjob-r5
PROD_DB=swissjobhunter
R5_DB=swissjobhunter_r5_rehearsal
R5_SLOT=jobhunt_shadow_r5_rehearsal
MODE=${1:-up}

case "$MODE" in
  up|bootstrap) ;;
  *) echo "Uso: $0 [up|bootstrap]" >&2; exit 2 ;;
esac

mkdir -p "$R5_ROOT"

for path in \
  "$COMPOSE" \
  "$COMPOSE_BIN" \
  "$NAS_ROOT/.env.prod" \
  "$NAS_ROOT/.env.core.prod" \
  "$NAS_ROOT/.env.core.admin.prod" \
  "$NAS_ROOT/.env.core.capture.prod"
do
  test -f "$path" || { echo "Falta $path" >&2; exit 1; }
done

resolve_image() {
  resolved_image=$("$DOCKER_BIN" image inspect --format '{{.Id}}' "$1") || {
    echo "No existe la imagen local $1" >&2
    exit 1
  }
  case "$resolved_image" in
    sha256:*) printf '%s\n' "$resolved_image" ;;
    *) echo "Docker devolvió una identidad de imagen inválida para $1" >&2; exit 1 ;;
  esac
}

# La racha debe ejecutar exactamente los mismos bytes aunque una etiqueta local
# se reconstruya. Compose recibe IDs de contenido, no tags mutables.
R5_LEGACY_IMAGE=$(resolve_image "${R5_LEGACY_IMAGE_SOURCE:-swissjob-backend:prod}")
R5_CORE_IMAGE=$(resolve_image "${R5_CORE_IMAGE_SOURCE:-swissjob-core:r5-cycle}")
R5_FRONTEND_IMAGE=$(resolve_image "${R5_FRONTEND_IMAGE_SOURCE:-swissjob-frontend:prod}")
export R5_LEGACY_IMAGE R5_CORE_IMAGE R5_FRONTEND_IMAGE

{
  printf '%s\n' \
    "R5_LEGACY_IMAGE=$R5_LEGACY_IMAGE" \
    "R5_CORE_IMAGE=$R5_CORE_IMAGE" \
    "R5_FRONTEND_IMAGE=$R5_FRONTEND_IMAGE"
} > "$R5_ROOT/release.images"
chmod 600 "$R5_ROOT/release.images"


for network in swissjob-r5_legacy-net swissjob-r5_core-net; do
  "$DOCKER_BIN" network inspect "$network" >/dev/null 2>&1 \
    || "$DOCKER_BIN" network create --driver bridge "$network" >/dev/null
done

if test -f "$R5_ROOT/.env.core.redis.r5"; then
  R5_REDIS_SECRET=$(sed -n 's/^CORE_REDIS_PASSWORD=//p' "$R5_ROOT/.env.core.redis.r5")
else
  R5_REDIS_SECRET=$(openssl rand -hex 32)
fi
test -n "$R5_REDIS_SECRET" || { echo 'Secreto Redis R5 vacío' >&2; exit 1; }

if test -f "$R5_ROOT/.env.legacy.secret.r5"; then
  R5_LEGACY_SECRET=$(sed -n 's/^SECRET_KEY=//p' "$R5_ROOT/.env.legacy.secret.r5")
else
  R5_LEGACY_SECRET=$(openssl rand -hex 32)
fi
test -n "$R5_LEGACY_SECRET" || { echo 'SECRET_KEY R5 vacío' >&2; exit 1; }

awk -F= '$1 ~ /^(DATABASE_URL|BACKEND_CORS_ORIGINS)$/' \
  "$NAS_ROOT/.env.prod" \
  | sed "s|/$PROD_DB|/$R5_DB|g" > "$R5_ROOT/.env.legacy.r5"
printf '%s\n' "SECRET_KEY=$R5_LEGACY_SECRET" >> "$R5_ROOT/.env.legacy.r5"
cat >> "$R5_ROOT/.env.legacy.r5" <<'EOF'
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
SCHEDULER_ENABLED=false
SCHEDULER_DAILY_HARVEST_ENABLED=false
EOF

awk -F= '$1 ~ /^(CORE_DATABASE_URL|CORE_ENV)$/' "$NAS_ROOT/.env.core.prod" \
  | sed "s|/$PROD_DB|/$R5_DB|g" > "$R5_ROOT/.env.core.r5"
printf '%s\n' \
  "CORE_BROKER_URL=redis://:$R5_REDIS_SECRET@redis-core:6379/0" \
  "CORE_RESULT_BACKEND=redis://:$R5_REDIS_SECRET@redis-core:6379/1" \
  "CORE_SHADOW_CYCLE_START_HOUR=11" \
  "CORE_SHADOW_CYCLE_START_MINUTE=30" \
  "CORE_SHADOW_PRE_GATE_EVERY_S=1800" \
  "CORE_SHADOW_RUN_CYCLE_HOUR=11" \
  "CORE_SHADOW_RUN_CYCLE_MINUTE=35" \
  "CORE_CAPTURE_SLOT=$R5_SLOT" \
  >> "$R5_ROOT/.env.core.r5"

sed "s|/$PROD_DB|/$R5_DB|g" "$NAS_ROOT/.env.core.admin.prod" \
  > "$R5_ROOT/.env.core.admin.r5"
sed "s|/$PROD_DB|/$R5_DB|g" "$NAS_ROOT/.env.core.capture.prod" \
  > "$R5_ROOT/.env.core.capture.r5"
printf '%s\n' "CORE_CAPTURE_SLOT=$R5_SLOT" >> "$R5_ROOT/.env.core.capture.r5"
printf '%s\n' "CORE_REDIS_PASSWORD=$R5_REDIS_SECRET" \
  > "$R5_ROOT/.env.core.redis.r5"
printf '%s\n' "SECRET_KEY=$R5_LEGACY_SECRET" \
  > "$R5_ROOT/.env.legacy.secret.r5"
chmod 600 "$R5_ROOT"/.env.*.r5

for spec in \
  ".env.legacy.r5:DATABASE_URL" \
  ".env.core.r5:CORE_DATABASE_URL" \
  ".env.core.admin.r5:CORE_ADMIN_DATABASE_URL" \
  ".env.core.capture.r5:CAPTURE_DSN"
do
  file=${spec%%:*}
  key=${spec#*:}
  value=$(sed -n "s/^$key=//p" "$R5_ROOT/$file")
  case "$value" in
    *"/$R5_DB"|*"/$R5_DB?"*) ;;
    *) echo "$key no apunta exactamente a $R5_DB" >&2; exit 1 ;;
  esac
done

grep -qx "CORE_CAPTURE_SLOT=$R5_SLOT" "$R5_ROOT/.env.core.capture.r5" || {
  echo 'Slot CDC R5 incorrecto' >&2
  exit 1
}

"$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" config \
  > "$R5_ROOT/compose.rendered.yml"
chmod 600 "$R5_ROOT/compose.rendered.yml"

if grep -Eq "/$PROD_DB([?\"[:space:]]|$)" "$R5_ROOT/compose.rendered.yml"; then
  echo 'El Compose R5 renderizado aún contiene un DSN de producción' >&2
  exit 1
fi

if grep -E '^[[:space:]]+container_name:' "$R5_ROOT/compose.rendered.yml" \
  | grep -vq -- '-r5$'; then
  echo 'Hay un contenedor de rehearsal sin sufijo -r5' >&2
  exit 1
fi

if test "$MODE" = bootstrap; then
  # El proyector toma staging en transacciones largas (incluye embeddings).
  # Parar solo capture permite que un lote preexistente se solape con el
  # TRUNCATE/backfill y vuelva no determinista el ensayo de rollback/replay.
  "$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" stop \
    core-capture core-worker 2>/dev/null || true
  for service in core-capture core-worker; do
    container=$(
      "$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" ps -q "$service"
    )
    if test -n "$container" \
      && test "$("$DOCKER_BIN" inspect -f '{{.State.Running}}' "$container")" = true; then
      echo "$service sigue activo; rollback/replay abortado" >&2
      exit 1
    fi
  done
  "$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" up -d redis redis-core
  "$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" run --rm --no-deps core-migrate
  "$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" run --rm --no-deps core-capture \
    python -c "
import os
from jobhunt_core.shadow.gate import rollback_replay
print(rollback_replay(
    capture_dsn=os.environ['CAPTURE_DSN'],
    core_dsn=os.environ['CORE_DATABASE_URL'],
    slot=os.environ['CORE_CAPTURE_SLOT'],
    confirm=True,
    operator='nas-r5-bootstrap',
))"
fi

# Converge el esquema y la configuración canónica ANTES de arrancar escritores.
"$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" run --rm --no-deps core-migrate
"$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" run --rm --no-deps core-worker \
  python -c "
import asyncio
import sqlalchemy as sa
from jobhunt_core import embeddings, matching
from jobhunt_core.database import task_session_factory
from jobhunt_core.dedup import (
    exact_intra_backfill,
    lexical_backfill,
    revalidate_pending_candidates,
)
from jobhunt_core.embedding_recipes import LEGACY_V1, ROLE_COMPOSITE_V2

NAME = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
SHA = 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'

async def main():
    async with task_session_factory() as factory:
        async with factory() as s:
            await embeddings.register_model(
                s, NAME, SHA, recipe_version=ROLE_COMPOSITE_V2, active=True)
            await embeddings.register_model(
                s, NAME, SHA, recipe_version=LEGACY_V1, active=False)
            # `hybrid-rrf` NO gobierna el feed mientras no esté validada contra
            # un conjunto de desarrollo independiente (Fase 3 del relevo). Antes
            # este bloque la dejaba como ÚNICA activa y lo exigía con un assert,
            # de modo que cada deploy devolvía la política sin validar al camino
            # canónico — el mismo patrón que perdió `wal_level` en producción:
            # una decisión de seguridad que vive solo en un artefacto que otro
            # paso reescribe.
            #
            # Las dos quedan activas. La CANÓNICA es la primera por nombre
            # (`tasks/matching.py`: `WHERE active ORDER BY name, prompt_version`,
            # y solo la primera mueve `current_eval_id`), y 'cosine-baseline'
            # precede alfabéticamente a 'hybrid-rrf': la validada manda y la
            # experimental corre en SOMBRA, append-only.
            canonical_id = await matching.ensure_policy(
                s, 'cosine-baseline', 'v1', active=True)
            # v1 queda INACTIVA: su desarrollo midió nDCG 0.098/0.000 y no
            # vuelve al feed ni en sombra — mantenerla evaluando solo duplica
            # coste. v2 corre en SOMBRA: la canónica sigue siendo la primera
            # por nombre (cosine-baseline) hasta la promoción explícita.
            await matching.ensure_policy(
                s, matching.HYBRID_POLICY_NAME,
                matching.HYBRID_POLICY_VERSION,
                weights=matching.HYBRID_POLICY_WEIGHTS, active=False)
            v2_id = await matching.ensure_policy(
                s, matching.HYBRID_POLICY_NAME,
                matching.HYBRID2_POLICY_VERSION,
                weights=matching.HYBRID2_POLICY_WEIGHTS, active=True)
            active = set((await s.execute(sa.text(
                'SELECT id FROM scoring_policies WHERE active'
            ))).scalars())
            if active != {canonical_id, v2_id}:
                raise RuntimeError(f'políticas activas inesperadas: {active}')

            exact_backfill = await exact_intra_backfill(s)
            lexical = await lexical_backfill(s)
            preview = await revalidate_pending_candidates(s)
            applied = await revalidate_pending_candidates(s, apply=True)
            if preview['hash_ids'] != applied['hash_ids']:
                raise RuntimeError('preview/apply de dedup no coinciden')
            second = await revalidate_pending_candidates(s, apply=True)
            if second['n']:
                raise RuntimeError('revalidación dedup no fue idempotente')
            await s.commit()
            print({'hybrid_policy_id': str(hybrid_id),
                   'exact_intra_backfill': exact_backfill,
                   'lexical_backfill': lexical,
                   'revalidation': applied})

asyncio.run(main())
"

"$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" run --rm --no-deps --user 0 \
  core-worker chown 100:101 /tmp/hf-cache
"$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" up -d

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
            # P1-D (revisión externa 2026-09-02): el despliegue NO es
            # autoridad sobre la canonicidad. Antes este bloque forzaba
            # active=True/False por política en cada corrida — un redeploy
            # podía deshacer una promoción o resucitar una política suspendida
            # (el runbook del NAS reactivaba hybrid-rrf/v1, medida
            # 0.098/0.000). Ahora: bootstrap_policy_catalog asegura las FILAS
            # del catálogo sin tocar la activación, y el conjunto activo solo
            # lo cambia el operador con «python -m jobhunt_core.policy_ctl
            # declare name:version ...» (promoción/rollback explícitos y
            # atómicos). Trampa anti-regresión: v1/v2/v3 jamás pueden estar
            # activas tras un deploy.
            ids = await matching.bootstrap_policy_catalog(s)
            filas = (await s.execute(sa.text(
                'SELECT name, prompt_version, active FROM scoring_policies '
                'ORDER BY name, prompt_version'))).all()
            activas = {(f.name, f.prompt_version) for f in filas if f.active}
            prohibidas = activas & {('hybrid-rrf', 'v1'), ('hybrid-rrf', 'v2')}
            if prohibidas:
                raise RuntimeError(
                    f'política suspendida ACTIVA tras el deploy: {prohibidas} '
                    '— la activación solo se cambia vía policy_ctl declare')
            if ('hybrid-rrf', 'v3') in activas:
                # v3 activa es estado legado tolerado (sombra previa a v4); el
                # deploy NO la toca — la transición a v4 es un declare explícito:
                print('AVISO: hybrid-rrf/v3 sigue activa (sombra legada). '
                      'Sucesora con receta: policy_ctl declare '
                      'cosine-baseline:v1 hybrid-rrf:v4')
            if not activas:
                # Primer bootstrap de un entorno vacío: línea base segura.
                await matching.declare_active_policies(
                    s, [ids[('cosine-baseline', 'v1')]])
                activas = {('cosine-baseline', 'v1')}

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
            print({'politicas_activas': sorted(map(str, activas)),
                   'exact_intra_backfill': exact_backfill,
                   'lexical_backfill': lexical,
                   'revalidation': applied})

asyncio.run(main())
"

"$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" run --rm --no-deps --user 0 \
  core-worker chown 100:101 /tmp/hf-cache
"$COMPOSE_BIN" -p "$PROJECT" -f "$COMPOSE" up -d

#!/usr/bin/env bash
# Despliegue al NAS sin las trampas que ya han mordido (A19-26, A19-21).
#
# 1. El BFF de producción se construye con `backend/Dockerfile.prod`, NO con
#    `backend/Dockerfile`. Sólo el primero crea el usuario `app` que el compose
#    del NAS exige; con el otro el contenedor ni arranca
#    (`unable to find user app`) y deja el servicio caído. Pasó el 2026-09-24.
# 2. Recrear en producción necesita el proyecto de compose correcto
#    (`-p swissjob` / `-p swissjob-r5`), o choca con el nombre y no recrea nada.
# 3. `RELEASE_SHA` debe ser el SHA real: con `unknown`, la comprobación «todos
#    publican el mismo SHA» se cumple entre `unknown`s sin significar nada.
# 4. (2026-09-25) La etiqueta se cambia POR SERVICIO, nunca con un sed global:
#    la primera versión de este script reescribía también `core-capture` sin
#    recrearlo, dejando el compose apuntando a una imagen que no corría — la
#    deriva A19-21 fabricada a mano.
# 5. (2026-09-25) Las migraciones del núcleo se aplican ANTES de recrear, con un
#    contenedor desechable de la imagen nueva en la red de la API. El código
#    nuevo espera la cabeza nueva; recrear primero deja `/v1/ready` en 503.
#    El BFF no lo necesita: su entrypoint corre `alembic upgrade head`.
# 6. Los tres workers se recrean junto a `core-api`: modelo, cosecha y el
#    worker de operaciones que ejecuta el beat deben publicar la misma release.
#
# Uso:  scripts/deploy_nas.sh core|bff|frontend [--dry-run]
set -euo pipefail

SERVICIO="${1:-}"
DRY="${2:-}"
[[ "$SERVICIO" =~ ^(core|bff|frontend)$ ]] || {
  echo "uso: $0 core|bff|frontend [--dry-run]" >&2
  exit 2
}

for req in docker ssh scp git python3; do
  command -v "$req" >/dev/null || { echo "falta $req" >&2; exit 2; }
done

RAIZ="$(git rev-parse --show-toplevel)"
cd "$RAIZ"

case "$SERVICIO" in
  core) ARBOL=jobhunt_core ;;
  bff) ARBOL=backend ;;
  frontend) ARBOL=frontend ;;
esac
if [[ -n "$(git status --short -- "$ARBOL")" ]]; then
  echo "ABORTA: $ARBOL tiene cambios sin commitear; RELEASE_SHA mentiría" >&2
  git status --short -- "$ARBOL" >&2
  exit 1
fi

SHA="$(git rev-parse --short HEAD)"
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
C=/share/Public/swissjob/bin-docker-compose
E=/share/CACHEDEV1_DATA/Public/unification-e15-20260914

if [[ "$SERVICIO" == core ]]; then
  IMAGEN="swissjob-core:point5-$SHA"
  COMPOSE="core.configured.yml"; PROYECTO="swissjob-r5"
  SERVICIOS=(core-api core-worker core-harvest-worker core-ops-worker)  # core-capture NO: ver trampa 4
  CONSTRUIR=(env "RELEASE_SHA=$SHA" docker compose build core-api)
  ETIQUETAR=(docker tag swissjob-core:dev "$IMAGEN")
  PATRON="swissjob-core:point5-"
  CONTENEDOR_API="swissjob-core-api-r5"
  CONTENEDOR_SALUD="$CONTENEDOR_API"
elif [[ "$SERVICIO" == bff ]]; then
  IMAGEN="swissjob-backend:point5-$SHA"
  COMPOSE="swissjob.configured.yml"; PROYECTO="swissjob"
  SERVICIOS=(backend)
  CONSTRUIR=(docker build --build-arg INSTALL_BROWSERS=false -f backend/Dockerfile.prod -t "$IMAGEN" backend/)
  ETIQUETAR=(true)
  PATRON="swissjob-backend:point5-"
  CONTENEDOR_SALUD="swissjob-backend"
else
  IMAGEN="swissjob-frontend:point5-$SHA"
  COMPOSE="swissjob.configured.yml"; PROYECTO="swissjob"
  SERVICIOS=(frontend)
  CONSTRUIR=(docker build -f frontend/Dockerfile.prod -t "$IMAGEN" frontend/)
  ETIQUETAR=(true)
  PATRON="swissjob-frontend:point5-"
  CONTENEDOR_SALUD="swissjob-frontend"
fi

echo "== servicio=$SERVICIO  imagen=$IMAGEN  compose=$COMPOSE  proyecto=$PROYECTO  recrea=${SERVICIOS[*]}"
if [[ "$DRY" == "--dry-run" ]]; then
  echo "(dry-run: no se construye ni se toca el NAS)"
  exit 0
fi

echo "== construyendo"
"${CONSTRUIR[@]}"
"${ETIQUETAR[@]}"

if [[ "$SERVICIO" == bff ]]; then
  docker run --rm --entrypoint id "$IMAGEN" app >/dev/null 2>&1 || {
    echo "ABORTA: la imagen no tiene el usuario 'app' — ¿Dockerfile en vez de Dockerfile.prod?" >&2
    exit 1
  }
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "== empaquetando"
docker save "$IMAGEN" | gzip -1 > "$TMP/imagen.tar.gz"
echo "== enviando ($(du -h "$TMP/imagen.tar.gz" | cut -f1))"
scp -q "$TMP/imagen.tar.gz" "nas:/share/Public/swissjob/$SERVICIO-$SHA.tar.gz"
ssh nas "gunzip -c /share/Public/swissjob/$SERVICIO-$SHA.tar.gz | $D load"

echo "== copia .before y cambio de etiqueta POR SERVICIO (trampa 4)"
# Se edita con python para tocar sólo la línea `image:` de cada servicio listado.
ssh nas "cp $E/$COMPOSE $E/$COMPOSE.before-$SHA"
# El NAS no tiene python3: se edita en local sobre una copia y se sube.
scp -q "nas:$E/$COMPOSE" "$TMP/compose.yml"
if [[ "$SERVICIO" == frontend ]]; then
  python3 scripts/ensure_swissjob_frontend.py "$TMP/compose.yml"
fi
if [[ "$SERVICIO" == core ]]; then
  # La primera adopción parte del compose vivo con un único core-worker.
  # Se clona localmente para conservar sus secretos sin imprimirlos ni
  # sustituirlos por los marcadores de la copia versionada.
  python3 scripts/split_core_workers.py "$TMP/compose.yml"
fi
python3 - <<PYEOF
import re, sys
ruta = "$TMP/compose.yml"
servicios = "${SERVICIOS[*]}".split()
lineas = open(ruta).read().split("\n")
actual = None; cambiadas = 0
for i, l in enumerate(lineas):
    m = re.match(r"^  ([a-z_-]+):\s*$", l)
    if m: actual = m.group(1)
    if actual in servicios and re.match(r"^    image: $PATRON[a-f0-9]+\s*$", l):
        lineas[i] = "    image: $IMAGEN"; cambiadas += 1
open(ruta, "w").write("\n".join(lineas))
print(f"etiquetas cambiadas: {cambiadas} (esperadas {len(servicios)})")
sys.exit(0 if cambiadas == len(servicios) else 1)
PYEOF
scp -q "$TMP/compose.yml" "nas:$E/$COMPOSE"
ssh nas "grep -n '$PATRON' $E/$COMPOSE"

if [[ "$SERVICIO" == core ]]; then
  echo "== migraciones del núcleo ANTES de recrear (trampa 5)"
  URL=$(ssh nas "$D inspect $CONTENEDOR_API --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^CORE_DATABASE_URL='")
  ssh nas "$D run --rm --network container:$CONTENEDOR_API -e '$URL' $IMAGEN python -m jobhunt_core.migrate" \
    | grep -E "upgrade|al día|Error" || true
fi

echo "== recreando ${SERVICIOS[*]} (con el proyecto correcto: trampa 2)"
ssh nas "cd $E && $C -p $PROYECTO -f $COMPOSE up -d --no-deps ${SERVICIOS[*]}"

echo "== salud"
for _ in $(seq 1 30); do
  estado="$(ssh nas "$D inspect $CONTENEDOR_SALUD --format '{{.State.Health.Status}}' 2>/dev/null || true")"
  [[ "$estado" == healthy ]] && break
  sleep 10
done
[[ "$estado" == healthy ]] || { echo "ABORTA: $CONTENEDOR_SALUD no alcanzó healthy" >&2; exit 1; }
ssh nas "$D ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'swissjob-(frontend|backend|core-api|core-worker|core-harvest-worker|core-ops-worker|core-capture)'"
if [[ "$SERVICIO" == core ]]; then
  echo "== coherencia de release remota"
  release="$(ssh nas "$D exec $CONTENEDOR_API python -c 'import json,urllib.request; d=json.load(urllib.request.urlopen(\"http://127.0.0.1:8000/v1/ready\",timeout=5)); assert d.get(\"status\")==\"ready\" and d.get(\"authoritative\") is True; print(d.get(\"release\",\"\"))'")"
  [[ "$release" == "$SHA" ]] || { echo "ABORTA: /v1/ready publica $release, esperado $SHA" >&2; exit 1; }
  for contenedor in swissjob-core-api-r5 swissjob-core-worker-r5 swissjob-core-harvest-worker-r5 swissjob-core-ops-worker-r5; do
    ejecutada="$(ssh nas "$D inspect $contenedor --format '{{.Config.Image}}'")"
    [[ "$ejecutada" == "$IMAGEN" ]] || { echo "ABORTA: $contenedor corre $ejecutada" >&2; exit 1; }
  done
fi
echo "== listo: $IMAGEN"
echo "   vuelta atrás: ssh nas 'cp $E/$COMPOSE.before-$SHA $E/$COMPOSE' && recrear"
echo "   y RECUERDA: copiar el compose a deploy/nas/ con los secretos sustituidos (T13 §7)"

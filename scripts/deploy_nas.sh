#!/usr/bin/env bash
# Despliegue al NAS sin las tres trampas que ya han mordido (A19-26).
#
# 1. El BFF de producción se construye con `backend/Dockerfile.prod`, NO con
#    `backend/Dockerfile`. Sólo el primero crea el usuario `app` que el compose
#    del NAS exige; con el otro el contenedor ni arranca
#    (`unable to find user app`) y deja el servicio caído. Pasó el 2026-09-24.
# 2. Recrear en producción necesita el proyecto de compose correcto
#    (`-p swissjob` / `-p swissjob-r5`), o choca con el nombre y no recrea nada.
# 3. `RELEASE_SHA` debe ser el SHA real: con `unknown`, la comprobación «todos
#    publican el mismo SHA» se cumple entre `unknown`s sin significar nada.
#
# Uso:  scripts/deploy_nas.sh core|bff [--dry-run]
set -euo pipefail

SERVICIO="${1:-}"
DRY="${2:-}"
[[ "$SERVICIO" =~ ^(core|bff)$ ]] || {
  echo "uso: $0 core|bff [--dry-run]" >&2
  exit 2
}

for req in docker ssh scp git; do
  command -v "$req" >/dev/null || { echo "falta $req" >&2; exit 2; }
done

RAIZ="$(git rev-parse --show-toplevel)"
cd "$RAIZ"

# El árbol del servicio que se despliega tiene que estar limpio: si no, el SHA
# horneado en la imagen no identifica el código que lleva dentro.
ARBOL=$([[ "$SERVICIO" == core ]] && echo jobhunt_core || echo backend)
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
  COMPOSE="core.configured.yml"; PROYECTO="swissjob-r5"; UNIDAD="core-api"
  CONSTRUIR=(env "RELEASE_SHA=$SHA" docker compose build core-api)
  ETIQUETAR=(docker tag swissjob-core:dev "$IMAGEN")
  PATRON="swissjob-core:point5-"
else
  IMAGEN="swissjob-backend:point5-$SHA"
  COMPOSE="swissjob.configured.yml"; PROYECTO="swissjob"; UNIDAD="backend"
  # Dockerfile.prod, no Dockerfile: ver trampa 1 arriba.
  CONSTRUIR=(docker build -f backend/Dockerfile.prod -t "$IMAGEN" backend/)
  ETIQUETAR=(true)
  PATRON="swissjob-backend:point5-"
fi

echo "== servicio=$SERVICIO  imagen=$IMAGEN  compose=$COMPOSE  proyecto=$PROYECTO"
if [[ "$DRY" == "--dry-run" ]]; then
  echo "(dry-run: no se construye ni se toca el NAS)"
  exit 0
fi

echo "== construyendo"
"${CONSTRUIR[@]}"
"${ETIQUETAR[@]}"

# Comprobación de la trampa 1 ANTES de enviar 1 GB por la red: si la imagen no
# tiene el usuario que el compose exige, el contenedor no arrancaría.
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

echo "== copia .before del compose y cambio de etiqueta"
ssh nas "cp $E/$COMPOSE $E/$COMPOSE.before-$SHA && sed -i 's|$PATRON[a-f0-9]*|$PATRON$SHA|' $E/$COMPOSE && grep -n '$PATRON' $E/$COMPOSE | head -2"

echo "== recreando (con el proyecto correcto: ver trampa 2)"
ssh nas "cd $E && $C -p $PROYECTO -f $COMPOSE up -d --no-deps $UNIDAD"

echo "== salud"
for _ in $(seq 1 30); do
  estado="$(ssh nas "$D inspect ${PROYECTO}-${UNIDAD} --format '{{.State.Health.Status}}' 2>/dev/null || true" || true)"
  [[ "$estado" == healthy ]] && break
  sleep 10
done
ssh nas "$D ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'swissjob-(backend|core-api)'"
echo "== listo: $IMAGEN"
echo "   vuelta atrás: cp $E/$COMPOSE.before-$SHA $E/$COMPOSE && recrear"

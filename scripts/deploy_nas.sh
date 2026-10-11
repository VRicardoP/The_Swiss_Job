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
# 7. El worker BFF usa la misma fuente que la API, pero otra imagen con Chromium.
#    Se despliega explícitamente para no dejar código Celery antiguo en producción.
# 8. BFF y worker reciben del manifiesto versionado las listas LEGACY_DISABLED_*;
#    cambiar sólo la imagen dejaría activos productores que ya pertenecen a Core.
# 9. El respaldo incluye el servicio: BFF y worker comparten SHA y no deben
#    sobrescribir el punto de vuelta atrás del otro al desplegarse por separado.
# 10. El compose transformado se valida como candidato antes de sustituir el vivo.
# 11. `--config-only` conserva la imagen viva de BFF/worker y recrea sólo para
#     aplicar la política de productores; no publica código sin necesidad.
#
# Uso:  scripts/deploy_nas.sh core|bff|worker|frontend [--dry-run|--config-only]
set -euo pipefail

SERVICIO="${1:-}"
MODO="${2:-}"
[[ "$SERVICIO" =~ ^(core|bff|worker|frontend)$ ]] || {
  echo "uso: $0 core|bff|worker|frontend [--dry-run|--config-only]" >&2
  exit 2
}
[[ -z "$MODO" || "$MODO" == "--dry-run" || "$MODO" == "--config-only" ]] || {
  echo "modo no válido: $MODO" >&2
  exit 2
}
if [[ "$MODO" == "--config-only" && ! "$SERVICIO" =~ ^(bff|worker)$ ]]; then
  echo "--config-only sólo se admite para bff o worker" >&2
  exit 2
fi

for req in docker ssh scp git python3; do
  command -v "$req" >/dev/null || { echo "falta $req" >&2; exit 2; }
done

RAIZ="$(git rev-parse --show-toplevel)"
cd "$RAIZ"

case "$SERVICIO" in
  core) ARBOL=jobhunt_core ;;
  bff|worker) ARBOL=backend ;;
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
elif [[ "$SERVICIO" == worker ]]; then
  IMAGEN="swissjob-worker:point5-$SHA"
  COMPOSE="swissjob.configured.yml"; PROYECTO="swissjob"
  SERVICIOS=(worker)
  CONSTRUIR=(docker build -f backend/Dockerfile.prod -t "$IMAGEN" backend/)
  ETIQUETAR=(true)
  PATRON="swissjob-worker:point5-"
  CONTENEDOR_SALUD="swissjob-worker"
else
  IMAGEN="swissjob-frontend:point5-$SHA"
  COMPOSE="swissjob.configured.yml"; PROYECTO="swissjob"
  SERVICIOS=(frontend)
  CONSTRUIR=(docker build -f frontend/Dockerfile.prod -t "$IMAGEN" frontend/)
  ETIQUETAR=(true)
  PATRON="swissjob-frontend:point5-"
  CONTENEDOR_SALUD="swissjob-frontend"
fi

if [[ "$MODO" == "--config-only" ]]; then
  IMAGEN="$(ssh nas "$D inspect $CONTENEDOR_SALUD --format '{{.Config.Image}}'")"
  [[ "$IMAGEN" =~ ^${PATRON}[a-f0-9]+$ ]] || {
    echo "ABORTA: imagen viva inesperada para $SERVICIO: $IMAGEN" >&2
    exit 1
  }
fi

echo "== servicio=$SERVICIO  modo=${MODO:---release}  imagen=$IMAGEN  compose=$COMPOSE  proyecto=$PROYECTO  recrea=${SERVICIOS[*]}"
if [[ "$MODO" == "--dry-run" ]]; then
  echo "(dry-run: no se construye ni se toca el NAS)"
  exit 0
fi

if [[ "$MODO" != "--config-only" ]]; then
  echo "== construyendo"
  "${CONSTRUIR[@]}"
  "${ETIQUETAR[@]}"

  if [[ "$SERVICIO" == bff || "$SERVICIO" == worker ]]; then
    docker run --rm --entrypoint id "$IMAGEN" app >/dev/null 2>&1 || {
      echo "ABORTA: la imagen no tiene el usuario 'app' — ¿Dockerfile en vez de Dockerfile.prod?" >&2
      exit 1
    }
  fi
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if [[ "$MODO" != "--config-only" ]]; then
  echo "== empaquetando"
  docker save "$IMAGEN" | gzip -1 > "$TMP/imagen.tar.gz"
  echo "== enviando ($(du -h "$TMP/imagen.tar.gz" | cut -f1))"
  scp -q "$TMP/imagen.tar.gz" "nas:/share/Public/swissjob/$SERVICIO-$SHA.tar.gz"
  ssh nas "gunzip -c /share/Public/swissjob/$SERVICIO-$SHA.tar.gz | $D load"
else
  echo "== config-only: se conserva la imagen viva $IMAGEN"
fi

echo "== copia .before y cambio de etiqueta POR SERVICIO (trampa 4)"
# Se edita con python para tocar sólo la línea `image:` de cada servicio listado.
ssh nas "cp $E/$COMPOSE $E/$COMPOSE.before-$SERVICIO-$SHA"
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
import re
import sys
from pathlib import Path

ruta = Path("$TMP/compose.yml")
servicios = "${SERVICIOS[*]}".split()
sincronizar_productores = "$SERVICIO" in {"bff", "worker"}
claves_productores = ("LEGACY_DISABLED_PROVIDERS", "LEGACY_DISABLED_SCRAPERS")
politicas = {}

if sincronizar_productores:
    manifiesto = Path("deploy/nas/swissjob.configured.yml")
    actual = None
    for linea in manifiesto.read_text().splitlines():
        servicio = re.match(r"^  ([a-z_-]+):\s*$", linea)
        if servicio:
            actual = servicio.group(1)
        if actual in servicios:
            valor = re.match(
                r"^      (LEGACY_DISABLED_PROVIDERS|LEGACY_DISABLED_SCRAPERS):\s+(.+)$",
                linea,
            )
            if valor:
                politicas.setdefault(actual, {})[valor.group(1)] = valor.group(2)
    esperadas = {(servicio, clave) for servicio in servicios for clave in claves_productores}
    encontradas = {
        (servicio, clave)
        for servicio, valores in politicas.items()
        for clave in valores
    }
    if encontradas != esperadas:
        sys.exit("ABORTA: política LEGACY_DISABLED_* incompleta en el manifiesto")

lineas = ruta.read_text().splitlines()
salida = []
actual = None
entornos = set()
etiquetadas = set()
aplicadas = {servicio: set() for servicio in servicios}
for linea in lineas:
    servicio = re.match(r"^  ([a-z_-]+):\s*$", linea)
    if servicio:
        actual = servicio.group(1)
    if actual in servicios and re.match(r"^    image: $PATRON[a-f0-9]+\s*$", linea):
        linea = "    image: $IMAGEN"
        etiquetadas.add(actual)
    if actual in servicios and linea == "    environment:":
        entornos.add(actual)
    if sincronizar_productores and actual in servicios:
        valor = re.match(
            r"^      (LEGACY_DISABLED_PROVIDERS|LEGACY_DISABLED_SCRAPERS):",
            linea,
        )
        if valor:
            clave = valor.group(1)
            linea = f"      {clave}: {politicas[actual][clave]}"
            aplicadas[actual].add(clave)
        if re.match(r"^    restart:", linea):
            if actual not in entornos:
                sys.exit(f"ABORTA: {actual} no tiene environment en el compose remoto")
            for clave in claves_productores:
                if clave not in aplicadas[actual]:
                    salida.append(f"      {clave}: {politicas[actual][clave]}")
                    aplicadas[actual].add(clave)
    salida.append(linea)

if etiquetadas != set(servicios):
    sys.exit(
        f"ABORTA: etiquetas incompletas; cambiadas={sorted(etiquetadas)}, "
        f"esperadas={sorted(servicios)}"
    )
if sincronizar_productores and any(
    aplicadas[servicio] != set(claves_productores) for servicio in servicios
):
    sys.exit("ABORTA: política LEGACY_DISABLED_* no aplicada a todos los servicios")
ruta.write_text("\n".join(salida) + "\n")
print(f"etiquetas cambiadas: {len(etiquetadas)} (esperadas {len(servicios)})")
if sincronizar_productores:
    print("política de productores aplicada:", sorted(aplicadas))
PYEOF
CANDIDATO="$E/$COMPOSE.next-$SERVICIO-$SHA"
scp -q "$TMP/compose.yml" "nas:$CANDIDATO"
ssh nas "$C -p $PROYECTO -f $CANDIDATO config --quiet"
ssh nas "mv $CANDIDATO $E/$COMPOSE"
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
if [[ "$SERVICIO" == worker ]]; then
  respuesta=""
  for _ in $(seq 1 30); do
    respuesta="$(ssh nas "$D exec $CONTENEDOR_SALUD celery -A celery_app inspect ping --timeout 5" 2>/dev/null || true)"
    [[ "$respuesta" == *pong* ]] && break
    sleep 10
  done
  [[ "$respuesta" == *pong* ]] || { echo "ABORTA: $CONTENEDOR_SALUD no responde al ping Celery" >&2; exit 1; }
else
  for _ in $(seq 1 30); do
    estado="$(ssh nas "$D inspect $CONTENEDOR_SALUD --format '{{.State.Health.Status}}' 2>/dev/null || true")"
    [[ "$estado" == healthy ]] && break
    sleep 10
  done
  [[ "$estado" == healthy ]] || { echo "ABORTA: $CONTENEDOR_SALUD no alcanzó healthy" >&2; exit 1; }
fi
ssh nas "$D ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'swissjob-(frontend|backend|worker|core-api|core-worker|core-harvest-worker|core-ops-worker|core-capture)'"
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
echo "   vuelta atrás: ssh nas 'cp $E/$COMPOSE.before-$SERVICIO-$SHA $E/$COMPOSE' && recrear"
echo "   y RECUERDA: copiar el compose a deploy/nas/ con los secretos sustituidos (T13 §7)"

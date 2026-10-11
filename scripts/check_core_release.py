#!/usr/bin/env python3
"""Coherencia de la release del core: lo que el compose dice vs lo que corre.

El 2026-09-24 (H16) los contenedores del core corrían `swissjob-core:d908ea2`
mientras `docker-compose.yml` apuntaba a `swissjob-core:dev`, una imagen 4 días
más vieja. Nadie lo sabía porque **nada compara las dos cosas**: mientras no se
recree nada, el compose puede mentir indefinidamente. Al recrear `core-api`, la
imagen vieja se encontró una base más nueva (`core0042` contra `core0040`
esperado) y quedó `not_ready`.

El sistema SÍ lo detectó —`/v1/ready` para eso está, y el healthcheck lo
consulta— pero sólo después de romperse, y el único síntoma era un contenedor
`unhealthy` en un `ps` que nadie mira. Esto lo comprueba ANTES.

Tres condiciones, cada una con su propio motivo de fallo:

1. Los contenedores del core corren la MISMA imagen a la que resuelve el
   compose. Es la que falló en H16.
2. `/v1/ready` responde `ready`: la cabeza de alembic de la base coincide con la
   que espera el código que corre.
3. La release es NOMBRABLE y el código inmutable (`authoritative: true`). Con
   `release: unknown` la comparación «todos publican el mismo SHA» se cumpliría
   entre `unknown`s sin significar nada (auditoría G9 P2-A/P2-B).

Uso local:
  python3 scripts/check_core_release.py [--url http://127.0.0.1:8003]
Uso contra un host remoto con Compose independiente:
  SSH_HOST=nas DOCKER_BIN=/ruta/docker COMPOSE_BIN=/ruta/docker-compose \
  COMPOSE_FILE=/ruta/core.yml COMPOSE_PROJECT_NAME=swissjob-r5 \
  python3 scripts/check_core_release.py [--capture-retired]
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

SERVICIOS_OBLIGATORIOS = (
    "core-api",
    "core-worker",
    "core-harvest-worker",
    "core-ops-worker",
)
SERVICIO_CAPTURE = "core-capture"
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")
COMPOSE_BIN = os.environ.get("COMPOSE_BIN")
COMPOSE_FILE = os.environ.get("COMPOSE_FILE")
COMPOSE_PROJECT_NAME = os.environ.get("COMPOSE_PROJECT_NAME")
SSH_HOST = os.environ.get("SSH_HOST")
READY_CONTAINER = os.environ.get("READY_CONTAINER", "swissjob-core-api-r5")
REMOTE_READY_URL = os.environ.get("REMOTE_READY_URL", "http://127.0.0.1:8000")


def _servicios_a_revisar(capture_retirado: bool) -> tuple[str, ...]:
    """Return the exact service set expected for the declared phase."""
    if capture_retirado:
        return SERVICIOS_OBLIGATORIOS
    return (*SERVICIOS_OBLIGATORIOS, SERVICIO_CAPTURE)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    if SSH_HOST:
        command = ["ssh", SSH_HOST, shlex.join(command)]
    return subprocess.run(command, capture_output=True, text=True)


def _compose_json(*args: str) -> dict:
    command = [COMPOSE_BIN] if COMPOSE_BIN else [DOCKER_BIN, "compose"]
    if COMPOSE_PROJECT_NAME:
        command.extend(("-p", COMPOSE_PROJECT_NAME))
    if COMPOSE_FILE:
        command.extend(("-f", COMPOSE_FILE))
    salida = _run([*command, *args])
    if salida.returncode != 0:
        sys.exit(f"no se pudo hablar con compose:\n{salida.stderr}")
    return json.loads(salida.stdout)


def _id_de_imagen(referencia: str) -> str | None:
    salida = _run([DOCKER_BIN, "image", "inspect", referencia, "--format", "{{.Id}}"])
    return salida.stdout.strip() if salida.returncode == 0 else None


def _id_de_contenedor(nombre: str) -> str | None:
    salida = _run([DOCKER_BIN, "inspect", nombre, "--format", "{{.Image}}"])
    return salida.stdout.strip() if salida.returncode == 0 else None


def _revisa_imagenes(capture_retirado: bool) -> tuple[list[str], int]:
    """Condición 1: compose y contenedores, la misma imagen."""
    servicios = _compose_json("config", "--format", "json").get("services", {})
    revisados = _servicios_a_revisar(capture_retirado)
    fallos = []
    if capture_retirado and SERVICIO_CAPTURE in servicios:
        fallos.append(f"{SERVICIO_CAPTURE}: el compose aún declara la captura retirada")
    for servicio in revisados:
        declarada = (servicios.get(servicio) or {}).get("image")
        if not declarada:
            fallos.append(f"{servicio}: el compose no declara imagen")
            continue
        esperada = _id_de_imagen(declarada)
        if esperada is None:
            fallos.append(f"{servicio}: la imagen «{declarada}» no existe localmente")
            continue
        contenedor = (servicios[servicio].get("container_name")) or servicio
        real = _id_de_contenedor(contenedor)
        if real is None:
            fallos.append(f"{servicio}: no hay contenedor «{contenedor}» en marcha")
        elif real != esperada:
            fallos.append(
                f"{servicio}: corre {real[7:19]} pero el compose apunta a "
                f"«{declarada}» = {esperada[7:19]}; un `up -d` lo cambiaría "
                "bajo tus pies"
            )
    return fallos, len(revisados)


def _ready_json(url: str) -> dict:
    endpoint = f"{url}/v1/ready"
    if SSH_HOST:
        remote_endpoint = f"{REMOTE_READY_URL}/v1/ready"
        code = (
            "import json,urllib.request; "
            f"print(json.dumps(json.load(urllib.request.urlopen({remote_endpoint!r},timeout=10))))"
        )
        salida = _run([DOCKER_BIN, "exec", READY_CONTAINER, "python", "-c", code])
        if salida.returncode != 0:
            raise RuntimeError(salida.stderr.strip() or "sonda ready remota falló")
        return json.loads(salida.stdout)
    with urllib.request.urlopen(endpoint, timeout=10) as response:
        return json.load(response)


def _revisa_ready(url: str) -> list[str]:
    """Condiciones 2 y 3: la base casa con el código, y la release se puede nombrar."""
    try:
        cuerpo = _ready_json(url)
    except urllib.error.HTTPError as e:
        try:
            cuerpo = json.load(e)
        except Exception:
            return [f"/v1/ready devolvió HTTP {e.code} sin cuerpo legible"]
        return [
            f"/v1/ready responde HTTP {e.code}: alembic en la base "
            f"«{cuerpo.get('alembic')}» contra «{cuerpo.get('expected')}» que "
            "espera el código que corre"
        ]
    except Exception as e:  # conexión rechazada, timeout…
        return [f"no se pudo consultar {url}/v1/ready: {e}"]

    fallos = []
    if cuerpo.get("status") != "ready":
        fallos.append(f"/v1/ready dice «{cuerpo.get('status')}»: {cuerpo}")
    if cuerpo.get("release") in (None, "", "unknown"):
        fallos.append(
            "la release no es nombrable (`unknown`): construye con "
            "`RELEASE_SHA=$(git rev-parse --short HEAD) docker compose build`"
        )
    if not cuerpo.get("authoritative"):
        fallos.append(
            "`authoritative: false`: el código es mutable (¿perfil dev?) o la "
            "release no es nombrable"
        )
    return fallos


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8003")
    parser.add_argument(
        "--capture-retired",
        action="store_true",
        help="expect the final four-service topology without core-capture",
    )
    args = parser.parse_args()

    fallos_imagenes, cantidad_servicios = _revisa_imagenes(args.capture_retired)
    fallos = fallos_imagenes + _revisa_ready(args.url)
    if fallos:
        print("La release del core NO es coherente:", file=sys.stderr)
        for fallo in fallos:
            print(f"  - {fallo}", file=sys.stderr)
        return 1
    cuerpo = _ready_json(args.url)
    print(
        f"Core coherente: release {cuerpo['release']}, alembic "
        f"{cuerpo['alembic']}, authoritative, y los "
        f"{cantidad_servicios} servicios sobre la imagen del compose"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

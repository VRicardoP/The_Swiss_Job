#!/usr/bin/env python3
"""C7 — comprueba que el compose base no se reabre solo.

Tres cosas que se deshacen sin querer al editar `docker-compose.yml` y que
nadie nota hasta que alguien escanea la red:

1. Un `ports:` sin interfaz se publica en 0.0.0.0, es decir, a toda la LAN.
   Así estaban postgres (5435) y redis (6380) con credenciales de desarrollo.
2. Un `${VAR:-contraseña}` devuelve una clave conocida a quien no rellene el
   `.env`, en silencio y sin avisar de nada.
3. `redis` sin `--requirepass` acepta conexiones anónimas.

Se ejecuta sobre el modelo YA RESUELTO (`docker compose config`), no sobre el
texto del YAML: lo que importa es lo que compose acaba publicando, no cómo se
escribió. Uso:  python3 scripts/check_compose_exposure.py [fichero-compose]
"""

from __future__ import annotations

import json
import subprocess
import sys

# Único servicio que puede publicarse a la LAN: el servidor de desarrollo de
# Vite, que no guarda credenciales. Ampliar esta lista es una decisión, y por
# eso vive aquí escrita y no en un `if` dentro del bucle.
PUBLICABLES_A_LA_LAN = {"frontend"}
INTERFAZ_ESPERADA = "127.0.0.1"
# Contraseñas que estuvieron publicadas en este repositorio.
CLAVES_QUEMADAS = ("swissjob_dev_2024",)


def _modelo_resuelto(fichero: str) -> dict:
    # `--project-directory .`: sin esto compose resuelve los `env_file` relativos
    # contra el directorio DEL FICHERO, y comprobar una copia del compose fuera
    # del repositorio fallaría por no encontrar `.env` — un fallo que se lee
    # igual que «el compose está mal» sin serlo.
    orden = [
        "docker", "compose", "--project-directory", ".",
        "-f", fichero, "config", "--format", "json",
    ]  # fmt: skip
    entorno_de_relleno = {
        "POSTGRES_PASSWORD": "comprobacion",
        "REDIS_PASSWORD": "comprobacion",
    }
    import os

    salida = subprocess.run(
        orden,
        capture_output=True,
        text=True,
        env={**os.environ, **entorno_de_relleno},
    )
    if salida.returncode != 0:
        sys.exit(f"no se pudo resolver {fichero}:\n{salida.stderr}")
    return json.loads(salida.stdout)


def _revisa_puertos(servicios: dict) -> list[str]:
    fallos = []
    for nombre, servicio in sorted(servicios.items()):
        if nombre in PUBLICABLES_A_LA_LAN:
            continue
        for puerto in servicio.get("ports") or []:
            interfaz = puerto.get("host_ip") or "0.0.0.0"
            if interfaz != INTERFAZ_ESPERADA:
                fallos.append(
                    f"{nombre}: publica {puerto.get('published')} en {interfaz}; "
                    f'usa "${{HOST_BIND_IP:-{INTERFAZ_ESPERADA}}}:…"'
                )
    return fallos


def _revisa_claves_por_defecto(fichero: str) -> list[str]:
    """Sobre el TEXTO del YAML, no sobre el modelo resuelto.

    El modelo resuelto mezcla el `.env` de quien ejecute esto: una máquina de
    desarrollo que conserve la clave vieja haría saltar la comprobación sin que
    el compose tenga culpa. Lo que aquí se vigila es el default escrito en el
    fichero, que es lo que hereda quien no rellena nada.
    """
    with open(fichero, encoding="utf-8") as f:
        texto = f.read()
    return [
        f"{fichero}: contiene la contraseña publicada «{quemada}» como valor o "
        "default; exígela con ${VAR:?} y sin default"
        for quemada in CLAVES_QUEMADAS
        if quemada in texto
    ]


def _revisa_auth_de_redis(servicios: dict) -> list[str]:
    fallos = []
    for nombre, servicio in sorted(servicios.items()):
        if not nombre.startswith("redis"):
            continue
        orden = " ".join(servicio.get("command") or [])
        if "requirepass" not in orden:
            fallos.append(f"{nombre}: sin --requirepass, acepta conexiones anónimas")
    return fallos


def main() -> int:
    fichero = sys.argv[1] if len(sys.argv) > 1 else "docker-compose.yml"
    servicios = _modelo_resuelto(fichero).get("services", {})
    fallos = (
        _revisa_puertos(servicios)
        + _revisa_claves_por_defecto(fichero)
        + _revisa_auth_de_redis(servicios)
    )
    if fallos:
        print(f"C7: {fichero} se ha reabierto:", file=sys.stderr)
        for fallo in fallos:
            print(f"  - {fallo}", file=sys.stderr)
        return 1
    publicados = sum(len(s.get("ports") or []) for s in servicios.values())
    print(f"C7 OK: {fichero}, {publicados} puertos publicados, ninguno a la LAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

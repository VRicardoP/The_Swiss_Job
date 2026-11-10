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
import re
import subprocess
import sys

# Único servicio que puede publicarse a la LAN: el servidor de desarrollo de
# Vite, que no guarda credenciales. Ampliar esta lista es una decisión, y por
# eso vive aquí escrita y no en un `if` dentro del bucle.
PUBLICABLES_A_LA_LAN = {"frontend"}
INTERFAZ_ESPERADA = "127.0.0.1"
# Contraseñas que estuvieron publicadas en este repositorio. Se conservan como
# red adicional: una clave quemada hay que cazarla aunque su nombre de variable
# no parezca sensible.
CLAVES_QUEMADAS = ("swissjob_dev_2024",)

# A-66 — un default de contraseña se caza por su FORMA, no por su valor.
#
# El docstring de este script prometía «un `${VAR:-contraseña}` devuelve una
# clave conocida», y la implementación era una lista de UN literal quemado más
# un `in texto`. Así que se le colaban dos credenciales por defecto en nueve
# sitios del compose base —`CORE_REDIS_PASSWORD:-core_redis_dev` y
# `CORE_DB_PASSWORD:-jobhunt_core_dev`, y la segunda es la contraseña con la que
# `core-migrate` CREA el rol `jobhunt_core`—, mientras `CLAUDE.md` afirmaba que
# la guarda garantizaba «ninguna contraseña publicada como defecto».
#
# Es el mismo error de diseño que dejó pasar la credencial de A-02: **un guard
# que busca valores concretos en vez de formas no es un guard.** Buscar la forma
# caza también la contraseña que nadie ha quemado todavía, que es justo la que
# importa.
DEFAULTS_SENSIBLES = re.compile(
    r"\$\{(?P<clave>[A-Za-z_]*(?:PASSWORD|SECRET|TOKEN|KEY|DSN)[A-Za-z_]*)"
    r":-(?P<valor>[^}]+)\}"
)

# Los dos defaults que YA existían cuando se escribió la comprobación por forma,
# congelados por NOMBRE. Es un ratchet, no una amnistía: cualquier clave
# sensible con default que NO esté en esta lista falla, así que no puede
# colarse una nueva.
#
# Por qué no se quitan ya: son las contraseñas del postgres y del redis DEL
# NÚCLEO en el compose de DESARROLLO, y no están en el `.env` de la máquina de
# desarrollo —sólo en `.env.example`—, de modo que exigirlas con `${VAR:?}`
# rompería `docker compose up` hasta que alguien añada dos líneas a su `.env`.
# Eso es una decisión del propietario y va en la misma ventana que A-61, A-16 y
# A-25 (ver el ledger).
#
# OJO, y es la lección de A-70, que está en esta misma auditoría: esta lista es
# EXPLÍCITA, con los nombres escritos. Si fuera derivada del estado actual
# —`ratchet.get(clave, valor_de_hoy)`— se desactivaría sola, que es justo el
# defecto que A-70 describe.
DEFAULTS_SENSIBLES_TOLERADOS = frozenset(
    {
        ("docker-compose.yml", "CORE_DB_PASSWORD"),
        ("docker-compose.yml", "CORE_REDIS_PASSWORD"),
    }
)


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

    fallos = [
        f"{fichero}: contiene la contraseña publicada «{quemada}» como valor o "
        "default; exígela con ${VAR:?} y sin default"
        for quemada in CLAVES_QUEMADAS
        if quemada in texto
    ]

    # Y por FORMA: cualquier `${CLAVE_SENSIBLE:-algo}`, con el valor que sea.
    # Se reporta la CLAVE y no el valor, para no volcar la credencial en el log
    # del CI — que es exactamente cómo se filtró la de A-02.
    for linea_n, linea in enumerate(texto.splitlines(), 1):
        if linea.lstrip().startswith("#"):
            continue
        for m in DEFAULTS_SENSIBLES.finditer(linea):
            clave = m.group("clave")
            if (fichero, clave) in DEFAULTS_SENSIBLES_TOLERADOS:
                continue
            fallos.append(
                f"{fichero}:{linea_n}: {clave} trae un default; "
                "exígela con ${VAR:?} y sin default"
            )
    return fallos


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

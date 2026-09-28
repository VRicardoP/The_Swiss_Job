"""Ningún secreto en la topología versionada del NAS (T13 §7 / A19-04).

`deploy/nas/*.yml` son copias de los composes que Container Station ejecuta, con
cada valor sensible sustituido por `${VARIABLE}`. Este script es la costura que
impide que un `cp` distraído vuelva a meter una contraseña en el repo: falla si
alguna clave con nombre de secreto lleva un literal, o si aparece cualquier
literal largo con pinta de token. Corre en CI y se puede lanzar a mano.

Uso: python3 scripts/check_no_secrets.py [ruta ...]   (por defecto deploy/nas)
Salida 0 = limpio · 1 = hay literales · 2 = no hay ficheros que comprobar.
"""

from __future__ import annotations

import pathlib
import re
import sys

CLAVE_SENSIBLE = re.compile(
    r"^\s*(?P<clave>[A-Z_]*(PASSWORD|SECRET|KEY|TOKEN|DSN|_URL|URL_ASYNC|API_BASE_URL|_BACKEND|"
    r"SMTP_USER|SMTP_USERNAME|SMTP_FROM|EMAIL|PROFILE_ID)[A-Z_]*)\s*:\s*(?P<valor>.+?)\s*$"
)
# Un literal largo sin espacios tras `clave:` que no sea una variable. Excluye lo
# que legítimamente es largo: imágenes, sumas sha256 de artefactos, nombres de red.
LITERAL_LARGO = re.compile(
    r"^\s*(?P<clave>[A-Za-z_]+)\s*:\s*['\"]?(?P<valor>[A-Za-z0-9+/=_.:@-]{24,})['\"]?\s*$"
)
CLAVES_LARGAS_LEGITIMAS = {
    "image",
    "container_name",
    "PYTHON_SHA256",
    "source",
    "target",
    "hostname",
    "CORE_CAPTURE_SLOT",
    "PATH",
    "HF_HOME",
    "SENTENCE_TRANSFORMERS_HOME",
    "name",
}


def es_variable(valor: str) -> bool:
    v = valor.strip().strip("'\"")
    return v.startswith("${") and v.endswith("}")


def revisar(ruta: pathlib.Path) -> list[str]:
    hallazgos = []
    for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        if linea.lstrip().startswith("#"):
            continue
        m = CLAVE_SENSIBLE.match(linea)
        if m and not es_variable(m.group("valor")):
            hallazgos.append(f"{ruta}:{n}: {m.group('clave')} lleva un literal")
            continue
        m = LITERAL_LARGO.match(linea)
        if (
            m
            and m.group("clave") not in CLAVES_LARGAS_LEGITIMAS
            and not es_variable(m.group("valor"))
        ):
            hallazgos.append(
                f"{ruta}:{n}: {m.group('clave')} tiene un literal largo con pinta de token"
            )
    return hallazgos


def main(argv: list[str]) -> int:
    raices = [pathlib.Path(a) for a in argv[1:]] or [pathlib.Path("deploy/nas")]
    ficheros = [f for r in raices for f in (r.rglob("*.yml") if r.is_dir() else [r])]
    if not ficheros:
        print("check_no_secrets: no hay ficheros que comprobar", file=sys.stderr)
        return 2
    hallazgos = [h for f in ficheros for h in revisar(f)]
    for h in hallazgos:
        print(h)
    print(f"check_no_secrets: {len(ficheros)} ficheros, {len(hallazgos)} hallazgos")
    return 1 if hallazgos else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

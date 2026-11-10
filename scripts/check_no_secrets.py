"""Ningún secreto en la topología versionada del NAS ni en la documentación.

Origen (T13 §7 / A19-04): `deploy/nas/*.yml` son copias de los composes que
Container Station ejecuta, con cada valor sensible sustituido por
`${VARIABLE}`. Este script es la costura que impide que un `cp` distraído
vuelva a meter una contraseña en el repo.

Ampliado el 2026-11-02 (auditoría A-02) con DOS sintaxis y DOS criterios,
porque con uno solo de cada no servía:

- **YAML** (`clave: valor`) — sólo en `.yml`/`.yaml`. Criterio ESTRICTO: ahí no
  debe haber NINGÚN literal sensible, porque son composes parametrizados. En un
  `.md` esta regla disparaba con anotaciones de Python citadas
  (`GLASSDOOR_PARTNER_KEY: str = ""`), así que no se aplica fuera de YAML.
- **`.env`** (`CLAVE=valor`) — en todos los ficheros. Criterio por FORMA: sólo
  es hallazgo si el valor parece una credencial generada (ver
  `parece_credencial`) o si una URL lleva credencial embebida. La
  documentación de este proyecto pega salidas de `grep` sobre `.env.prod`, y
  esa es exactamente la sintaxis por la que se filtró una contraseña de
  producción a `docs/DEPLOY_NAS.md` y al remoto, mientras el guard —apuntado a
  ese mismo fichero— devolvía «0 hallazgos».

La lección, por si se vuelve a ampliar: **un guard que busca valores concretos
en vez de formas no es un guard.** La versión anterior de
`check_compose_exposure.py` tenía el mismo defecto con una lista de literales
quemados.

Uso: python3 scripts/check_no_secrets.py [ruta ...]   (por defecto deploy/nas)
Revisa `*.yml`, `*.yaml` y `*.md` al recibir un directorio.
Salida 0 = limpio · 1 = hay literales · 2 = no hay ficheros que comprobar.
"""

from __future__ import annotations

import pathlib
import re
import sys

_NOMBRE_SENSIBLE = (
    r"[A-Z_]*(?:PASSWORD|SECRET|KEY|TOKEN|DSN|_URL|URL_ASYNC|API_BASE_URL|_BACKEND|"
    r"SMTP_USER|SMTP_USERNAME|SMTP_FROM|EMAIL|PROFILE_ID)[A-Z_]*"
)
CLAVE_SENSIBLE = re.compile(
    rf"^\s*(?P<clave>{_NOMBRE_SENSIBLE})\s*:\s*(?P<valor>.+?)\s*$"
)
# Sintaxis `.env` (`CLAVE=valor`), que la de YAML NO ve: sus dos regex exigen
# `:` como separador. Esa ceguera es por donde se fue una contraseña de
# producción a `docs/DEPLOY_NAS.md` y al remoto — el guard, apuntado a ese
# fichero, devolvía «0 hallazgos» (auditoría 2026-11-02, A-02). La documentación
# de este proyecto pega salidas de `grep` sobre `.env.prod`, así que es el
# formato que más falta vigilar.
# El valor es una cadena entrecomillada o una tirada SIN espacios. Tomar
# `.*$` se tragaba el resto de una línea de comando con varias asignaciones
# en línea (`POSTGRES_PASSWORD=ci REDIS_PASSWORD=ci docker compose …`), y el
# conjunto sí tenía forma de credencial: un falso positivo garantizado.
CLAVE_SENSIBLE_ENV = re.compile(
    rf"^\s*(?:export\s+)?(?P<clave>{_NOMBRE_SENSIBLE})="
    r"(?P<valor>\"[^\"]*\"|'[^']*'|\S*)"
)
# Clases de clave cuyo valor ES la credencial. Las demás que casan el nombre
# (`*_URL`, `*_DSN`, `*_BACKEND`) llevan la credencial DENTRO de una URL, y se
# revisan con `credencial_en_url`: un `redis://redis:6379/0` no es un secreto y
# marcarlo sólo entrenaría a ignorar el guard.
_CLASES_CREDENCIAL = ("PASSWORD", "SECRET", "TOKEN", "KEY")
# Formas con las que la documentación representa un valor que NO está: hay que
# aceptarlas o el guard es inservible sobre `docs/`. Medido antes de escribirlo:
# en `docs/**/*.md` hay 25 líneas con sintaxis `.env` y 18 claves distintas, y
# éstas son las formas reales que aparecen.
MARCADORES = re.compile(
    r"^$|^[\"']{0,2}$|[<>]|\.\.\.|…|\*{3,}|\bchangeme\b|\bredacted\b|\bredactado\b|"
    r"\bxxx+\b|\btu[-_]|\byour[-_]|\bexample\.(com|org)\b|\bpon[-_]|\bTODO\b",
    re.IGNORECASE,
)
# Un literal parece una credencial GENERADA, no una palabra escrita a mano, si
# es largo y mezcla al menos tres clases de carácter. Umbrales calibrados
# midiendo los valores reales de `docs/`, no elegidos a ojo:
#
#   la fuga de A-02 ........... 32 caracteres, 3 clases  → credencial
#   `audit-not-a-real-secret` . 23 caracteres, 2 clases  → marcador
#   `ci` (dummy del CI) .......  2 caracteres, 1 clase    → marcador
#   `***` (ya redactado) ......  3 caracteres, 1 clase    → marcador
#
# LO QUE ESTO NO CAZA, dicho a propósito: una credencial larga y toda en
# minúsculas (una passphrase en kebab-case, p. ej.) pasa por marcador. Cerrar
# ese hueco exige distinguir palabras de diccionario de ruido, y a cambio
# llenaría `docs/` de falsos positivos. Para los composes sigue estando
# `LITERAL_LARGO`, que no mira clases.
_MIN_LONGITUD_CREDENCIAL = 12
_MIN_CLASES_CREDENCIAL = 3
_CLASES_CARACTER = (r"[a-z]", r"[A-Z]", r"\d", r"[^a-zA-Z0-9]")
# Credencial embebida en una URL: `esquema://usuario:CREDENCIAL@host`.
CREDENCIAL_EN_URL = re.compile(r"://[^/\s:@]+:(?P<cred>[^@/\s]+)@")
# Un literal largo sin espacios tras `clave:` que no sea una variable. Excluye lo
# que legítimamente es largo: imágenes, sumas sha256 de artefactos, nombres de red.
LITERAL_LARGO = re.compile(
    r"^\s*(?P<clave>[A-Za-z_]+)\s*:\s*['\"]?(?P<valor>[A-Za-z0-9+/=_.:@-]{24,})['\"]?\s*$"
)
# Banderas booleanas cuyo nombre contiene TOKEN pero cuyo valor fijo no es una
# credencial. La lista es por clave y valor: no permite convertirlas en secretos
# literales ni relajar otras claves sensibles.
LITERALES_NO_SECRETOS = {
    "AUTH_REFRESH_TOKEN_IN_BODY": {"false"},
}

CLAVES_LARGAS_LEGITIMAS = {
    "image",
    "container_name",
    "PYTHON_SHA256",
    "source",
    "target",
    "hostname",
    "condition",  # depends_on: service_completed_successfully
    "PATH",
    "HF_HOME",
    "SENTENCE_TRANSFORMERS_HOME",
    "name",
}


def es_variable(valor: str) -> bool:
    v = valor.strip().strip("'\"")
    return v.startswith("${") and v.endswith("}")


def es_marcador(valor: str) -> bool:
    """¿El valor representa un secreto AUSENTE en vez de contenerlo?

    `<el-mismo-en-las-dos-líneas>`, `gsk_...`, vacío, `changeme`. Sin esto el
    guard no se puede apuntar a `docs/`, que es donde hizo falta.
    """
    return es_variable(valor) or bool(MARCADORES.search(valor.strip()))


def parece_credencial(valor: str) -> bool:
    """¿El literal tiene FORMA de credencial generada? (ver los umbrales)"""
    v = valor.strip().strip("'\"")
    if len(v) < _MIN_LONGITUD_CREDENCIAL:
        return False
    clases = sum(bool(re.search(p, v)) for p in _CLASES_CARACTER)
    return clases >= _MIN_CLASES_CREDENCIAL


def credencial_en_url(valor: str) -> str | None:
    """La credencial embebida en una URL, si la hay y no es un marcador."""
    m = CREDENCIAL_EN_URL.search(valor)
    if m is None:
        return None
    cred = m.group("cred")
    return None if es_marcador(cred) or not parece_credencial(cred) else cred


def revisar_linea_env(clave: str, valor: str) -> str | None:
    """Motivo por el que esta línea `CLAVE=valor` lleva un secreto, o None.

    Un correo NO se trata como credencial por este camino aunque su clave case
    el nombre (`*EMAIL`, `SMTP_FROM`): es un identificador, no un secreto, y
    marcarlo llenaría `docs/` de falsos positivos. El camino YAML conserva su
    criterio anterior sin cambios, porque allí se persigue otra cosa: que la
    topología versionada no lleve NINGÚN literal.
    """
    if credencial_en_url(valor) is not None:
        return f"{clave} lleva una URL con credencial embebida"
    if es_marcador(valor) or not parece_credencial(valor):
        return None
    if any(c in clave for c in _CLASES_CREDENCIAL):
        return f"{clave} lleva un literal"
    return None


def revisar(ruta: pathlib.Path) -> list[str]:
    hallazgos = []
    # Las dos regex de YAML sólo se aplican a YAML. En un `.md` disparaban con
    # anotaciones de Python citadas (`GLASSDOOR_PARTNER_KEY: str = ""`), que
    # tienen la misma forma `clave: valor` y no son ningún secreto. La de
    # sintaxis `.env` sí corre en los dos: el formato pegado en la
    # documentación es justo ése.
    es_yaml = ruta.suffix in (".yml", ".yaml")
    for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        if linea.lstrip().startswith("#"):
            continue
        m = CLAVE_SENSIBLE_ENV.match(linea)
        if m:
            motivo = revisar_linea_env(m.group("clave"), m.group("valor"))
            if motivo is not None:
                hallazgos.append(f"{ruta}:{n}: {motivo}")
            continue
        if not es_yaml:
            continue
        m = CLAVE_SENSIBLE.match(linea)
        if m and not es_variable(m.group("valor")):
            valor = m.group("valor").strip().strip("'\"").lower()
            if valor in LITERALES_NO_SECRETOS.get(m.group("clave"), set()):
                continue
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
    # `.yaml` y `.md` además de `.yml`: el primero porque un compose puede
    # llamarse así y se saltaba, y el segundo porque la documentación de este
    # proyecto pega salidas de `.env.prod` — que es como se filtró una
    # contraseña de producción al remoto (A-02).
    ficheros = sorted(
        {
            f
            for r in raices
            for f in (
                (g for p in ("*.yml", "*.yaml", "*.md") for g in r.rglob(p))
                if r.is_dir()
                else [r]
            )
        }
    )
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

"""Genera `backend/.env.prod.example` DESDE `Settings` (T13 §5).

El fichero de ejemplo se escribía a mano y derivaba: el 2026-09-28 tenía 28
claves frente a 130 campos de `Settings`, y le faltaban `CORE_API_BASE_URL`,
`CORE_CONSUMER_KEY`, `MATCH_SCORE_THRESHOLD` y las listas `LEGACY_DISABLED_*`
—justo las que deciden qué cosecha quién—. Generarlo desde la fuente de verdad
convierte la deriva en imposible, y `tests/test_env_example_covers_settings.py`
falla si alguien vuelve a editarlo a mano y se queda corto.

Uso (dentro del contenedor backend):  python scripts/gen_env_example.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from config import Settings  # noqa: E402

DESTINO = pathlib.Path(__file__).resolve().parents[1] / ".env.prod.example"

# Campos que NO deben aparecer, con su motivo (config.py lo documenta).
EXCLUIDOS = {
    "ALLOW_DEV_CREDENTIALS": "permiso explícito de dev; ausente en producción a propósito",
}
SENSIBLE = re.compile(r"(PASSWORD|SECRET|KEY|TOKEN|DSN|_URL$|URL_ASYNC$|_BACKEND$)")
# Valores de producción conocidos que difieren del defecto del código. Vienen del
# acta del punto 4 (traspaso nativo) y del .env efectivo; NO son secretos.
PRODUCCION = {
    "LEGACY_DISABLED_PROVIDERS": [
        "arbeitnow",
        "euremotejobs",
        "globaljobs",
        "jobgether",
        "jobspresso",
        "nav_arbeidsplassen",
        "ostjob",
        "publicjobs",
        "remotive",
        "thehub",
        "weworkremotely",
        "workingnomads",
        "zebis",
        "zentraljob",
    ],
    "LEGACY_DISABLED_SCRAPERS": ["financejobs", "irishjobs"],
    "MATCH_SCORE_THRESHOLD": 42.0,
}


def _render(nombre: str, campo) -> str:
    if nombre in PRODUCCION:
        valor = PRODUCCION[nombre]
        origen = "valor de producción (acta del punto 4)"
    else:
        valor = campo.default
        origen = "defecto del código"
    if SENSIBLE.search(nombre):
        return f"{nombre}=CHANGE_ME  # secreto: rellenar; defecto del código NO válido en producción"
    if isinstance(valor, (list, dict)):
        cuerpo = json.dumps(valor, ensure_ascii=False)
        return f"{nombre}='{cuerpo}'  # {origen}"
    if valor is None:
        return f"{nombre}=  # vacío por defecto"
    if isinstance(valor, bool):
        return f"{nombre}={'true' if valor else 'false'}  # {origen}"
    return f"{nombre}={valor}  # {origen}"


def main() -> int:
    lineas = [
        "# .env.prod.example — GENERADO por backend/scripts/gen_env_example.py desde Settings.",
        "# No editar a mano: regenerar tras cambiar config.py. La prueba",
        "# tests/test_env_example_covers_settings.py falla si este fichero se queda corto.",
        "#",
        "# Los CHANGE_ME son secretos o URLs con credencial: el arranque muere con una",
        "# contraseña de dev o un marcador de plantilla salvo ALLOW_DEV_CREDENTIALS=true,",
        "# que a propósito no aparece aquí.",
        "",
        "# --- Variables del compose (no son campos de Settings; las usan postgres y redis) ---",
        "POSTGRES_USER=swissjob",
        "POSTGRES_PASSWORD=CHANGE_ME",
        "POSTGRES_DB=swissjobhunter",
        "REDIS_PASSWORD=CHANGE_ME",
        "",
        "# --- Campos de Settings (backend/config.py), en su orden ---",
    ]
    campos = Settings.model_fields
    for nombre, campo in campos.items():
        if nombre in EXCLUIDOS:
            lineas.append(f"# {nombre}: omitido — {EXCLUIDOS[nombre]}")
            continue
        lineas.append(_render(nombre, campo))
    DESTINO.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(
        f"{DESTINO}: {len(campos) - len(EXCLUIDOS)} campos + {len(EXCLUIDOS)} omitidos"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

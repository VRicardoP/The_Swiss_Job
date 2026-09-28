"""T13 §5 — `.env.prod.example` no puede quedarse corto respecto a `Settings`.

Se escribía a mano y el 2026-09-28 cubría 28 de 130 campos. Ahora lo genera
`scripts/gen_env_example.py`; esta prueba falla si alguien lo edita a mano y deja
fuera un campo, o si añade un campo a `Settings` sin regenerar.
"""

import pathlib
import re

from config import Settings

EJEMPLO = pathlib.Path(__file__).resolve().parents[1] / ".env.prod.example"
COMPOSE = {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "REDIS_PASSWORD"}
OMITIDOS_A_PROPOSITO = {"ALLOW_DEV_CREDENTIALS"}


def _claves():
    texto = EJEMPLO.read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", texto, re.M))


def test_todo_campo_de_settings_esta_en_el_ejemplo():
    faltan = set(Settings.model_fields) - _claves() - OMITIDOS_A_PROPOSITO
    assert not faltan, f"faltan en .env.prod.example (regenerar): {sorted(faltan)}"


def test_el_ejemplo_no_inventa_claves():
    sobran = _claves() - set(Settings.model_fields) - COMPOSE
    assert not sobran, f"claves sin campo en Settings: {sorted(sobran)}"


def test_los_secretos_no_llevan_valor_real():
    texto = EJEMPLO.read_text(encoding="utf-8")
    for linea in texto.splitlines():
        m = re.match(
            r"^([A-Z0-9_]*(PASSWORD|SECRET|KEY|TOKEN|DSN)[A-Z0-9_]*)=(.*)$", linea
        )
        if m:
            assert m.group(3).startswith("CHANGE_ME"), f"{m.group(1)} lleva un valor"


def test_el_permiso_de_dev_sigue_fuera():
    """Control: ALLOW_DEV_CREDENTIALS aparece sólo como comentario, nunca como clave."""
    assert "ALLOW_DEV_CREDENTIALS" not in _claves()
    assert "ALLOW_DEV_CREDENTIALS" in EJEMPLO.read_text(encoding="utf-8")

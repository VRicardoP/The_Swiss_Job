"""Runner COMPARTIDO de Alembic por subprocess para los tests de migración."""

import os
import subprocess
from pathlib import Path

# Ruta ABSOLUTA al ini (rev. 1ª A-12: independiente del CWD, la misma
# disciplina que migrate.py — los tests deben correr también desde jobhunt_core/).
_INI = str(Path(__file__).resolve().parent.parent / "alembic.ini")


def run_alembic(
    db_url: str, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    """`alembic -c <ini> <args>` contra `db_url` (CORE_DATABASE_URL inyectada).

    Captura stdout/stderr — los tests de fronteras de datos inspeccionan
    `returncode` y `stderr + stdout` con `check=False`.
    """
    env = {**os.environ, "CORE_DATABASE_URL": db_url}
    return subprocess.run(
        ["alembic", "-c", _INI, *args],
        check=check, capture_output=True, env=env,
    )

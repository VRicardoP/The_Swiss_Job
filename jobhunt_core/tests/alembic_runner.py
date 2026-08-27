"""Runner COMPARTIDO de Alembic para los tests de migración.

O-2 (auditoría de eficiencia 2026-08-27) — MEDIDO en aislamiento sobre una BD
desechable `bench_*`:

    python -c 'import alembic.config'        :  887 ms
    alembic CLI 'current' (arranque puro)    : 1190 ms
    alembic CLI 'upgrade head' (29 migr.)    : 1446 ms
      -> SQL real de las migraciones         :  257 ms
      -> sobrecoste de arranque              : 1190 ms  (82 %)

La suite invoca este runner 94 veces por corrida completa, así que pagaba ~112
segundos SOLO en arrancar intérpretes de Python para hacer un trabajo de SQL de
257 ms, y encima en un proceso donde SQLAlchemy ya estaba importado.

Lo que NO cambia (y es el motivo por el que este atajo es admisible): cada BD
desechable se sigue creando de cero y migrando de cero, `alembic_version`
incluida. No se comparte esquema, ni base, ni estado de Alembic entre tests —
lo único que desaparece es el `fork` + `import`. La capacidad de refutar de la
suite (ciclos downgrade/upgrade reales sobre datos reales) queda intacta.

Aislamiento entre invocaciones: `Config` NUEVO en cada llamada — el
`ScriptDirectory` y el mapa de revisiones se cachean DENTRO del Config, y
reutilizarlo entre dos bases distintas es justo el estado compartido que aquí
no queremos. La URL va por `Config.attributes["core_url"]` (la lee `env.py`):
inyección explícita y por invocación, sin tocar el singleton `settings`.
"""

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

    Vía EN PROCESO por defecto (ver docstring del módulo). Con `check=False` el
    llamador NO está migrando: está afirmando sobre el COMPORTAMIENTO DEL CLI
    (`returncode` y `stderr + stdout` — p.ej. la down-migration que se niega a
    truncar `destination`). Esa fidelidad solo la da un proceso de verdad, así
    que ese camino sigue siendo subproceso.
    """
    if not check:
        return _run_cli(db_url, *args, check=False)
    return _run_in_process(db_url, *args)


def _run_cli(
    db_url: str, *args: str, check: bool
) -> subprocess.CompletedProcess:
    """El CLI real, en su propio intérprete: returncode y stderr auténticos."""
    env = {**os.environ, "CORE_DATABASE_URL": db_url}
    return subprocess.run(
        ["alembic", "-c", _INI, *args],
        check=check, capture_output=True, env=env,
    )


def _run_in_process(db_url: str, *args: str) -> subprocess.CompletedProcess:
    """`upgrade`/`downgrade` sin arrancar un intérprete.

    Whitelist explícita en vez de `getattr(command, verbo)`: un verbo nuevo
    (o mal escrito) debe fallar aquí y no invocar cualquier atributo del
    módulo `command`. Un fallo de la migración propaga la excepción de
    Alembic — que es lo que `check=True` promete: no seguir en silencio.
    """
    from alembic import command
    from alembic.config import Config

    verbos = {"upgrade": command.upgrade, "downgrade": command.downgrade}
    verbo, *resto = args
    if verbo not in verbos:
        raise ValueError(
            f"run_alembic en proceso solo cubre {sorted(verbos)}; "
            f"para {verbo!r} usa check=False (CLI real)"
        )
    cfg = Config(_INI)
    cfg.attributes["core_url"] = db_url
    verbos[verbo](cfg, *resto)
    return subprocess.CompletedProcess([verbo, *resto], 0, b"", b"")

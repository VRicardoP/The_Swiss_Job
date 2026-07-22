"""A-01: cadena de migraciones PROPIA del core, con un solo head."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_single_head_is_baseline():
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["core0001"]


def test_version_table_lives_in_core_schema():
    """DoD 'migra solo el core': la version table se configura SIEMPRE en el
    esquema del core (jobhunt.alembic_version), nunca en public (legacy).
    env.py no es importable fuera de alembic → guardia estática sobre su fuente.
    """
    env_src = (
        Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    ).read_text(encoding="utf-8")
    # En ambos modos (offline y online).
    assert env_src.count("version_table_schema=settings.CORE_DB_SCHEMA") == 2

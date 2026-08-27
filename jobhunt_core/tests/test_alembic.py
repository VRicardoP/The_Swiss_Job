"""A-01: cadena de migraciones PROPIA del core, con un solo head."""

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from jobhunt_core.config import settings
from jobhunt_core.tests.alembic_runner import run_alembic


def test_single_head_chain():
    """Invariante: UN solo head, con la cadena anclada en el baseline core0001."""
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"múltiples heads: {heads}"
    assert script.get_bases() == ["core0001"]


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


# --------------------------------------------- O-2: Alembic en proceso

_ADMIN = os.getenv("CORE_ADMIN_DATABASE_URL")


def _with_db(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))


@contextmanager
def _bd_desechable(admin_engine, prefijo: str):
    """CREATE DATABASE + extensiones + esquema; DROP WITH (FORCE) al salir."""
    dbname = f"{prefijo}_{uuid.uuid4().hex[:12]}"
    with admin_engine.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    try:
        boot = sa.create_engine(
            _with_db(_ADMIN, dbname), poolclass=sa.pool.NullPool
        )
        try:
            with boot.begin() as c:
                c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
                c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                c.execute(
                    sa.text(
                        f'CREATE SCHEMA IF NOT EXISTS "{settings.CORE_DB_SCHEMA}" '
                        "AUTHORIZATION jobhunt_core"
                    )
                )
        finally:
            boot.dispose()
        yield dbname
    finally:
        with admin_engine.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))


def _consulta(dbname: str, sql: str):
    engine = sa.create_engine(
        _with_db(_ADMIN, dbname),
        poolclass=sa.pool.NullPool,
        connect_args={"options": f"-csearch_path={settings.CORE_DB_SCHEMA},public"},
    )
    try:
        with engine.begin() as c:
            return c.execute(sa.text(sql)).scalar_one()
    finally:
        engine.dispose()


@pytest.mark.skipif(not _ADMIN, reason="requiere BD (ejecutar vía core-migrate)")
def test_dos_bases_desechables_del_mismo_proceso_no_comparten_estado():
    """GUARDA PERMANENTE de O-2 (2026-08-27).

    Migrar en proceso ahorra ~740 ms por invocación (medido), pero solo es
    admisible si NO introduce estado compartido: el `Config` de Alembic cachea
    su `ScriptDirectory` y `settings` es un singleton de todo el proceso. Esta
    guarda exige, en una sola corrida y en el mismo intérprete, que:

    1. cada BD desechable llega a head POR SÍ MISMA (`alembic_version` propia);
    2. una escritura en la primera NO es visible en la segunda (bases virgen y
       separadas de verdad, no un esquema reciclado);
    3. `settings.CORE_DATABASE_URL` sigue APUNTANDO DONDE APUNTABA — la URL se
       inyecta por invocación (`Config.attributes`) y jamás reapunta el
       singleton, que es de quien cuelgan los engines del resto de la suite.

    Si alguien vuelve a "optimizar" reutilizando Config, esquema o base entre
    tests, este test cae.
    """
    url_antes = settings.CORE_DATABASE_URL
    admin_engine = sa.create_engine(
        _ADMIN, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        with _bd_desechable(admin_engine, "jobhunt_iso_a") as a, \
                _bd_desechable(admin_engine, "jobhunt_iso_b") as b:
            run_alembic(_with_db(settings.CORE_DATABASE_URL, a), "upgrade", "head")
            run_alembic(_with_db(settings.CORE_DATABASE_URL, b), "upgrade", "head")

            head_a = _consulta(a, "SELECT version_num FROM alembic_version")
            head_b = _consulta(b, "SELECT version_num FROM alembic_version")
            assert head_a == head_b  # misma cadena aplicada entera

            _consulta(
                a,
                "INSERT INTO consumers (id, name) VALUES "
                f"('{uuid.uuid4()}', 'aislamiento') RETURNING 1",
            )
            assert _consulta(a, "SELECT count(*) FROM consumers") == 1
            # La 2ª base fue migrada DESPUÉS y en el MISMO proceso: si
            # compartieran algo, esto no sería 0.
            assert _consulta(b, "SELECT count(*) FROM consumers") == 0
    finally:
        admin_engine.dispose()

    assert settings.CORE_DATABASE_URL == url_antes


def test_runner_en_proceso_rechaza_verbos_fuera_de_la_whitelist():
    """`getattr(command, verbo)` invocaría cualquier atributo del módulo; la
    whitelist hace que un verbo no cubierto falle en el runner y no en Alembic
    con un error irreconocible. `check=False` sigue siendo la vía para el CLI."""
    with pytest.raises(ValueError, match="check=False"):
        run_alembic("postgresql://nadie@nada/nada", "current")

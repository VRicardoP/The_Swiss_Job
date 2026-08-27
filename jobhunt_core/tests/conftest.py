"""Aislamiento de SESIÓN de la suite (P2-8, revisión externa Fase B).

La suite YA NO corre contra la BD compartida de dev: `pytest_configure`
crea UNA BD DESECHABLE (`jobhunt_suite_<hex>`) con el patrón ya existente
de test_integration_migration_rehearsal/projector — CREATE DATABASE +
extensión vector + esquema del core (owner jobhunt_core) + `alembic
upgrade head` vía tests/alembic_runner — y reapunta
`settings.CORE_DATABASE_URL` (y el env `CORE_DATABASE_URL`) a ella ANTES
de la colección: todo engine que nace de settings (los fixtures `db` de
los tests compartidos, `task_session_factory`, `database.engine`/
`SessionLocal` — que se crean al importar, DURANTE la colección) apunta a
la BD desechable. `pytest_unconfigure` la DROPea WITH (FORCE).

Por qué hook de plugin y no fixture de sesión: un fixture corre DESPUÉS de
la colección, y para entonces `database.engine`/`SessionLocal` (usados por
api/deps y harvest/runner como default) ya estarían ligados a la URL de
dev — `pytest_configure` corre ANTES de importar ningún módulo de test.

La migración corre COMO el rol jobhunt_core (patrón migrate.py paso 3):
mismo layout de privilegios que producción, y `settings.CORE_DATABASE_URL`
sigue llevando el rol propio (invariante de test_isolation).

Los tests que ya crean su BD desechable PROPIA (capture/projector/metrics/
gate/rehearsal) usan CORE_ADMIN_DATABASE_URL directamente y siguen IGUAL
(sin anidar). test_integration_admin verifica los GRANTs/roles REALES del
clúster vía el DSN admin (transacción con rollback, sin rastro): también
sigue igual. Sin CORE_ADMIN_DATABASE_URL no se crea nada (los tests de
integración ya se saltan solos).
"""

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

# Estado del hook (una BD por invocación de pytest; sin fixtures de por medio).
_suite: dict = {}


def _url_with_dbname(url: str, dbname: str) -> str:
    """La MISMA URL (esquema/credenciales/host) apuntando a otra BD."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))


def pytest_configure(config):
    admin_url = os.getenv("CORE_ADMIN_DATABASE_URL")
    if not admin_url:
        return  # sin BD: los tests de integración se saltan solos

    import sqlalchemy as sa

    from jobhunt_core.config import settings
    from jobhunt_core.tests.alembic_runner import run_alembic

    dbname = f"jobhunt_suite_{uuid.uuid4().hex[:12]}"
    admin_engine = sa.create_engine(
        admin_url, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as c:
            role = c.execute(
                sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'jobhunt_core'")
            ).scalar()
            if not role:
                raise RuntimeError(
                    "rol jobhunt_core AUSENTE en el clúster: ejecutar "
                    "`python -m jobhunt_core.migrate` (bootstrap) antes de la suite"
                )
            c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    finally:
        admin_engine.dispose()

    # Bootstrap mínimo DENTRO de la BD nueva (lo que migrate hace en dev):
    # pgvector en public + esquema del core propiedad del rol del core.
    bootstrap = sa.create_engine(
        _url_with_dbname(admin_url, dbname), poolclass=sa.pool.NullPool
    )
    try:
        with bootstrap.begin() as c:
            c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            c.execute(
                sa.text(
                    f'CREATE SCHEMA IF NOT EXISTS "{settings.CORE_DB_SCHEMA}" '
                    "AUTHORIZATION jobhunt_core"
                )
            )
    finally:
        bootstrap.dispose()

    # Migración a head COMO el rol del core (patrón migrate.py paso 3).
    suite_url = _url_with_dbname(settings.CORE_DATABASE_URL, dbname)
    run_alembic(suite_url, "upgrade", "head")

    # Reapuntar settings (singleton compartido por todo import posterior) y
    # el env (para cualquier proceso hijo que re-lea la configuración).
    settings.CORE_DATABASE_URL = suite_url
    os.environ["CORE_DATABASE_URL"] = suite_url
    _suite.update(dbname=dbname, admin_url=admin_url)


def pytest_unconfigure(config):
    if not _suite:
        return
    import sqlalchemy as sa

    admin_engine = sa.create_engine(
        _suite["admin_url"], poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as c:
            c.execute(
                sa.text(f'DROP DATABASE IF EXISTS "{_suite["dbname"]}" WITH (FORCE)')
            )
    finally:
        admin_engine.dispose()
        _suite.clear()


@pytest.fixture(autouse=True)
def _barrido_al_ralenti(monkeypatch):
    """El barrido de arbeitnow se AUTOLIMITA a `PAGE_PAUSE_S` s/página (auditoría G10
    P1-2): contra el feed real es lo que hace `complete=True` alcanzable, pero en la
    suite el feed va MOCKEADO y ahí la pausa solo alarga la ejecución.

    La pausa se ESCALA, no se anula (auditoría G11 P1-1/P3-3). Con `0.0` el backoff del
    reintento —`pause * 2**intento`— valía exactamente CERO en toda la suite: ningún test
    podía observar una espera, y por eso 706 tests convivieron con un `Retry-After` que
    dormía 24 h. Una prueba que no puede ver el mecanismo no lo protege. Con un épsilon
    la forma se conserva (se calcula, se acota y se duerme de verdad) y la suite sigue
    siendo instantánea. Los tests que MIDEN el ritmo o el techo suben el valor ellos
    mismos.
    """
    from jobhunt_core.harvest.providers import arbeitnow

    monkeypatch.setattr(arbeitnow, "PAGE_PAUSE_S", 0.001)

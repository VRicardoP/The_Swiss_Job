"""Tests de INTEGRACIÓN contra Postgres real (rev. 3ª #1/#2).

Requieren el DSN admin → se ejecutan vía el contenedor del job de migración:

    docker compose -f docker-compose.yml -f docker-compose.dev.yml \
    run --rm core-migrate python -m pytest jobhunt_core/tests -q
(sin los dos `-f` se probaría el código de la IMAGEN, no el del árbol de trabajo)

Sin CORE_ADMIN_DATABASE_URL (p.ej. en core-api --no-deps) se saltan. Cada test
trabaja dentro de una transacción SIN commit: no deja rastro en la BD.
"""

import os

import pytest
import sqlalchemy as sa

from jobhunt_core.migrate import _verify_isolation, _verify_pgvector

_ADMIN = os.getenv("CORE_ADMIN_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _ADMIN, reason="requiere CORE_ADMIN_DATABASE_URL (ejecutar vía core-migrate)"
)


@pytest.fixture()
def admin_conn():
    engine = sa.create_engine(_ADMIN, poolclass=sa.pool.NullPool)
    with engine.connect() as conn:  # transacción abierta; rollback al salir
        conn.execute(sa.text("REVOKE ALL ON SCHEMA public FROM jobhunt_core"))
        conn.execute(sa.text("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM jobhunt_core"))
        conn.execute(sa.text("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM jobhunt_core"))
        yield conn
        conn.rollback()
    engine.dispose()


def _legacy_table_present(conn, table: str) -> bool:
    return conn.execute(
        sa.text("SELECT to_regclass('public.' || :t) IS NOT NULL"), {"t": table}
    ).scalar()


def test_column_level_grant_is_detected(admin_conn):
    """Un GRANT SELECT(col) no aparece en has_table_privilege pero DEBE
    hacer fallar la verificación
    (rev. 3ª #2). Rollback → sin rastro."""
    admin_conn.execute(sa.text("CREATE TABLE public._probe_colacl (id int, s text)"))
    admin_conn.execute(
        sa.text("GRANT SELECT (id) ON public._probe_colacl TO jobhunt_core")
    )
    with pytest.raises(RuntimeError, match="COLUMNA"):
        _verify_isolation(admin_conn, "jobhunt")


def test_clean_state_passes_and_pgvector_resolves(admin_conn):
    """Estado real aislado pasa y pgvector resuelve como el rol del Core."""
    _verify_isolation(admin_conn, "jobhunt")
    _verify_pgvector(admin_conn, "jobhunt")  # revienta si el tipo/operador no resuelve

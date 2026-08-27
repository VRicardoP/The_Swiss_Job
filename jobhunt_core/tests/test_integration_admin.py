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

from jobhunt_core.migrate import _verify_capture, _verify_isolation, _verify_pgvector

_ADMIN = os.getenv("CORE_ADMIN_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _ADMIN, reason="requiere CORE_ADMIN_DATABASE_URL (ejecutar vía core-migrate)"
)


@pytest.fixture()
def admin_conn():
    engine = sa.create_engine(_ADMIN, poolclass=sa.pool.NullPool)
    with engine.connect() as conn:  # transacción abierta; rollback al salir
        yield conn
        conn.rollback()
    engine.dispose()


def _legacy_table_present(conn, table: str) -> bool:
    return conn.execute(
        sa.text("SELECT to_regclass('public.' || :t) IS NOT NULL"), {"t": table}
    ).scalar()


def test_column_level_grant_is_detected(admin_conn):
    """Un GRANT SELECT(col) sobre una tabla FUERA de la whitelist RO de B-01
    no aparece en has_table_privilege pero DEBE hacer fallar la verificación
    (rev. 3ª #2). Rollback → sin rastro."""
    admin_conn.execute(sa.text("CREATE TABLE public._probe_colacl (id int, s text)"))
    admin_conn.execute(
        sa.text("GRANT SELECT (id) ON public._probe_colacl TO jobhunt_core")
    )
    with pytest.raises(RuntimeError, match="COLUMNA"):
        _verify_isolation(admin_conn, "jobhunt")


def test_write_grant_on_whitelisted_table_is_detected(admin_conn):
    """B-01: la whitelist es SELECT-only — un INSERT hasta en una tabla
    enumerada (public.jobs) debe reventar la verificación. Rollback."""
    if not _legacy_table_present(admin_conn, "jobs"):
        pytest.skip("BD sin esquema legacy migrado (public.jobs ausente)")
    admin_conn.execute(sa.text("GRANT INSERT ON public.jobs TO jobhunt_core"))
    with pytest.raises(RuntimeError, match="ESCRITURA"):
        _verify_isolation(admin_conn, "jobhunt")


def test_missing_enumerated_select_is_detected(admin_conn):
    """B-01, la otra dirección de "EXACTAMENTE ese conjunto": perder el
    SELECT enumerado (deriva) también falla — la captura y las métricas
    morirían en silencio más tarde. Rollback."""
    if not _legacy_table_present(admin_conn, "users"):
        pytest.skip("BD sin esquema legacy migrado (public.users ausente)")
    admin_conn.execute(sa.text("REVOKE SELECT ON public.users FROM jobhunt_core"))
    with pytest.raises(RuntimeError, match="SIN el SELECT"):
        _verify_isolation(admin_conn, "jobhunt")


def test_capture_membership_is_detected(admin_conn):
    """P3 B-01: jobhunt_capture no hereda de NADIE — una membership (que le
    daría por herencia lo que su whitelist niega) revienta la verificación.
    Rollback → sin rastro."""
    admin_conn.execute(sa.text("CREATE ROLE _probe_cap_parent"))
    admin_conn.execute(sa.text("GRANT _probe_cap_parent TO jobhunt_capture"))
    with pytest.raises(RuntimeError, match="memberships"):
        _verify_capture(admin_conn)


def test_clean_state_passes_and_pgvector_resolves(admin_conn):
    """Estado real limpio (GRANTs RO de B-01 instalados) → la verificación
    pasa — whitelist permitida, nada más; el rol de captura verifica; y
    pgvector (cast + <=>) resuelve COMO el rol del core (rev. 3ª #1)."""
    _verify_isolation(admin_conn, "jobhunt")
    _verify_capture(admin_conn)  # jobhunt_capture: LOGIN REPLICATION, plano
    _verify_pgvector(admin_conn, "jobhunt")  # revienta si el tipo/operador no resuelve

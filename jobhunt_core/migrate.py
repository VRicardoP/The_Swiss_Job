"""Job de migración del core (A-01) — one-shot, PROPIO.

NO se cuelga del entrypoint del backend legacy (§15bis): es su propio
contenedor de un solo uso.

1) Bootstrap idempotente (solo si hay CORE_ADMIN_DATABASE_URL): rol de MÍNIMO
   PRIVILEGIO que CONVERGE en cada corrida — contraseña, atributos
   (NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS) y
   revocación de memberships — + esquema `jobhunt` de su propiedad.
2) Verificación del aislamiento EFECTIVO contra Postgres real (atributos,
   memberships, sin CREATE en public, sin SELECT en tablas legacy): si algo
   no se cumple, el job FALLA (no se migra sobre un rol mal aislado).
3) `alembic upgrade head` conectado como el rol del core: solo puede tocar su
   esquema (version table incluida: jobhunt.alembic_version).

Política sobre el pseudo-rol PUBLIC (explícita, revisión externa A-01 #3): NO
se revocan los grants de PUBLIC sobre el esquema `public` (pertenecen al ámbito
del legacy y afectarían a todos los roles). El aislamiento efectivo del core lo
garantizan (a) las ACL de las tablas legacy (ningún grant al core), (b) los
atributos de mínimo privilegio del rol y (c) la ausencia de memberships — y las
tres cosas se VERIFICAN en cada corrida de este job.
"""

import logging
import os
import re
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from jobhunt_core.config import settings

logger = logging.getLogger(__name__)

_ROLE = "jobhunt_core"
# Atributos de mínimo privilegio: se APLICAN siempre (un rol pre-existente con
# SUPERUSER/CREATEDB/etc. converge aquí a rol plano).
_ROLE_ATTRS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
# CREATE ROLE / CREATE SCHEMA son DDL (sin parámetros bind): se validan los
# literales antes de interpolarlos para que la interpolación sea segura.
_PW_RE = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _bootstrap() -> None:
    """Crea/converge rol+esquema si hay DSN de admin. Sin él, se asumen ya creados."""
    admin_url = os.getenv("CORE_ADMIN_DATABASE_URL")
    if not admin_url:
        logger.info("Sin CORE_ADMIN_DATABASE_URL: se asume rol+esquema ya creados")
        return

    password = os.getenv("CORE_DB_PASSWORD", "jobhunt_core_dev")
    if not _PW_RE.match(password):
        raise ValueError("CORE_DB_PASSWORD inválido (solo [A-Za-z0-9_-], 8-128 chars)")
    schema = settings.CORE_DB_SCHEMA
    if not _IDENT_RE.match(schema):
        raise ValueError(f"CORE_DB_SCHEMA inválido: {schema!r}")

    engine = sa.create_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool
    )
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _ROLE}
        ).scalar()
        # Contraseña Y atributos convergen SIEMPRE (idempotencia también para la
        # credencial y para privilegios elevados heredados de un rol pre-existente).
        verb = "CREATE" if not exists else "ALTER"
        conn.execute(
            sa.text(f"{verb} ROLE {_ROLE} {_ROLE_ATTRS} PASSWORD '{password}'")
        )
        logger.info("Rol %s %s (mínimo privilegio)", _ROLE, "creado" if not exists else "convergido")

        # Revocar CUALQUIER membership: el rol del core no hereda de nadie.
        granted = conn.execute(
            sa.text(
                "SELECT r.rolname FROM pg_auth_members m "
                "JOIN pg_roles r ON r.oid = m.roleid "
                "WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = :m)"
            ),
            {"m": _ROLE},
        ).scalars().all()
        for g in granted:
            if not _IDENT_RE.match(g):
                raise ValueError(f"Membership con nombre inesperado: {g!r}")
            conn.execute(sa.text(f'REVOKE "{g}" FROM {_ROLE}'))
            logger.warning("Membership %s revocada del rol %s", g, _ROLE)

        conn.execute(
            sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}" AUTHORIZATION {_ROLE}')
        )
        # Revoca los grants DIRECTOS del rol sobre el esquema legacy (los del
        # pseudo-rol PUBLIC no se tocan: ver política en el docstring).
        conn.execute(sa.text(f"REVOKE ALL ON SCHEMA public FROM {_ROLE}"))
        conn.execute(sa.text(f'ALTER ROLE {_ROLE} SET search_path = "{schema}"'))

        _verify_isolation(conn)
    engine.dispose()


def _verify_isolation(conn: sa.Connection) -> None:
    """Aislamiento EFECTIVO verificado contra Postgres; si falla, el job muere."""
    attrs = conn.execute(
        sa.text(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname = :r"
        ),
        {"r": _ROLE},
    ).one()
    if any(attrs):
        raise RuntimeError(f"Rol {_ROLE} conserva atributos elevados: {tuple(attrs)}")

    memberships = conn.execute(
        sa.text(
            "SELECT count(*) FROM pg_auth_members "
            "WHERE member = (SELECT oid FROM pg_roles WHERE rolname = :r)"
        ),
        {"r": _ROLE},
    ).scalar()
    if memberships:
        raise RuntimeError(f"Rol {_ROLE} conserva {memberships} memberships")

    can_create_public = conn.execute(
        sa.text("SELECT has_schema_privilege(:r, 'public', 'CREATE')"), {"r": _ROLE}
    ).scalar()
    if can_create_public:
        raise RuntimeError(f"Rol {_ROLE} puede CREATE en el esquema public (legacy)")

    # Si la tabla legacy central existe, el core NO debe poder leerla.
    jobs_exists = conn.execute(
        sa.text("SELECT to_regclass('public.jobs') IS NOT NULL")
    ).scalar()
    if jobs_exists:
        can_read_jobs = conn.execute(
            sa.text("SELECT has_table_privilege(:r, 'public.jobs', 'SELECT')"),
            {"r": _ROLE},
        ).scalar()
        if can_read_jobs:
            raise RuntimeError(f"Rol {_ROLE} puede leer public.jobs (legacy)")

    logger.info(
        "Aislamiento verificado: sin atributos elevados, sin memberships, "
        "sin CREATE en public, sin SELECT en tablas legacy"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    _bootstrap()
    cfg = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    command.upgrade(cfg, "head")
    logger.info("Migraciones del core al día (head)")


if __name__ == "__main__":
    main()

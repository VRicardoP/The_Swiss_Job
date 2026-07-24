"""Job de migración del core (A-01) — one-shot, PROPIO.

NO se cuelga del entrypoint del backend legacy (§15bis): es su propio
contenedor de un solo uso.

1) Bootstrap idempotente (solo si hay CORE_ADMIN_DATABASE_URL): rol de MÍNIMO
   PRIVILEGIO que CONVERGE en cada corrida — contraseña, atributos
   (NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS) y
   revocación de memberships — + esquema `jobhunt` de su propiedad. Desde
   B-01 (sombra) también: rol `jobhunt_capture` (LOGIN REPLICATION, para el
   slot CDC) y los GRANTs RO ENUMERADOS de §1 a jobhunt_core (USAGE en public
   + SELECT sobre EXACTAMENTE jobs/user_profiles/users/match_results).
2) Verificación del aislamiento EFECTIVO contra Postgres real (atributos,
   memberships, sin CREATE en public, y en tablas legacy SOLO la whitelist
   SELECT-only de B-01 — ni una tabla ni un privilegio más): si algo no se
   cumple, el job FALLA (no se migra sobre un rol mal aislado).
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
from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from jobhunt_core.config import settings

logger = logging.getLogger(__name__)

_ROLE = "jobhunt_core"
# Atributos de mínimo privilegio: se APLICAN siempre (un rol pre-existente con
# SUPERUSER/CREATEDB/etc. converge aquí a rol plano).
_ROLE_ATTRS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
# Rol de REPLICATION dedicado a la captura CDC de la sombra (B-01, CONTRATOS
# FASE B §1/§2): SOLO abre el slot lógico y streamea WAL — no lee tablas.
_CAPTURE_ROLE = "jobhunt_capture"
_CAPTURE_ATTRS = "LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
# GRANTs RO ENUMERADOS (§1): jobhunt_core obtiene SELECT sobre EXACTAMENTE
# estas tablas legacy (reconciliación de métricas + backfill del snapshot).
# Cualquier otra tabla o cualquier otro privilegio sigue PROHIBIDO y la
# verificación de abajo lo detecta.
_LEGACY_RO_TABLES = ("jobs", "user_profiles", "users", "match_results")
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
    capture_password = os.getenv("CORE_CAPTURE_PASSWORD", "jobhunt_capture_dev")
    if not _PW_RE.match(capture_password):
        raise ValueError(
            "CORE_CAPTURE_PASSWORD inválido (solo [A-Za-z0-9_-], 8-128 chars)"
        )
    schema = settings.CORE_DB_SCHEMA
    if not _IDENT_RE.match(schema):
        raise ValueError(f"CORE_DB_SCHEMA inválido: {schema!r}")
    # Cross-check ANTES de tocar nada (rev. #5): si la contraseña que se va a
    # fijar en el rol no coincide con la de CORE_DATABASE_URL, la rotación
    # dejaría a api/worker (y al propio Alembic) sin poder autenticar.
    url_password = urlsplit(settings.CORE_DATABASE_URL).password
    if url_password != password:
        raise ValueError(
            "CORE_DB_PASSWORD no coincide con la contraseña de CORE_DATABASE_URL: "
            "rotarían desincronizadas (rol con una, clientes con otra)"
        )

    engine = sa.create_engine(admin_url, poolclass=sa.pool.NullPool)
    # TRANSACCIONAL (rev. #5): CREATE/ALTER ROLE, REVOKE y CREATE SCHEMA son
    # transaccionales en Postgres; un fallo (incluida la verificación) hace
    # rollback y NO deja el bootstrap a medias.
    with engine.begin() as conn:
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
        # pseudo-rol PUBLIC no se tocan: ver política en el docstring); el
        # USAGE acotado de la sombra se re-instala justo después (B-01, §1).
        conn.execute(sa.text(f"REVOKE ALL ON SCHEMA public FROM {_ROLE}"))
        # search_path = jobhunt, public: pgvector (tipo + operadores) vive en
        # public y A-02 lo necesita; las tablas legacy quedan protegidas por
        # ACL (verificadas abajo), no por invisibilidad del esquema (rev. 3ª #1).
        conn.execute(
            sa.text(f'ALTER ROLE {_ROLE} SET search_path = "{schema}", public')
        )

        _bootstrap_capture(conn, capture_password)

        _verify_isolation(conn, schema)
        _verify_capture(conn)
        _verify_pgvector(conn, schema)
    engine.dispose()


def _bootstrap_capture(conn: sa.Connection, capture_password: str) -> None:
    """Infra CDC de la sombra (B-01, CONTRATOS_FASE_B §1) — idempotente.

    1) Rol `jobhunt_capture` LOGIN REPLICATION (solo abre el slot lógico y
       streamea WAL: la decodificación lógica no pasa por las ACL de tabla,
       el rol no necesita — ni recibe — SELECT sobre nada) y SIN memberships
       (no hereda de nadie — misma disciplina que jobhunt_core: una
       membership le daría por herencia lo que la whitelist le niega).
    2) GRANTs RO ENUMERADOS a `jobhunt_core`: USAGE sobre `public` + SELECT
       sobre EXACTAMENTE _LEGACY_RO_TABLES (backfill por snapshot y métricas
       read-only de §5). El resto de `public` sigue a cero privilegios y
       `_verify_isolation` lo impone.
    Una tabla enumerada ausente (entorno sin legacy migrado) se salta con
    warning: el GRANT converge en la siguiente corrida — la verificación
    también la trata como opcional mientras no exista.
    """
    exists = conn.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _CAPTURE_ROLE}
    ).scalar()
    verb = "CREATE" if not exists else "ALTER"
    # Contraseña Y atributos convergen SIEMPRE (misma disciplina que _ROLE).
    conn.execute(
        sa.text(f"{verb} ROLE {_CAPTURE_ROLE} {_CAPTURE_ATTRS} PASSWORD '{capture_password}'")
    )
    logger.info(
        "Rol %s %s (LOGIN REPLICATION, sin SELECT sobre tabla alguna)",
        _CAPTURE_ROLE,
        "creado" if not exists else "convergido",
    )

    # Revocar CUALQUIER membership (mismo bucle que _bootstrap para
    # jobhunt_core): el rol de captura no hereda de nadie — una membership
    # pre-existente le daría por herencia privilegios que su whitelist niega.
    granted = conn.execute(
        sa.text(
            "SELECT r.rolname FROM pg_auth_members m "
            "JOIN pg_roles r ON r.oid = m.roleid "
            "WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = :m)"
        ),
        {"m": _CAPTURE_ROLE},
    ).scalars().all()
    for g in granted:
        if not _IDENT_RE.match(g):
            raise ValueError(f"Membership con nombre inesperado: {g!r}")
        conn.execute(sa.text(f'REVOKE "{g}" FROM {_CAPTURE_ROLE}'))
        logger.warning("Membership %s revocada del rol %s", g, _CAPTURE_ROLE)

    conn.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {_ROLE}"))
    for table in _LEGACY_RO_TABLES:
        present = conn.execute(
            sa.text("SELECT to_regclass('public.' || :t) IS NOT NULL"), {"t": table}
        ).scalar()
        if not present:
            logger.warning(
                "Tabla legacy public.%s ausente: GRANT SELECT pospuesto "
                "(entorno sin legacy migrado)",
                table,
            )
            continue
        conn.execute(sa.text(f"GRANT SELECT ON public.{table} TO {_ROLE}"))
    logger.info(
        "GRANTs RO de sombra instalados: USAGE en public + SELECT sobre %s para %s",
        ", ".join(_LEGACY_RO_TABLES),
        _ROLE,
    )


def _verify_capture(conn: sa.Connection) -> None:
    """El rol de captura existe con EXACTAMENTE los atributos del contrato:
    LOGIN + REPLICATION, nada elevado (super/createdb/createrole/bypassrls)
    y CERO memberships (no hereda privilegios de nadie)."""
    row = conn.execute(
        sa.text(
            "SELECT rolcanlogin, rolreplication, rolsuper, rolcreatedb, "
            "rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = :r"
        ),
        {"r": _CAPTURE_ROLE},
    ).one_or_none()
    if row is None:
        raise RuntimeError(f"Rol {_CAPTURE_ROLE} no existe tras el bootstrap")
    if not (row.rolcanlogin and row.rolreplication):
        raise RuntimeError(f"Rol {_CAPTURE_ROLE} sin LOGIN o sin REPLICATION")
    if row.rolsuper or row.rolcreatedb or row.rolcreaterole or row.rolbypassrls:
        raise RuntimeError(f"Rol {_CAPTURE_ROLE} con atributos elevados: {tuple(row)}")
    memberships = conn.execute(
        sa.text(
            "SELECT count(*) FROM pg_auth_members "
            "WHERE member = (SELECT oid FROM pg_roles WHERE rolname = :r)"
        ),
        {"r": _CAPTURE_ROLE},
    ).scalar()
    if memberships:
        raise RuntimeError(f"Rol {_CAPTURE_ROLE} conserva {memberships} memberships")
    logger.info(
        "Rol de captura verificado: %s (LOGIN REPLICATION, plano, sin memberships)",
        _CAPTURE_ROLE,
    )


def _verify_isolation(conn: sa.Connection, schema: str) -> None:
    """Aislamiento EFECTIVO y EXHAUSTIVO contra Postgres (rev. #4, B-01).

    No solo el estado deseado: guarda contra la DERIVA — enumera TODAS las
    relaciones/secuencias de `public` y exige el owner correcto del esquema
    del core, cero objetos del core en `public` y cero privilegios SALVO la
    whitelist RO de la sombra (B-01, §1): SELECT — y solo SELECT — sobre
    EXACTAMENTE _LEGACY_RO_TABLES. Ya no es "cero privilegios": es "ese
    conjunto acotado y ni un privilegio más".
    """
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

    # TODA relación de public (tablas/vistas/matviews/particionadas/foráneas)
    # con CUALQUIER privilegio para el rol del core — incluidas las ACL DE
    # COLUMNA (rev. 3ª #2: un GRANT SELECT(col) no aparece en
    # has_table_privilege pero sí en has_any_column_privilege). Se separa
    # lectura de escritura para poder permitir la whitelist RO de B-01.
    privileged = conn.execute(
        sa.text(
            "SELECT c.relname, "
            "(has_table_privilege(:r, c.oid, 'SELECT') "
            " OR has_any_column_privilege(:r, c.oid, 'SELECT')) AS can_read, "
            "(has_table_privilege(:r, c.oid, "
            "'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            " OR has_any_column_privilege(:r, c.oid, "
            "'INSERT,UPDATE,REFERENCES')) AS can_write "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f') "
            "AND (has_table_privilege(:r, c.oid, "
            "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(:r, c.oid, "
            "'SELECT,INSERT,UPDATE,REFERENCES'))"
        ),
        {"r": _ROLE},
    ).all()
    allowed = set(_LEGACY_RO_TABLES)
    writable = [row.relname for row in privileged if row.can_write]
    if writable:
        raise RuntimeError(
            f"Rol {_ROLE} tiene privilegios de ESCRITURA (de tabla o de COLUMNA) "
            f"en public: {writable} — la sombra es SELECT-only (§1)"
        )
    off_whitelist = [
        row.relname for row in privileged if row.can_read and row.relname not in allowed
    ]
    if off_whitelist:
        raise RuntimeError(
            f"Rol {_ROLE} tiene privilegios (de tabla o de COLUMNA) en public "
            f"fuera de la whitelist RO de B-01: {off_whitelist}"
        )
    # Y la otra dirección de "EXACTAMENTE ese conjunto": toda tabla enumerada
    # que exista debe tener su SELECT (deriva = GRANT perdido → la captura y
    # las métricas fallarían en silencio más tarde).
    missing = conn.execute(
        sa.text(
            "SELECT t.name FROM unnest(CAST(:tables AS text[])) AS t(name) "
            "WHERE to_regclass('public.' || t.name) IS NOT NULL "
            "AND NOT has_table_privilege(:r, ('public.' || t.name)::regclass, 'SELECT')"
        ),
        {"r": _ROLE, "tables": list(_LEGACY_RO_TABLES)},
    ).scalars().all()
    if missing:
        raise RuntimeError(
            f"Rol {_ROLE} SIN el SELECT enumerado de B-01 sobre: {missing}"
        )

    # Funciones SECURITY DEFINER en public ejecutables por el core: escalada
    # potencial (corren con los privilegios del dueño) — no debe existir ninguna.
    secdef = conn.execute(
        sa.text(
            "SELECT p.proname FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.prosecdef "
            "AND has_function_privilege(:r, p.oid, 'EXECUTE')"
        ),
        {"r": _ROLE},
    ).scalars().all()
    if secdef:
        raise RuntimeError(
            f"Funciones SECURITY DEFINER de public ejecutables por {_ROLE}: {secdef}"
        )

    # CASE fuerza el orden de evaluación: el planner de Postgres puede evaluar
    # los predicados del WHERE en cualquier orden, y has_sequence_privilege
    # revienta sobre relaciones que no son secuencias.
    seq_reachable = conn.execute(
        sa.text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND CASE WHEN c.relkind = 'S' "
            "THEN has_sequence_privilege(:r, c.oid, 'USAGE,SELECT,UPDATE') "
            "ELSE false END"
        ),
        {"r": _ROLE},
    ).scalars().all()
    if seq_reachable:
        raise RuntimeError(f"Rol {_ROLE} tiene privilegios en secuencias de public: {seq_reachable}")

    # Nada en public debe pertenecer al rol del core.
    owned_in_public = conn.execute(
        sa.text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' "
            "AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname = :r)"
        ),
        {"r": _ROLE},
    ).scalars().all()
    if owned_in_public:
        raise RuntimeError(f"Objetos de public propiedad de {_ROLE}: {owned_in_public}")

    # El esquema del core debe pertenecer al rol del core (un `jobhunt`
    # pre-existente con otro owner impediría migrar o filtraría propiedad).
    schema_owner = conn.execute(
        sa.text(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = :s"
        ),
        {"s": schema},
    ).scalar()
    if schema_owner != _ROLE:
        raise RuntimeError(f"El esquema {schema} pertenece a {schema_owner!r}, no a {_ROLE}")

    audited = conn.execute(
        sa.text(
            "SELECT count(*) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f','S')"
        )
    ).scalar()
    logger.info(
        "Aislamiento verificado (exhaustivo): sin atributos elevados, sin "
        "memberships, SELECT-only sobre la whitelist RO de B-01 (%s) y ni un "
        "privilegio más sobre las %d relaciones/secuencias de public, sin "
        "objetos propios en public, esquema %s con owner %s",
        ", ".join(_LEGACY_RO_TABLES),
        audited,
        schema,
        _ROLE,
    )


def _verify_pgvector(conn: sa.Connection, schema: str) -> None:
    """pgvector resoluble COMO el rol del core (rev. 3ª #1).

    A-02 crea columnas vector(384) y usa el operador <=>; ambos viven en
    `public`. Se prueba con SET LOCAL ROLE + el search_path real del rol —
    si no resuelve, el job muere aquí (no en la primera migración de A-02).
    """
    try:
        conn.execute(sa.text(f"SET LOCAL search_path = \"{schema}\", public"))
        conn.execute(sa.text(f"SET LOCAL ROLE {_ROLE}"))
        dist = conn.execute(
            sa.text("SELECT '[1,2,3]'::vector <=> '[1,2,4]'::vector")
        ).scalar()
        conn.execute(sa.text("RESET ROLE"))
    except Exception as exc:
        raise RuntimeError(
            "pgvector NO resoluble con el rol del core (¿extensión ausente o "
            f"fuera del search_path?): {exc}"
        ) from exc
    logger.info("pgvector verificado como %s (cast + operador <=>, dist=%s)", _ROLE, dist)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    _bootstrap()
    cfg = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    command.upgrade(cfg, "head")
    logger.info("Migraciones del core al día (head)")


if __name__ == "__main__":
    main()

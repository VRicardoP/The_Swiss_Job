"""Orquestación de la migración de durables del portfolio (C-4, parte 3).

Encadena las partes 1 (síntesis de vacantes-sombra) y 2 (mapeo a las tablas de
tracking) en el ORDEN correcto POR USUARIO del portfolio, y ofrece la
RECONCILIACIÓN del ensayo de cutover: conteos por clasificación + CHECKSUMS por
tabla del core.

- ACOPLAMIENTO CERO con el origen: la entrada son DICTS (los durables ya
  extraídos del portfolio); este módulo no importa modelos del portfolio ni
  toca su BD. La reversibilidad descansa en (a) que el origen NUNCA se muta
  (entrada de solo lectura) y (b) el ensayo de down-migrations sobre copia
  desechable (A-12) — aquí no se recrea.
- `migrate_portfolio` NO commitea: el llamador decide COMMIT (cutover real) o
  ROLLBACK (dry-run: inspeccionar el informe y descartar). Los checksums se
  calculan en la MISMA sesión, así que un dry-run los ve antes de decidir.
- Los CHECKSUMS son deterministas y sobre columnas de NEGOCIO (ignoran ids
  sintéticos uuid4 y timestamps de reloj como saved_at/created_at): dos
  migraciones equivalentes del MISMO origen coinciden bit a bit — la señal de
  "sin pérdida / sin divergencia" del DoD. Tabla vacía ⇒ md5('') (constante):
  el arnés exige checksums != vacío ("checksums de cero = confianza falsa").
"""

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.import_portfolio import ensure_import_scope, synthesize_vacancies
from jobhunt_core.import_portfolio_durables import (
    migrate_applications,
    migrate_saved_searches,
    provision_profile,
)

logger = logging.getLogger(__name__)

# Claves de los conteos de clasificación (parte 2) — se agregan entre usuarios.
_APP_COUNT_KEYS = (
    "applications", "bookmarks", "unresolved", "consolidated", "invalid_status",
)
_SS_COUNT_KEYS = ("migrated", "existing", "invalid_filters", "no_name")

# Tablas de tracking del core que produce la migración (core0011).
CORE_TRACKING_TABLES = (
    "applications",
    "application_status_events",
    "profile_vacancy_state",
    "saved_searches",
)

# Checksum DETERMINISTA por tabla: md5 de las filas ordenadas por su propia
# representación de NEGOCIO (subconsulta → concat_ws de las columnas estables,
# excluidos id uuid4 y timestamps de reloj). ORDER BY sobre la representación
# completa ⇒ inmune a empates. coalesce(..., '') ⇒ tabla vacía = md5('').
_CHECKSUM_SQL = {
    "applications": (
        "SELECT md5(coalesce(string_agg(r, chr(10) ORDER BY r), '')) FROM ("
        "  SELECT concat_ws('|', profile_id::text, vacancy_id::text, status, "
        "    coalesce(notes,''), coalesce(follow_up_date::text,''), snapshot::text"
        "  ) AS r FROM applications"
        ") sub"
    ),
    "application_status_events": (
        "SELECT md5(coalesce(string_agg(r, chr(10) ORDER BY r), '')) FROM ("
        "  SELECT concat_ws('|', a.profile_id::text, a.vacancy_id::text, e.status"
        "  ) AS r FROM application_status_events e "
        "  JOIN applications a ON a.id = e.application_id"
        ") sub"
    ),
    "profile_vacancy_state": (
        "SELECT md5(coalesce(string_agg(r, chr(10) ORDER BY r), '')) FROM ("
        "  SELECT concat_ws('|', profile_id::text, vacancy_id::text, "
        "    (saved_at IS NOT NULL)::text, (dismissed_at IS NOT NULL)::text, "
        "    coalesce(notes,'')"
        "  ) AS r FROM profile_vacancy_state"
        ") sub"
    ),
    "saved_searches": (
        "SELECT md5(coalesce(string_agg(r, chr(10) ORDER BY r), '')) FROM ("
        "  SELECT concat_ws('|', profile_id::text, name, filters::text, "
        "    min_score::text, is_active::text, coalesce(last_run_at::text,'')"
        "  ) AS r FROM saved_searches"
        ") sub"
    ),
}


def _accumulate(total: dict, counts: dict) -> None:
    """Suma in-place los conteos de un usuario en el acumulado del lote."""
    for key, value in counts.items():
        total[key] += value


async def migrate_portfolio(
    session: AsyncSession, users: list[dict]
) -> dict:
    """Migra los durables de una lista de usuarios del portfolio.

    users = [{external_ref, applications: [dict], saved_searches: [dict]}].
    Por cada usuario, en ORDEN: provision_profile → síntesis de las vacantes-
    sombra de SUS candidaturas (parte 1) → migrate_applications →
    migrate_saved_searches (parte 2). El scope `portfolio-import` se da de alta
    UNA vez (idempotente). NO commitea (todo-o-nada del llamador).

    Devuelve conteos AGREGADOS (mismas claves que las partes) + `per_user` con
    el desglose. Re-ejecución IDEMPOTENTE a nivel de datos (los checksums no
    cambian): los conteos de `applications` son estables (clasifican el durable,
    no si ya existía la fila), pero los de `saved_searches` desplazan
    migrated→existing en el segundo pase — la verificación de "sin duplicar" es
    el checksum, no el conteo.
    """
    scope_id = await ensure_import_scope(session)
    app_totals = {k: 0 for k in _APP_COUNT_KEYS}
    ss_totals = {k: 0 for k in _SS_COUNT_KEYS}
    per_user: list[dict] = []
    for user in users:
        external_ref = str(user["external_ref"])
        profile_id = await provision_profile(session, external_ref)
        apps = list(user.get("applications") or [])
        # Síntesis SOLO de las candidaturas con url (parte 1): las sin url las
        # clasifica migrate_applications como 'unresolved'. El sink dedup global
        # ⇒ una URL compartida por varios usuarios reutiliza la misma vacante.
        await synthesize_vacancies(
            session,
            scope_id,
            [
                {k: row.get(k) for k in ("url", "title", "company", "description")}
                for row in apps
                if row.get("url")
            ],
        )
        app_counts = await migrate_applications(session, profile_id, apps)
        ss_counts = await migrate_saved_searches(
            session, profile_id, list(user.get("saved_searches") or [])
        )
        _accumulate(app_totals, app_counts)
        _accumulate(ss_totals, ss_counts)
        per_user.append(
            {
                "external_ref": external_ref,
                "applications": app_counts,
                "saved_searches": ss_counts,
            }
        )
    logger.info(
        "import_portfolio_migrate: %d usuarios → applications=%s saved_searches=%s",
        len(users),
        app_totals,
        ss_totals,
    )
    return {
        "users": len(users),
        "applications": app_totals,
        "saved_searches": ss_totals,
        "per_user": per_user,
    }


async def table_checksums(
    session: AsyncSession, tables: tuple[str, ...] = CORE_TRACKING_TABLES
) -> dict:
    """count + checksum de negocio por tabla de tracking (reconciliación C-4).

    {tabla: {"count": int, "checksum": hex md5}}. El checksum es determinista y
    ajeno a ids/timestamps sintéticos: sirve para (a) comparar dos ejecuciones
    del mismo origen (idempotencia real, sin divergencia) y (b) el gate de
    cutover ("sin pérdida"). Se computa en la sesión actual: un dry-run sin
    commit ya lo ve. EMPTY_CHECKSUM = md5('') delata una tabla vacía.
    """
    report: dict[str, dict] = {}
    for table in tables:
        count = (
            await session.execute(sa.text(f"SELECT count(*) FROM {table}"))
        ).scalar_one()
        checksum = (
            await session.execute(sa.text(_CHECKSUM_SQL[table]))
        ).scalar_one()
        report[table] = {"count": count, "checksum": checksum}
    return report


# md5 de la cadena vacía: checksum de una tabla SIN filas. El arnés de ensayo
# rechaza reconciliar contra checksums iguales a este (DoD: jamás validar sobre
# esquema fresco — "checksums de cero = confianza falsa").
EMPTY_CHECKSUM = "d41d8cd98f00b204e9800998ecf8427e"

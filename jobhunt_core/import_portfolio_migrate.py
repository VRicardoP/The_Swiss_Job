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
- Los durables IRRECUPERABLES (unresolved / invalid_status / no_name) se
  ENUMERAN en el informe (`staged`), no solo se cuentan: la reconciliación
  lista QUÉ se quedó fuera y por qué. El staging PERSISTENTE (tabla) es una
  parte futura de C-4; el origen es de solo lectura y nada se destruye.
- Los CHECKSUMS son deterministas y sobre CLAVES DE NEGOCIO PORTABLES — jamás
  sobre los PK uuid4 (profile_id/vacancy_id son aleatorios por-BD): se proyecta
  el `external_ref` del perfil y la `url_normalized` de la vacante-sombra. Así
  dos migraciones del MISMO origen coinciden bit a bit AUNQUE corran en BDs
  distintas (copia del NAS vs core) — la señal de "sin pérdida / sin divergencia"
  del DoD, comparable cross-entorno. También se ignoran los timestamps de reloj
  (saved_at/created_at, vía IS NOT NULL). Tabla vacía ⇒ md5('') (constante): el
  arnés exige checksums != vacío ("checksums de cero = confianza falsa").
"""

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.import_portfolio import (
    PORTFOLIO_IMPORT_SOURCE,
    ensure_import_scope,
    synthesize_vacancies,
)
from jobhunt_core.import_portfolio_durables import (
    migrate_applications,
    migrate_saved_searches,
    provision_profile,
)

logger = logging.getLogger(__name__)

# Claves de los conteos de clasificación (parte 2) — se agregan entre usuarios.
_APP_COUNT_KEYS = (
    "applications", "bookmarks", "unresolved", "consolidated", "invalid_status",
    "collision",
)
_SS_COUNT_KEYS = ("migrated", "existing", "invalid_filters", "no_name")

# Tablas de tracking del core que produce la migración (core0011).
CORE_TRACKING_TABLES = (
    "applications",
    "application_status_events",
    "profile_vacancy_state",
    "saved_searches",
)

# Cadena de identidad de la vacante-sombra portfolio-import (misma que
# resolve_vacancy_by_url): incarnación ACTIVA → source_listing.
_PORTFOLIO_VACANCY_JOIN = (
    "JOIN source_listing_incarnations i ON i.vacancy_id = v.id AND i.ended_at IS NULL "
    "JOIN source_listings sl ON sl.id = i.source_listing_id "
    f"JOIN sources src ON src.id = sl.source_id AND src.name = '{PORTFOLIO_IMPORT_SOURCE}' "
)


def _vac_key(col: str) -> str:
    """Clave de vacante PORTABLE (url_normalized de la vacante-sombra), como
    subconsulta correlacionada — NUNCA el PK uuid4 (aleatorio por-BD). Fallback
    marcado 'vid:<uuid>' si la vacante no fuese portfolio-import: anomalía VISIBLE
    sin descartar la fila ni enmascarar divergencia."""
    return (
        "coalesce((SELECT sl.url_normalized FROM source_listing_incarnations i "
        "JOIN source_listings sl ON sl.id = i.source_listing_id "
        f"JOIN sources src ON src.id = sl.source_id AND src.name = '{PORTFOLIO_IMPORT_SOURCE}' "
        f"WHERE i.vacancy_id = {col} AND i.ended_at IS NULL LIMIT 1), 'vid:' || {col}::text)"
    )


def _checksum(inner: str) -> str:
    """md5 de las filas de `inner` (alias r, un json_build_array(...)::text)
    ordenadas por su propia representación ⇒ determinista e inmune a empates.
    La codificación JSON entrecomilla y escapa CADA campo (los saltos de línea →
    \\n, las comillas → \\"), así que ni el separador de filas chr(10) ni ningún
    delimitador embebido en datos de usuario (notes/name con '|' o multilínea)
    pueden fundir dos filas distintas en una — inyectiva (P3 análisis 2).
    coalesce('') ⇒ objetivo vacío = md5('') (EMPTY_CHECKSUM)."""
    return (
        "SELECT md5(coalesce(string_agg(r, chr(10) ORDER BY r), '')) "
        f"FROM ({inner}) sub"
    )


# Checksum + count por objetivo de reconciliación. Cada fila se serializa con
# json_build_array (codificación INYECTIVA, a prueba de delimitadores) sobre
# CLAVES DE NEGOCIO PORTABLES (external_ref, url_normalized) — jamás PK uuid4 —
# para que dos migraciones del mismo origen coincidan AUNQUE corran en BDs
# distintas (copia NAS vs core). Los timestamps de reloj (saved_at) se reducen a
# boolean; last_run_at/follow_up_date vienen del origen (deterministas).
# `portfolio_vacancies` cubre la canónica SINTETIZADA (title/company de un
# bookmark puro solo vive ahí, no en las 4 tablas de tracking).
_CHECKSUM_SPECS = {
    "applications": {
        "count": "SELECT count(*) FROM applications",
        "checksum": _checksum(
            "SELECT json_build_array(p.external_ref, " + _vac_key("a.vacancy_id")
            + ", a.status, a.notes, a.follow_up_date, a.snapshot)::text AS r "
            "FROM applications a JOIN profiles p ON p.id = a.profile_id"
        ),
    },
    "application_status_events": {
        "count": "SELECT count(*) FROM application_status_events",
        "checksum": _checksum(
            "SELECT json_build_array(p.external_ref, " + _vac_key("a.vacancy_id")
            + ", e.status)::text AS r "
            "FROM application_status_events e "
            "JOIN applications a ON a.id = e.application_id "
            "JOIN profiles p ON p.id = a.profile_id"
        ),
    },
    "profile_vacancy_state": {
        "count": "SELECT count(*) FROM profile_vacancy_state",
        "checksum": _checksum(
            "SELECT json_build_array(p.external_ref, " + _vac_key("pvs.vacancy_id")
            + ", (pvs.saved_at IS NOT NULL), (pvs.dismissed_at IS NOT NULL), "
            "pvs.notes)::text AS r "
            "FROM profile_vacancy_state pvs JOIN profiles p ON p.id = pvs.profile_id"
        ),
    },
    "saved_searches": {
        "count": "SELECT count(*) FROM saved_searches",
        "checksum": _checksum(
            "SELECT json_build_array(p.external_ref, ss.name, ss.filters, "
            "ss.min_score, ss.is_active, ss.last_run_at)::text AS r "
            "FROM saved_searches ss JOIN profiles p ON p.id = ss.profile_id"
        ),
    },
    "portfolio_vacancies": {
        "count": (
            "SELECT count(*) FROM vacancies v "
            "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
            + _PORTFOLIO_VACANCY_JOIN
            + "WHERE v.merged_into IS NULL AND v.archived_at IS NULL"
        ),
        "checksum": _checksum(
            "SELECT json_build_array(sl.url_normalized, o.content->>'title', "
            "o.content->>'company', o.content->>'description')::text AS r "
            "FROM vacancies v "
            "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
            + _PORTFOLIO_VACANCY_JOIN
            + "WHERE v.merged_into IS NULL AND v.archived_at IS NULL"
        ),
    },
}

# Objetivos de reconciliación por defecto (4 tablas de tracking + la canónica
# sintetizada). CORE_TRACKING_TABLES sigue siendo el subconjunto de tablas
# físicas de tracking (lo usa el arnés para la aserción de rollback).
CHECKSUM_TARGETS = tuple(_CHECKSUM_SPECS)


def _accumulate(total: dict, counts: dict) -> None:
    """Suma in-place los conteos de un usuario en el acumulado del lote.

    `total.get(key, 0)`: si la parte 2 añadiese una clasificación nueva no
    presente en _APP_COUNT_KEYS/_SS_COUNT_KEYS, se AGREGA en vez de reventar con
    KeyError (desacople parte 2↔parte 3, P3 análisis 2)."""
    for key, value in counts.items():
        total[key] = total.get(key, 0) + value


async def migrate_portfolio(
    session: AsyncSession, users: list[dict]
) -> dict:
    """Migra los durables de una lista de usuarios del portfolio.

    users = [{external_ref, applications: [dict], saved_searches: [dict]}].
    ORDEN: alta del scope `portfolio-import` (idempotente) → síntesis GLOBAL de
    TODAS las vacantes-sombra en una pasada (parte 1; detecta colisiones de URL
    entre usuarios) → por cada usuario: provision_profile → migrate_applications
    → migrate_saved_searches (parte 2). NO commitea (todo-o-nada del llamador).

    Devuelve conteos AGREGADOS (mismas claves que las partes) + `per_user` con
    el desglose + `staged` (ENUMERACIÓN, por PASE, de los durables que NO se
    mapearon limpiamente: {external_ref, kind, reason, durable} — irrecuperables
    unresolved/invalid_status/collision/no_name y degradados consolidated_real)
    — la reconciliación lista QUÉ se quedó fuera, no solo cuántos. Re-ejecución
    IDEMPOTENTE a nivel de datos (los
    checksums no cambian): los conteos de `applications` son estables (clasifican
    el durable, no si ya existía la fila), pero los de `saved_searches` desplazan
    migrated→existing en el segundo pase — la verificación de "sin duplicar" es
    el checksum, no el conteo.
    """
    scope_id = await ensure_import_scope(session)
    app_totals = {k: 0 for k in _APP_COUNT_KEYS}
    ss_totals = {k: 0 for k in _SS_COUNT_KEYS}
    per_user: list[dict] = []
    staged: list[dict] = []
    # Síntesis GLOBAL (parte 1) de TODAS las vacantes-sombra ANTES de mapear: una
    # sola pasada detecta las colisiones de URL normalizada TAMBIÉN entre usuarios
    # (un `seen` por-usuario las dejaría escapar → mis-resolución silenciosa). El
    # sink dedup global ⇒ una URL compartida reutiliza la misma vacante. `collided`
    # (2ª+ URL distinta con misma clave) se enruta a staging en migrate_applications
    # en vez de resolverse a la vacante equivocada (P1 análisis 2).
    collided = await synthesize_vacancies(
        session,
        scope_id,
        [
            {k: row.get(k) for k in ("url", "title", "company", "description")}
            for user in users
            for row in (user.get("applications") or [])
            if row.get("url")
        ],
    )
    for user in users:
        external_ref = str(user["external_ref"])
        profile_id = await provision_profile(session, external_ref)
        apps = list(user.get("applications") or [])
        # Sink de staging POR USUARIO → se etiqueta con external_ref al agregar
        # (parte 2 solo conoce el profile_id).
        user_staged: list[dict] = []
        app_counts = await migrate_applications(
            session, profile_id, apps, staging=user_staged, collided=collided
        )
        ss_counts = await migrate_saved_searches(
            session,
            profile_id,
            list(user.get("saved_searches") or []),
            staging=user_staged,
        )
        _accumulate(app_totals, app_counts)
        _accumulate(ss_totals, ss_counts)
        staged.extend({"external_ref": external_ref, **rec} for rec in user_staged)
        per_user.append(
            {
                "external_ref": external_ref,
                "applications": app_counts,
                "saved_searches": ss_counts,
            }
        )
    logger.info(
        "import_portfolio_migrate: %d usuarios → applications=%s saved_searches=%s "
        "(%d durables irrecuperables enumerados)",
        len(users),
        app_totals,
        ss_totals,
        len(staged),
    )
    return {
        "users": len(users),
        "applications": app_totals,
        "saved_searches": ss_totals,
        "staged": staged,
        "per_user": per_user,
    }


async def table_checksums(
    session: AsyncSession, targets: tuple[str, ...] = CHECKSUM_TARGETS
) -> dict:
    """count + checksum de negocio por objetivo de reconciliación (C-4).

    {objetivo: {"count": int, "checksum": hex md5}}. El checksum proyecta CLAVES
    DE NEGOCIO PORTABLES (external_ref, url_normalized), ajeno a PK uuid4 y
    timestamps de reloj: sirve para (a) comparar dos ejecuciones del mismo origen
    AUNQUE en BDs distintas (idempotencia/paridad, sin divergencia) y (b) el gate
    de cutover ("sin pérdida"). Se computa en la sesión actual: un dry-run sin
    commit ya lo ve. EMPTY_CHECKSUM = md5('') delata un objetivo vacío.
    """
    report: dict[str, dict] = {}
    for target in targets:
        spec = _CHECKSUM_SPECS[target]
        count = (await session.execute(sa.text(spec["count"]))).scalar_one()
        checksum = (await session.execute(sa.text(spec["checksum"]))).scalar_one()
        report[target] = {"count": count, "checksum": checksum}
    return report


# md5 de la cadena vacía: checksum de una tabla SIN filas. El arnés de ensayo
# rechaza reconciliar contra checksums iguales a este (DoD: jamás validar sobre
# esquema fresco — "checksums de cero = confianza falsa").
EMPTY_CHECKSUM = "d41d8cd98f00b204e9800998ecf8427e"

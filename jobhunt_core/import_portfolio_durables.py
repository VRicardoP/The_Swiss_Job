"""Mapeo de durables del portfolio a las tablas de tracking del core (C-4, parte 2).

Se APOYA en la parte 1 (import_portfolio): primero se sintetizan las
vacantes-sombra de las URLs de los durables (synthesize_vacancies); aquí cada
durable se resuelve a su vacancy_id (resolve_vacancy_by_url) y se escribe en
las tablas de tracking (applications, application_status_events,
profile_vacancy_state, saved_searches). Los durables llegan como DICTS: el
core NO importa modelos del portfolio (acoplamiento cero con el origen).

Reglas C-4:
- status == 'saved'  → BOOKMARK: matching.set_saved (upsert de saved_at) +
  UPDATE de profile_vacancy_state.notes (no hay helper de notes). Si ADEMÁS
  trae follow_up_date, TAMBIÉN una application con status 'saved' — el
  bookmark solo no lleva follow_up_date y el dato no debe perderse.
- status != 'saved'  → application (el enum del origen es subconjunto del del
  core → mapeo 1:1 por nombre) + un evento inicial SINTETIZADO con ese mismo
  status: el historial arranca en el estado actual. El evento inicial se
  emite para TODA application nueva (también las de status 'saved' de la
  regla anterior) — un historial sin arranque sería un hueco.
- CONSOLIDACIÓN: varios durables del mismo perfil que resuelven a la MISMA
  vacante no pueden producir dos applications (UNIQUE(profile_id,
  vacancy_id)) → se AGRUPA por vacancy_id ANTES de insertar y gana la
  candidatura REAL MÁS RECIENTE (por updated_at/created_at, no por el orden
  del lote), conservando follow_up_date/notes de las demás (coalesce). Si se
  descarta otra candidatura real (status distinto), se LOGUEA (auditable).
- Durables sin url o con url irresoluble (nunca sintetizada, cuarentena del
  sink, vacante fundida/archivada) → 'unresolved' con log: se OMITEN (staging
  en una parte futura de C-4), jamás se inserta un vínculo a ciegas.
- IDEMPOTENTE: applications con INSERT ... ON CONFLICT (profile_id,
  vacancy_id) DO NOTHING (el evento inicial SOLO si la fila es nueva —
  RETURNING); saved_searches con dedup por (profile_id, name, FILTERS) vía
  existence-check con LIMIT 1 (el esquema no trae UNIQUE y no se añaden
  constraints aquí; el name solo perdería búsquedas distintas homónimas, y
  scalar_one_or_none envenenaría el re-run ante un duplicado preexistente).
  Los conteos devueltos CLASIFICAN el lote (no cuentan inserciones):
  re-ejecutar produce los MISMOS conteos.
"""

import json
import logging
import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core import matching
from jobhunt_core.import_portfolio import resolve_vacancy_by_url
from jobhunt_core.profiles import ensure_consumer, upsert_profile

logger = logging.getLogger(__name__)

# Consumer (tenant) del piloto; external_ref = str(user_id) del portfolio.
PORTFOLIO_CONSUMER = "portfolio"
SAVED_STATUS = "saved"
# Enum application_status del core (core0011). Los 6 estados del portfolio
# (saved/applied/phone_screen/technical/offer/rejected) son subconjunto.
APPLICATION_STATUSES = frozenset(
    {
        "saved", "applied", "phone_screen", "technical",
        "interview", "offer", "rejected", "withdrawn",
    }
)
# Tope del esquema (String(200)) — también es la clave de dedup.
SAVED_SEARCH_NAME_MAX = 200


async def provision_profile(session: AsyncSession, external_ref: str) -> uuid.UUID:
    """Perfil del piloto: consumer 'portfolio' + external_ref (idempotente,
    reutiliza los helpers ON CONFLICT de profiles.py)."""
    consumer_id = await ensure_consumer(session, PORTFOLIO_CONSUMER)
    return await upsert_profile(session, consumer_id, external_ref)


async def migrate_applications(
    session: AsyncSession, profile_id: uuid.UUID, rows: list[dict]
) -> dict:
    """Escribe los durables de job_application en el tracking del core.

    Devuelve conteos de CLASIFICACIÓN del lote (estables bajo re-ejecución):
    applications (una por vacante con candidatura), bookmarks (saved),
    unresolved (sin url o url irresoluble — omitidos con log), consolidated
    (durables extra fundidos en una application existente del lote),
    invalid_status (status fuera del enum — cuarentena por-item).
    """
    counts = {
        "applications": 0, "bookmarks": 0, "unresolved": 0,
        "consolidated": 0, "invalid_status": 0,
    }
    # AGRUPAR por vacancy_id ANTES de insertar: dos durables sobre la misma
    # vacante deben consolidarse en UNA application (UNIQUE del esquema).
    groups: dict[uuid.UUID, list[dict]] = {}
    for row in rows:
        if row.get("status") not in APPLICATION_STATUSES:
            # Cuarentena por-item (misma disciplina que la parte 1): un status
            # fuera del enum reventaría el INSERT y abortaría el lote entero.
            counts["invalid_status"] += 1
            logger.warning(
                "import_portfolio_durables: status %r fuera del enum — durable "
                "OMITIDO (title=%r)",
                row.get("status"),
                row.get("title"),
            )
            continue
        url = row.get("url")
        vacancy_id = await resolve_vacancy_by_url(session, url) if url else None
        if vacancy_id is None:
            counts["unresolved"] += 1
            logger.warning(
                "import_portfolio_durables: durable sin vacante resoluble "
                "OMITIDO (title=%r, url=%r) — pendiente de staging en una "
                "parte futura de C-4",
                row.get("title"),
                url,
            )
            continue
        groups.setdefault(vacancy_id, []).append(row)

    for vacancy_id, group in groups.items():
        # --- BOOKMARKS: los saved marcan saved_at (upsert idempotente, una vez por
        # vacante) y coexisten con la application (estado estable ADR-03). Las notes
        # se COALESCEN (primera no vacía entre los saved de la vacante) en UN solo
        # UPDATE — no last-write-wins por-durable (P2 análisis 2).
        saved_rows = [r for r in group if r.get("status") == SAVED_STATUS]
        if saved_rows:
            await matching.set_saved(session, profile_id, vacancy_id, True)
            counts["bookmarks"] += len(saved_rows)
            bookmark_note = next((r.get("notes") for r in saved_rows if r.get("notes")), None)
            if bookmark_note is not None:
                # No hay helper de notes: UPDATE directo tras el upsert de set_saved
                # (la fila de profile_vacancy_state ya existe seguro).
                await session.execute(
                    sa.text(
                        "UPDATE profile_vacancy_state SET notes = :n "
                        "WHERE profile_id = :pid AND vacancy_id = :vid"
                    ),
                    {"n": bookmark_note, "pid": profile_id, "vid": vacancy_id},
                )

        # --- APPLICATION consolidada de la vacante: candidaturas reales +
        # bookmarks con follow_up_date (regla C-4: ese dato no se pierde).
        real = [r for r in group if r.get("status") != SAVED_STATUS]
        saved_fu = [
            r for r in group
            if r.get("status") == SAVED_STATUS and _as_date(r.get("follow_up_date"))
        ]
        candidates = real + saved_fu
        if not candidates:
            continue
        # Gana la candidatura REAL MÁS RECIENTE — por updated_at/created_at, NO por
        # la posición en el lote (P3 análisis 2: no confiar en el orden del export;
        # el estado actual es el del durable más nuevo). Las OTRAS candidaturas
        # reales de la misma vacante (UNIQUE ⇒ una sola application) se descartan:
        # su status/evento se pierden, así que se LOGUEA para auditoría (paridad
        # con invalid_status/unresolved; migración reconciliable).
        if real:
            winner = max(real, key=_recency_key)
            for r in real:
                if r is not winner:
                    logger.warning(
                        "import_portfolio_durables: candidatura real DESCARTADA por "
                        "consolidación (vacante compartida) — status=%r title=%r; "
                        "gana la más reciente (status=%r)",
                        r.get("status"),
                        r.get("title"),
                        winner.get("status"),
                    )
        else:
            winner = saved_fu[0]
        counts["consolidated"] += len(candidates) - 1
        counts["applications"] += 1
        follow_up = next(
            (
                d
                for d in (_as_date(r.get("follow_up_date")) for r in [winner] + candidates)
                if d
            ),
            None,
        )
        notes = next(
            (r.get("notes") for r in [winner] + candidates if r.get("notes")), None
        )
        # Lo presentable del durable: conserva el texto original aunque la
        # vacante-sombra sea mínima. source_listing_incarnation_id queda NULL
        # (el vínculo por vacante basta para el piloto).
        snapshot = {
            "title": winner.get("title"),
            "company": winner.get("company"),
            "url": winner.get("url"),
            "description": winner.get("description"),
        }
        application_id = (
            await session.execute(
                sa.text(
                    "INSERT INTO applications "
                    "(id, profile_id, vacancy_id, snapshot, status, notes, "
                    " follow_up_date) "
                    "VALUES (:id, :pid, :vid, CAST(:snap AS jsonb), :st, :n, :fud) "
                    "ON CONFLICT (profile_id, vacancy_id) DO NOTHING RETURNING id"
                ),
                {
                    "id": uuid.uuid4(), "pid": profile_id, "vid": vacancy_id,
                    "snap": json.dumps(snapshot, ensure_ascii=False),
                    "st": winner["status"], "n": notes, "fud": follow_up,
                },
            )
        ).scalar_one_or_none()
        if application_id is not None:
            # Evento inicial SINTETIZADO con el status actual — SOLO si la
            # application es NUEVA (re-ejecutar no re-emite historial).
            await session.execute(
                sa.text(
                    "INSERT INTO application_status_events "
                    "(id, application_id, status) VALUES (:id, :aid, :st)"
                ),
                {"id": uuid.uuid4(), "aid": application_id, "st": winner["status"]},
            )
    logger.info(
        "import_portfolio_durables: %d durables → %d applications, %d bookmarks "
        "(%d sin resolver, %d consolidados, %d status inválidos)",
        len(rows),
        counts["applications"],
        counts["bookmarks"],
        counts["unresolved"],
        counts["consolidated"],
        counts["invalid_status"],
    )
    return counts


async def migrate_saved_searches(
    session: AsyncSession, profile_id: uuid.UUID, rows: list[dict]
) -> dict:
    """Escribe los durables de saved_search en saved_searches del core.

    Mapeo: filters (JSON como STRING) → JSONB (objeto; si no parsea a dict →
    {} con log — el original sigue en el portfolio), last_notified_at →
    last_run_at; notify_frequency/notify_push/total_matches los cubren los
    server defaults de core0011 ('daily'/true/0). IDEMPOTENTE por
    existence-check sobre (profile_id, name) — el esquema no trae UNIQUE y no
    se añaden constraints aquí.
    """
    counts = {"migrated": 0, "existing": 0, "invalid_filters": 0, "no_name": 0}
    for row in rows:
        name = row.get("name")
        if not name or not isinstance(name, str):
            # Sin nombre no hay clave de dedup — cuarentena por-item.
            counts["no_name"] += 1
            logger.warning(
                "import_portfolio_durables: saved_search sin name — OMITIDA"
            )
            continue
        if len(name) > SAVED_SEARCH_NAME_MAX:
            # Recorte defensivo al tope del esquema: consistente en cada
            # re-ejecución (la clave de dedup es el nombre YA recortado).
            logger.warning(
                "import_portfolio_durables: name de saved_search recortado a "
                "%d caracteres (%r…)",
                SAVED_SEARCH_NAME_MAX,
                name[:40],
            )
            name = name[:SAVED_SEARCH_NAME_MAX]
        raw_filters = row.get("filters")
        try:
            filters = json.loads(raw_filters) if raw_filters else {}
        except (TypeError, ValueError):
            filters = None
        if not isinstance(filters, dict):
            # Inválido = no parsea o no es objeto JSON (un array/escalar no es
            # un conjunto de filtros): fallback {} con log de auditoría.
            counts["invalid_filters"] += 1
            logger.warning(
                "import_portfolio_durables: filters INVÁLIDO en búsqueda %r — "
                "se importa con {} (el original queda en el portfolio)",
                name,
            )
            filters = {}
        filters_json = json.dumps(filters, ensure_ascii=False)
        # Dedup por (profile_id, name, FILTERS): el name SOLO perdería en silencio
        # dos búsquedas legítimas distintas con el mismo nombre (el origen no impone
        # UNIQUE(user_id, name)) — P2 análisis 2. `.first()`/LIMIT 1 (no
        # scalar_one_or_none): un duplicado preexistente ya no envenena el re-run con
        # MultipleResultsFound — P3 análisis 2. Aún es check-then-act (no atómico);
        # aceptable para la migración one-shot en freeze (un solo escritor).
        exists = (
            await session.execute(
                sa.text(
                    "SELECT 1 FROM saved_searches "
                    "WHERE profile_id = :pid AND name = :n "
                    "AND filters = CAST(:f AS jsonb) LIMIT 1"
                ),
                {"pid": profile_id, "n": name, "f": filters_json},
            )
        ).first()
        if exists:
            counts["existing"] += 1
            continue
        await session.execute(
            sa.text(
                "INSERT INTO saved_searches "
                "(id, profile_id, name, filters, min_score, is_active, last_run_at) "
                "VALUES (:id, :pid, :n, CAST(:f AS jsonb), :ms, :act, :lra)"
            ),
            {
                "id": uuid.uuid4(), "pid": profile_id, "n": name,
                "f": filters_json,
                "ms": int(row.get("min_score") or 0),
                "act": bool(row.get("is_active", True)),
                "lra": _as_datetime(row.get("last_notified_at")),
            },
        )
        counts["migrated"] += 1
    logger.info(
        "import_portfolio_durables: %d saved_searches → %d migradas, %d ya "
        "existentes (%d filters inválidos, %d sin name)",
        len(rows),
        counts["migrated"],
        counts["existing"],
        counts["invalid_filters"],
        counts["no_name"],
    )
    return counts


def _as_date(value) -> date | None:
    """Coerción defensiva de frontera: date, datetime o ISO-string → date."""
    if isinstance(value, datetime):  # antes que date: datetime ES date
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_datetime(value) -> datetime | None:
    """Coerción defensiva de frontera: datetime o ISO-string → datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _recency_key(row: dict) -> datetime:
    """Clave de recencia ORDEN-INDEPENDIENTE para elegir la candidatura ganadora:
    updated_at, si no created_at, si no epoch (las filas sin fecha van al fondo,
    deterministas). No confiar en la posición del durable en el lote."""
    dt = _as_datetime(row.get("updated_at")) or _as_datetime(row.get("created_at"))
    return dt or datetime.min.replace(tzinfo=timezone.utc)

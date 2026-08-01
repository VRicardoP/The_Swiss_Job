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
    session: AsyncSession,
    profile_id: uuid.UUID,
    rows: list[dict],
    *,
    staging: list | None = None,
    collided: set | None = None,
) -> dict:
    """Escribe los durables de job_application en el tracking del core.

    Devuelve conteos de CLASIFICACIÓN del lote (estables bajo re-ejecución):
    applications (una por vacante con candidatura), bookmarks (saved),
    unresolved (sin url o url irresoluble — omitidos con log), consolidated
    (durables extra fundidos en una application existente del lote),
    invalid_status (status fuera del enum — cuarentena por-item), collision
    (url que comparte clave normalizada con otra distinta — ver `collided`).

    `collided` (opcional): conjunto de URLs COLISIONADAS que devolvió
    synthesize_vacancies. Un durable con una de esas URLs NO se resuelve
    (resolve_vacancy_by_url lo mapearía a la vacante de OTRA URL → vínculo
    equivocado): se enruta a staging con razón 'collision' (P1 análisis 2).

    `staging` (opcional): si se pasa una lista, cada durable IRRECUPERABLE
    (unresolved / invalid_status / collision) o DEGRADADO (consolidated_real: la
    candidatura real perdedora de una consolidación) se ENUMERA en ella
    {kind, reason, durable} — para que la reconciliación no reporte verde con
    pérdida silenciosa. El staging PERSISTENTE (tabla) llega en una parte futura
    de C-4; aquí solo se enumera en memoria (el origen es de solo lectura).
    """
    counts = {
        "applications": 0, "bookmarks": 0, "unresolved": 0,
        "consolidated": 0, "invalid_status": 0, "collision": 0,
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
            _record_skipped(staging, "application", "invalid_status", row)
            continue
        url = row.get("url")
        if url and collided and url in collided:
            # URL colisionada: resolve la mapearía a la vacante de OTRA URL
            # distinta (vínculo equivocado). Se enruta a staging, NUNCA se
            # resuelve a ciegas (P1 análisis 2) — reconciliación manual.
            counts["collision"] += 1
            logger.warning(
                "import_portfolio_durables: durable con URL COLISIONADA OMITIDO "
                "(title=%r, url=%r) — vincularía a la vacante equivocada; a staging",
                row.get("title"),
                url,
            )
            _record_skipped(staging, "application", "collision", row)
            continue
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
            _record_skipped(staging, "application", "unresolved", row)
            continue
        groups.setdefault(vacancy_id, []).append(row)

    for vacancy_id, group in groups.items():
        saved_rows = [r for r in group if r.get("status") == SAVED_STATUS]
        real = [r for r in group if r.get("status") != SAVED_STATUS]
        saved_fu = [r for r in saved_rows if _as_date(r.get("follow_up_date"))]
        candidates = real + saved_fu

        # GANADOR DETERMINISTA de la application: la candidatura MÁS RECIENTE
        # (recencia + desempate por contenido, NUNCA el orden del lote — P2 rev.
        # externa). Los reales priman sobre los saved+follow_up.
        winner = max(real or saved_fu, key=_recency_key) if candidates else None

        # Coalesce DETERMINISTA: los candidatos se ordenan por _recency_key (más
        # reciente primero, con desempate por contenido), NO por el orden del lote —
        # así el valor elegido y el checksum del destino no dependen del snapshot
        # (P2 rev. externa 2). El ganador va primero (su valor prima).
        ordered = (
            [winner, *sorted(candidates, key=_recency_key, reverse=True)]
            if winner else []
        )
        follow_up = next(
            (d for d in (_as_date(r.get("follow_up_date")) for r in ordered) if d),
            None,
        )
        notes = next((r.get("notes") for r in ordered if r.get("notes")), None)
        # Nota del BOOKMARK (una sola columna profile_vacancy_state.notes): 1ª no
        # vacía entre los saved ORDENADOS por recencia (determinista).
        ordered_saved = sorted(saved_rows, key=_recency_key, reverse=True)
        bookmark_note = next(
            (r.get("notes") for r in ordered_saved if r.get("notes")), None
        )

        # --- STAGING de lo que NO cabe: sin pérdida silenciosa (P1/P3 rev. externa).
        staged_ids: set[int] = set()
        if winner is not None:
            for r in real:
                if r is not winner:
                    # Candidatura real perdedora: su status/historial se pierde
                    # (notes/follow_up se coalescen) — DEGRADACIÓN, no fold benigno.
                    logger.warning(
                        "import_portfolio_durables: candidatura real DESCARTADA por "
                        "consolidación — status=%r title=%r; gana status=%r",
                        r.get("status"), r.get("title"), winner.get("status"),
                    )
                    staged_ids.add(id(r))
                    _record_skipped(staging, "application", "consolidated_real", r)
        for r in saved_rows:
            note = r.get("notes")
            fu = _as_date(r.get("follow_up_date"))
            # Nota o follow_up de un bookmark que NO es el elegido (una sola columna
            # de cada): valor material distinto perdido → se enumera (P1 rev. externa).
            lost = (note and note != bookmark_note) or (fu is not None and fu != follow_up)
            if lost and id(r) not in staged_ids:
                staged_ids.add(id(r))
                _record_skipped(staging, "application", "consolidated_saved", r)

        # --- BOOKMARKS: marcar saved_at (idempotente) + la nota coalescida.
        if saved_rows:
            await matching.set_saved(session, profile_id, vacancy_id, True)
            counts["bookmarks"] += len(saved_rows)
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

        if winner is None:
            continue
        counts["consolidated"] += len(candidates) - 1
        counts["applications"] += 1
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
        "(%d sin resolver, %d consolidados, %d status inválidos, %d colisiones)",
        len(rows),
        counts["applications"],
        counts["bookmarks"],
        counts["unresolved"],
        counts["consolidated"],
        counts["invalid_status"],
        counts["collision"],
    )
    return counts


async def migrate_saved_searches(
    session: AsyncSession,
    profile_id: uuid.UUID,
    rows: list[dict],
    *,
    staging: list | None = None,
) -> dict:
    """Escribe los durables de saved_search en saved_searches del core.

    Mapeo: filters (JSON como STRING) → JSONB; filters inválido → {} + is_active
    FALSE (no alertar de todo) + enumerado en staging; last_notified_at →
    last_run_at; notify_frequency/notify_push/total_matches los cubren los
    server defaults de core0011 ('daily'/true/0). IDEMPOTENTE por existence-check
    sobre la TUPLA MATERIAL (name, filters, min_score, is_active, last_run_at) —
    el esquema no trae UNIQUE y no se añaden constraints aquí.

    `staging` (opcional): las búsquedas sin name (no_name — sin clave) y las de
    filters inválido (importadas desactivadas) se ENUMERAN en la lista
    {kind, reason, durable} para arreglo/reconciliación manual.
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
            _record_skipped(staging, "saved_search", "no_name", row)
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
        min_score = int(row.get("min_score") or 0)
        is_active = bool(row.get("is_active", True))
        last_run = _as_datetime(row.get("last_notified_at"))
        if not isinstance(filters, dict):
            # Filtro INVÁLIDO (no parsea o no es objeto): un {} ACTIVO alertaría de
            # TODAS las ofertas → NO se activa. Se importa DESACTIVADA con {} (para
            # que el usuario la vea) y el durable ORIGINAL se ENUMERA en staging para
            # arreglo manual — degradación conservadora, no pérdida silenciosa (P1
            # rev. externa).
            counts["invalid_filters"] += 1
            is_active = False
            logger.warning(
                "import_portfolio_durables: filters INVÁLIDO en búsqueda %r — se "
                "importa DESACTIVADA con {} y se enumera el original en staging",
                name,
            )
            _record_skipped(staging, "saved_search", "invalid_filters", row)
            filters = {}
        filters_json = json.dumps(filters, ensure_ascii=False)
        # Dedup por la TUPLA MATERIAL COMPLETA (name, filters, min_score, is_active,
        # last_run_at): dos búsquedas con igual name+filters pero distinto min_score/
        # is_active/last_run son DISTINTAS (el origen no impone UNIQUE) → ambas migran,
        # no se colapsa una config material en silencio (P1 rev. externa). La igualdad
        # EXACTA de las 5 columnas ⇒ re-run idempotente; IS NOT DISTINCT FROM iguala
        # NULL con NULL. `.first()`/LIMIT 1 evita MultipleResultsFound.
        exists = (
            await session.execute(
                sa.text(
                    "SELECT 1 FROM saved_searches "
                    "WHERE profile_id = :pid AND name = :n "
                    "AND filters = CAST(:f AS jsonb) AND min_score = :ms "
                    "AND is_active = :act "
                    "AND last_run_at IS NOT DISTINCT FROM :lra LIMIT 1"
                ),
                {"pid": profile_id, "n": name, "f": filters_json,
                 "ms": min_score, "act": is_active, "lra": last_run},
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
                "f": filters_json, "ms": min_score, "act": is_active,
                "lra": last_run,
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


def _record_skipped(
    staging: list | None, kind: str, reason: str, row: dict
) -> None:
    """Enumera un durable IRRECUPERABLE en el sink de staging (si se pasó).

    Guarda el durable ÍNTEGRO (identidad completa: url/title/status/name/…) para
    que la reconciliación pueda listar QUÉ se quedó fuera y por qué — no solo un
    conteo agregado. El staging PERSISTENTE (tabla) es una parte futura de C-4;
    este sink en memoria es su precursor (la lista que aquella parte volcará)."""
    if staging is not None:
        staging.append({"kind": kind, "reason": reason, "durable": row})


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


def _recency_key(row: dict) -> tuple:
    """Clave de recencia ORDEN-INDEPENDIENTE para elegir la candidatura ganadora:
    updated_at, si no created_at, si no epoch (las filas sin fecha van al fondo).
    DESEMPATE por contenido material (status/url/title/notes) para que un empate
    de fechas NO dependa del orden del lote (P2 rev. externa): dos durables con
    misma fecha Y mismo contenido son equivalentes (da igual cuál gane); si el
    contenido difiere, gana el mayor lexicográfico, determinista."""
    dt = _as_datetime(row.get("updated_at")) or _as_datetime(row.get("created_at"))
    dt = dt or datetime.min.replace(tzinfo=timezone.utc)
    # Desempate por TODOS los campos que viajan al destino (snapshot company/
    # description + columna follow_up_date), no solo status/url/title/notes — si no,
    # dos durables que empatan en la clave parcial pero difieren en company/
    # description/follow_up dejan el ganador (y el checksum) a merced del orden del
    # lote (P2 rev. externa 2).
    tiebreak = (
        str(row.get("status") or ""), str(row.get("url") or ""),
        str(row.get("title") or ""), str(row.get("notes") or ""),
        str(row.get("company") or ""), str(row.get("description") or ""),
        str(_as_date(row.get("follow_up_date")) or ""),
    )
    return (dt, tiebreak)

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
import math
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core import matching
from jobhunt_core.import_portfolio import durable_synthesizable, resolve_vacancy_by_url
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


class PreexistingStateError(RuntimeError):
    """El cutover intentó BOOKMARKear una vacante cuyo `profile_vacancy_state` YA existía (creado
    por otro consumer/proceso, NO por este run). C-4 solo INSERTA durables frescos: mutar (el
    upsert de set_saved + el UPDATE de notes) una fila preexistente deja una mutación que el
    snapshot antes/después (solo INSERTs) NO registra y el rollback-script NO puede deshacer
    (P1 rev. externa integral). Se ABORTA fail-closed (cota mono-piloto): jamás mutar una fila
    que C-4 no creó."""

    def __init__(self, profile_id, vacancy_id):
        super().__init__(
            f"profile_vacancy_state preexistente (profile={profile_id}, vacancy={vacancy_id}) — "
            f"C-4 no puede mutarla sin poder deshacerla; cutover abortado (fail-closed)"
        )
        self.profile_id = profile_id
        self.vacancy_id = vacancy_id


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
    preexisting_pvs: set[str] | None = None,
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
        "consolidated": 0, "invalid_status": 0, "collision": 0, "no_title": 0,
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
        if url:
            ok, reason = durable_synthesizable(row)
            if not ok:
                # NO sintetizable (sin título O frontera del sink: url>límite, payload no
                # codificable —surrogate— o NUL). Aunque su url resuelva (un hermano válido la
                # sintetizó), ESTE durable tendría snapshot impresentable/TÓXICO — un surrogate
                # en el snapshot reventaría el INSERT CAST(:snap AS jsonb) y abortaría el lote
                # (P1 rev. externa 3). MISMA clasificación que reconcile._route y la síntesis.
                counts["no_title"] += 1
                logger.warning(
                    "import_portfolio_durables: durable NO sintetizable (%s) OMITIDO "
                    "(url=%r) — a staging",
                    reason,
                    url,
                )
                _record_skipped(staging, "application", reason or "no_title", row)
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
            # PREFLIGHT fail-closed (solo por la vía del cutover, `preexisting_pvs` provisto): si el
            # profile_vacancy_state ya EXISTÍA ANTES del cutover (creado por el core/otro proceso),
            # el upsert de set_saved + el UPDATE de notes lo MUTARÍAN — mutación que la procedencia
            # (solo INSERTs) no captura y el rollback no deshace (P1 rev. externa integral). Se
            # aborta (cota mono-piloto): jamás mutar una fila ajena. Las llamadas DIRECTAS a
            # migrate_portfolio (idempotencia a nivel de datos, sin manifiesto) no pasan el set →
            # no se activa el preflight.
            if (
                preexisting_pvs is not None
                and f"{profile_id}:{vacancy_id}" in preexisting_pvs
            ):
                raise PreexistingStateError(profile_id, vacancy_id)
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
                    # G3-P3-2: `default=str` — la frontera que decide si el
                    # durable es sintetizable serializa con canonical_payload
                    # (que SÍ lo lleva), así que acepta un Decimal/date/UUID en
                    # company/description; sin él, el INSERT reventaba con
                    # TypeError y mataba la transacción ENTERA del cutover.
                    "snap": json.dumps(
                        _json_safe(snapshot), ensure_ascii=False,
                        allow_nan=False, default=str,
                    ),
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
    counts = {"migrated": 0, "existing": 0, "invalid_filters": 0,
              "invalid_min_score": 0, "no_name": 0}
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
        # G1 H-3 (contrato del extractor): la columna origen es JSONB real — el
        # driver entrega un dict, no un str. json.loads(dict) → TypeError y TODAS
        # las búsquedas migraban vacías y desactivadas. Se aceptan AMBAS formas.
        if isinstance(raw_filters, dict):
            filters = raw_filters
        else:
            try:
                filters = json.loads(raw_filters) if raw_filters else {}
            except (TypeError, ValueError):
                filters = None
        min_score, bad_score = _as_min_score(row.get("min_score"))
        is_active = bool(row.get("is_active", True))
        if bad_score:
            # G3-P3-5: mismo patrón que invalid_filters — un 0 ACTIVO alertaría
            # de TODAS las ofertas, así que se importa DESACTIVADA y el durable
            # ORIGINAL se ENUMERA en staging para arreglo manual.
            # G5-P3-5: y CUENTA, como su hermano. Sin contador, el resumen del
            # cutover reportaba la búsqueda DESACTIVADA como «migrada» y nada
            # más — cero rastro del umbral en la línea que lee el operador.
            counts["invalid_min_score"] += 1
            is_active = False
            logger.warning(
                "import_portfolio_durables: min_score INVÁLIDO (%r) en búsqueda "
                "%r — se importa DESACTIVADA con 0 y se enumera en staging",
                row.get("min_score"), name,
            )
            _record_skipped(staging, "saved_search", "invalid_min_score", row)
        # G1 H-3: la columna real del origen se llama `last_run_at`; se leen ambas
        # claves (el alias histórico primero) — antes quedaba NULL para todas.
        last_run = _as_datetime(
            row.get("last_notified_at") or row.get("last_run_at")
        )
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
        # G3-P3-2: `default=str`, como canonical_payload y persist_manifest —
        # un escalar no-JSON (Decimal de una columna numeric, date) abortaba la
        # transacción del cutover en el INSERT.
        filters_json = json.dumps(
            _json_safe(filters), ensure_ascii=False, allow_nan=False, default=str
        )
        # Dedup por la TUPLA MATERIAL COMPLETA (name, filters, min_score, is_active,
        # last_run_at): dos búsquedas con igual name+filters pero distinto min_score/
        # is_active/last_run son DISTINTAS (el origen no impone UNIQUE) → ambas migran,
        # no se colapsa una config material en silencio (P1 rev. externa). La igualdad
        # EXACTA de las 5 columnas ⇒ re-run idempotente; IS NOT DISTINCT FROM iguala
        # NULL con NULL. `.first()`/LIMIT 1 evita MultipleResultsFound.
        if bad_score:
            # G5-P3-6: el existence-check es por TUPLA MATERIAL, así que la
            # fila escrita por un import o ENSAYO con la regla ANTERIOR
            # (umbral fuera de cota, ACTIVA) ya no casa: se insertaría una
            # SEGUNDA búsqueda homónima y —peor— la garantía del propio fix
            # («fuera de cota ⇒ DESACTIVADA») no se cumpliría sobre el estado
            # pre-fix, que sobreviviría intacto. La idempotencia por tupla
            # material es indiferente a la INTENCIÓN de un cambio de regla, así
            # que se converge esa fila —y solo esa: MISMO durable, difiere
            # únicamente en lo que la regla cambió— antes de comprobar la
            # existencia. Sin esto haría falta un `rollback` previo en cada
            # cutover posterior a un cambio de cota.
            degradadas = (
                await session.execute(
                    sa.text(
                        "UPDATE saved_searches SET min_score = :ms, "
                        "is_active = :act "
                        "WHERE profile_id = :pid AND name = :n "
                        "AND filters = CAST(:f AS jsonb) "
                        "AND last_run_at IS NOT DISTINCT FROM :lra "
                        "AND (min_score < 0 OR min_score > :cota)"
                    ),
                    {"pid": profile_id, "n": name, "f": filters_json,
                     "ms": min_score, "act": is_active, "lra": last_run,
                     "cota": MIN_SCORE_MAX},
                )
            ).rowcount
            if degradadas:
                logger.warning(
                    "import_portfolio_durables: %d fila(s) previas de la "
                    "búsqueda %r tenían el umbral FUERA DE COTA sin degradar "
                    "(import anterior al cambio de regla) — convergidas a "
                    "0/DESACTIVADA en vez de duplicar la búsqueda",
                    degradadas, name,
                )
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
        "existentes (%d filters inválidos, %d min_score fuera de rango, "
        "%d sin name)",
        len(rows),
        counts["migrated"],
        counts["existing"],
        counts["invalid_filters"],
        counts["invalid_min_score"],
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


# Zona del PRODUCTO (G1-P3-7): follow_up_date es una FECHA que el usuario ve en
# hora suiza; el origen la guarda como timestamptz y asyncpg la entrega en UTC.
_PRODUCT_TZ = ZoneInfo("Europe/Zurich")


def _json_safe(value):
    """Saneo RECURSIVO de un valor para que sobreviva al CAST a jsonb.

    - NUL ('\\x00') → U+FFFD en cada str, CLAVES de dict incluidas (G1-P2-1):
      Postgres rechaza \\u0000 en jsonb aunque el str de Python sea codificable.
    - float NO FINITO (NaN/±Infinity) → su `str()` (G2-P2-1): `json.dumps` los
      emite como los tokens `NaN`/`Infinity`, que NO son JSON válido y el CAST
      a jsonb RECHAZA — un durable staged con un NaN del export (Postgres
      numeric admite NaN; json.loads materializa el token) abortaba la
      transacción ENTERA del cutover justo al registrar su propia cuarentena.
      Se conserva como texto ('nan'/'inf') para que la auditoría lo VEA.
    """
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = _json_safe(k)
            if key in out:
                # G2-H-1: dos claves DISTINTAS que colapsan tras el saneo
                # ('a\x00' y 'a\ufffd' — las urls tóxicas viajan como clave en
                # ledger/staging) perdían una entrada del manifiesto en
                # silencio. Se desambigua en vez de pisar.
                # G3-P3-3: el sufijo puede COLISIONAR a su vez (si la clave
                # sufijada ya existe se seguía pisando, 3 entradas → 2), y esto
                # no toca solo la auditoría: _json_safe se aplica también a
                # `filters` y al `snapshot`, que son DATOS DE PRODUCTO. Se
                # itera hasta encontrar hueco.
                base, n = key, len(out)
                while key in out:
                    key = f"{base}#{n}"
                    n += 1
            out[key] = _json_safe(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


# Cota del umbral de una búsqueda guardada: la del contrato de la API para el
# mismo campo (api/schemas.py, `ge=0, le=100`) — el destino es int4, así que
# fuera de rango el INSERT del cutover reventaría la transacción (G4-P3-1).
MIN_SCORE_MAX = 100


def _as_int(value, default: int | None = 0) -> int | None:
    """Coerción defensiva de frontera: entero del durable → int, o `default`.

    G2-P2-1: `int(row.get('min_score') or 0)` reventaba con un NaN del export
    (`nan` es truthy y `int(float('nan'))` lanza ValueError) y mataba la
    transacción del cutover ANTES de veredicto — igual que G1-P2-2. Un valor
    no finito o no numérico se degrada al default, y el MISMO helper lo usa el
    reconciliador (_classify_expected) para que ambos lados coincidan.

    G3-P3-5: la degradación era INCOHERENTE — `40.9` (float) daba 40 pero
    `'40.0'` o `Decimal('40.0')` (la forma en que un extractor genérico
    entrega una columna numeric) caían al default. El decimal TEXTUAL se
    acepta con la misma regla de truncado que el float. Con `default=None` el
    llamador DISTINGUE la degradación en vez de tragársela."""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        pass
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return int(number) if math.isfinite(number) else default


def _as_min_score(value) -> tuple[int, bool]:
    """(min_score, DEGRADADO) — coerción del umbral de una búsqueda guardada.

    G3-P3-5: un min_score que no coerciona caía a 0 —el valor MENOS
    restrictivo: la búsqueda pasa a alertar de TODAS las ofertas— y encima se
    migraba ACTIVA y sin rastro en staging, al contrario que su hermano
    `invalid_filters` del mismo bucle. El caller ENUMERA la degradación y no
    la activa. Ausente (None) NO es degradación: la columna es nullable y 0 es
    su valor por contrato. DEFINICIÓN ÚNICA: la usan la migración y el lado
    ESPERADO del reconciliador, o divergirían.

    G4-P3-1: el helper blindaba lo NO FINITO pero no la COTA del destino, la
    única que la BD impone (`saved_searches.min_score` es int4) — un valor
    numéricamente válido y fuera de rango se coercionaba SIN marcar
    degradación, se importaba ACTIVO, no iba a staging y el INSERT reventaba
    con `DataError: value out of int32 range`: excepción no capturada en la
    frontera que ABORTA el cutover entero, la lección de G1-P2-2/G2-P2-1/
    G3-P3-2. Y el segundo intento de coerción que añadió G3 (`float(str(v))`
    en `_as_int`, cuyo ÚNICO consumidor es esta función) AMPLIABA el vector:
    `Decimal('1e10')` antes degradaba a 0 y pasó a producir el int que
    revienta. La cota es la del CONTRATO de la API para el mismo campo
    (`Field(None, ge=0, le=100)`), más estrecha que la de int4: fuera de ella
    el valor entra por el camino ya escrito (`invalid_min_score` + búsqueda
    DESACTIVADA), espejado en `_classify_expected` por compartir helper."""
    if value is None:
        return 0, False
    coerced = _as_int(value, default=None)
    if coerced is None or not 0 <= coerced <= MIN_SCORE_MAX:
        return 0, True
    return coerced, False


def _as_date(value) -> date | None:
    """Coerción defensiva de frontera: date, datetime o ISO-string → date.

    G1-P3-7: un datetime AWARE se convierte a la zona del producto
    (Europe/Zurich) antes de .date() — el wall-clock UTC desplazaba un día los
    seguimientos de madrugada (2026-01-02 00:30 CH = 2026-01-01T23:30Z migraba
    como 2026-01-01), y reconcile no lo veía (mismo _as_date en ambos lados).
    Un datetime NAIVE se toma como wall-clock local ya resuelto."""
    if isinstance(value, datetime):  # antes que date: datetime ES date
        if value.tzinfo is not None:
            return value.astimezone(_PRODUCT_TZ).date()
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            # G2-H-2: un ISO-string de DATETIME ('2026-01-02T00:30:00+01:00')
            # no lo acepta date.fromisoformat y el follow_up se perdía en
            # SILENCIO (y el reconciliador, con el mismo helper, no lo veía).
            # G3-P3-1: se parsea y se resuelve por la MISMA rama que el objeto
            # — reencaminarlo por _as_datetime lo anclaba a UTC (regla de
            # _recency_key, pensada para comparar instantes, no para derivar
            # una fecha) y el MISMO wall-clock naive daba un día MÁS a partir
            # de las 23:00 según llegara como objeto o como cadena, contra el
            # docstring de aquí arriba.
            try:
                return _as_date(datetime.fromisoformat(value))
            except ValueError:
                return None
    return None


def _as_datetime(value) -> datetime | None:
    """Coerción defensiva de frontera: datetime o ISO-string → datetime.

    G1-P2-2: los NAIVE se ANCLAN a UTC (coherente con _ts_key, que ya asume
    UTC para los naive) — sin el ancla, max()/sorted() con _recency_key sobre
    un grupo que mezcla fila con fecha naive y fila sin fechas (fallback
    AWARE datetime.min utc) lanzaba TypeError y mataba la transacción del
    cutover, incluso antes de veredicto en _classify_expected."""
    dt = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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

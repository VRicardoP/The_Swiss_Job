"""Runtime synthesis of vacancies referenced by user applications.

The internal ``portfolio-import`` source has no provider and never harvests.
It only gives stable corpus identity to an application whose URL is not yet in
Core, through the same validated ``RawListingSink`` used by native sources.
"""

import hashlib
import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.sink import (
    MAX_URL_LEN,
    RawListingSink,
    _payload_has_nul,
    _preprocess,
    canonical_payload,
    normalize_url,
)
from jobhunt_core.harvest.types import RawListing

logger = logging.getLogger(__name__)

PORTFOLIO_IMPORT_SOURCE = "portfolio-import"
# Tier 0: mismo tier que las fuentes internas sin cosecha (legacy_shadow).
PORTFOLIO_IMPORT_TIER = 0
# Scope DETERMINISTA: re-ejecutar ensure_source_scope jamás crea scopes nuevos
PORTFOLIO_IMPORT_SCOPE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "portfolio-import-scope")
# DT-148 B4: las cuatro razones VALEN su propia cadena, así que el mapeo de
# abajo era la identidad. Y `PORTFOLIO_IMPORT_SCOPE_ID` estaba definida DOS
# veces, con el mismo valor: la segunda ocultaba que la primera ya existía.
_Q_NO_URL = "no_url"
_Q_NO_TITLE = "no_title"
_Q_LIMIT = "limit"
_Q_MALFORMED = "malformed"


def register_handlers() -> None:
    """Alta IDEMPOTENTE de identidad/normalización de `portfolio-import`
    (mismo patrón que arbeitnow/legacy_shadow): sin esto el sink no produce
    canónica (offer_revisions) para la fuente. El payload usa las claves del
    core (`company_name`, no `company`).

    G4-P2-4: se invoca al importar el módulo y también desde
    `harvest.registry`, porque NINGÚN módulo del `celery_app.conf.include` la
    importa — el worker llegaba a reparar el primary de una vacante
    `portfolio-import` (la que el usuario se apuntó) sin normalizador y le
    anulaba la canónica estando VIVA."""
    register_extractor(
        PORTFOLIO_IMPORT_SOURCE,
        lambda payload: (payload.get("title"), payload.get("company_name")),
    )
    register_normalizer(
        PORTFOLIO_IMPORT_SOURCE,
        lambda raw: {
            "title": raw.get("title"),
            "company": raw.get("company_name"),
            "description": raw.get("description"),
            "tags": raw.get("tags"),
            "location": None,
            "remote": None,
            "salary": None,
        },
    )


register_handlers()


async def ensure_source_scope(session: AsyncSession) -> uuid.UUID:
    """Alta IDEMPOTENTE de la fuente `portfolio-import` y su scope.

    Devuelve siempre el mismo scope_id (determinista): el sink resuelve la
    fuente por el scope, y la migración de durables puede re-ejecutarse sin
    dejar fuentes ni scopes duplicados.
    """
    await session.execute(
        sa.text(
            "INSERT INTO sources (id, name, tier) VALUES (:i, :n, :t) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"i": uuid.uuid4(), "n": PORTFOLIO_IMPORT_SOURCE, "t": PORTFOLIO_IMPORT_TIER},
    )
    source_id = (
        await session.execute(
            sa.text("SELECT id FROM sources WHERE name = :n"),
            {"n": PORTFOLIO_IMPORT_SOURCE},
        )
    ).scalar_one()
    # enabled=false: sin provider registrado, el runner no debe cosecharlo.
    await session.execute(
        sa.text(
            "INSERT INTO harvest_scopes (id, source_id, params, tier, enabled) "
            "VALUES (:i, :s, '{}'::jsonb, :t, false) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": PORTFOLIO_IMPORT_SCOPE_ID, "s": source_id, "t": PORTFOLIO_IMPORT_TIER},
    )
    # G1 H-12: el ON CONFLICT DO NOTHING no garantiza la PROPIEDAD del scope —
    # si el id determinista ya existía apuntando a OTRA fuente (estado
    # inconsistente previo), el sink escribiría todo el corpus sintetizado bajo
    # esa fuente ajena sin error. Fail-closed: verificar y abortar.
    owner = (
        await session.execute(
            sa.text("SELECT source_id FROM harvest_scopes WHERE id = :i"),
            {"i": PORTFOLIO_IMPORT_SCOPE_ID},
        )
    ).scalar_one()
    if owner != source_id:
        raise RuntimeError(
            f"harvest_scopes {PORTFOLIO_IMPORT_SCOPE_ID} apunta a la fuente "
            f"{owner}, no a {PORTFOLIO_IMPORT_SOURCE} ({source_id}) — estado "
            "inconsistente: la síntesis no debe escribir bajo una fuente ajena"
        )
    return PORTFOLIO_IMPORT_SCOPE_ID


def _raw_listing(
    url: str, title: str, company: str | None, description: str | None
) -> RawListing:
    """Build the validated sink listing for one application URL."""
    external_id = hashlib.sha256(normalize_url(url).encode()).hexdigest()
    return RawListing(
        external_id=external_id,
        url=url,
        payload={
            "title": title,
            "company_name": company,
            "description": description,
            "tags": [],
        },
    )


def _valid_title(value) -> bool:
    """Return whether the sink can create a presentable canonical title."""
    return isinstance(value, str) and bool(value.strip())


def _synthesizable(
    item: dict, listing: RawListing, url_normalized: str
) -> tuple[bool, str | None]:
    """(True, None) si este durable produciría una vacante-sombra PRESENTABLE; (False, razón)
    si no. Razones: no_title (título no normalizable → el sink no crea canónica → impresentable),
    limit (url > MAX_URL_LEN) o malformed (payload/url no codificable —surrogate— o con NUL). La
    frontera del sink se REUTILIZA (`_preprocess`), no se replica: la partición sintetizable/no
    debe coincidir EXACTA con la del sink (P2 rev. externa 2). Es POR DURABLE — un grupo (url)
    sintetiza si ≥1 de sus durables es sintetizable; la razón solo aplica si NINGUNO lo es."""
    if not _valid_title(item.get("title")):
        return False, _Q_NO_TITLE
    # G2-P3-2: la TOXICIDAD se comprueba ANTES que el límite, como en el sink
    # (_preprocess encodea estricto y serializa el payload antes de medir) y
    # como manda la precedencia declarada del módulo (malformed > limit): una
    # url >2048 bytes con un surrogate en el fragmento —que normalize_url
    # descarta, así que no revienta antes— se registraba como 'limit' mientras
    # el sink la cuarentena por tóxica. La partición ya coincidía; mentía la
    # razón AUDITABLE.
    try:
        canonical_payload(listing.payload).encode()  # el sink hashea el canon
        listing.external_id.encode()
        listing.url.encode()
    except (TypeError, ValueError, UnicodeEncodeError):
        return False, _Q_MALFORMED
    # G3-P3-2 (residual de G2-P3-2): el NUL es 'malformed' por el docstring de
    # aquí arriba y por la precedencia declarada del módulo (malformed > limit),
    # pero solo lo detectaba _preprocess, que corre DESPUÉS de medir el límite:
    # una url con NUL Y más de MAX_URL_LEN bytes se registraba como 'limit'. El
    # sink SÍ reporta el NUL (_limit_violations), así que la razón AUDITABLE
    # mentía en la esquina compuesta.
    if "\x00" in listing.url or _payload_has_nul(listing.payload):
        return False, _Q_MALFORMED
    # G1-P3-5: BYTES, no caracteres — el sink mide bytes (_limit_violations,
    # C6-P2-2); medir chars aquí mandaba una url multibyte ≤2048 chars pero
    # >2048 bytes a _preprocess y el código registraba 'malformed' en vez de
    # 'limit'. surrogatepass: un surrogate suelto no revienta la MEDIDA (esa
    # url cae después en _preprocess → malformed, como en el sink).
    if (
        len(listing.url.encode("utf-8", "surrogatepass")) > MAX_URL_LEN
        or len(url_normalized.encode("utf-8", "surrogatepass")) > MAX_URL_LEN
    ):
        return False, _Q_LIMIT
    if (
        _preprocess(listing) is None
    ):  # no codificable (surrogate) / NUL — el sink la cuarentena
        return False, _Q_MALFORMED
    return True, None


# Keep grouped failures deterministic regardless of input order.
# del lote (P2 rev. externa §4-LOCAL ronda 8).
_QUARANTINE_REASON_PRECEDENCE = (_Q_MALFORMED, _Q_LIMIT, _Q_NO_TITLE)


def _group_reason(reasons: set[str]) -> str:
    """Razón de cuarentena de un grupo por precedencia determinista (malformed > limit > no_title),
    independiente del orden del lote. Fallback no_title si el set viniera vacío (no debería)."""
    for r in _QUARANTINE_REASON_PRECEDENCE:
        if r in reasons:
            return r
    return _Q_NO_TITLE


def listing_synthesizable(row: dict) -> tuple[bool, str | None]:
    """Validate an application listing against the real sink boundary."""
    url = row.get("url")
    if not url:
        return False, _Q_NO_URL
    try:
        url_normalized = normalize_url(url)
        raw_title = row.get("title")
        listing = _raw_listing(
            url,
            raw_title if isinstance(raw_title, str) else "",
            row.get("company"),
            row.get("description"),
        )
    except ValueError:
        return False, _Q_MALFORMED
    return _synthesizable(row, listing, url_normalized)


async def synthesize_vacancies(
    session: AsyncSession,
    scope_id: uuid.UUID,
    items: list[dict],
) -> set[str]:
    """Sintetiza vacantes-sombra para los items del portfolio CON url.

    items = [{url, title, company, description}]. Los items sin url se OMITEN
    con log porque sin URL no hay identidad resoluble. El lote entero pasa por
    (slots, incarnaciones, revisiones, canónica) y su idempotencia son suyas.

    Devuelve el CONJUNTO de URLs COLISIONADAS. Una COLISIÓN = dos URLs DISTINTAS
    que normalizan a la MISMA clave (p.ej. el id de la oferta vive en el fragmento
    que normalize_url descarta, portales SPA). Ante colisión NO queda vacante-sombra
    y se devuelven TODAS las URLs del grupo (ambigüedad no resoluble): el llamador
    las rechaza en vez de que resolve las mapee a la vacante equivocada.
    Detección en TRES vías:
      · INTRA-LOTE (>1 url distinta bajo el mismo external_id) — del propio lote.
      · CROSS-RUN — pre-query SCOPEADA a portfolio-import (esa fuente no tiene escritor
        concurrente: race-free) antes de sintetizar.
      · CROSS-SOURCE — REVALIDACIÓN post-attach dentro de un SAVEPOINT: se sintetiza,
        se lee el estado final (la vacante resultó tener la url de otra fuente) y, si
        colisiona, se REVIERTE la cadena creada y se re-sintetiza sin ella — no queda
        artefacto de corpus falso (ver _synthesize_pruning_collisions).
    La ventana de carrera residual (attach concurrente TRAS la revalidación) es del
    bloqueo transaccional del sink. Pasa todos los
    items en UNA llamada.
    """
    # --- Pasada 1: validar/normalizar y AGRUPAR por external_id (= clave sha256(url_norm)).
    # La cuarentena de TÍTULO/frontera-del-sink es POR GRUPO, no por item: un grupo sintetiza
    # si ≥1 de sus durables es SINTETIZABLE (`_synthesizable`), y la razón (no_title/limit/
    # malformed) solo aplica si NINGUNO lo es — así un hermano válido con la misma url NO deja
    # el grupo sin sintetizar (P1 rev. externa 2). el rechazo del dato inválido no
    # afecta al representante válido. Solo sin-url y url-malformada son por-item
    # (deterministas por url). `grp["synth"]` = una listing sintetizable que representa al grupo.
    groups: dict[str, dict] = {}
    skipped = {
        "no_url": 0,
        "malformed": 0,
        "no_title": 0,
        "limit": 0,
        "collision": 0,
        "dup": 0,
    }
    for item in items:
        url = item.get("url")
        if not url:
            skipped["no_url"] += 1
            logger.warning(
                "portfolio_vacancies: item sin url OMITIDO (title=%r)",
                item.get("title"),
            )
            continue
        raw_title = item.get("title")
        try:
            url_normalized = normalize_url(url)
            listing = _raw_listing(
                url,
                raw_title if isinstance(raw_title, str) else "",
                item.get("company"),
                item.get("description"),
            )
        except ValueError as exc:
            # URL malformada (o no codificable): CUARENTENA por-item — determinista por url, no
            # aborta el lote válido (el external_id se calcula con normalize_url ANTES del sink).
            skipped["malformed"] += 1
            logger.warning(
                "portfolio_vacancies: URL malformada OMITIDA (%s: %s)",
                exc.__class__.__name__,
                exc,
            )
            continue
        grp = groups.setdefault(
            listing.external_id,
            {
                "urln": url_normalized,
                "by_url": {},
                "count": 0,
                "synth": None,
                "reasons": set(),
            },
        )
        grp["count"] += 1
        grp["by_url"].setdefault(url, listing)  # una RawListing por url distinta
        ok, reason = _synthesizable(item, listing, url_normalized)
        if ok:
            if grp["synth"] is None:
                grp["synth"] = listing  # una listing sintetizable representa al grupo
        else:
            # Se ACUMULAN TODAS las razones del grupo (no la del primero): la definitiva se elige
            # por precedencia determinista si el grupo NO sintetiza (P2 rev. externa 8).
            grp["reasons"].add(reason)

    # --- Estado PORTFOLIO-IMPORT ya persistido (colisión CROSS-RUN, race-free: esa
    # fuente no tiene escritor concurrente — su scope nace deshabilitado). Detectar
    # ANTES de sintetizar evita que el sink SOBRESCRIBA la incarnación de una ejecución
    # previa (el post-attach no lo vería: quedaría una sola url activa).
    prior = await _portfolio_incarnation_urls(
        session, [g["urln"] for g in groups.values()]
    )

    # --- Pasada 2: colisión INTRA-LOTE (>1 url distinta bajo el mismo external_id) o
    # CROSS-RUN (portfolio-import ya tiene otra url con esta clave) → no sintetizar.
    listings = []
    collided: set[str] = set()
    for grp in groups.values():
        batch_urls = set(grp["by_url"])
        prior_urls = prior.get(grp["urln"], set())
        if len(batch_urls) > 1 or len(batch_urls | prior_urls) > 1:
            skipped["collision"] += grp["count"]
            collided.update(batch_urls)
            logger.warning(
                "portfolio_vacancies: COLISIÓN intra-lote/cross-run (%s) — lote=%r "
                "portfolio=%r; omitidas",
                grp["urln"],
                sorted(batch_urls),
                sorted(prior_urls),
            )
            continue
        url = next(iter(batch_urls))
        if grp["synth"] is None:
            # NINGÚN durable de esta url es sintetizable (todos sin título / frontera del sink):
            # no se crea vacante (ni impresentable ni created-null); con la razón real,
            # elegida por precedencia determinista (independiente del orden del lote).
            reason = _group_reason(grp["reasons"])
            skipped[reason] += grp["count"]
            logger.warning(
                "portfolio_vacancies: url no sintetizable (%s) OMITIDA (%s)",
                url,
                reason,
            )
            continue
        listings.append(
            grp["synth"]
        )  # la listing SINTETIZABLE (título + frontera del sink ok)
        skipped["dup"] += grp["count"] - 1  # exactos-dup del mismo url

    cross_source = await _synthesize_pruning_collisions(
        session, scope_id, listings, skipped
    )
    collided |= cross_source
    logger.info(
        # len - cross_source (G1 H-14a): las cadenas REVERTIDAS por colisión
        # cross-source no cuentan como sintetizadas — antes el log las sumaba.
        "portfolio_vacancies: %d items → %d sintetizadas (omitidos: %d sin url, %d malformadas, "
        "%d sin título, %d sobre-límite, %d colisiones, %d duplicados).",
        len(items),
        len(listings) - len(cross_source),
        skipped["no_url"],
        skipped["malformed"],
        skipped["no_title"],
        skipped["limit"],
        skipped["collision"],
        skipped["dup"],
    )
    return collided


def _normalized_or_none(url: str) -> str | None:
    """normalize_url tolerante para la REVALIDACIÓN post-attach (G1-P3-6): una url
    persistida no normalizable (ValueError, p.ej. IPv6 con corchete) devuelve None —
    que difiere de cualquier clave y se trata como COLISIÓN (conservador: revertir
    antes que dejar un vínculo dudoso)."""
    try:
        return normalize_url(url)
    except ValueError:
        return None


async def _synthesize_pruning_collisions(
    session: AsyncSession, scope_id: uuid.UUID, listings: list, skipped: dict
) -> set[str]:
    """Sintetiza `listings` y, si la REVALIDACIÓN post-attach detecta que una vacante
    resultante tiene otra url ORIGINAL (colisión CROSS-SOURCE — el sink adjuntó por
    url_normalized a una vacante de otra fuente), REVIERTE la cadena creada vía SAVEPOINT
    y re-sintetiza SOLO las no colisionadas → NO deja un vínculo FALSO en el corpus (la
    incarnación, revisión y evidencia se descartan
    Devuelve las URLs colisionadas al llamador.

    La ventana de carrera residual (un attach concurrente TRAS la revalidación) es
    cubierta por el bloqueo transaccional del sink
    antes de exponer la operación."""
    collided: set[str] = set()
    to_try = list(listings)
    while to_try:
        nested = await session.begin_nested()
        await RawListingSink().handle(session, str(scope_id), tuple(to_try))
        urln_of = {lst.url: normalize_url(lst.url) for lst in to_try}
        incarnation_urls = await _vacancy_incarnation_urls(
            session, list(urln_of.values())
        )
        # G1-P3-6: colisión = la vacante tiene una incarnación activa cuya url
        # NORMALIZADA difiere de la clave (ids en fragmento colapsados u otra
        # oferta real fusionada). Comparar urls CRUDAS revertía attaches
        # LEGÍTIMOS cross-source (misma clave, grafía distinta: host en
        # mayúsculas, barra final) que el sink adjuntó bien por url_normalized.
        new_collided = {
            lst.url
            for lst in to_try
            if any(
                _normalized_or_none(u) != urln_of[lst.url]
                for u in incarnation_urls.get(urln_of[lst.url], set()) - {lst.url}
            )
        }
        if not new_collided:
            await nested.commit()
            break
        await nested.rollback()  # descarta TODA la cadena de este intento
        collided |= new_collided
        skipped["collision"] += len(new_collided)
        for url in sorted(new_collided):
            logger.warning(
                "portfolio_vacancies: COLISIÓN cross-source (%s) — cadena REVERTIDA, "
                "omitida",
                url,
            )
        to_try = [lst for lst in to_try if lst.url not in new_collided]
    return collided


async def _portfolio_incarnation_urls(
    session: AsyncSession, url_normalizeds: list[str]
) -> dict[str, set[str]]:
    """{url_normalized: {urls}} de incarnaciones activas y presentables ya persistidas
    en PORTFOLIO-IMPORT (colisión CROSS-RUN, race-free: sin escritor concurrente en esa
    fuente)."""
    keys = list({u for u in url_normalizeds if u})
    if not keys:
        return {}
    rows = await session.execute(
        sa.text(
            "SELECT sl.url_normalized, i.url FROM source_listings sl "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "JOIN source_listing_incarnations i "
            "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
            "JOIN vacancies v ON v.id = i.vacancy_id "
            "  AND v.merged_into IS NULL AND v.archived_at IS NULL "
            "WHERE sl.url_normalized IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"src": PORTFOLIO_IMPORT_SOURCE, "keys": keys},
    )
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r.url_normalized, set()).add(r.url)
    return out


async def _vacancy_incarnation_urls(
    session: AsyncSession, url_normalizeds: list[str]
) -> dict[str, set[str]]:
    """{url_normalized: {urls activas de TODA la vacante}} para las claves dadas — UNA
    consulta batched (no N) para la revalidación post-attach de colisiones cross-source/
    cross-run."""
    keys = list({u for u in url_normalizeds if u})
    if not keys:
        return {}
    rows = await session.execute(
        sa.text(
            "SELECT pi_sl.url_normalized, all_i.url FROM source_listings pi_sl "
            "JOIN sources pi_s ON pi_s.id = pi_sl.source_id AND pi_s.name = :src "
            "JOIN source_listing_incarnations pi_i "
            "  ON pi_i.source_listing_id = pi_sl.id AND pi_i.ended_at IS NULL "
            "JOIN vacancies v ON v.id = pi_i.vacancy_id "
            "  AND v.merged_into IS NULL AND v.archived_at IS NULL "
            "JOIN source_listing_incarnations all_i "
            "  ON all_i.vacancy_id = v.id AND all_i.ended_at IS NULL "
            "WHERE pi_sl.url_normalized IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"src": PORTFOLIO_IMPORT_SOURCE, "keys": keys},
    )
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r.url_normalized, set()).add(r.url)
    return out


def normalized_key(url: str) -> str | None:
    """Clave de dedup ESTABLE (url normalizada) o None si la url NO es una clave usable:
    no normalizable (ValueError de normalize_url, p.ej. IPv6 con corchete) o NO codificable
    en utf-8 (surrogate suelto — normalize_url NO falla porque urlsplit no codifica, pero esa
    clave revienta como bind-param en Postgres: asyncpg DataError). El sink cuarentena esas
    mismas urls (_preprocess); este helper CENTRALIZA "¿es esta url una clave usable?" para
    que ningún módulo C-4 pase una clave tóxica a una query. UnicodeEncodeError ⊂ ValueError."""
    try:
        key = normalize_url(url)
        key.encode()
    except ValueError:
        return None
    return key


async def resolve_vacancy_by_url(session: AsyncSession, url: str) -> uuid.UUID | None:
    """vacancy_id de la vacante-sombra activa para esa URL, o None.

    Resuelve por (fuente 'portfolio-import', url_normalized) → incarnación
    ACTIVA (ended_at IS NULL) y una vacante PRESENTABLE (merged_into IS NULL,
    archived_at IS NULL — misma guarda que toda otra resolución de vacancy_id del
    core: nunca se enlaza una candidatura a una vacante fundida/archivada). Las
    UNIQUE del esquema garantizan a lo sumo una fila. Una URL no usable como clave
    (malformada o con mojibake no codificable) ⇒ None (jamás se sintetizó una vacante
    suya; honra el contrato uuid|None — el llamador no envuelve en try/except).
    """
    url_normalized = normalized_key(url)
    if url_normalized is None:
        return None
    return (
        await session.execute(
            sa.text(
                "SELECT i.vacancy_id FROM source_listings sl "
                "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
                "JOIN source_listing_incarnations i "
                "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
                "JOIN vacancies v "
                "  ON v.id = i.vacancy_id AND v.merged_into IS NULL "
                "  AND v.archived_at IS NULL "
                "WHERE sl.url_normalized = :urln"
            ),
            {"src": PORTFOLIO_IMPORT_SOURCE, "urln": url_normalized},
        )
    ).scalar_one_or_none()

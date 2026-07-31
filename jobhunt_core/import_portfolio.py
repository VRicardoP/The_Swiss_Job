"""Síntesis de vacantes-sombra para la importación del portfolio (C-4, parte 1).

Los durables del portfolio (candidaturas/bookmarks) referencian empleos por URL
que NO existen en el corpus del core: aquí se SINTETIZAN como vacantes bajo la
fuente `portfolio-import`, REUTILIZANDO la cadena de identidad existente
(RawListingSink, A-04..A-06) — jamás se inserta a mano en vacancies/offer_
revisions. El sink garantiza dedup e idempotencia vía UNIQUE(source_id,
external_id) y UNIQUE(source_id, url_normalized).

- La fuente nace con su scope DESHABILITADO (sin provider registrado: el
  runner de cosecha jamás debe tocarla — mismo criterio que legacy_shadow).
- `external_id` DETERMINISTA (sha256 de la URL normalizada): re-importar la
  misma URL colapsa en el mismo slot → misma vacante.
- Los items SIN url se OMITEN con log (no identificables; irán a staging en
  una parte futura de C-4).
"""

import hashlib
import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.sink import RawListingSink, normalize_url
from jobhunt_core.harvest.types import RawListing

logger = logging.getLogger(__name__)

PORTFOLIO_IMPORT_SOURCE = "portfolio-import"
# Tier 0: mismo tier que las fuentes internas sin cosecha (legacy_shadow).
PORTFOLIO_IMPORT_TIER = 0
# Scope DETERMINISTA: re-ejecutar ensure_import_scope jamás crea scopes nuevos
# (ON CONFLICT (id) DO NOTHING con el mismo id en cada invocación).
PORTFOLIO_IMPORT_SCOPE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "portfolio-import-scope")

# Registro exact-match de identidad/normalización (mismo patrón que arbeitnow/
# legacy_shadow): sin esto el sink no produce canónica (offer_revisions) para
# la fuente. El payload usa las claves del core (`company_name`, no `company`).
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


async def ensure_import_scope(session: AsyncSession) -> uuid.UUID:
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
    return PORTFOLIO_IMPORT_SCOPE_ID


def durable_to_raw_listing(
    url: str, title: str, company: str | None, description: str | None
) -> RawListing:
    """RawListing del sink a partir de un durable del portfolio.

    `external_id` = sha256 de la URL NORMALIZADA (64 hex < límite 200 del
    esquema): la misma URL siempre produce el mismo slot — la idempotencia de
    la re-importación descansa en las UNIQUE del sink.
    """
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


async def synthesize_vacancies(
    session: AsyncSession, scope_id: uuid.UUID, items: list[dict]
) -> None:
    """Sintetiza vacantes-sombra para los items del portfolio CON url.

    items = [{url, title, company, description}]. Los items sin url se OMITEN
    con log (sin URL no hay identidad resoluble; el staging llega en una parte
    futura de C-4). El lote entero pasa por el sink real: toda la cadena
    (slots, incarnaciones, revisiones, canónica) y su idempotencia son suyas.
    """
    listings = []
    for item in items:
        url = item.get("url")
        if not url:
            logger.warning(
                "import_portfolio: item sin url OMITIDO (title=%r) — "
                "pendiente de staging en una parte futura de C-4",
                item.get("title"),
            )
            continue
        try:
            listings.append(
                durable_to_raw_listing(
                    url,
                    item.get("title") or "",
                    item.get("company"),
                    item.get("description"),
                )
            )
        except ValueError as exc:
            # URL malformada: CUARENTENA por-item, no abortar el lote válido. El
            # external_id se calcula con normalize_url ANTES del sink, FUERA de su
            # cuarentena (_preprocess); sin esto un solo durable tóxico envenenaría
            # el lote entero y, al ser determinista, lo bloquearía indefinidamente
            # (P1 análisis 1) — justo lo que la cuarentena del sink evita.
            logger.warning(
                "import_portfolio: URL malformada OMITIDA (%s: %s) — title=%r",
                exc.__class__.__name__,
                exc,
                item.get("title"),
            )
            continue
    if not listings:
        return
    await RawListingSink().handle(session, str(scope_id), tuple(listings))


async def resolve_vacancy_by_url(
    session: AsyncSession, url: str
) -> uuid.UUID | None:
    """vacancy_id de la vacante-sombra activa para esa URL, o None.

    Resuelve por (fuente 'portfolio-import', url_normalized) → incarnación
    ACTIVA (ended_at IS NULL). Las UNIQUE del esquema garantizan a lo sumo
    una fila. Una URL MALFORMADA ⇒ None (jamás se sintetizó una vacante suya;
    honra el contrato uuid|None — el llamador no envuelve en try/except).
    """
    try:
        url_normalized = normalize_url(url)
    except ValueError:
        return None
    return (
        await session.execute(
            sa.text(
                "SELECT i.vacancy_id FROM source_listings sl "
                "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
                "JOIN source_listing_incarnations i "
                "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
                "WHERE sl.url_normalized = :urln"
            ),
            {"src": PORTFOLIO_IMPORT_SOURCE, "urln": url_normalized},
        )
    ).scalar_one_or_none()

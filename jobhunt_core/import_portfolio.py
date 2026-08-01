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
) -> set[str]:
    """Sintetiza vacantes-sombra para los items del portfolio CON url.

    items = [{url, title, company, description}]. Los items sin url se OMITEN
    con log (sin URL no hay identidad resoluble; el staging llega en una parte
    futura de C-4). El lote entero pasa por el sink real: toda la cadena
    (slots, incarnaciones, revisiones, canónica) y su idempotencia son suyas.

    Devuelve el CONJUNTO de URLs COLISIONADAS. Una COLISIÓN = dos URLs DISTINTAS
    que normalizan a la MISMA clave (p.ej. el id de la oferta vive en el fragmento
    que normalize_url descarta, portales SPA). Ante colisión NO se sintetiza y se
    devuelven TODAS las URLs del grupo (ambigüedad no resoluble: no se elige
    ganador por orden del lote): el llamador debe enrutarlas a staging en vez de
    que resolve_vacancy_by_url las mapee a la vacante equivocada (P1 rev. externa).
    La detección es DOS-PASADAS y consulta el estado PERSISTIDO (source_listing_
    incarnations.url), así que atrapa colisiones tanto intra-lote como CROSS-RUN
    (una ejecución previa ya confirmada). Pasa TODOS los items en UNA llamada.
    """
    # --- Pasada 1: validar/normalizar y AGRUPAR por external_id (= clave de
    # identidad sha256(url_normalized)). Cuarentena por-item de sin-url/malformadas.
    groups: dict[str, dict] = {}
    skipped = {"no_url": 0, "malformed": 0, "collision": 0, "dup": 0}
    for item in items:
        url = item.get("url")
        if not url:
            skipped["no_url"] += 1
            logger.warning(
                "import_portfolio: item sin url OMITIDO (title=%r) — "
                "pendiente de staging en una parte futura de C-4",
                item.get("title"),
            )
            continue
        try:
            url_normalized = normalize_url(url)
            listing = durable_to_raw_listing(
                url,
                item.get("title") or "",
                item.get("company"),
                item.get("description"),
            )
        except ValueError as exc:
            # URL malformada: CUARENTENA por-item, no abortar el lote válido (el
            # external_id se calcula con normalize_url ANTES del sink, FUERA de su
            # cuarentena; sin esto un durable tóxico envenenaría el lote entero).
            skipped["malformed"] += 1
            logger.warning(
                "import_portfolio: URL malformada OMITIDA (%s: %s) — title=%r",
                exc.__class__.__name__, exc, item.get("title"),
            )
            continue
        grp = groups.setdefault(
            listing.external_id, {"urln": url_normalized, "by_url": {}, "count": 0}
        )
        grp["count"] += 1
        grp["by_url"].setdefault(url, listing)  # una RawListing por url distinta

    # --- Estado PERSISTIDO: url ORIGINAL de la incarnación activa por url_normalized
    # (colisión CROSS-RUN — P1 rev. externa: una ejecución previa importó otra URL
    # con la misma clave y ya está confirmada).
    persisted = await _persisted_urls(session, [g["urln"] for g in groups.values()])

    # --- Pasada 2: por grupo, sintetizar (limpio) o colisión (stage-all).
    listings = []
    collided: set[str] = set()
    for grp in groups.values():
        batch_urls = set(grp["by_url"])
        prior_url = persisted.get(grp["urln"])
        all_urls = batch_urls | ({prior_url} if prior_url else set())
        if len(all_urls) > 1:
            # COLISIÓN (intra-lote o cross-run): ambigüedad no resoluble → NO se
            # sintetiza; TODAS las URLs del grupo van a staging (no se vincula a la
            # vacante equivocada, no se elige ganador por orden).
            skipped["collision"] += grp["count"]
            collided.update(batch_urls)
            logger.warning(
                "import_portfolio: COLISIÓN de URL normalizada (%s) — urls=%r "
                "(persistida=%r); TODAS a staging (reconciliar a mano)",
                grp["urln"], sorted(batch_urls), prior_url,
            )
            continue
        if prior_url is None:
            # Grupo limpio y NUEVO: sintetizar una vacante-sombra.
            listings.append(next(iter(grp["by_url"].values())))
            skipped["dup"] += grp["count"] - 1  # exactos-dup del mismo url
        else:
            # Re-import EXACTO (misma url ya persistida): idempotente, la vacante ya
            # existe → no re-sintetizar; el durable resolverá igual.
            skipped["dup"] += grp["count"]
    logger.info(
        "import_portfolio: %d items → %d a sintetizar (omitidos: %d sin url, %d "
        "malformadas, %d colisiones, %d duplicados). NOTA: el sink puede además "
        "descartar en cuarentena (url>1000/NUL/…); el vínculo de esos durables se "
        "detecta como resolve→None en el paso de applications.",
        len(items), len(listings), skipped["no_url"], skipped["malformed"],
        skipped["collision"], skipped["dup"],
    )
    if listings:
        await RawListingSink().handle(session, str(scope_id), tuple(listings))
    return collided


async def _persisted_urls(
    session: AsyncSession, url_normalizeds: list[str]
) -> dict[str, str]:
    """{url_normalized: url ORIGINAL} de las incarnaciones ACTIVAS ya persistidas
    en portfolio-import — para detectar colisiones CROSS-RUN (una ejecución previa
    confirmada importó otra URL con la misma clave)."""
    keys = list({u for u in url_normalizeds if u})
    if not keys:
        return {}
    rows = await session.execute(
        sa.text(
            "SELECT sl.url_normalized, i.url FROM source_listings sl "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "JOIN source_listing_incarnations i "
            "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
            "WHERE sl.url_normalized IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"src": PORTFOLIO_IMPORT_SOURCE, "keys": keys},
    )
    return {r.url_normalized: r.url for r in rows}


async def resolve_vacancy_by_url(
    session: AsyncSession, url: str
) -> uuid.UUID | None:
    """vacancy_id de la vacante-sombra activa para esa URL, o None.

    Resuelve por (fuente 'portfolio-import', url_normalized) → incarnación
    ACTIVA (ended_at IS NULL) y una vacante PRESENTABLE (merged_into IS NULL,
    archived_at IS NULL — misma guarda que toda otra resolución de vacancy_id del
    core: nunca se enlaza una candidatura a una vacante fundida/archivada). Las
    UNIQUE del esquema garantizan a lo sumo una fila. Una URL MALFORMADA ⇒ None
    (jamás se sintetizó una vacante suya; honra el contrato uuid|None — el
    llamador no envuelve en try/except).
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
                "JOIN vacancies v "
                "  ON v.id = i.vacancy_id AND v.merged_into IS NULL "
                "  AND v.archived_at IS NULL "
                "WHERE sl.url_normalized = :urln"
            ),
            {"src": PORTFOLIO_IMPORT_SOURCE, "urln": url_normalized},
        )
    ).scalar_one_or_none()

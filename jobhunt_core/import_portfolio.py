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
    La detección es DOS-PASADAS y consulta el estado PERSISTIDO de TODO el corpus
    elegible para attach (incarnaciones activas y presentables de CUALQUIER fuente,
    source_listing_incarnations.url), así que atrapa colisiones intra-lote, CROSS-RUN
    (ejecución previa confirmada) y CROSS-SOURCE (el sink adjunta por url_normalized
    a otra fuente). Pasa TODOS los items en UNA llamada.
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
    synthesized: dict[str, str] = {}  # url_normalized → su url del lote (revalidar)
    for grp in groups.values():
        batch_urls = set(grp["by_url"])
        prior_urls = prior.get(grp["urln"], set())
        if len(batch_urls) > 1 or len(batch_urls | prior_urls) > 1:
            skipped["collision"] += grp["count"]
            collided.update(batch_urls)
            logger.warning(
                "import_portfolio: COLISIÓN intra-lote/cross-run (%s) — lote=%r "
                "portfolio=%r; a staging",
                grp["urln"], sorted(batch_urls), sorted(prior_urls),
            )
            continue
        listings.append(next(iter(grp["by_url"].values())))
        synthesized[grp["urln"]] = next(iter(batch_urls))
        skipped["dup"] += grp["count"] - 1  # exactos-dup del mismo url
    if listings:
        await RawListingSink().handle(session, str(scope_id), tuple(listings))

    # --- REVALIDACIÓN POST-ATTACH (race-free): se lee el estado YA CONFIRMADO tras el
    # attach del sink. Si la vacante resultante de una url sintetizada tiene otra
    # incarnación activa con url ORIGINAL DISTINTA (cross-source o cross-run, incluida
    # cualquiera confirmada concurrentemente por otro harvester del core), es colisión:
    # su durable se enruta a staging. Un chequeo PREVIO no basta (TOCTOU); leer el
    # resultado sí, porque ve toda escritura ya confirmada (P1 rev. externa 3).
    incarnation_urls = await _vacancy_incarnation_urls(session, list(synthesized))
    for urln, batch_url in synthesized.items():
        urls = incarnation_urls.get(urln, {batch_url})
        if len(urls - {batch_url}) > 0:  # otra url distinta comparte la vacante
            collided.add(batch_url)
            skipped["collision"] += 1
            logger.warning(
                "import_portfolio: COLISIÓN post-attach (%s) — la vacante tiene urls "
                "%r ademas de %r; el durable a staging (reconciliar a mano)",
                urln, sorted(urls - {batch_url}), batch_url,
            )
    logger.info(
        "import_portfolio: %d items → %d sintetizadas (omitidos: %d sin url, %d "
        "malformadas, %d colisiones, %d duplicados).",
        len(items), len(listings), skipped["no_url"], skipped["malformed"],
        skipped["collision"], skipped["dup"],
    )
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

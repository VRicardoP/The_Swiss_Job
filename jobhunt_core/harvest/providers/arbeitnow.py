"""Provider Arbeitnow (Tier 0 — API pública gratuita, sin credenciales).

Diseño SOUND para paginación por offset con orden NO contractual (3ª revisión
externa A-03): sin cursor estable del proveedor, NINGÚN corte finito de
"páginas antiguas" demuestra que el feed esté drenado. Por tanto:

- Cada run recorre el feed COMPLETO (página 1 → fin de `links.next`), acotado
  por un tope de seguridad. La incrementalidad vive en la EMISIÓN, no en el
  fetch: solo los items con `created_at >= watermark` van al sink (idempotente,
  ADR-05 — los re-emitidos se deduplican por claves estables).
- El watermark SOLO se consolida si el run AGOTÓ el feed. Si el tope de
  seguridad corta antes, el watermark queda INTACTO (fallo conservador, con
  warning): nada puede perderse, solo re-emitirse.
- Sin ancla, sin backfill, sin resume por página, sin heurísticas de orden:
  no queda estado del que pueda depender una pérdida.
- Garantía: todo item >= watermark visible en el feed durante un run se emite
  en ese run. Retrodatados por debajo del watermark: indetectables para
  cualquier esquema de watermark (limitación documentada).
- Items sin timestamp o sin url/slug se saltan con log; jamás afectan al corte.
NO está en la lista restringida del proyecto (jobs.ch/LinkedIn/... siguen OFF).
"""

import logging

import httpx

from jobhunt_core.harvest.provider import BaseProvider
from jobhunt_core.harvest.types import FetchResult, RawListing

logger = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"
# Tope de SEGURIDAD de páginas por run (feed patológico/bucle de la API). Si se
# alcanza sin agotar el feed, NO se consolida el watermark (conservador). El
# feed real de Arbeitnow son unas decenas de páginas.
DEFAULT_MAX_PAGES = 50
MIN_MAX_PAGES = 2
HTTP_TIMEOUT_S = 20.0


class ArbeitnowProvider(BaseProvider):
    name = "arbeitnow"
    # Parámetros SEMÁNTICOS del scope (el runner reinicia el cursor si cambian).
    SEMANTIC_PARAMS = ("keyword",)

    async def fetch_new(
        self, params: dict, cursor: dict | None, http: httpx.AsyncClient
    ) -> FetchResult:
        watermark = int((cursor or {}).get("watermark", 0))
        max_pages = int(params.get("max_pages", DEFAULT_MAX_PAGES))
        if max_pages < MIN_MAX_PAGES:
            raise ValueError(f"max_pages debe ser >= {MIN_MAX_PAGES}")
        keyword = (params.get("keyword") or "").lower() or None

        collected: list[RawListing] = []
        top_seen = watermark
        pages = 0
        page = 1
        exhausted = False
        while pages < max_pages:
            resp = await http.get(API_URL, params={"page": page}, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            body = resp.json()
            items = body.get("data") or []
            pages += 1
            if not items:
                exhausted = True
                break
            for item in items:
                created = _parse_created_at(item)
                if created is None:
                    # Sin timestamp: se salta el ITEM (jamás decide nada más).
                    logger.warning("arbeitnow: item sin created_at válido, saltado")
                    continue
                top_seen = max(top_seen, created)
                if created < watermark:
                    continue  # antiguo: no se emite; el barrido SIGUE hasta el fin
                listing = _to_listing(item, keyword)
                if listing is not None:
                    collected.append(listing)
            if not (body.get("links") or {}).get("next"):
                exhausted = True
                break
            page += 1

        if exhausted:
            next_cursor = {"watermark": top_seen}
        else:
            # Tope de seguridad sin agotar el feed: watermark INTACTO (nada se
            # consolida sin drenaje demostrado; solo habrá re-emisión).
            logger.warning(
                "arbeitnow: tope de %d páginas sin agotar el feed; watermark intacto",
                max_pages,
            )
            next_cursor = {"watermark": watermark}
        logger.info(
            "arbeitnow: %d emitidas (%d páginas, %s) cursor=%s",
            len(collected), pages, "feed agotado" if exhausted else "tope", next_cursor,
        )
        return FetchResult(
            listings=tuple(collected), next_cursor=next_cursor, pages_fetched=pages
        )


def _parse_created_at(item: dict) -> int | None:
    try:
        value = item.get("created_at")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_listing(item: dict, keyword: str | None) -> RawListing | None:
    """Validación en frontera: sin url o sin identidad estable → se salta con log."""
    url = item.get("url")
    external_id = item.get("slug") or url
    if not url or not external_id:
        logger.warning("arbeitnow: item sin url/slug, saltado: %r", item.get("title"))
        return None
    if keyword and not _matches_keyword(item, keyword):
        return None
    return RawListing(external_id=str(external_id), url=str(url), payload=item)


def _matches_keyword(item: dict, keyword: str) -> bool:
    """Filtro de scope en cliente (la API no filtra server-side)."""
    haystack = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("description") or ""),
            " ".join(item.get("tags") or []),
        ]
    ).lower()
    return keyword in haystack

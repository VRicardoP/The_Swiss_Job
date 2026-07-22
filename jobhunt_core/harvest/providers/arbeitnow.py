"""Provider Arbeitnow (Tier 0 — API pública gratuita, sin credenciales).

Cursor incremental SIN pérdida (auditoría A-03):
- Feed ordenado por `created_at` (epoch) desc. Corte de "ya visto" ESTRICTO
  (`created < watermark`): los empates de segundo en la frontera se RE-EMITEN
  y los deduplica el sink idempotente (upsert por claves estables, ADR-05).
- El watermark SOLO avanza si el run drenó hasta el watermark viejo o agotó el
  feed. Si se corta por presupuesto de páginas con backlog pendiente, el cursor
  guarda estado de BACKFILL (`pending_watermark` + `backfill_page`) y el run
  siguiente reanuda el drenaje con 1 página de solape (contra desplazamientos
  por borrados); al completar, el watermark salta a `pending_watermark`.
  Así "diferido al próximo run" nunca se convierte en "perdido".
- Items sin timestamp o sin url/slug se saltan con log (validación en frontera),
  jamás disparan el corte.
NO está en la lista restringida del proyecto (jobs.ch/LinkedIn/... siguen OFF).
"""

import logging

import httpx

from jobhunt_core.harvest.provider import BaseProvider
from jobhunt_core.harvest.types import FetchResult, RawListing

logger = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"
# Presupuesto de páginas por run: acota coste/carga; el backlog restante se
# drena en runs siguientes vía backfill (nunca se pierde).
DEFAULT_MAX_PAGES = 3
HTTP_TIMEOUT_S = 20.0
# Solape al reanudar un backfill: si borrados desplazaron items hacia arriba,
# la página previa se re-escanea (los duplicados los absorbe el sink).
BACKFILL_OVERLAP_PAGES = 1


class ArbeitnowProvider(BaseProvider):
    name = "arbeitnow"

    async def fetch_new(
        self, params: dict, cursor: dict | None, http: httpx.AsyncClient
    ) -> FetchResult:
        cur = cursor or {}
        watermark = int(cur.get("watermark", 0))
        pending = cur.get("pending_watermark")
        start_page = max(1, int(cur.get("backfill_page", 1)) - BACKFILL_OVERLAP_PAGES)
        max_pages = int(params.get("max_pages", DEFAULT_MAX_PAGES))
        keyword = (params.get("keyword") or "").lower() or None

        collected: list[RawListing] = []
        top_seen = watermark  # máximo visto (fija pending la primera vez)
        pages = 0
        page = start_page
        crossed = False
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
                    # Sin timestamp ≠ antiguo: se salta, NUNCA dispara el corte.
                    logger.warning("arbeitnow: item sin created_at válido, saltado")
                    continue
                top_seen = max(top_seen, created)
                if created < watermark:  # estricto: los == watermark se re-emiten
                    crossed = True
                    break
                listing = _to_listing(item, keyword)
                if listing is not None:
                    collected.append(listing)

            if crossed:
                break
            if not (body.get("links") or {}).get("next"):
                exhausted = True
                break
            page += 1

        # Al salir por presupuesto, `page` ya apunta a la SIGUIENTE página no
        # pedida (el bucle la incrementó antes de comprobar el presupuesto).
        next_cursor = _decide_cursor(
            watermark=watermark, pending=pending, top_seen=top_seen,
            drained=crossed or exhausted, next_page=page,
        )
        logger.info(
            "arbeitnow: %d nuevas (%d páginas desde %d) cursor=%s",
            len(collected), pages, start_page, next_cursor,
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


def _decide_cursor(
    *, watermark: int, pending, top_seen: int, drained: bool, next_page: int
) -> dict:
    """La regla que evita la pérdida: el watermark solo avanza si se DRENÓ."""
    if drained:
        # Drenado completo: consolidar (el pending del backfill, o el tope del run).
        return {"watermark": int(pending) if pending is not None else top_seen}
    # Presupuesto agotado con backlog: watermark INTACTO + estado de reanudación.
    return {
        "watermark": watermark,
        "pending_watermark": int(pending) if pending is not None else top_seen,
        "backfill_page": next_page,
    }


def _matches_keyword(item: dict, keyword: str) -> bool:
    """Filtro de scope en cliente (la API no filtra server-side): título,
    descripción o tags contienen la keyword."""
    haystack = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("description") or ""),
            " ".join(item.get("tags") or []),
        ]
    ).lower()
    return keyword in haystack

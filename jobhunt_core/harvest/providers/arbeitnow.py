"""Provider Arbeitnow (Tier 0 — API pública gratuita, sin credenciales).

Cursor incremental SIN pérdida sobre paginación por offset MUTABLE y orden NO
contractual (revisiones externas A-03):

- Se re-escanea SIEMPRE desde la página 1 y se EMITE TODO item >= watermark
  hacia el sink idempotente (los duplicados los absorbe el upsert por claves
  estables, ADR-05). El ANCLA (`anchor_ts`) NO descarta nada: solo contabiliza
  el progreso del backfill (qué páginas son re-escaneo y cuáles avanzan), de
  modo que una oferta INSERTADA entre runs por encima del ancla se emite igual.
- El corte de "territorio antiguo" es POR PÁGINA COMPLETA (todos sus items <
  watermark), no por el primer item viejo: tolera desorden intra-página. Si se
  detecta desorden (created_at creciente dentro del escaneo), se exigen DOS
  páginas antiguas consecutivas antes de cortar (conservador).
- El watermark SOLO avanza si el run drenó (cortó por antiguo o agotó el feed).
  Si se corta por presupuesto, guarda `pending_watermark` + `anchor_ts`.
- Garantía honesta: se emite todo item >= watermark visible en el feed durante
  algún run; publicaciones RETRODATADAS por debajo del watermark son
  indetectables para cualquier esquema de watermark (limitación documentada).
- Items sin timestamp o sin url/slug se saltan con log; jamás disparan cortes.
NO está en la lista restringida del proyecto (jobs.ch/LinkedIn/... siguen OFF).
"""

import logging
from dataclasses import dataclass, field

import httpx

from jobhunt_core.harvest.provider import BaseProvider
from jobhunt_core.harvest.types import FetchResult, RawListing

logger = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"
# Presupuesto de páginas de PROGRESO por run (nuevas o terminales); el backlog
# restante se drena en runs siguientes vía el ancla (nunca se pierde).
DEFAULT_MAX_PAGES = 3
# Mínimo para garantizar progreso del backfill (liveness).
MIN_MAX_PAGES = 2
# Techo de páginas de RE-ESCANEO (por encima del ancla). Superarlo indica
# deriva/insercación masiva: se suelta el ancla (re-emisión idempotente).
MAX_SKIP_PAGES = 25
HTTP_TIMEOUT_S = 20.0
# Páginas completas antiguas consecutivas para cortar: 1 normalmente, 2 si se
# observó desorden en el feed (el orden desc NO es contractual en la API).
OLD_PAGES_TO_STOP = 1
OLD_PAGES_TO_STOP_DISORDERED = 2


@dataclass
class _Scan:
    """Estado del escaneo de un run (mantiene fetch_new legible, CC ≤ 10)."""

    watermark: int
    anchor_ts: int | None
    keyword: str | None
    top_seen: int = 0
    min_processed: int | None = None
    prev_created: int | None = None
    disorder: bool = False
    collected: list[RawListing] = field(default_factory=list)

    def __post_init__(self):
        self.top_seen = self.watermark

    def process_page(self, items: list[dict]) -> str:
        """Emite TODO item >= watermark; devuelve el tipo de página:
        'old' (toda < watermark) · 'rescan' (toda > ancla) · 'progress'."""
        page_ts: list[int] = []
        for item in items:
            created = _parse_created_at(item)
            if created is None:
                # Sin timestamp ≠ antiguo: se salta, NUNCA dispara cortes.
                logger.warning("arbeitnow: item sin created_at válido, saltado")
                continue
            page_ts.append(created)
            if self.prev_created is not None and created > self.prev_created:
                self.disorder = True  # el feed no viene en desc estricto
            self.prev_created = created
            self.top_seen = max(self.top_seen, created)
            if created < self.watermark:
                continue  # antiguo: no se emite (el corte lo decide la PÁGINA)
            # EMISIÓN INCONDICIONAL de lo >= watermark (el ancla no descarta:
            # una inserción tardía por encima del ancla debe llegar al sink).
            self.min_processed = created if self.min_processed is None else min(
                self.min_processed, created
            )
            listing = _to_listing(item, self.keyword)
            if listing is not None:
                self.collected.append(listing)

        if not page_ts or max(page_ts) < self.watermark:
            return "old"
        if self.anchor_ts is not None and min(page_ts) > self.anchor_ts:
            return "rescan"
        return "progress"


class ArbeitnowProvider(BaseProvider):
    name = "arbeitnow"
    # Parámetros SEMÁNTICOS del scope (el runner reinicia el cursor si cambian).
    SEMANTIC_PARAMS = ("keyword",)

    async def fetch_new(
        self, params: dict, cursor: dict | None, http: httpx.AsyncClient
    ) -> FetchResult:
        cur = cursor or {}
        watermark = int(cur.get("watermark", 0))
        pending = cur.get("pending_watermark")
        anchor_raw = cur.get("anchor_ts")
        max_pages = int(params.get("max_pages", DEFAULT_MAX_PAGES))
        if max_pages < MIN_MAX_PAGES:
            raise ValueError(f"max_pages debe ser >= {MIN_MAX_PAGES} (liveness del backfill)")

        scan = _Scan(
            watermark=watermark,
            anchor_ts=int(anchor_raw) if anchor_raw is not None else None,
            keyword=(params.get("keyword") or "").lower() or None,
        )
        progress_pages = rescan_pages = consecutive_old = 0
        pages = 0
        page = 1
        anchor_reset = exhausted = stopped_old = False
        while progress_pages < max_pages:
            if rescan_pages > MAX_SKIP_PAGES:
                # Deriva/inserción masiva sobre el ancla: soltarla; el próximo
                # run re-emite desde arriba (idempotente) y re-ancla.
                logger.warning("arbeitnow: re-escaneo > %d páginas, ancla reiniciada", MAX_SKIP_PAGES)
                anchor_reset = True
                break
            resp = await http.get(API_URL, params={"page": page}, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            body = resp.json()
            items = body.get("data") or []
            pages += 1
            if not items:
                exhausted = True
                break
            kind = scan.process_page(items)
            if kind == "rescan":
                rescan_pages += 1
                consecutive_old = 0
            else:
                progress_pages += 1
                consecutive_old = consecutive_old + 1 if kind == "old" else 0
            needed = OLD_PAGES_TO_STOP_DISORDERED if scan.disorder else OLD_PAGES_TO_STOP
            if consecutive_old >= needed:
                stopped_old = True
                break
            if not (body.get("links") or {}).get("next"):
                exhausted = True
                break
            page += 1

        next_cursor = _decide_cursor(
            watermark=watermark, pending=pending, scan=scan,
            drained=stopped_old or exhausted, anchor_reset=anchor_reset,
        )
        logger.info(
            "arbeitnow: %d emitidas (%d páginas: %d progreso / %d re-escaneo%s) cursor=%s",
            len(scan.collected), pages, progress_pages, rescan_pages,
            ", DESORDEN detectado" if scan.disorder else "", next_cursor,
        )
        return FetchResult(
            listings=tuple(scan.collected), next_cursor=next_cursor, pages_fetched=pages
        )


def _decide_cursor(
    *, watermark: int, pending, scan: _Scan, drained: bool, anchor_reset: bool
) -> dict:
    """La regla que evita la pérdida: el watermark solo avanza si se DRENÓ."""
    if drained:
        # Drenado completo: consolidar (el pending del backfill, o el tope del run).
        return {"watermark": int(pending) if pending is not None else scan.top_seen}
    partial = {
        "watermark": watermark,
        "pending_watermark": int(pending) if pending is not None else scan.top_seen,
    }
    if not anchor_reset:
        anchor = scan.min_processed if scan.min_processed is not None else scan.anchor_ts
        if anchor is not None:
            partial["anchor_ts"] = int(anchor)
        if scan.min_processed is None and scan.anchor_ts is not None:
            logger.warning(
                "arbeitnow: run sin progreso (ancla intacta en %s); "
                "considere subir max_pages", scan.anchor_ts,
            )
    return partial


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

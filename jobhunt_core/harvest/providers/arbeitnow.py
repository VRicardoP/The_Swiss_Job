"""Provider Arbeitnow (Tier 0 — API pública gratuita, sin credenciales).

Cursor incremental SIN pérdida sobre paginación por offset MUTABLE (revisión
externa A-03): la API no ofrece cursor estable ni garantiza inmutabilidad del
feed, así que NUNCA se reanuda por número de página. En su lugar:

- Corte de "ya visto" ESTRICTO (`created < watermark`): los empates de segundo
  en la frontera se RE-EMITEN y los deduplica el sink idempotente (ADR-05).
- El watermark SOLO avanza si el run drenó hasta el watermark viejo o agotó el
  feed. Si se corta por presupuesto, el cursor guarda `pending_watermark` y un
  ANCLA LÓGICA (`anchor_ts` = created_at del último item procesado): el run
  siguiente re-escanea DESDE LA PÁGINA 1 saltando lo ya emitido (barato) y
  continúa emitiendo desde el ancla. Los borrados/desplazamientos del feed solo
  provocan re-emisión (jamás salto): la inmunidad viene de re-escanear desde el
  principio, no de asumir estabilidad de páginas.
- Garantía honesta: bajo orden desc por created_at, ningún item ≥ watermark se
  pierde; publicaciones RETRODATADAS por debajo del watermark son indetectables
  para cualquier esquema de watermark (limitación documentada).
- Items sin timestamp o sin url/slug se saltan con log; jamás disparan el corte.
NO está en la lista restringida del proyecto (jobs.ch/LinkedIn/... siguen OFF).
"""

import logging
from dataclasses import dataclass, field

import httpx

from jobhunt_core.harvest.provider import BaseProvider
from jobhunt_core.harvest.types import FetchResult, RawListing

logger = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"
# Presupuesto de páginas de EMISIÓN por run; el backlog restante se drena en
# runs siguientes vía el ancla (nunca se pierde).
DEFAULT_MAX_PAGES = 3
# Mínimo para garantizar progreso: con 1 sola página, un run podría gastarla
# entera en la frontera del ancla sin avanzar (liveness, revisión A-03 #4).
MIN_MAX_PAGES = 2
# Techo de páginas de SKIP (re-escaneo de lo ya emitido). Superarlo indica
# deriva profunda del feed: se suelta el ancla y se re-emite (idempotente).
MAX_SKIP_PAGES = 25
HTTP_TIMEOUT_S = 20.0


@dataclass
class _Scan:
    """Estado del escaneo de un run (mantiene fetch_new legible, CC ≤ 10)."""

    watermark: int
    anchor_ts: int | None
    keyword: str | None
    top_seen: int = 0
    last_processed_ts: int | None = None
    collected: list[RawListing] = field(default_factory=list)
    crossed: bool = False
    skipping: bool = field(init=False)

    def __post_init__(self):
        self.top_seen = self.watermark
        self.skipping = self.anchor_ts is not None

    def process_page(self, items: list[dict]) -> bool:
        """Procesa una página; devuelve True si EMITIÓ algo (página de emisión)."""
        emitted = False
        for item in items:
            created = _parse_created_at(item)
            if created is None:
                # Sin timestamp ≠ antiguo: se salta, NUNCA dispara el corte.
                logger.warning("arbeitnow: item sin created_at válido, saltado")
                continue
            self.top_seen = max(self.top_seen, created)
            if created < self.watermark:  # estricto: los == watermark se re-emiten
                self.crossed = True
                return emitted
            if self.skipping and created > self.anchor_ts:
                continue  # ya emitido en runs previos de este backfill
            self.skipping = False
            # El ancla avanza por lo PROCESADO (incluye filtrados por keyword):
            # si no, un feed pobre en la keyword estancaría el backfill.
            self.last_processed_ts = created
            listing = _to_listing(item, self.keyword)
            if listing is not None:
                self.collected.append(listing)
                emitted = True
        return emitted or not self.skipping


class ArbeitnowProvider(BaseProvider):
    name = "arbeitnow"
    # Parámetros SEMÁNTICOS del scope: si cambian, el runner reinicia el cursor
    # (revisión A-03 #3). Los operativos (max_pages) quedan fuera.
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
        emission_pages = skip_pages = 0
        pages = page = 1
        anchor_reset = exhausted = False
        while emission_pages < max_pages:
            if skip_pages > MAX_SKIP_PAGES:
                # Deriva profunda: soltar el ancla; el próximo run re-emite
                # desde arriba (el sink idempotente absorbe los duplicados).
                logger.warning("arbeitnow: skip > %d páginas, ancla reiniciada", MAX_SKIP_PAGES)
                anchor_reset = True
                break
            resp = await http.get(API_URL, params={"page": page}, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            body = resp.json()
            items = body.get("data") or []
            if not items:
                exhausted = True
                break
            if scan.process_page(items):
                emission_pages += 1
            else:
                skip_pages += 1
            pages = page
            if scan.crossed:
                break
            if not (body.get("links") or {}).get("next"):
                exhausted = True
                break
            page += 1

        next_cursor = _decide_cursor(
            watermark=watermark, pending=pending, scan=scan,
            drained=scan.crossed or exhausted, anchor_reset=anchor_reset,
        )
        logger.info(
            "arbeitnow: %d nuevas (%d páginas: %d emisión / %d skip) cursor=%s",
            len(scan.collected), pages, emission_pages, skip_pages, next_cursor,
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
        anchor = scan.last_processed_ts if scan.last_processed_ts is not None else scan.anchor_ts
        if anchor is not None:
            partial["anchor_ts"] = int(anchor)
        if scan.last_processed_ts is None and scan.anchor_ts is not None:
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

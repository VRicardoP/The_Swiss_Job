"""Provider Arbeitnow (Tier 0 — API pública gratuita, sin credenciales).

Diseño FINAL tras la 4ª revisión externa de A-03 — el único sound para una API
de paginación por offset MUTABLE y sin cursor/snapshot del proveedor:

- Cada run barre el feed COMPLETO y EMITE TODOS los listings válidos que casan
  con el scope. Nada se filtra por "ya visto": la deduplicación y el refresco
  son del sink idempotente (A-04 necesita ver TODO lo visible en cada cosecha
  para refrescar `last_seen_at` y detectar revisiones — CONTRATOS §1/A-04).
- El "cursor" es SOLO metadato de observabilidad (`last_top_seen`) + el
  OBJETIVO ADAPTATIVO de páginas (`page_target`): si un barrido se corta por
  el tope, el objetivo se duplica (persistido) hasta poder agotar el feed —
  liveness garantizada sin techo configurado a mano.
- Un barrido que no agota `links.next` devuelve `complete=False`: el runner lo
  persiste igualmente (los listings vistos son válidos) pero NO actualiza
  `last_complete_at` y reporta `partial` (alerta operativa).
- Borrados/desplazamientos durante la paginación solo causan omisión TEMPORAL
  (el siguiente barrido completo los ve): sin filtro de emisión no existe
  estado capaz de convertir una omisión en pérdida permanente.
- Items sin url/slug se saltan con log (validación de frontera). El AISLAMIENTO
  es POR ITEM, dentro de una página bien formada: el SOBRE (cuerpo, `data`,
  `links`) tiene que cumplir la forma contractual o el barrido falla con
  `ProviderResponseError` — un sobre inválido degradado a "página vacía" es
  indistinguible del final del feed y hacía confirmar cosechas completas que
  nunca ocurrieron (auditoría externa 2026-08-27 P1-1).
NO está en la lista restringida del proyecto (jobs.ch/LinkedIn/... siguen OFF).
"""

import logging

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.types import FetchResult, RawListing

logger = logging.getLogger(__name__)

SOURCE_NAME = "arbeitnow"


def register_handlers() -> None:
    """Alta IDEMPOTENTE de identidad y normalización de la fuente.

    Se invoca al importar el módulo y, cuando un proceso llega a la fuente sin
    haberlo importado, desde `harvest.registry` (G4-P2-4): el registro es
    memoria POR PROCESO y confiarlo al EFECTO de un import lo hace
    irrecuperable si el import ya ocurrió."""
    # Identidad determinista (A-05): título/empresa crudos del payload.
    register_extractor(
        SOURCE_NAME,
        lambda payload: (payload.get("title"), payload.get("company_name")),
    )
    # Contenido canónico (A-06): el fn solo ESCOGE campos; la coerción es
    # central. Arbeitnow no publica salario en el feed → salary None (el
    # content_hash del raw sí cambiaría si apareciera, pero el text_hash no
    # depende de él — ADR-02).
    register_normalizer(
        SOURCE_NAME,
        lambda raw: {
            "title": raw.get("title"),
            "company": raw.get("company_name"),
            "description": raw.get("description"),
            "tags": raw.get("tags"),
            "location": raw.get("location"),
            "remote": raw.get("remote"),
            "salary": None,
        },
    )


register_handlers()

API_URL = "https://www.arbeitnow.com/api/job-board-api"
# Objetivo inicial de páginas por run; crece solo (x2, persistido en el cursor)
# si el feed resulta mayor, y el tamaño APRENDIDO se conserva tras los barridos
# completos (sin oscilar partial/complete — rev. 5ª P2#1).
DEFAULT_PAGE_TARGET = 50
# LÍMITE CONTRACTUAL DE CAPACIDAD (rev. 5ª P2#2): si el feed legítimo lo supera,
# los barridos quedan 'partial' sobre el mismo prefijo y se emite una ALERTA
# PERSISTENTE (error en cada run). Configurable por scope: params.hard_max_pages.
HARD_MAX_PAGES = 500
MIN_PAGE_TARGET = 2
HTTP_TIMEOUT_S = 20.0


class ArbeitnowProvider(BaseProvider):
    name = "arbeitnow"
    # Parámetros SEMÁNTICOS del scope (el runner reinicia el cursor si cambian).
    SEMANTIC_PARAMS = ("keyword",)

    async def fetch_new(
        self, params: dict, cursor: dict | None, http: httpx.AsyncClient
    ) -> FetchResult:
        cur = cursor or {}
        configured, hard_max, keyword = _parse_config(params or {})
        # Objetivo adaptativo (rev. 4ª #1): arranca en lo configurado y, si un
        # barrido anterior se quedó corto, usa el objetivo crecido persistido.
        target = min(max(configured, _cursor_int(cur, "page_target")), hard_max)

        collected: list[RawListing] = []
        top_seen = _cursor_int(cur, "last_top_seen")
        pages = 0
        page = 1
        exhausted = False
        while pages < target:
            resp = await http.get(API_URL, params={"page": page}, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            body = resp.json()
            items = _page_items(body, page)
            pages += 1
            if not items:
                exhausted = True
                break
            for item in items:
                # AISLAMIENTO por item: un item malformado (no-objeto, tags no-string, fecha
                # rara…) se SALTA con log — jamás revienta la página entera dejando el scope
                # reintentando la misma página tóxica (P2 rev. externa integral).
                if not isinstance(item, dict):
                    logger.warning("arbeitnow: item no-objeto saltado: %r", item)
                    continue
                try:
                    created = _parse_created_at(item)
                    if created is not None:
                        top_seen = max(top_seen, created)
                    # EMISIÓN TOTAL de lo válido que casa con el scope: el watermark
                    # ya NO filtra (A-04 refresca last_seen_at/revisiones con esto).
                    listing = _to_listing(item, keyword)
                    if listing is not None:
                        collected.append(listing)
                except Exception as exc:  # frontera de datos externos: nunca tumbar el barrido
                    logger.warning(
                        "arbeitnow: item malformado saltado (%s): %r",
                        exc, item.get("slug") or item.get("url"),
                    )
            if not _has_next_page(body, page):
                exhausted = True
                break
            page += 1

        next_cursor: dict = {"last_top_seen": top_seen}
        if exhausted:
            complete = True
            # Conservar el tamaño APRENDIDO (rev. 5ª P2#1): sin esto el ciclo
            # oscilaría partial/complete con alertas periódicas. Un feed que
            # encoge no cuesta nada: el barrido para igual en la exhaustión
            # (el target es solo un techo).
            if target > configured:
                next_cursor["page_target"] = target
        else:
            complete = False
            grown = min(target * 2, hard_max)
            next_cursor["page_target"] = grown
            logger.warning(
                "arbeitnow: barrido INCOMPLETO (%d páginas sin agotar el feed); "
                "objetivo adaptativo %d→%d", pages, target, grown,
            )
            if target >= hard_max:
                # ALERTA PERSISTENTE (cada run): capacidad contractual excedida.
                logger.error(
                    "arbeitnow: CAPACIDAD EXCEDIDA — el feed supera "
                    "hard_max_pages=%d; ampliar el límite del scope", hard_max,
                )
        logger.info(
            "arbeitnow: %d emitidas (%d páginas, %s) cursor=%s",
            len(collected), pages, "completo" if complete else "PARCIAL", next_cursor,
        )
        return FetchResult(
            listings=tuple(collected), next_cursor=next_cursor,
            pages_fetched=pages, complete=complete,
        )


def _page_items(body, page: int) -> list:
    """Items de una página, EXIGIENDO la forma contractual del sobre.

    Un cuerpo no-objeto o un `data` que no es lista NO es una página vacía: es una
    respuesta que ya no cumple el contrato. Degradarla a `[]` la hacía indistinguible
    del final del feed y el runner confirmaba una cosecha completa inexistente
    (auditoría externa 2026-08-27 P1-1). Error TRANSITORIO: el runner cuenta el fallo
    y deja cursor y `last_complete_at` intactos.
    """
    if not isinstance(body, dict):
        raise ProviderResponseError(
            f"página {page}: el cuerpo no es un objeto JSON, sino {type(body).__name__}"
        )
    data = body.get("data")
    if not isinstance(data, list):
        raise ProviderResponseError(
            f"página {page}: 'data' no es una lista, sino {type(data).__name__}"
        )
    return data


def _has_next_page(body: dict, page: int) -> bool:
    """Si el sobre anuncia página siguiente. `links` ausente o `null` = fin normal
    (la última página del feed puede no traerlo); `links` presente pero NO-objeto es
    forma inválida → error tipado, jamás un fin de paginación inventado que declararía
    completo un barrido cortado a medias (auditoría externa 2026-08-27 P1-1)."""
    links = body.get("links")
    if links is None:
        return False
    if not isinstance(links, dict):
        raise ProviderResponseError(
            f"página {page}: 'links' no es un objeto, sino {type(links).__name__}"
        )
    return bool(links.get("next"))


def _parse_config(params: dict) -> tuple[int, int, str | None]:
    """Valida TIPOS y rangos del JSON de configuración (rev. 3ª, APPROVE P2):
    `max_pages="abc"`, `hard_max_pages=null` o `keyword=123` son errores
    PERMANENTES — ProviderConfigError explícito, jamás un
    ValueError/TypeError/AttributeError genérico que consuma retries y deje
    `consecutive_failures` creciendo por un error de configuración."""
    if not isinstance(params, dict):
        raise ProviderConfigError(
            f"params debe ser un objeto JSON, no {type(params).__name__}"
        )
    try:
        configured = int(params.get("max_pages", DEFAULT_PAGE_TARGET))
        hard_max = int(params.get("hard_max_pages", HARD_MAX_PAGES))
    except (TypeError, ValueError) as exc:
        raise ProviderConfigError(f"max_pages/hard_max_pages inválidos: {exc}") from exc
    keyword_raw = params.get("keyword")
    if keyword_raw is not None and not isinstance(keyword_raw, str):
        raise ProviderConfigError(
            f"keyword debe ser string, no {type(keyword_raw).__name__}"
        )
    if configured < MIN_PAGE_TARGET:
        raise ProviderConfigError(f"max_pages debe ser >= {MIN_PAGE_TARGET}")
    # Config inválida FALLA explícita (rev. A-04 #4): con hard_max < target
    # mínimo el barrido haría 0 peticiones y devolvería un cursor parcial
    # (page_target=0, listings=[]) para siempre — livelock silencioso.
    if hard_max < MIN_PAGE_TARGET or hard_max < configured:
        raise ProviderConfigError(
            f"hard_max_pages ({hard_max}) debe ser >= {MIN_PAGE_TARGET} "
            f"y >= max_pages ({configured})"
        )
    keyword = (keyword_raw or "").lower() or None
    return configured, hard_max, keyword


def _cursor_int(cur: dict, key: str) -> int:
    """El cursor sale de NUESTRA BD, pero si se corrompe tampoco lo arregla un
    retry (rev. 3ª): misma clasificación permanente que la config inválida."""
    try:
        return int(cur.get(key, 0))
    except (TypeError, ValueError) as exc:
        raise ProviderConfigError(f"cursor corrupto: {key}={cur.get(key)!r}") from exc


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
            # Solo tags STRING: un tag no-string (p.ej. tags=[1]) reventaría el join
            # (P2 rev. externa integral).
            " ".join(t for t in (item.get("tags") or []) if isinstance(t, str)),
        ]
    ).lower()
    return keyword in haystack

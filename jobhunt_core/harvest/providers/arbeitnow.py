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
  nunca ocurrieron (auditoría externa 2026-08-27 P1-1; `links`, auditoría G9 P1-A).
- UNA COSECHA QUE NO INGIERE NADA NO ES UNA COSECHA COMPLETA (auditoría G10),
  salvo que el feed lo declare explícitamente (`data` vacío y `links.next` nulo).
  Las dos formas de fingirlo con el sobre intacto son error de frontera:
  `data: []` CONTRADICIENDO su propio `links.next` (P1-1) y una página de items
  que ya no se reconocen como ofertas (P2-1, p.ej. `url`→`job_url`).
- Un sobre inválido a MITAD de barrido no tira las páginas ya cosechadas: se
  emiten como barrido INCOMPLETO con el fallo marcado (`FetchResult.error`),
  que el runner contabiliza. Solo en la PRIMERA página, sin nada que preservar,
  el error sube tal cual (auditoría G9 P2-C). Y la preservación cubre TODA la
  familia de fallos transitorios —429, timeouts, 200 con HTML o cuerpo vacío—,
  no solo la excepción propia: el docstring lo prometía y no era cierto (G10 P1-2).
- El barrido se AUTOLIMITA (`PAGE_PAUSE_S`) y reintenta con backoff los rechazos
  del bucket: sin ritmo, `complete=True` era INALCANZABLE contra el feed real
  (auditoría G10 P1-2). Y el ritmo trae un PRESUPUESTO: cada espera tiene techo
  (`MAX_RETRY_WAIT_S`) y el barrido entero tiene reloj (`SWEEP_BUDGET_S`) — sin
  eso, obedecer `Retry-After` era dormir 24 h (o para siempre con `nan`/`inf`) en
  una tarea que nada podía interrumpir (auditoría G11 P1-1).
NO está en la lista restringida del proyecto (jobs.ch/LinkedIn/... siguen OFF).
"""

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass

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
# RITMO DEL BARRIDO (auditoría G10 P1-2). Medido en vivo contra el feed público el
# 2026-08-27: a ráfaga, la petición 11 devuelve HTTP 429 (sin Retry-After); con 1 s
# de pausa el 429 llega en la 12 y a partir de ahí solo pasa ~1 de cada 3 (el bucket
# repone ~1 petición cada 3,2 s); con 3,5 s pasaron 16 de 16 sin un solo rechazo. El
# feed necesita entre 18 y 31 páginas, así que SIN ritmo `complete=True` era
# inalcanzable y el reintento de la tarea volvía a golpear con la misma ráfaga. A 3,5
# s/página un barrido completo son ~110 s una vez al día, y respeta el «this is a free
# public API for jobs, please do not abuse» que la propia API manda en `meta.terms`.
PAGE_PAUSE_S = 3.5
# Reintento del TRANSPORTE (no del sobre): 3 intentos por página con backoff
# exponencial sobre la pausa del barrido — escala CON ella a propósito, así que con
# pausa muy pequeña (feed mockeado en la suite) apenas duerme.
HTTP_ATTEMPTS = 3
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# TECHO DE UNA ESPERA (auditoría G11 P1-1). `Retry-After` se obedecía literalmente:
# `86400` —el ban típico de un CDN— dormía 24 h dentro de la tarea Celery, y `nan`/`inf`
# dormían PARA SIEMPRE (el temporizador de `asyncio.sleep` no vence nunca). El servidor
# sigue mandando, pero dentro de un techo: por encima de un minuto ya no es "reintenta
# luego", es "vuelve en otra corrida", y la cosecha es diaria.
MAX_RETRY_WAIT_S = 60.0
# PRESUPUESTO DE TIEMPO DEL BARRIDO COMPLETO (auditoría G11 P1-1). El tope de páginas no
# acota el tiempo: medido instrumentando `asyncio.sleep`, 500 páginas (el tope
# contractual, al que el objetivo adaptativo llega duplicándose) con dos reintentos por
# página piden 12 246 s = 3,4 h SIN una sola cabecera hostil. Con `acks_late=True` y el
# `visibility_timeout` de 3600 s del canal Redis, un barrido que pasa de la hora ve su
# mensaje restituido a la cola mientras el original sigue vivo. Al vencer, el barrido se
# RINDE con lo cosechado (parcial, sin marcar fallo de la fuente): 1500 s + el peor caso
# de una página en curso (3 peticiones de 20 s + 2 esperas de 60 s + la pausa = 184 s)
# = 1684 s, por debajo del `task_soft_time_limit` de 1800 s (celery_app.py).
SWEEP_BUDGET_S = 1500.0


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

        # El ritmo y el presupuesto se leen AQUÍ, no como default del parámetro: un
        # default se evalúa UNA vez al importar y dejaría de responder a un
        # `monkeypatch`/rebind del módulo, que es como los fija la suite. No son ajustes
        # de entorno —no existe ningún `CORE_HARVEST_*` para ellos y es deliberado: la
        # imagen del core es inmutable, así que cambiar el ritmo es reconstruir—.
        sweep = await _sweep_feed(
            http, target, keyword, _cursor_int(cur, "last_top_seen"),
            PAGE_PAUSE_S, SWEEP_BUDGET_S,
        )
        collected, pages = sweep.listings, sweep.pages

        next_cursor: dict = {"last_top_seen": sweep.top_seen}
        if sweep.error is not None:
            # Sobre inválido a mitad de barrido (G9 P2-C): NI completo NI corto de
            # páginas — el objetivo adaptativo NO crece (no fue el tope quien cortó),
            # pero se conserva el tamaño ya aprendido.
            complete = False
            if target > configured:
                next_cursor["page_target"] = target
            logger.error(
                "arbeitnow: barrido CORTADO por forma inválida tras %d páginas "
                "(lo cosechado se emite igual): %s", pages, sweep.error,
            )
        elif sweep.timed_out:
            # PRESUPUESTO agotado (G11 P1-1): parcial, pero NI fallo de la fuente (no
            # cuenta backoff: la fuente no hizo nada mal) NI corte por el tope de páginas
            # — el objetivo NO se duplica, porque duplicarlo solo alargaría un barrido que
            # ya no cabe en el reloj. Se conserva el tamaño aprendido, como en el fallo.
            complete = False
            if target > configured:
                next_cursor["page_target"] = target
            logger.error(
                "arbeitnow: barrido AGOTÓ SU PRESUPUESTO de %.0f s tras %d páginas — "
                "el feed no cabe en una corrida a este ritmo; la vigilancia lo verá como "
                "'cosecha_sin_completar' si persiste", SWEEP_BUDGET_S, pages,
            )
        elif sweep.exhausted:
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
            listings=collected, next_cursor=next_cursor,
            pages_fetched=pages, complete=complete, error=sweep.error,
        )


@dataclass(frozen=True)
class _Sweep:
    """Resultado CRUDO de un barrido del feed (sin decisiones de cursor)."""

    listings: tuple[RawListing, ...]
    pages: int          # páginas con el sobre ÍNTEGRO recorridas
    top_seen: int
    exhausted: bool     # el feed se agotó (fin legítimo), no el tope de páginas
    error: str | None   # sobre inválido a mitad de barrido (lo previo se conserva)
    timed_out: bool     # se rindió por PRESUPUESTO de tiempo, no por tope ni por fallo


async def _sweep_feed(
    http: httpx.AsyncClient, target: int, keyword: str | None, top_seen: int,
    pause: float, budget: float,
) -> _Sweep:
    """Recorre el feed hasta agotarlo, hasta el tope de páginas o hasta un fallo.

    RITMO (auditoría G10 P1-2): `pause` segundos entre páginas. El feed real corta con
    429 a la petición ~11 si se le dispara a ráfaga, y necesita 18–31 páginas: sin ritmo
    el barrido no podía terminar NUNCA y el retry de la tarea repetía la ráfaga.

    PRESERVACIÓN (auditoría G9 P2-C, ampliada en G10 P1-2): si algo falla en la página
    k>1, las k−1 anteriores son válidas y se emiten igual — el runner las persiste como
    barrido INCOMPLETO (su invariante de siempre) y contabiliza el fallo por
    `FetchResult.error`. El todo-o-nada dejaba a una fuente averiada en la página 2 sin
    ingerir una sola oferta, y sin más rastro que un log. Cubre las TRES familias que
    ocurren de verdad —sobre inválido, fallo HTTP y cuerpo no-JSON (HTML de CDN, cuerpo
    vacío)—, no solo la excepción propia. En la PRIMERA página no hay nada que preservar:
    el error sube tal cual (P1-1).

    PRESUPUESTO (auditoría G11 P1-1): `budget` segundos de reloj para el barrido entero.
    Ni el tope de páginas ni el techo de una espera acotan el TIEMPO —500 páginas con dos
    reintentos cada una son 3,4 h de sueño legítimo—, y una tarea que se pasa de la hora
    con `acks_late=True` ve su mensaje restituido a la cola. Al vencer, el barrido se
    rinde con lo cosechado: `timed_out`, que NO es fallo de la fuente.
    """
    collected: list[RawListing] = []
    pages = 0
    page = 1
    exhausted = False
    timed_out = False
    error: str | None = None
    vence = time.monotonic() + budget
    try:
        while pages < target:
            if page > 1:
                if time.monotonic() >= vence:
                    timed_out = True
                    break
                await asyncio.sleep(pause)
            body = await _get_page(http, page, pause)
            # El sobre se valida ENTERO (`data` Y `links`) ANTES de usar nada de él:
            # una página VACÍA sin `links` cortaba el barrido como "fin del feed" sin
            # llegar nunca a mirar `links` — el mismo falso verde por la puerta de
            # atrás (auditoría G9 P1-A).
            items = _page_items(body, page)
            has_next = _has_next_page(body, page)
            top_seen = _harvest_page(items, has_next, keyword, collected, top_seen, page)
            pages += 1
            if not items or not has_next:
                exhausted = True
                break
            page += 1
    except (ProviderResponseError, httpx.HTTPError, json.JSONDecodeError) as exc:
        if page == 1:
            raise
        error = _describe_failure(exc, page)
    return _Sweep(tuple(collected), pages, top_seen, exhausted, error, timed_out)


async def _get_page(http: httpx.AsyncClient, page: int, pause: float):
    """Una página del feed, con REINTENTO de los fallos transitorios del transporte.

    El 429 del feed real llega SIN `Retry-After` (medido), así que el backoff es
    exponencial sobre la pausa del barrido; si algún día lo trae, manda la cabecera —
    pero ACOTADA por `MAX_RETRY_WAIT_S` (G11 P1-1: obedecerla a pelo era dormir un día).
    Un fallo que sobrevive a los reintentos sube: lo clasifica `_sweep_feed` (página 1
    ⇒ error de la fuente; página k>1 ⇒ preservar lo cosechado).
    """
    intento = 0
    while True:
        intento += 1
        try:
            resp = await http.get(API_URL, params={"page": page}, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            if intento >= HTTP_ATTEMPTS or not _is_retryable(exc):
                raise
            espera = _retry_delay(exc, pause, intento)
            logger.warning(
                "arbeitnow: página %d falló (%s), intento %d/%d; reintento en %.1f s",
                page, _reason(exc), intento, HTTP_ATTEMPTS, espera,
            )
            await asyncio.sleep(espera)


def _is_retryable(exc: Exception) -> bool:
    """Un rechazo del bucket (429) o un 5xx pasajero se reintentan; un 404/403 no
    se arregla insistiendo."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return True  # timeouts y cortes de conexión


def _retry_delay(exc: Exception, pause: float, intento: int) -> float:
    """Cuánto esperar antes del siguiente intento — ACOTADO (auditoría G11 P1-1).

    El servidor manda si manda un número de segundos, pero dentro de
    `[0, MAX_RETRY_WAIT_S]`: `86400` dormía un día entero dentro de la tarea, `-5` daba
    un reintento INMEDIATO contra una API que acababa de pedir calma, y `nan`/`inf` —que
    `float()` acepta— dormían para siempre, porque su temporizador no vence nunca. Un
    valor no finito no es una espera: se cae al backoff propio, también con techo.
    """
    espera = pause * 2**intento
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            cabecera = float(exc.response.headers["retry-after"])
        except (KeyError, TypeError, ValueError):
            pass
        else:
            if math.isfinite(cabecera):
                espera = cabecera
    return min(max(espera, 0.0), MAX_RETRY_WAIT_S)


def _reason(exc: Exception) -> str:
    """Etiqueta CORTA del fallo: el mensaje de httpx trae la URL entera y el del
    JSONDecodeError, el cuerpo — ninguno cabe en un `FetchResult.error`."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, json.JSONDecodeError):
        return f"cuerpo no-JSON ({exc.msg})"
    return f"{type(exc).__name__}: {exc}"


def _describe_failure(exc: Exception, page: int) -> str:
    """El texto que viaja en `FetchResult.error` hasta el log del runner."""
    if isinstance(exc, ProviderResponseError):
        return str(exc)  # ya viene con el número de página
    return f"página {page}: {_reason(exc)}"


def _harvest_page(
    items: list, has_next: bool, keyword: str | None,
    collected: list[RawListing], top_seen: int, page: int,
) -> int:
    """Cosecha UNA página y devuelve el watermark, exigiendo que la página no se
    contradiga a sí misma (auditoría G10 P1-1 y P2-1).

    Las dos incoherencias tienen el sobre INTACTO y por eso pasaban la validación de
    frontera entera; las dos declaraban COMPLETA una cosecha de cero ofertas:
    - `data` vacío mientras `links.next` anuncia página siguiente: el feed dice a la vez
      que se acabó y que no. No es el final; es una respuesta que dejó de cumplir.
    - items que ya no se reconocen como ofertas (subida de versión que renombra
      `url`→`job_url`): página con contenido y cero utilizables. Tampoco es el final.
    Las dos son `ProviderResponseError`, o sea el camino que ya existía: página 1 sube,
    página k>1 preserva lo cosechado.
    """
    if not items:
        if has_next:
            raise ProviderResponseError(
                f"página {page}: 'data' vacío pero el sobre anuncia links.next — "
                "el feed se contradice; no es el final"
            )
        return top_seen  # final LEGÍTIMO del feed: vacío y sin anunciar siguiente
    cosecha = _collect_items(items, keyword, collected, top_seen)
    if not cosecha.usable:
        raise ProviderResponseError(
            f"página {page}: {len(items)} items y 0 utilizables — la forma del item "
            "dejó de cumplir el contrato (¿campos renombrados?)"
        )
    return cosecha.top_seen


@dataclass(frozen=True)
class _PageHarvest:
    """Lo que aporta UNA página: watermark y cuántos items CUMPLEN el contrato de item.

    `usable` se cuenta ANTES del filtro de scope a propósito (G10 P2-1): un item válido
    que la keyword descarta sigue demostrando que la API mantiene su forma, mientras que
    una página entera de items irreconocibles demuestra lo contrario.
    """

    top_seen: int
    usable: int


def _collect_items(
    items: list, keyword: str | None, collected: list[RawListing], top_seen: int
) -> _PageHarvest:
    """Emite los items VÁLIDOS de una página y devuelve watermark + items utilizables.

    AISLAMIENTO por item: un item malformado (no-objeto, tags no-string, fecha rara…) se
    SALTA con log — jamás revienta la página entera dejando el scope reintentando la misma
    página tóxica (P2 rev. externa integral).
    """
    usable = 0
    for item in items:
        if not isinstance(item, dict):
            logger.warning("arbeitnow: item no-objeto saltado: %r", item)
            continue
        try:
            created = _parse_created_at(item)
            if created is not None:
                top_seen = max(top_seen, created)
            listing = _to_listing(item)
            if listing is None:
                continue
            usable += 1
            # EMISIÓN TOTAL de lo válido que casa con el scope: el watermark
            # ya NO filtra (A-04 refresca last_seen_at/revisiones con esto).
            if keyword is None or _matches_keyword(item, keyword):
                collected.append(listing)
        except Exception as exc:  # frontera de datos externos: nunca tumbar el barrido
            logger.warning(
                "arbeitnow: item malformado saltado (%s): %r",
                exc, item.get("slug") or item.get("url"),
            )
    return _PageHarvest(top_seen, usable)


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
    """Si el sobre anuncia página siguiente. `links` es OBLIGATORIO y objeto.

    Arbeitnow es un paginador Laravel: `links` viaja en TODAS las páginas, también en la
    TERMINAL — lo que se anula allí es `links.next`, no `links` (comprobado en vivo contra
    el feed público el 2026-08-27: `?page=1` y `?page=5000` responden 200 con las claves
    `data`/`links`/`meta`, y la terminal trae `links.next: null`). Un sobre SIN `links`, o
    con `links: null`, no es "la última página": es una respuesta que dejó de cumplir el
    contrato, y leerla como fin de paginación declaraba COMPLETO un barrido cortado a
    medias — el mismo falso verde de P1-1 por la otra clave (auditoría G9 P1-A).
    `links.next` ausente o `null` sigue siendo el final LEGÍTIMO del feed.
    """
    links = body.get("links")
    if not isinstance(links, dict):
        raise ProviderResponseError(
            f"página {page}: 'links' ausente o no-objeto ({type(links).__name__})"
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


def _to_listing(item: dict) -> RawListing | None:
    """Validación en frontera: sin url o sin identidad estable → se salta con log.

    Decide si el item CUMPLE EL CONTRATO, nada más: el filtro de scope (keyword) vive
    aparte porque son dos preguntas distintas y confundirlas hacía indistinguible «la API
    cambió de forma» de «este scope no quiere nada de esta página» (G10 P2-1).
    """
    url = item.get("url")
    external_id = item.get("slug") or url
    if not url or not external_id:
        logger.warning("arbeitnow: item sin url/slug, saltado: %r", item.get("title"))
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

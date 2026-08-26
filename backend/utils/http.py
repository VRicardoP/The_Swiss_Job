"""HTTP utilities: retry with exponential backoff using httpx."""

import asyncio
import logging
from typing import Any

import httpx

from utils import fetch_diagnostics as diag

logger = logging.getLogger(__name__)

DEFAULT_RETRY_STATUSES = [429, 500, 502, 503, 504]

# Códigos de redirección y los dos que PRESERVAN el método y el cuerpo.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_METHOD_PRESERVING = frozenset({307, 308})

# G4/P3-1: `httpx.TooManyRedirects` deriva de `httpx.HTTPError`, así que el
# `except` de reintento lo repetía — 21 saltos × 4 intentos = 84 peticiones
# donde antes había 4. Con el seguimiento manual el tope es duro y por
# petición; el bucle no puede multiplicarlo.
_MAX_REDIRECTS = 3

# Cabeceras que httpx SÍ retira al saltar de host. El resto (las propietarias:
# `x-rapidapi-key`, `x-api-key`…) las conservaba, así que una credencial
# viajaba al host nuevo (G4/P3-2). Aquí se retiran TODAS las cabeceras del
# llamante en un salto cross-host: es la política conservadora y no hay ningún
# provider que necesite lo contrario.
_CROSS_HOST_SAFE_HEADERS = frozenset({"accept", "accept-language", "user-agent"})


async def _send(client, method: str, url: str, kwargs: dict) -> httpx.Response:
    """Una sola petición, sin seguir redirecciones."""
    if method.upper() == "POST":
        return await client.post(url, **kwargs)
    return await client.get(url, **kwargs)


def _resolve_redirect(current: httpx.URL, location: str) -> httpx.URL:
    """Destino de un `Location`, CONSERVANDO la query de la base.

    G4/P2-7: `httpx` resuelve el `Location` con `URL.join`, que DESCARTA la
    query de la base. Un portal que responda `308` con
    `Location: /public/vacancy/search` (sin query) hacía que la petición
    seguida perdiera `page`/`size`: cada página pedida devolvía la PÁGINA 1, el
    provider cosechaba N veces las mismas 20 ofertas, el dedup exacto las
    colapsaba y el run terminaba con `job_count > 0`, cero issues y veredicto
    `ok`. Pérdida silenciosa del inventario profundo — peor, en
    observabilidad, que el 308 no seguido que originó el fix G3/P1-3.
    Si el `Location` trae su propia query, manda la suya: el portal está
    diciendo explícitamente adónde ir.

    G5/P3-1 — la query se conserva SOLO dentro del mismo host. `_headers_for_host`
    ya retira las cabeceras propietarias en un salto cross-host, pero la query
    se copiaba SIEMPRE, incluido ese salto: el `app_id`+`app_key` de adzuna y el
    `affid` de careerjet viajaban enteros al host nuevo. El canal no existía
    antes de este seguimiento manual (el `follow_redirects=True` de httpx
    resuelve con `URL.join`, que DESCARTA la query); lo abrió el fix de G4/P2-7
    por la otra puerta. Un `Location` cross-host es el portal diciendo «vete a
    otro sitio»: los parámetros de la petición original no son suyos.
    """
    target = current.join(location)
    if not target.query and current.query and target.host == current.host:
        target = target.copy_with(query=current.query)
    return target


def _headers_for_host(headers: dict[str, str] | None, cross_host: bool) -> dict | None:
    """Cabeceras que pueden viajar al destino (ver `_CROSS_HOST_SAFE_HEADERS`)."""
    if not headers or not cross_host:
        return headers
    return {k: v for k, v in headers.items() if k.lower() in _CROSS_HOST_SAFE_HEADERS}


async def _send_following_redirects(
    client,
    method: str,
    url: str,
    kwargs: dict,
    params: dict | None,
    safe_url: str | None = None,
) -> httpx.Response:
    """Petición siguiendo redirecciones A MANO, con tres garantías que
    `follow_redirects=True` de httpx no da (G4/P2-7, P3-1, P3-2, P3-3):

    - la query de la base SOBREVIVE a un `Location` relativo sin query;
    - las cabeceras propietarias NO cruzan de host;
    - un 301/302/303 sobre un POST NO se degrada a GET perdiendo el cuerpo:
      se devuelve el 3xx tal cual, que el llamante registra como fallo visible.

    Devuelve la última respuesta: si no se pudo (o no se debió) seguir, es el
    propio 3xx y el llamante lo trata como no-2xx.
    """
    current = httpx.URL(url)
    if params:
        current = current.copy_merge_params(params)
    origin_host = current.host
    # G5/P2-1 — lo que se REGISTRA nunca es `current`: lleva la credencial
    # (el path de jooble, la query de adzuna/careerjet tras fusionar `params`).
    log_url = safe_url or url

    for _ in range(_MAX_REDIRECTS + 1):
        response = await _send(client, method, str(current), kwargs)
        if response.status_code not in _REDIRECT_STATUSES:
            return response

        location = response.headers.get("location")
        if not location:
            return response

        if method.upper() == "POST" and response.status_code not in _METHOD_PRESERVING:
            # G4/P3-3: 301/302/303 degradan el POST a GET y descartan el cuerpo
            # — el `keywords`/`location`/`page` de jooble se perdía y el run
            # salía `ok` con la respuesta de un GET sin filtros.
            logger.error(
                "HTTP %d sobre un POST a %s: seguirlo degradaría el método y "
                "perdería el cuerpo — no se sigue",
                response.status_code,
                log_url,
            )
            return response

        target = _resolve_redirect(current, location)
        if target.host != origin_host:
            kwargs = dict(kwargs)
            kwargs["headers"] = _headers_for_host(kwargs.get("headers"), True)
        current = target

    logger.error("Demasiadas redirecciones (%d) para %s", _MAX_REDIRECTS, log_url)
    return response


async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    max_retry_delay: float = 30.0,
    timeout: float = 15.0,
    retry_on_status: list[int] | None = None,
    diag_url: str | None = None,
) -> Any | None:
    """HTTP request with exponential-backoff retry.

    Returns parsed JSON on success, None on failure.
    Supports both GET and POST methods.

    G5/P2-1 — `diag_url` es la forma de la URL que se PUBLICA (logs,
    `fetch_diagnostics` y, a través de ellos, `SourceHealth.last_error_detail`,
    que el panel muestra, y el cuerpo de la alerta de fuente caída). Por
    defecto es `url`; los providers que incrustan una credencial en la URL
    —hoy solo `jooble`, con la API key en el path— pasan aquí su forma
    redactada. Se aplica en la RAÍZ y no en cada rama a propósito: `fa5d493`
    redactó únicamente la salida de `diag.json_items` y dejó abiertas las tres
    de este helper (403, 429 y fallo de parseo), que además son las
    PROBABLES — key revocada, rate limit, timeout.
    """
    if retry_on_status is None:
        retry_on_status = DEFAULT_RETRY_STATUSES
    # A partir de aquí NINGUNA salida visible usa `url`: solo `safe_url`.
    safe_url = diag_url or url

    last_error: str = ""
    for attempt in range(max_retries + 1):
        try:
            # G3/P1-3: httpx trae follow_redirects=False por defecto. Un portal
            # que empieza a responder 308 (ostjob/zentraljob, 2026-08-18) mataba
            # la fuente entera. Se sigue el redirect AQUÍ, en la raíz, para que
            # ningún provider dependa de acordarse de activarlo — pero A MANO
            # (ver `_send_following_redirects`): el `follow_redirects=True` de
            # httpx perdía la query, filtraba las cabeceras propietarias a otro
            # host y degradaba el POST.
            kwargs: dict[str, Any] = {
                "timeout": timeout,
                "follow_redirects": False,
            }
            if headers:
                kwargs["headers"] = headers
            if json_body and method.upper() == "POST":
                kwargs["json"] = json_body

            response = await _send_following_redirects(
                client, method, url, kwargs, params, safe_url
            )

            # Retryable server errors
            if response.status_code in retry_on_status and attempt < max_retries:
                wait = min(backoff_factor * (2**attempt), max_retry_delay)
                logger.warning(
                    "HTTP %d from %s, retrying in %.1fs...",
                    response.status_code,
                    safe_url,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            if response.status_code != 200:
                logger.error(
                    "HTTP %d from %s: %s",
                    response.status_code,
                    safe_url,
                    response.text[:500],
                )
                # G4/P2-4: `retry_on_status` es la ÚNICA fuente de verdad sobre
                # qué se reintenta. Antes, todo status no-2xx que no fuera 4xx
                # caía en la escalera de reintentos con la pausa que añadió
                # G3/P1-3 — incluidos los 520/521/522/524 de Cloudflare, que no
                # están en `DEFAULT_RETRY_STATUSES`. Cada petición pasaba de
                # instantánea a 7 s, y `thehub` paga el helper UNA VEZ POR
                # OFERTA en su bucle de detalles: ×46 detalles = 322 s, ×75 =
                # 526 s, contra un `soft_time_limit` de 540 s que es para los
                # 20 providers. Un solo endpoint detrás de un challenge de
                # Cloudflare se comía la cosecha de API entera. El caso
                # retryable ya se ha reintentado (y pausado) arriba: aquí solo
                # queda registrar y salir.
                diag.record(diag.KIND_HTTP, safe_url, status=response.status_code)
                return None

            data = response.json()
            if data is None:
                # Un 200 con cuerpo `null` parsea a None por el camino de
                # ÉXITO (sin JSONDecodeError): sin registro aquí se rompía el
                # contrato del que dependen todos los llamantes — "el None de
                # fetch_with_retry es un fetch fallido cuyo issue ya registró
                # utils.http" — y el run salía `empty` en silencio.
                diag.record(
                    diag.KIND_NETWORK, safe_url, detail="200 con cuerpo JSON null"
                )
            return data

        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.error(
                "Request error (attempt %d): %s: %s",
                attempt + 1,
                type(exc).__name__,
                exc,
            )
            if attempt < max_retries:
                await asyncio.sleep(backoff_factor * (2**attempt))
            continue
        except (httpx.HTTPError, ValueError, RecursionError) as exc:
            # ValueError cubre TODO fallo de parseo de response.json():
            # json.JSONDecodeError y también UnicodeDecodeError (un 200 cuyo
            # cuerpo no es UTF-8), que ANTES escapaba del helper y rompía el
            # contrato "None ⇒ issue ya registrado" (r2/H1, G1). Ambas derivan
            # de ValueError; se captura la base — mismo patrón que fetch_rss —
            # y no traga de más: el resto del try lanza errores tipados httpx.
            # RecursionError (r3/R10): json.loads con anidamiento extremo NO
            # lanza ValueError sino RecursionError, que también escapaba y
            # rompía el mismo contrato — se agrupa con los fallos de parseo.
            last_error = f"{type(exc).__name__}: {exc}"
            logger.error(
                "Unexpected error (attempt %d): %s: %s",
                attempt + 1,
                type(exc).__name__,
                exc,
            )
            if attempt < max_retries:
                await asyncio.sleep(backoff_factor * (2**attempt))
            continue

    # Solo se llega aquí agotando reintentos por excepción (los cortes por
    # status ya han registrado y devuelto arriba).
    logger.error("Failed after %d attempts for %s", max_retries + 1, safe_url)
    diag.record(diag.KIND_NETWORK, safe_url, detail=last_error or "sin respuesta")
    return None


async def fetch_rss(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    diag_url: str | None = None,
) -> str | None:
    """Fetch RSS/XML content as raw text. Returns None on failure.

    El fallo DEFINITIVO (agotados los reintentos) se registra en
    `fetch_diagnostics` para que el pipeline no lo confunda con un feed vacío
    (V.0). Se registra el último estado visto, no cada reintento.

    G5/P2-1 — `diag_url`: misma garantía que en `fetch_with_retry`, para un
    feed con token en la URL.
    """
    safe_url = diag_url or url
    last_status: int | None = None
    last_error: str = ""
    for attempt in range(max_retries + 1):
        try:
            # G3/P1-3: sigue redirecciones también en los feeds RSS (un feed
            # que migra de host responde 301/308 y, sin esto, la fuente muere).
            # Mismo seguimiento manual que `fetch_with_retry` (G4/P2-7, P3-1,
            # P3-2): tope duro de saltos y cabeceras que no cruzan de host.
            rss_kwargs: dict[str, Any] = {
                "timeout": timeout,
                "follow_redirects": False,
            }
            if headers:
                rss_kwargs["headers"] = headers
            response = await _send_following_redirects(
                client, "GET", url, rss_kwargs, None, safe_url
            )
            if response.status_code == 200:
                return response.text
            last_status = response.status_code
            logger.warning(
                "RSS fetch HTTP %d (attempt %d)", response.status_code, attempt + 1
            )
            if attempt < max_retries:
                await asyncio.sleep(backoff_factor * (2**attempt))
        except (httpx.HTTPError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.error("RSS fetch error (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries:
                await asyncio.sleep(backoff_factor * (2**attempt))

    if last_status is not None:
        diag.record(diag.KIND_HTTP, safe_url, status=last_status)
    else:
        diag.record(diag.KIND_NETWORK, safe_url, detail=last_error or "sin respuesta")
    return None

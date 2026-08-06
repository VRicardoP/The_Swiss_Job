"""HTTP utilities: retry with exponential backoff using httpx."""

import asyncio
import json
import logging
from typing import Any

import httpx

from utils import fetch_diagnostics as diag

logger = logging.getLogger(__name__)

DEFAULT_RETRY_STATUSES = [429, 500, 502, 503, 504]


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
) -> Any | None:
    """HTTP request with exponential-backoff retry.

    Returns parsed JSON on success, None on failure.
    Supports both GET and POST methods.
    """
    if retry_on_status is None:
        retry_on_status = DEFAULT_RETRY_STATUSES

    last_error: str = ""
    for attempt in range(max_retries + 1):
        try:
            kwargs: dict[str, Any] = {"timeout": timeout}
            if headers:
                kwargs["headers"] = headers
            if params:
                kwargs["params"] = params
            if json_body and method.upper() == "POST":
                kwargs["json"] = json_body

            if method.upper() == "POST":
                response = await client.post(url, **kwargs)
            else:
                response = await client.get(url, **kwargs)

            # Retryable server errors
            if response.status_code in retry_on_status and attempt < max_retries:
                wait = min(backoff_factor * (2**attempt), max_retry_delay)
                logger.warning(
                    "HTTP %d from %s, retrying in %.1fs...",
                    response.status_code,
                    url,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            if response.status_code != 200:
                logger.error(
                    "HTTP %d from %s: %s",
                    response.status_code,
                    url,
                    response.text[:500],
                )
                # Non-retryable client error (except 429)
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    diag.record(diag.KIND_HTTP, url, status=response.status_code)
                    return None
                if attempt == max_retries:
                    diag.record(diag.KIND_HTTP, url, status=response.status_code)
                    return None
                continue

            return response.json()

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
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
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
    logger.error("Failed after %d attempts for %s", max_retries + 1, url)
    diag.record(diag.KIND_NETWORK, url, detail=last_error or "sin respuesta")
    return None


async def fetch_rss(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
) -> str | None:
    """Fetch RSS/XML content as raw text. Returns None on failure.

    El fallo DEFINITIVO (agotados los reintentos) se registra en
    `fetch_diagnostics` para que el pipeline no lo confunda con un feed vacío
    (V.0). Se registra el último estado visto, no cada reintento.
    """
    last_status: int | None = None
    last_error: str = ""
    for attempt in range(max_retries + 1):
        try:
            response = await client.get(url, headers=headers, timeout=timeout)
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
        diag.record(diag.KIND_HTTP, url, status=last_status)
    else:
        diag.record(diag.KIND_NETWORK, url, detail=last_error or "sin respuesta")
    return None

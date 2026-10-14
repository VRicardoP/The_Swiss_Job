"""jobs.admin.ch — the federal job portal, read from its own public JSON API.

The retiring scraper drove a headless browser (`NEEDS_PLAYWRIGHT = True`) and
guessed at the rendered DOM with a list of six speculative selectors. What
production shows it produced, over 43 stored rows: a title, a URL and the
CONSTANT company "Swiss Federal Administration" — location, description,
published_at and tags are empty in **every** row
(`2026-10-14-dt144-swissjob-metrics.json`, field presence `[43,0,0,43,0,0]`).

The portal is a React application over a PUBLIC JSON API, declared by its own
bundle (`baseUrl` in the settings object of
`/careercenter/1000624/static/index-*.js`): no browser is needed, and Core's
image deliberately carries no Chromium. The API answers 431 offers with the
department, the workplace, the publication date, the workload, a home-office
flag and the task and requirement texts.

So field-by-field parity with the producer is impossible AND undesirable: the
producer leaves those fields empty. What this adapter must not break is the
identity (the portal's own id and canonical link) and the rule that a failure
is never read as "no offers".
"""

import asyncio
import json
import logging
import random
from urllib.parse import urlsplit

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.providers.browser_headers import (
    BROWSER_HEADERS,
    MAX_PAGES_PARAM,
    page_budget,
)
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.providers.search_metadata import swiss_canton
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "stelle_admin"
# Declared by the portal's own bundle, not guessed.
API_URL = "https://ohws.prospective.ch/public/v1/medium/1000624/jobs"
PORTAL_HOST = "jobs.admin.ch"
LANG = "de"
PAGE_SIZE = 100
MAX_PAGES = 8  # 800 offers of the 431 published: ceiling, not a target
PAGE_PAUSE_S = 3.0  # = StelleAdminScraper.RATE_LIMIT_SECONDS
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TAGS = 15  # = BaseJobProvider.MAX_TAGS
MAX_DESCRIPTION_LENGTH = 5000
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
JITTER_RATIO = 0.5
# Attribute ids from the portal's own filter map (`filterIds` in its settings).
HOME_OFFICE_ATTRIBUTE = "125"

logger = logging.getLogger(__name__)


def _s(value) -> str:
    return value if isinstance(value, str) else ""


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _attribute(raw: dict, name: str) -> str:
    """First value of a portal attribute; attributes are lists of strings."""
    values = _object(raw.get("attributes")).get(name)
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return _s(values).strip()


def _sza(raw: dict, name: str) -> str:
    return _s(_object(raw.get("szas")).get(name)).strip()


def _identity(raw: dict) -> str:
    value = raw.get("id")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return ""
    value = str(value).strip()
    return value if value and len(value) <= 200 else ""


def _url(raw: dict) -> str:
    """The portal's own canonical link, or nothing.

    The host is validated: a link the API changed to somewhere else must not be
    persisted as a clickable offer URL.
    """
    link = _s(_object(raw.get("links")).get("directlink")).strip()
    if not link or "\\" in link:
        return ""
    try:
        parts = urlsplit(link)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or parts.username is not None:
        return ""
    host = parts.hostname or ""
    if host != PORTAL_HOST and not host.endswith("." + PORTAL_HOST):
        return ""
    if not parts.path.strip("/"):
        return ""
    return link


def _workload(raw: dict) -> str:
    low, high = _sza(raw, "sza_pensum.min"), _sza(raw, "sza_pensum.max")
    if low and high and low != high:
        return f"{low} - {high}%"
    return f"{high or low}%" if (high or low) else ""


def _description(raw: dict) -> str:
    parts = [_sza(raw, "sza_tasks"), _sza(raw, "sza_requirements")]
    text = strip_html_tags(" ".join(part for part in parts if part))
    return text[:MAX_DESCRIPTION_LENGTH]


def _content(raw: dict) -> dict:
    title = _s(raw.get("title")).strip() or _sza(raw, "sza_title")
    description = _description(raw)
    location = _attribute(raw, "arbeitsort") or _sza(raw, "sza_location.city")
    content = {
        "title": title,
        # The real hiring unit, which the scraper replaced with a constant.
        "company": _attribute(raw, "verwaltungseinheit")
        or "Swiss Federal Administration",
        "description": description,
        "location": location or "Switzerland",
        "remote": _attribute(raw, HOME_OFFICE_ATTRIBUTE).lower() == "true",
        "tags": extract_job_skills(title, description)[:MAX_TAGS],
        "salary": None,
    }
    canton = swiss_canton(content["location"])
    if canton:
        content["canton"] = canton
    workload = _workload(raw)
    if workload:
        content["employment_type"] = workload
    function = _attribute(raw, "funktion")
    if function:
        content["prof_group"] = function
    return content


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (
            _s(raw.get("title")).strip() or _sza(raw, "sza_title"),
            _attribute(raw, "verwaltungseinheit") or "Swiss Federal Administration",
        ),
    )


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds + random.uniform(0, seconds * JITTER_RATIO))


async def _read(http, offset: int) -> dict:
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with http.stream(
                "GET",
                API_URL,
                params={"lang": LANG, "offset": offset, "limit": PAGE_SIZE},
                headers=BROWSER_HEADERS,
                timeout=REQUEST_TIMEOUT_S,
                follow_redirects=True,
            ) as response:
                if response.status_code in RETRY_STATUSES:
                    raise httpx.HTTPStatusError(
                        "retryable response",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ProviderResponseError(
                            f"{SOURCE_NAME}: response exceeds byte budget"
                        )
                    chunks.append(chunk)
            body = b"".join(chunks)
            break
        except ProviderResponseError:
            raise
        except httpx.HTTPError as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                exc.response.status_code in RETRY_STATUSES
            )
            if not retryable or attempt == MAX_RETRIES:
                raise
            await _pause(RETRY_BACKOFF_S * (2**attempt))
    else:  # pragma: no cover - the loop either breaks or raises
        raise AssertionError("unreachable")
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise ProviderResponseError(f"{SOURCE_NAME}: unreadable JSON body") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ProviderResponseError(f"{SOURCE_NAME}: response carries no jobs list")
    return payload


class StelleAdminProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ()

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError("stelle_admin scope takes no query parameters")
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        by_identity: dict[str, dict] = {}
        pages = seen = invalid = 0
        announced = 0
        exhausted = False

        for page in range(budget):
            if pages:
                await _pause(PAGE_PAUSE_S)
            payload = await _read(http, page * PAGE_SIZE)
            pages += 1
            total = payload.get("total")
            if type(total) is int and total >= 0:
                announced = total
            rows = payload["jobs"]
            seen += len(rows)
            for raw in rows:
                if not isinstance(raw, dict):
                    invalid += 1
                    continue
                identity = _identity(raw)
                url = _url(raw)
                title = _s(raw.get("title")).strip() or _sza(raw, "sza_title")
                if not identity or not url or not title:
                    invalid += 1
                    continue
                by_identity.setdefault(identity, raw)
            if not rows or (announced and (page + 1) * PAGE_SIZE >= announced):
                exhausted = True
                break

        if not by_identity:
            if seen:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: nonempty response has no usable identities"
                )
            raise ProviderResponseError(f"{SOURCE_NAME}: sweep produced no offers")
        if invalid:
            logger.warning("%s: %d unusable rows", SOURCE_NAME, invalid)
        listings = tuple(
            RawListing(external_id="id:" + identity, url=_url(raw), payload=raw)
            for identity, raw in by_identity.items()
        )
        return FetchResult(
            listings,
            {
                "pages": pages,
                "items_seen": seen,
                "announced": announced,
                "identities": len(by_identity),
            },
            pages_fetched=pages,
            complete=exhausted and not invalid,
            error="invalid_stelle_admin_rows" if invalid else None,
        )

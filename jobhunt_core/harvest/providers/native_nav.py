"""NAV public search, one remote facet per scope (no legacy imports).

Raw Elasticsearch hits are retained. Offset advances by actual rows received;
a page/count contradiction is incomplete, never a successful empty harvest.
Two scopes, one per REMOTE_FACETS value, reproduce the previous coverage.
"""

import asyncio
import time
import uuid

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.providers.browser_headers import (
    BROWSER_HEADERS,
    MAX_PAGES_PARAM,
    page_budget,
)
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "nav_arbeidsplassen"
API_URL = "https://arbeidsplassen.nav.no/stillinger/api/search"
DETAIL_URL_PREFIX = "https://arbeidsplassen.nav.no/stillinger/stilling/"
REMOTE_FACETS = ("Kun hjemmekontor", "Delvis hjemmekontor")
PAGE_SIZE = 100
MAX_PAGES = 100
# The public endpoint returned 429 during the local parity sweep at 0.5 s/page.
# Conservative pacing, not a claim about an undocumented upstream quota.
# A 429 ends this sweep; never retry it immediately or report completeness.
PAGE_PAUSE_S = 10
SWEEP_BUDGET_S = 600
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _dict(value):
    return value if isinstance(value, dict) else {}


def _content(raw):
    source = _dict(raw.get("_source"))
    title = _text(source.get("title"))
    company = _text(source.get("businessName")) or _text(
        _dict(source.get("employer")).get("name")
    )
    description = strip_html_tags(
        _text(_dict(source.get("generatedSearchMetadata")).get("shortSummary"))
    )
    locations = source.get("locationList")
    location = ""
    for loc in locations if isinstance(locations, list) else []:
        if not isinstance(loc, dict):
            continue
        seen, parts = set(), []
        for candidate in (
            loc.get("city") or loc.get("municipal"),
            loc.get("county"),
            loc.get("country"),
        ):
            value = _text(candidate)
            if value and value.lower() not in seen:
                seen.add(value.lower())
                parts.append(value)
        if parts:
            location = ", ".join(parts)
            break
    tags = _dict(source.get("properties")).get("searchtagsai")
    tags = tags if isinstance(tags, list) else []
    merged, seen = [], set()
    for value in tags + extract_job_skills(title, description):
        tag = _text(value)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            merged.append(tag)
    return {
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "remote": True,
        "tags": merged[:15],
    }


def register_handlers():
    register_normalizer(SOURCE_NAME, _content)

    def identity(raw):
        content = _content(raw)
        return content["title"], content["company"]

    register_extractor(SOURCE_NAME, identity)


def _listing(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("_source"), dict):
        return None
    identity = _text(raw.get("_id"))
    try:
        parsed = uuid.UUID(identity)
    except (ValueError, AttributeError):
        return None
    # Public contract is canonical UUID, not arbitrary URL path or compact hex.
    if str(parsed) != identity:
        return None
    return RawListing(identity, DETAIL_URL_PREFIX + identity, raw)


async def _page(http, facet, offset, timeout):
    async with asyncio.timeout(timeout):
        async with http.stream(
            "GET",
            API_URL,
            params={"from": offset, "size": PAGE_SIZE, "remote": facet},
            timeout=25,
            follow_redirects=True,
            headers=BROWSER_HEADERS,
        ) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ProviderResponseError("NAV response byte budget exceeded")
                chunks.append(chunk)
    try:
        body = httpx.Response(200, content=b"".join(chunks)).json()
    except (ValueError, UnicodeError) as exc:
        raise ProviderResponseError("NAV invalid JSON") from exc
    block = body.get("hits") if isinstance(body, dict) else None
    if not isinstance(block, dict) or not isinstance(block.get("hits"), list):
        raise ProviderResponseError("NAV invalid hits envelope")
    total = block.get("total")
    relation = "eq"
    if isinstance(total, dict):
        relation = total.get("relation")
        total = total.get("value")
    if type(total) is not int or total < 0 or relation not in {"eq", "gte"}:
        raise ProviderResponseError("NAV invalid total")
    rows = block["hits"]
    if not rows and relation == "eq" and offset < total:
        raise ProviderResponseError("NAV empty page contradicts total")
    if relation == "eq" and rows and offset + len(rows) > total:
        raise ProviderResponseError("NAV rows exceed exact total")
    return rows, not rows or (relation == "eq" and offset + len(rows) >= total)


class NavProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ("remote",)

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if (
            not isinstance(params, dict)
            or set(params) - {MAX_PAGES_PARAM} != {"remote"}
            or params["remote"] not in REMOTE_FACETS
        ):
            raise ProviderConfigError(
                "NAV requires one supported remote facet per scope"
            )
        # The retiring producer reads three pages (nav_arbeidsplassen.py:56).
        # Declaring that same budget keeps parity AND keeps the sweep honest:
        # finishing it is complete, not truncated.
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        started = time.monotonic()
        listings, pages, offset, invalid = [], 0, 0, 0
        exhausted, error = False, None
        for page in range(budget):
            remaining = SWEEP_BUDGET_S - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                rows, exhausted = await _page(
                    http, params["remote"], offset, min(25, remaining)
                )
            except (httpx.HTTPError, ProviderResponseError, TimeoutError) as exc:
                if not listings:
                    raise
                error = (
                    f"http_{exc.response.status_code}"
                    if isinstance(exc, httpx.HTTPStatusError)
                    else type(exc).__name__
                )
                break
            pages += 1
            offset += len(rows)
            for row in rows:
                listing = _listing(row)
                if listing is None:
                    invalid += 1
                else:
                    listings.append(listing)
            if exhausted:
                break
            if page + 1 < budget:
                remaining = SWEEP_BUDGET_S - (time.monotonic() - started)
                if remaining <= 0:
                    break
                await asyncio.sleep(min(PAGE_PAUSE_S, remaining))
        if offset and not listings:
            raise ProviderResponseError("NAV nonempty feed has no usable identities")
        error = error or ("invalid_nav_items" if invalid else None)
        swept = exhausted or (MAX_PAGES_PARAM in params and pages >= budget)
        return FetchResult(
            tuple(listings),
            {"items_seen": offset, "pages": pages},
            pages_fetched=pages,
            complete=swept and error is None,
            error=error,
        )

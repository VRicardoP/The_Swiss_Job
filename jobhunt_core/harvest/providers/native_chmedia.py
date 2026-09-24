"""Ostjob/Zentraljob native producers, with verified public API pagination.

No source activation and no legacy imports. The portal uses pageSize, NOT size;
its pages metadata, NOT a short page, establishes completeness. Requests have
a fixed page/time/byte budget; exceeding it is visibly incomplete.
Every run refreshes from page one: offset pagination is mutable, not a durable
cursor. The task/runner owns retries, admission, persistence and fencing.
"""

import asyncio
import time
from urllib.parse import urlsplit

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.providers.search_metadata import swiss_canton
from jobhunt_core.harvest.types import FetchResult, RawListing

DOMAINS = {"ostjob": "ostjob.ch", "zentraljob": "zentraljob.ch"}
PAGE_SIZE = 100  # Public API verified: 100 items, 65 pages for 6,453 offers.
MAX_PAGES = 100
PAGE_PAUSE_S = 0.5
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SWEEP_BUDGET_S = 600  # Absolute request deadlines are clipped to this budget.


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _http_url(value):
    value = _text(value)
    if "\\" in value:
        return ""
    try:
        value.encode("utf-8")
        parsed = urlsplit(value)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            return value
    except ValueError:
        pass
    return ""


def _listing(name, raw):
    if not isinstance(raw, dict):
        return None
    # externalId is the employer's ATS id, NOT unique within this portal.
    # Application URLs can also be shared by unrelated positions. The portal
    # numeric id is unique and /stelle/<id> redirects to its current detail.
    portal_id = raw.get("id")
    if isinstance(portal_id, str) and portal_id.isascii() and portal_id.isdecimal():
        portal_id = int(portal_id) if len(portal_id) <= 20 else None
    if type(portal_id) is not int or not 0 < portal_id < 2**63:
        return None
    url = f"https://{DOMAINS[name]}/stelle/{portal_id}"
    apply_url = (
        _http_url(raw.get("urlApplication"))
        or _http_url(raw.get("urlDescription"))
        or None
    )
    return RawListing(f"id:{portal_id}", url, raw, apply_url=apply_url)


def _content(raw):
    title = _text(raw.get("title"))
    company = raw.get("company")
    company = _text(company.get("name")) if isinstance(company, dict) else ""
    city = _text(raw.get("workplaceCity"))
    cantons = raw.get("cantons")
    canton = _text(cantons[0]) if isinstance(cantons, list) and cantons else ""
    location = (
        f"{city}, {canton}" if city and canton else city or canton or "Switzerland"
    )
    description = strip_html_tags(_text(raw.get("activity")))
    keywords = [k.strip() for k in _text(raw.get("keywords")).split(",") if k.strip()]
    tags = list(dict.fromkeys(keywords + extract_job_skills(title, description)))[:15]
    return {
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "tags": tags,
        "remote": raw.get("homeOffice", False),
        # Same rule as the retiring writer (base_chmedia.py:74): the portal
        # code when it already ships one, otherwise resolved from the
        # composed location. Saved searches filter on this field.
        "canton": canton if len(canton) == 2 else swiss_canton(location),
    }


def register_handlers():
    for name in DOMAINS:
        register_normalizer(name, _content)

        def identity(raw):
            content = _content(raw)
            return content["title"], content["company"]

        register_extractor(name, identity)


async def _page(http, url, page):
    async with http.stream(
        "GET",
        url,
        params={"page": page, "pageSize": PAGE_SIZE},
        timeout=25,
        follow_redirects=True,
        headers={"User-Agent": "SwissJobHunter/1.0"},
    ) as response:
        response.raise_for_status()
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ProviderResponseError("CH Media response exceeds byte budget")
            chunks.append(chunk)
    try:
        body = httpx.Response(200, content=b"".join(chunks)).json()
    except (ValueError, UnicodeError) as exc:
        raise ProviderResponseError("CH Media invalid JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise ProviderResponseError("CH Media invalid items envelope")
    total_pages = body.get("pages")
    if type(total_pages) is not int or total_pages < 0:
        raise ProviderResponseError("CH Media invalid pages metadata")
    rows = body["items"]
    if not rows and total_pages > page:
        raise ProviderResponseError("CH Media empty page contradicts remaining pages")
    if rows and total_pages < page:
        raise ProviderResponseError("CH Media rows outside declared page range")
    return rows, page >= total_pages


class CHMediaProvider(BaseProvider):
    def __init__(self, name):
        if name not in DOMAINS:
            raise ProviderConfigError("Unsupported CH Media source")
        self.name = name
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or params:
            raise ProviderConfigError("CH Media scope has no query parameters")
        listings, pages, seen, invalid = [], 0, 0, 0
        exhausted, error = False, None
        url = f"https://api.{DOMAINS[self.name]}/public/vacancy/search/"
        started = time.monotonic()
        for page in range(1, MAX_PAGES + 1):
            remaining = SWEEP_BUDGET_S - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(min(25, remaining)):
                    rows, exhausted = await _page(http, url, page)
            except (httpx.HTTPError, ProviderResponseError, TimeoutError) as exc:
                if not listings:
                    raise
                error = type(exc).__name__
                break
            pages += 1
            seen += len(rows)
            for raw in rows:
                listing = _listing(self.name, raw)
                if listing is None:
                    invalid += 1
                else:
                    listings.append(listing)
            if exhausted:
                break
            if page < MAX_PAGES:
                await asyncio.sleep(PAGE_PAUSE_S)
        if seen and not listings:
            raise ProviderResponseError(
                "CH Media nonempty feed has no usable identities"
            )
        error = error or ("invalid_chmedia_items" if invalid else None)
        return FetchResult(
            tuple(listings),
            {"items_seen": seen, "pages": pages},
            pages_fetched=pages,
            complete=exhausted and error is None,
            error=error,
        )

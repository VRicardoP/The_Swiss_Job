"""IrishJobs/Jobs.ie native producer using StepStone's public result-list API.

Both sites expose ``POST /public-api/resultlist/unifiedResultlist``. The JSON
contract avoids the HTML route that rejects datacentre addresses while keeping
the same listings, stable platform identity, page budget and pacing as the
retiring scraper.
"""

import asyncio
import json
import re
from urllib.parse import urljoin, urlsplit

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
    blocking_status,
)
from jobhunt_core.harvest.providers.browser_headers import MAX_PAGES_PARAM, page_budget
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "irishjobs"
SITES = (
    ("https://www.irishjobs.ie", 301),
    ("https://www.jobs.ie", 302),
)
LISTING_PATH = "/jobs/work-from-home"
API_PATH = "/public-api/resultlist/unifiedResultlist"
API_HEADERS = {"Accept": "application/json"}
MAX_PAGES = 8
PAGE_PAUSE_S = 2.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SWEEP_BUDGET_S = 300
REQUEST_TIMEOUT_S = 40

_ALLOWED_HOSTNAMES = frozenset(urlsplit(site).hostname or "" for site, _ in SITES)
_PLATFORM_ID = re.compile(r"-job(\d+)/?$")


def _s(value):
    return value if isinstance(value, str) else ""


def canonical_identity_url(url):
    """Reduce a listing URL to its stable StepStone platform id."""
    url = url.strip()
    match = _PLATFORM_ID.search(url)
    return f"{SITES[0][0]}/job/job{match.group(1)}" if match else url


def _resolve_job_url(raw, site):
    """Return a rebuilt offer URL on one of the two declared sites."""
    if not raw:
        return None
    if "\\" in raw or "%5c" in raw.lower():
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        return None
    try:
        parts = urlsplit(urljoin(f"{site}/", raw))
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme not in ("http", "https")
        or parts.username is not None
        or port is not None
    ):
        return None
    hostname = parts.hostname or ""
    if hostname not in _ALLOWED_HOSTNAMES or not parts.path or parts.path == "/":
        return None
    return f"https://{hostname}{parts.path}"


def _items(body):
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProviderResponseError("irishjobs: unreadable result-list JSON") from exc
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ProviderResponseError("irishjobs: result-list items is not a list")
    return items


def _listing(item, site):
    if not isinstance(item, dict):
        return None
    title = _s(item.get("title")).strip()
    url = _resolve_job_url(_s(item.get("url")).strip(), site)
    if not title or url is None:
        return None
    return RawListing("url:" + canonical_identity_url(url), url, item)


def _content(raw):
    title = _s(raw.get("title")).strip()
    description = strip_html_tags(_s(raw.get("textSnippet")))
    return {
        "title": title,
        "company": _s(raw.get("companyName")).strip() or "Unknown",
        "description": description,
        "location": _s(raw.get("location")).strip() or "Ireland",
        "remote": True,
        "tags": extract_job_skills(title, description)[:15],
        "salary": _s(raw.get("salary")).strip() or None,
    }


def register_handlers():
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (
            _s(raw.get("title")).strip(),
            _s(raw.get("companyName")).strip() or "Unknown",
        ),
    )


async def _page(http, site, site_id, page):
    search_url = f"{site}{LISTING_PATH}?page={page}"
    async with http.stream(
        "POST",
        f"{site}{API_PATH}",
        json={"url": search_url, "lang": "en", "siteId": site_id, "userData": {}},
        timeout=REQUEST_TIMEOUT_S,
        follow_redirects=True,
        headers=API_HEADERS,
    ) as response:
        response.raise_for_status()
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ProviderResponseError("irishjobs response exceeds byte budget")
            chunks.append(chunk)
    return _items(b"".join(chunks))


class IrishJobsProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ()

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError("irishjobs scope takes no query parameters")
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        by_identity, invalid, pages, seen = {}, 0, 0, 0
        error, exhausted = None, True
        first_failure = None
        loop = asyncio.get_running_loop()
        started = loop.time()
        for site, site_id in SITES:
            for page in range(1, budget + 1):
                if loop.time() - started >= SWEEP_BUDGET_S:
                    error, exhausted = error or "time_budget", False
                    break
                try:
                    items = await _page(http, site, site_id, page)
                except Exception as exc:
                    if first_failure is None:
                        first_failure = exc
                    error, exhausted = error or type(exc).__name__, False
                    break
                pages += 1
                seen += len(items)
                if not items:
                    break
                for item in items:
                    listing = _listing(item, site)
                    if listing is None:
                        invalid += 1
                    else:
                        by_identity.setdefault(listing.external_id, listing)
                if page < budget:
                    await asyncio.sleep(PAGE_PAUSE_S)
                else:
                    exhausted = False
            if site != SITES[-1][0]:
                await asyncio.sleep(PAGE_PAUSE_S * 2)
        listings = tuple(by_identity.values())
        if not listings and first_failure is not None:
            raise first_failure
        if seen and not listings:
            raise ProviderResponseError(
                "irishjobs: nonempty feed has no usable identities"
            )
        error = error or ("invalid_irishjobs_items" if invalid else None)
        exhausted = exhausted or (MAX_PAGES_PARAM in params and pages >= budget)
        return FetchResult(
            listings,
            {"pages": pages, "items_seen": seen},
            pages_fetched=pages,
            complete=exhausted and error is None,
            error=error,
            # El barrido lo cortó `first_failure`: si fue un 403/429/503 del
            # portal, cuenta como bloqueo (DT-148 A2).
            blocked_with=blocking_status(first_failure),
        )

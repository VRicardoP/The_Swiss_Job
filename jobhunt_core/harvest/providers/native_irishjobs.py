"""IrishJobs/Jobs.ie native producer — the StepStone blob parse of its scraper.

IrishJobs.ie and Jobs.ie are one StepStone platform on two hosts, harvested as
a single source and deduplicated by platform id. Rules copied, not imported
(see backend/scrapers/irishjobs.py); each one is a defect already paid for:

- The state anchor demands `] = ` so it cannot match the read-only references
  or the unrelated "google-onetap" blob living on the same page.
- The literal is read by BALANCED braces, respecting JSON strings: a `}` inside
  a string used to truncate it.
- Identity is the platform id, not the URL: the portal re-edits the slug of a
  live posting (measured: 40 clone rows out of 919) and URL identity turned each
  edit into a clone whose original stopped refreshing. The published URL stays
  the real one.
- Item URLs are re-resolved and rebuilt: an absolute URL to any other host used
  to reach the user as a clickable link, and every URL variant was a new row.
- A 200 whose blob we cannot read is a FAILURE, never "0 offers". Only
  `items: []` is a legitimate empty page (verified live on both hosts).
- Salary amounts stay in the raw payload in EUR/GBP: pre-filling the CHF fields
  would store them unconverted (€22/h becoming 22 CHF/year).
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
)
from jobhunt_core.harvest.providers.browser_headers import (
    BROWSER_HEADERS,
    MAX_PAGES_PARAM,
    page_budget,
)
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "irishjobs"
HOSTS = ("https://www.irishjobs.ie", "https://www.jobs.ie")
LISTING_PATH = "/jobs/work-from-home"
# Eight pages per host and a 2 s pause, exactly like the retiring scraper
# (irishjobs.py:271-272). Asking for a ninth is what made the portal stop
# answering: the live sweep read 8 pages and then timed out, twice.
MAX_PAGES = 8
PAGE_PAUSE_S = 2.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SWEEP_BUDGET_S = 300
# Pages weigh ~1 MB and the platform slows down after a run of them.
# Measured from the NAS: a single page takes 0.4-1.3 s, so this is head
# room for that slowdown, not a way around it.
REQUEST_TIMEOUT_S = 40

_ALLOWED_HOSTNAMES = frozenset(urlsplit(host).hostname or "" for host in HOSTS)
_STATE_ANCHOR = re.compile(
    r"""window\.__PRELOADED_STATE__\[\s*["']app-unifiedResultlist["']\s*\]\s*=\s*"""
)
_PLATFORM_ID = re.compile(r"-job(\d+)/?$")
_SCRIPT = re.compile(r"<script[^>]*>(.*?)</script>", re.S)


def _s(value):
    return value if isinstance(value, str) else ""


def canonical_identity_url(url):
    """URL reduced to the platform id, for a STABLE identity.

    The host is normalised to HOSTS[0]: the StepStone id is global, not
    per-host (verified: 0 ids repeated across hosts over 919 live rows).
    Without a recognisable `-job<id>` the URL is returned as-is — a volatile
    identity is preferable to an ambiguous one.
    """
    url = url.strip()
    match = _PLATFORM_ID.search(url)
    return f"{HOSTS[0]}/job/job{match.group(1)}" if match else url


def _resolve_job_url(raw, host):
    """Absolute, rebuilt offer URL, or None. Never a link to a foreign host."""
    if not raw:
        return None
    # urllib only breaks the netloc on / ? # ; browsers also on \ (and %5C),
    # so `https://evil.com\@www.irishjobs.ie/x` would navigate to evil.com.
    if "\\" in raw or "%5c" in raw.lower():
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        return None
    try:
        parts = urlsplit(urljoin(f"{host}/", raw))
        port = parts.port  # validated on access; raises on a bad port
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
    # Rebuilt without query/fragment/port: each variant would be another row.
    return f"https://{hostname}{parts.path}"


def _balanced_object(text, start):
    """The balanced `{...}` literal starting at `start`, respecting strings."""
    if start >= len(text) or text[start] != "{":
        return None
    depth, in_str, escaped = 0, False, False
    for i in range(start, len(text)):
        char = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _state(body):
    for script in _SCRIPT.findall(body):
        match = _STATE_ANCHOR.search(script)
        if match is None:
            continue
        literal = _balanced_object(script, match.end())
        if literal is None:
            raise ProviderResponseError("irishjobs: truncated state literal")
        try:
            return json.loads(literal)
        except (ValueError, RecursionError) as exc:
            raise ProviderResponseError("irishjobs: unreadable state literal") from exc
    raise ProviderResponseError("irishjobs: no state script found")


def _items(body):
    data = _state(body)
    results = data.get("searchResults") if isinstance(data, dict) else None
    if not isinstance(results, dict):
        raise ProviderResponseError("irishjobs: searchResults is not an object")
    items = results.get("items")
    if not isinstance(items, list):
        raise ProviderResponseError("irishjobs: searchResults.items is not a list")
    return items


def _listing(item, host):
    if not isinstance(item, dict):
        return None
    title = _s(item.get("title")).strip()
    url = _resolve_job_url(_s(item.get("url")).strip(), host)
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
        # Derived from the /jobs/work-from-home scope, not from the item.
        "remote": True,
        "tags": extract_job_skills(title, description)[:15],
        # EUR/GBP text only; no CHF amount is invented at this boundary.
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


async def _page(http, host, page):
    url = f"{host}{LISTING_PATH}"
    async with http.stream(
        "GET",
        url,
        params={"page": page},
        timeout=REQUEST_TIMEOUT_S,
        follow_redirects=True,
        headers=BROWSER_HEADERS,
    ) as response:
        response.raise_for_status()
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ProviderResponseError("irishjobs response exceeds byte budget")
            chunks.append(chunk)
    return _items(b"".join(chunks).decode("utf-8", "replace"))


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
        loop = asyncio.get_running_loop()
        started = loop.time()
        for host in HOSTS:
            for page in range(1, budget + 1):
                if loop.time() - started >= SWEEP_BUDGET_S:
                    error, exhausted = error or "time_budget", False
                    break
                try:
                    items = await _page(http, host, page)
                except Exception as exc:
                    if not by_identity:
                        raise
                    error, exhausted = type(exc).__name__, False
                    break
                pages += 1
                seen += len(items)
                if not items:
                    break
                for item in items:
                    listing = _listing(item, host)
                    if listing is None:
                        invalid += 1
                    else:
                        # One platform id, one listing: the two hosts publish
                        # the same opening. First host seen wins, as before.
                        by_identity.setdefault(listing.external_id, listing)
                if page < budget:
                    await asyncio.sleep(PAGE_PAUSE_S)
                else:
                    exhausted = False
            # Both hosts are one platform. Moving to the second one straight
            # after eight pages of the first is what timed out twice live; the
            # retiring scraper rarely asks for sixteen in a row because its
            # incremental cursor stops it earlier.
            if host != HOSTS[-1]:
                await asyncio.sleep(PAGE_PAUSE_S * 2)
        listings = tuple(by_identity.values())
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
        )

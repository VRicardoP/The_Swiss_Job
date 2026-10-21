"""schuljobs.ch — the SSR listing, its AJAX scroll and the JSON-LD detail.

Rules copied, not imported, each because the producer needed it
(`backend/scrapers/schuljobs.py`):

- The board pages by an AJAX scroll endpoint keyed by a `searchhash` the first
  page carries. No hash means no pagination, said out loud rather than guessed.
- `data-nextpage` and the fragment's `nextpage` are cast SAFELY: `int("")` used
  to take down the whole sweep and lose the details already downloaded.
- A scroll response that is not a JSON object, or carries no `html`, ends the
  sweep; a FAILURE on a scroll page is reported, because learning only the
  first page would make the next run's early-stop skip the scroll pages
  forever.
- Only half the detail pages carry a JSON-LD JobPosting (41 of 83 measured on
  2026-10-15), so a detail without it is normal: the listing stub is already a
  usable offer and the sweep does not fail over it.

The declared sweep is bounded by the scope's `max_pages`, and the provisioner
declares four pages — the initial listing plus three scroll pages, which is the
83 offers measured live and about twice what the producer's dynamic budget
fetches. The ceiling is 26 pages; raising the sweep is a scope decision, not a
code change.
"""

import asyncio
import json
import logging
import random
import re

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
from jobhunt_core.harvest.providers.html_response import TagStackParser
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.providers.search_metadata import swiss_canton
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "schuljobs"
BASE_URL = "https://www.schuljobs.ch"
LISTING_URL = f"{BASE_URL}/suche"
SCROLL_URL = f"{BASE_URL}/scroll/searchhash/{{searchhash}}/page/{{page}}"
SCROLL_PAGE_SIZE = 20  # = SchulJobsScraper.PAGE_SIZE
MAX_PAGES = 26  # = 1 + SchulJobsScraper.MAX_SCROLL_PAGES
PAGE_PAUSE_S = 2.0  # = SchulJobsScraper.RATE_LIMIT_SECONDS
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_DETAIL_BYTES = 4 * 1024 * 1024
MAX_DETAIL_FETCHES = MAX_PAGES * SCROLL_PAGE_SIZE
SWEEP_BUDGET_S = 900
MAX_TAGS = 15  # = BaseJobProvider.MAX_TAGS
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
JITTER_RATIO = 0.5

_JOB_URL_RE = re.compile(r"^/job/[^/]+/([A-Za-z0-9]+)$")
_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_JSON_LD_TYPE_RE = re.compile(r"\btype\s*=\s*(['\"])application/ld\+json\1", re.I)

logger = logging.getLogger(__name__)


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def safe_int(value) -> int:
    """`int()` that cannot take a sweep down: anything unusable is zero."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else 0
    return 0


def job_identity(url: str) -> str:
    """The portal's own job id, or nothing.

    The host is validated and the path must be the canonical offer shape: a
    listing link pointing anywhere else is not an offer of this board.
    """
    if not url or "\\" in url:
        return ""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or parts.username is not None:
        return ""
    host = parts.hostname or ""
    if host != "schuljobs.ch" and not host.endswith(".schuljobs.ch"):
        return ""
    match = _JOB_URL_RE.match(parts.path)
    return match.group(1) if match else ""


class _Listing(TagStackParser):
    """`a.js-joboffer-detail` stubs, plus the scroll keys the page carries.

    The metadata lives in the card's `<p>` as "ZH · City · Company", exactly as
    the producer reads it.
    """

    def __init__(self):
        super().__init__()
        # DT-148 B4: `stubs` nunca se escribía (las tarjetas viven en `_cards`)
        # y `_open` SOMBREABA con otra lista vacía la pila de `TagStackParser`,
        # que es la que de verdad se usa. Dos campos que parecían estado.
        self.searchhash = ""
        self.nextpage = ""
        self._cards: list[dict] = []
        self._link: dict | None = None
        self._link_depth = 0
        self._chunks: list[str] = []
        self._paragraph_depth = 0
        self._paragraph: list[str] = []

    def on_open(self, tag, classes, attrs, depth):
        if not self.searchhash and "data-searchhash" in attrs:
            self.searchhash = attrs.get("data-searchhash") or ""
        if not self.nextpage and "data-nextpage" in attrs:
            self.nextpage = attrs.get("data-nextpage") or ""
        if tag == "a" and "js-joboffer-detail" in classes and self._link is None:
            self._link = {"href": attrs.get("href", ""), "title": ""}
            self._link_depth = depth
            self._chunks = []
            return
        if tag == "p" and not self._paragraph_depth and self._cards:
            self._paragraph_depth = depth
            self._paragraph = []

    def on_close(self, tag, depth):
        if self._link is not None and self._link_depth >= depth:
            self._link["title"] = "".join(self._chunks)
            self._cards.append({"link": self._link, "meta": ""})
            self._link = None
        if self._paragraph_depth and self._paragraph_depth >= depth:
            meta = "".join(self._paragraph)
            # The first paragraph after a link belongs to that card.
            for card in reversed(self._cards):
                if not card["meta"]:
                    card["meta"] = meta
                    break
            self._paragraph_depth = 0

    def on_text(self, data):
        stripped = data.strip()
        if not stripped:
            return
        if self._link is not None:
            self._chunks.append(stripped)
        elif self._paragraph_depth:
            self._paragraph.append(stripped)

    def rows(self) -> list[dict]:
        out = []
        for card in self._cards:
            title = card["link"]["title"].strip()
            href = card["link"]["href"]
            if not title or not href:
                continue
            url = href if href.startswith("http") else f"{BASE_URL}{href}"
            canton, location, company = _meta_parts(card["meta"])
            out.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "canton": canton,
                    "url": url,
                }
            )
        return out


def _meta_parts(meta: str) -> tuple[str | None, str, str]:
    """ "ZH · City · Company" — a two-letter head is the canton, never a city."""
    parts = [part.strip() for part in meta.split("·")]
    if len(parts) >= 3:
        canton = parts[0] if len(parts[0]) == 2 else None
        return canton, parts[1], parts[2]
    if len(parts) == 2:
        canton = parts[0] if len(parts[0]) == 2 else None
        return canton, parts[1], "Unknown"
    return None, "", "Unknown"


def parse_listing(html: str) -> _Listing:
    parser = _Listing()
    parser.feed(html)
    parser.close()
    return parser


def parse_detail(html: str) -> dict:
    """The detail page's JSON-LD JobPosting, or {} when it has none.

    Half the pages carry no JobPosting at all (41 of 83 measured), so an empty
    result is normal and the listing stub stands on its own.
    """
    for attributes, body in _SCRIPT_RE.findall(html):
        if not _JSON_LD_TYPE_RE.search(attributes):
            continue
        try:
            data = json.loads(body)
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("@type") != "JobPosting":
            continue
        detail = {}
        if _text(data.get("title")):
            detail["title"] = _text(data["title"])
        organisation = _object(data.get("hiringOrganization"))
        if _text(organisation.get("name")):
            detail["company"] = _text(organisation["name"])
        if _text(organisation.get("logo")):
            # Only a real logo: the producer stores it when truthy, and an
            # empty string would travel as a logo that is not there.
            detail["logo"] = organisation["logo"]
        address = _object(_object(data.get("jobLocation")).get("address"))
        if _text(address.get("addressLocality")):
            detail["location"] = _text(address["addressLocality"])
        if _text(address.get("addressRegion")):
            detail["canton"] = _text(address["addressRegion"])
        if isinstance(data.get("description"), str):
            detail["description"] = strip_html_tags(data["description"])
        if data.get("employmentType"):
            # Kept VERBATIM, list included: schema.org allows several values
            # ("FULL_TIME", "TEMPORARY") and the raw payload must stay the
            # portal's. `_content` is what renders it.
            detail["employment_type"] = data["employmentType"]
        if _text(data.get("datePosted")):
            detail["date_posted"] = _text(data["datePosted"])
        return detail
    return {}


def _employment_type(value) -> str:
    """schema.org's employmentType, which may be a list.

    The producer carried the list straight into a string column and the value
    was lost; here the several values are rendered, which is what the portal
    means.
    """
    if isinstance(value, list):
        return ", ".join(_text(item) for item in value if _text(item))
    return _text(value)


def _content(raw: dict) -> dict:
    title = _text(raw.get("title"))
    description = _text(raw.get("description"))
    location = _text(raw.get("location")) or "Switzerland"
    content = {
        "title": title,
        "company": _text(raw.get("company")) or "Unknown",
        "description": description,
        "location": location,
        "remote": False,
        "tags": extract_job_skills(title, description)[:MAX_TAGS],
        "salary": None,
    }
    # The listing already names the canton; the detail may override it, and
    # only if neither does is it derived from the city.
    canton = _text(raw.get("canton")) or swiss_canton(location)
    if canton:
        content["canton"] = canton
    employment_type = _employment_type(raw.get("employment_type"))
    if employment_type:
        content["employment_type"] = employment_type
    logo = raw.get("logo")
    if isinstance(logo, str):
        content["logo"] = logo
    return content


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (_text(raw.get("title")), _text(raw.get("company")) or "Unknown"),
    )


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds + random.uniform(0, seconds * JITTER_RATIO))


async def _read(http, url: str, byte_limit: int, *, ajax: bool = False) -> str:
    headers = dict(BROWSER_HEADERS)
    if ajax:
        # The frontend's own scroll request carries it and the endpoint expects it.
        headers["X-Requested-With"] = "XMLHttpRequest"
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with http.stream(
                "GET",
                url,
                headers=headers,
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
                    if size > byte_limit:
                        raise ProviderResponseError(
                            f"{SOURCE_NAME}: response exceeds byte budget"
                        )
                    chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", "replace")
        except ProviderResponseError:
            raise
        except httpx.HTTPError as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                exc.response.status_code in RETRY_STATUSES
            )
            if not retryable or attempt == MAX_RETRIES:
                raise
            await _pause(RETRY_BACKOFF_S * (2**attempt))
    raise AssertionError("unreachable")


class SchulJobsProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ()

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError("schuljobs scope takes no query parameters")
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        by_identity: dict[str, dict] = {}
        pages = invalid = 0
        error = None
        loop = asyncio.get_running_loop()
        started = loop.time()

        listing = parse_listing(await _read(http, LISTING_URL, MAX_RESPONSE_BYTES))
        pages += 1
        rows, invalid = _collect(listing.rows(), by_identity, invalid)
        if not rows and not by_identity:
            raise ProviderResponseError(f"{SOURCE_NAME}: the listing carries no offers")

        searchhash = listing.searchhash
        next_page = safe_int(listing.nextpage)
        if not searchhash:
            # Said out loud: without the hash there is no pagination, and the
            # sweep is the first page only.
            logger.warning("%s: no searchhash on the listing page", SOURCE_NAME)
        while searchhash and next_page and pages < budget:
            if loop.time() - started >= SWEEP_BUDGET_S:
                error = error or "time_budget"
                break
            await _pause(PAGE_PAUSE_S)
            url = SCROLL_URL.format(searchhash=searchhash, page=next_page)
            try:
                body = await _read(http, url, MAX_RESPONSE_BYTES, ajax=True)
            except Exception:
                # A failure mid-scroll is reported: learning only the first
                # page would leave the scroll pages unfetched forever.
                logger.warning("%s: scroll page %d failed", SOURCE_NAME, next_page)
                error = error or "scroll_failed"
                break
            pages += 1
            try:
                data = json.loads(body)
            except ValueError:
                error = error or "scroll_not_json"
                break
            if not isinstance(data, dict):
                error = error or "scroll_not_object"
                break
            fragment = data.get("html")
            if not isinstance(fragment, str) or not fragment:
                break
            page_rows = parse_listing(fragment).rows()
            fresh, invalid = _collect(page_rows, by_identity, invalid)
            next_page = safe_int(data.get("nextpage"))
            if len(page_rows) < SCROLL_PAGE_SIZE or not fresh:
                break

        details = 0
        for identity, row in by_identity.items():
            if details >= MAX_DETAIL_FETCHES:
                error = error or "detail_budget"
                break
            if loop.time() - started >= SWEEP_BUDGET_S:
                error = error or "time_budget"
                break
            await _pause(PAGE_PAUSE_S)
            details += 1
            try:
                row.update(
                    parse_detail(await _read(http, row["url"], MAX_DETAIL_BYTES))
                )
            except Exception as exc:
                # The listing stub is already a usable offer.
                logger.warning(
                    "%s: detail fetch failed for %r (%s)",
                    SOURCE_NAME,
                    identity,
                    type(exc).__name__,
                )

        if invalid:
            logger.warning("%s: %d links that are not offers", SOURCE_NAME, invalid)
        listings = tuple(
            RawListing(external_id="id:" + identity, url=row["url"], payload=row)
            for identity, row in by_identity.items()
        )
        return FetchResult(
            listings,
            {
                "pages": pages,
                "offers": len(by_identity),
                "details_fetched": details,
                "with_json_ld": sum(
                    1 for row in by_identity.values() if row.get("date_posted")
                ),
            },
            pages_fetched=pages,
            complete=error is None,
            error=error,
        )


def _collect(rows, by_identity, invalid):
    """Keep the offers of the board, counting the links that are not offers."""
    fresh = 0
    for row in rows:
        identity = job_identity(row["url"])
        if not identity:
            invalid += 1
            continue
        if identity not in by_identity:
            by_identity[identity] = row
            fresh += 1
    return fresh, invalid

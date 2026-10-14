"""myScience.ch native producer — the listing + detail scrape of the retiring scraper.

Rules copied, not imported: the core never loads the BFF runtime. Each bound
exists because the scraper has it (see backend/scrapers/myscience.py):

- Five listing pages at the portal's own two-second pace, and a detail page per
  offer, because the listing carries no description.
- A card without a link is NOT an offer: its URL used to degenerate into the
  home page and collide with the stored-URL unique key (G1/P3-9).
- A listing page that parses to nothing ends the sweep; a page that fails is a
  VISIBLE failure, never "0 offers".

Two deliberate departures from the scraper, both measured over the sealed
2026-10-14 capture and recorded in DT-144:

- `published_at` is read from the listing's own `<time datetime>` (schema.org
  `datePosted` on the detail page says the same). The scraper dropped it, which
  is why all 115 production rows have no date; the portal publishes it for every
  record, so the new owner keeps it instead of inheriting the loss.
- Identity is the portal's numeric id (`/jobs/id70453-…`), not the full URL. The
  URL carries the title in its slug: a retitled offer changes URL and the
  scraper would store it twice.

What is NOT taken, on purpose: the detail page's `Arbeitsort`/`Funktion`/`Pensum`
metadata. The scraper looks for `.descriptor` and `.long_value` inside the same
`.long_value_row`, and the live markup never has both, so it has always produced
no employment type. Reading them here would change what the corpus contains, and
`Funktion` is not an employment type; it stays a reported finding, not a guess.
"""

import asyncio
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
from jobhunt_core.harvest.providers.rss_text import extract_job_skills
from jobhunt_core.harvest.providers.search_metadata import swiss_canton
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "myscience"
BASE_URL = "https://www.myscience.ch"
LISTING_URL = f"{BASE_URL}/jobs"
MAX_PAGES = 5  # = MyScienceScraper.MAX_PAGES
PAGE_SIZE = 20  # = MyScienceScraper.PAGE_SIZE
PAGE_PAUSE_S = 2.0  # = MyScienceScraper.RATE_LIMIT_SECONDS
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_DETAIL_BYTES = 4 * 1024 * 1024
# Ceiling of the declared sweep (pages x page size): a portal change cannot turn
# the detail pass into an unbounded crawl.
MAX_DETAIL_FETCHES = MAX_PAGES * PAGE_SIZE
SWEEP_BUDGET_S = 600
MAX_TAGS = 15  # = BaseJobProvider.MAX_TAGS
REQUEST_TIMEOUT_S = 30
# = SCRAPER_MAX_RETRIES / SCRAPER_RETRY_BACKOFF_SECONDS / RETRYABLE_STATUS /
# SCRAPER_DELAY_JITTER_RATIO of the retiring engine: a transient 503 must not
# turn into a failed harvest, and the pauses carry the same jitter so the
# request intervals are not a machine fingerprint.
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
JITTER_RATIO = 0.5

_JOB_ID = re.compile(r"^/jobs/id(\d+)-")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
logger = logging.getLogger(__name__)


def _s(value) -> str:
    """Only a real string survives; anything else degrades THIS offer."""
    return value if isinstance(value, str) else ""


def _absolute(url: str) -> str:
    return url if url.startswith("http") else f"{BASE_URL}{url}"


class _Records(TagStackParser):
    """The listing's `#results_table > div[itemscope]` cards, in document order.

    Reproduces the retiring scraper's selectors: `.results_title` for the title,
    `.results_organization` for the company, `.location` for the city, the first
    `a[href]` for the link, `.centered_logo img` for the logo and the record's
    own `<time datetime>` for the publication date. Text is joined the way
    BeautifulSoup's `get_text(strip=True)` joins it.
    """

    _TEXT_CLASSES = {
        "results_title": "title",
        "results_organization": "company",
        "location": "location",
    }

    def __init__(self):
        super().__init__()
        self.records: list[dict] = []
        self._table_depth = 0
        self._record: dict | None = None
        self._record_depth = 0
        self._field: tuple[str, int] | None = None
        self._chunks: list[str] = []

    def on_open(self, tag, classes, attrs, depth):
        if not self._table_depth:
            if tag == "div" and attrs.get("id") == "results_table":
                self._table_depth = depth
            return
        if self._record is None:
            if tag == "div" and "itemscope" in attrs:
                self._record = {
                    "title": "",
                    "company": "",
                    "location": "",
                    "detail_url": "",
                    "logo": None,
                    "date": "",
                }
                self._record_depth = depth
            return
        if tag == "a" and not self._record["detail_url"]:
            self._record["detail_url"] = attrs.get("href", "")
        if tag == "time" and not self._record["date"]:
            self._record["date"] = attrs.get("datetime", "")
        if self._field is None:
            for name, field in self._TEXT_CLASSES.items():
                if name in classes:
                    self._field = (field, depth)
                    self._chunks = []
                    return

    def on_void(self, tag, classes, attrs):
        if self._record is None or self._record["logo"] is not None:
            return
        if (
            tag == "img"
            and attrs.get("src")
            and self.has_ancestor_class("centered_logo")
        ):
            self._record["logo"] = attrs["src"]

    def on_close(self, tag, depth):
        if self._field is not None and self._field[1] >= depth:
            field = self._field[0]
            if not self._record[field]:
                self._record[field] = "".join(self._chunks)
            self._field = None
        if self._record is not None and self._record_depth >= depth:
            self.records.append(self._record)
            self._record = None
        if self._table_depth and self._table_depth >= depth:
            self._table_depth = 0

    def on_text(self, data):
        if self._field is not None and data.strip():
            self._chunks.append(data.strip())


class _Detail(TagStackParser):
    """`#Description` text and `.centered_logo img` of a detail page.

    `#middle_content` scopes the parse exactly as the scraper's container does;
    the description keeps its newline-separated paragraphs.
    """

    def __init__(self):
        super().__init__()
        self.description_chunks: list[str] = []
        self.logo: str | None = None
        self._content_depth = 0
        self._description_depth = 0
        self._description_done = False

    def on_open(self, tag, classes, attrs, depth):
        if not self._content_depth:
            if attrs.get("id") == "middle_content":
                self._content_depth = depth
            return
        if (
            not self._description_depth
            and not self._description_done
            and attrs.get("id") == "Description"
        ):
            self._description_depth = depth

    def on_void(self, tag, classes, attrs):
        if not self._content_depth or self.logo is not None:
            return
        if (
            tag == "img"
            and attrs.get("src")
            and self.has_ancestor_class("centered_logo")
        ):
            self.logo = attrs["src"]

    def on_close(self, tag, depth):
        if self._description_depth and self._description_depth >= depth:
            self._description_depth = 0
            self._description_done = True
        if self._content_depth and self._content_depth >= depth:
            self._content_depth = 0

    def on_text(self, data):
        if self._description_depth and data.strip():
            self.description_chunks.append(data.strip())

    @property
    def description(self) -> str:
        return "\n".join(self.description_chunks)


def _stub(record: dict) -> dict | None:
    """A usable listing stub, or None when the card is not an offer."""
    title = record["title"].strip()
    href = record["detail_url"]
    if not title or not href:
        return None
    match = _JOB_ID.match(href)
    if match is None:
        # Without the portal's own id there is no stable identity, and the URL
        # slug carries the title: a retitled offer would be a second offer.
        return None
    stub = {
        "id": match.group(1),
        "title": title,
        "company": record["company"].strip() or "Unknown",
        "location": record["location"].strip(),
        "url": _absolute(href),
        "logo": _absolute(record["logo"]) if record["logo"] else None,
    }
    date = record["date"].strip()
    if _ISO_DATE.fullmatch(date):
        # Absent means UNKNOWN. An empty string would reach the read path as an
        # empty published_at instead of no date at all.
        stub["date"] = date
    return stub


def _records(html: str) -> list[dict]:
    parser = _Records()
    parser.feed(html)
    parser.close()
    return [stub for record in parser.records if (stub := _stub(record))]


def _detail(html: str) -> dict:
    parser = _Detail()
    parser.feed(html)
    parser.close()
    detail = {"description": parser.description}
    if parser.logo:
        detail["logo"] = _absolute(parser.logo)
    return detail


def _content(raw: dict) -> dict:
    title = _s(raw.get("title")).strip()
    description = _s(raw.get("description"))
    location = _s(raw.get("location")).strip() or "Switzerland"
    content = {
        "title": title,
        "company": _s(raw.get("company")).strip() or "Unknown",
        "description": description,
        "location": location,
        "remote": False,
        "tags": extract_job_skills(title, description)[:MAX_TAGS],
        "salary": None,
    }
    canton = swiss_canton(location)
    if canton:
        content["canton"] = canton
    logo = raw.get("logo")
    if isinstance(logo, str):
        content["logo"] = logo
    return content


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (
            _s(raw.get("title")).strip(),
            _s(raw.get("company")).strip() or "Unknown",
        ),
    )


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds + random.uniform(0, seconds * JITTER_RATIO))


async def _read(http, url: str, byte_limit: int, params=None):
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with http.stream(
                "GET",
                url,
                params=params,
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


class MyScienceProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ()

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError("myscience scope takes no query parameters")
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        by_identity: dict[str, dict] = {}
        pages = seen = invalid = 0
        error, exhausted = None, False
        first_failure = None
        loop = asyncio.get_running_loop()
        started = loop.time()

        for page in range(1, budget + 1):
            if loop.time() - started >= SWEEP_BUDGET_S:
                error = error or "time_budget"
                break
            if pages:
                await _pause(PAGE_PAUSE_S)
            try:
                html = await _read(
                    http, LISTING_URL, MAX_RESPONSE_BYTES, params={"p": page}
                )
            except Exception as exc:
                if first_failure is None:
                    first_failure = exc
                error = error or type(exc).__name__
                break
            pages += 1
            parser = _Records()
            parser.feed(html)
            parser.close()
            seen += len(parser.records)
            stubs = [stub for record in parser.records if (stub := _stub(record))]
            invalid += len(parser.records) - len(stubs)
            if not stubs:
                # The scraper ends the sweep on a page that parses to nothing.
                exhausted = True
                break
            for stub in stubs:
                by_identity.setdefault(stub["id"], stub)
            if page == budget:
                exhausted = True

        if not by_identity and first_failure is not None:
            raise first_failure
        if seen and not by_identity:
            raise ProviderResponseError(
                f"{SOURCE_NAME}: nonempty listing has no usable identities"
            )
        if not by_identity:
            raise ProviderResponseError(f"{SOURCE_NAME}: sweep produced no records")

        details = 0
        for stub in by_identity.values():
            if details >= MAX_DETAIL_FETCHES:
                error = error or "detail_budget"
                break
            if loop.time() - started >= SWEEP_BUDGET_S:
                # The detail pass is the long half of the sweep: without this
                # the same budget that bounds the listing loop would not bound
                # the run.
                error = error or "time_budget"
                break
            await _pause(PAGE_PAUSE_S)
            details += 1
            try:
                stub.update(_detail(await _read(http, stub["url"], MAX_DETAIL_BYTES)))
            except Exception as exc:
                # A dead detail page does not end the run: the listing stub is
                # already a minimal offer, exactly as the scraper treats it.
                logger.warning(
                    "%s: detail fetch failed for %r (%s)",
                    SOURCE_NAME,
                    stub["id"],
                    type(exc).__name__,
                )

        listings = tuple(
            RawListing(external_id="id:" + stub["id"], url=stub["url"], payload=stub)
            for stub in by_identity.values()
        )
        if invalid:
            logger.warning(
                "%s: %d cards without id, title or link", SOURCE_NAME, invalid
            )
        error = error or ("invalid_myscience_cards" if invalid else None)
        return FetchResult(
            listings,
            {
                "pages": pages,
                "items_seen": seen,
                "identities": len(by_identity),
                "details_fetched": details,
            },
            pages_fetched=pages,
            complete=exhausted and error is None,
            error=error,
        )

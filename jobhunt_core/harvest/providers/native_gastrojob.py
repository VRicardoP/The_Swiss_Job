"""Gastrojob.ch native producer — the AJAX sweep of the retiring scraper.

Rules copied, not imported: the core never loads the BFF runtime. Every one of
them exists because it broke before (see backend/scrapers/gastrojob.py):

- The public /stellen listing builds its offers with JavaScript and answers 200
  with zero offer links. The sweep therefore pages against the frontend's OWN
  AJAX endpoint — the URL comes from the pagination links of the site, not from
  reverse engineering — which answers a plain HTTP client with ten offers per
  page and expects the Referer the frontend sends.
- The listing mixes the portal's own ads with partner ads under
  /stellen/externe-partner/. Only the canonical /stellen/stelleninserat/<id>
  path is harvested, and the URL is REBUILT from that path: a tracking query or
  a different case would otherwise be a new row per offer.
- A page with no own offers is not automatically "no offers". The five-way
  verdict below is the whole point of this adapter: reading a changed structure
  as an empty listing is what killed this source once (VD.4b).
- The company comes ONLY from the listing. It is part of the legacy identity and
  only the listing is stable run to run; using the detail page as a fallback made
  a failed detail fetch produce "Unknown" and a healthy one produce the real
  name — two identities for one URL. Between ~3 % and ~13 % of the ads are
  anonymised and stay "Unknown" even though the detail microdata knows the name.
- "Erstmals aktiviert: DD.MM.YYYY (HH:MM)" is Swiss civil time. It is anchored to
  Europe/Zurich so the date does not drift one or two hours, and the ambiguous
  autumn hour resolves to the first instant: one hour of error, one night a year,
  against losing the date entirely.
"""

import asyncio
import logging
import random
import re
from datetime import datetime
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

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

SOURCE_NAME = "gastrojob"
BASE_URL = "https://www.gastrojob.ch"
LISTING_URL = f"{BASE_URL}/stellen"
MAX_PAGES = 5  # = GastrojobScraper.MAX_PAGES
PAGE_SIZE = 10  # = GastrojobScraper.PAGE_SIZE
PAGE_PAUSE_S = 2.0  # = GastrojobScraper.RATE_LIMIT_SECONDS
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DETAIL_BYTES = 4 * 1024 * 1024
MAX_DETAIL_FETCHES = MAX_PAGES * PAGE_SIZE
SWEEP_BUDGET_S = 600
MAX_TAGS = 15  # = BaseJobProvider.MAX_TAGS
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
JITTER_RATIO = 0.5
# The frontend sends its own Referer and the endpoint expects it.
HEADERS = {**BROWSER_HEADERS, "Referer": LISTING_URL}

OWN_AD_PREFIX = "/stellen/stelleninserat/"
PARTNER_AD_PREFIX = "/stellen/externe-partner/"
COUNT_ATTRIBUTE = "data-mxn-advertisements-count"
# `[0-9]`, never `\d`: on str, `\d` also matches unicode digits (٢١) the portal
# never emits and that would make a URL or a date unstable.
_JOB_PATH_RE = re.compile(r"^/stellen/stelleninserat/([0-9]+)$")
_ERSTMALS_RE = re.compile(
    r"Erstmals aktiviert:\s*"
    r"([0-9]{2})\.([0-9]{2})\.([0-9]{4})\s*\(([0-9]{2}):([0-9]{2})\)"
)
_COUNT_RE = re.compile(r"[0-9]+")
# "<workload> bei <company> in <canton>", workload sometimes empty. Lazy `.*?`
# cuts at the FIRST " bei "; greedy company makes the separating " in " the
# LAST one, so a company whose name contains " in " survives.
_DESCRIPTION_RE = re.compile(
    r"^\s*(?P<workload>.*?)\s*\bbei\s+(?P<company>.+)\s+in\s+(?P<location>.+?)\s*$"
)
_WHITESPACE_RE = re.compile(r"\s+")
_ZURICH = ZoneInfo("Europe/Zurich")

logger = logging.getLogger(__name__)


class StructureUnknown(Exception):
    """A 200 whose content we cannot read — never "no offers"."""


def _s(value) -> str:
    return value if isinstance(value, str) else ""


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def listing_url(page: int) -> str:
    """The frontend's own AJAX pagination URL, parameter for parameter."""
    params = {
        "type": "5000",
        "tx_mxngastrojob_ajax[action]": "filteredAds",
        "tx_mxngastrojob_ajax[controller]": "Ajax",
        "tx_mxngastrojob_ajax[@widget_0][currentPage]": str(page),
        "tx_mxngastrojob_ajax[search]": "",
        "tx_mxngastrojob_ajax[filter]": "[]",
    }
    return f"{BASE_URL}/?{urlencode(params)}"


def _resolve_job_url(href: str) -> tuple[str, str] | None:
    """(canonical URL, portal id), or None when the href is not an offer.

    The host is validated so a protocol-relative or userinfo href cannot point
    the stored URL somewhere else, and the URL is rebuilt from the path.
    """
    if not href:
        return None
    # urllib only cuts the authority at `/ ? #`; browsers also cut at `\`.
    if "\\" in href:
        return None
    try:
        parts = urlsplit(urljoin(BASE_URL, href))
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if parts.username is not None:
        return None
    host = parts.hostname or ""
    if host != "gastrojob.ch" and not host.endswith(".gastrojob.ch"):
        return None
    match = _JOB_PATH_RE.match(parts.path)
    if match is None:
        return None
    return f"{BASE_URL}{parts.path}", match.group(1)


def parse_erstmals_aktiviert(text: str) -> str:
    """The listing's activation stamp as an ISO string with the Swiss offset."""
    match = _ERSTMALS_RE.search(text)
    if match is None:
        return ""
    day, month, year, hour, minute = (int(group) for group in match.groups())
    try:
        return datetime(year, month, day, hour, minute, tzinfo=_ZURICH).isoformat()
    except ValueError:
        # An impossible date degrades THIS date, never the offer.
        return ""


class _Fragment(TagStackParser):
    """The AJAX fragment: own-ad stubs plus the evidence the verdict needs.

    Each item is an `<a href="/stellen/stelleninserat/<id>">` wrapping the card,
    with an `h2` title, a `div.description` and a `div.hidden-jossen` stamp.
    `div.description` is joined VERBATIM because its regex needs the real
    spacing; the title is joined the way `get_text(strip=True)` joins it.
    """

    def __init__(self):
        super().__init__()
        self.stubs: list[dict] = []
        self.announced: str | None = None
        self.own_links = 0
        self.partner_links = 0
        self._link: dict | None = None
        self._link_depth = 0
        self._field: tuple[str, int, bool] | None = None
        self._chunks: list[str] = []

    def on_open(self, tag, classes, attrs, depth):
        if self.announced is None and COUNT_ATTRIBUTE in attrs:
            self.announced = attrs[COUNT_ATTRIBUTE]
        if tag == "a":
            href = attrs.get("href", "")
            if PARTNER_AD_PREFIX in href:
                self.partner_links += 1
            if OWN_AD_PREFIX in href and self._link is None:
                self.own_links += 1
                self._link = {"href": href, "title": "", "description": "", "stamp": ""}
                self._link_depth = depth
            return
        if self._link is None or self._field is not None:
            return
        if tag == "h2":
            self._field = ("title", depth, False)
        elif "description" in classes:
            self._field = ("description", depth, True)
        elif "hidden-jossen" in classes:
            self._field = ("stamp", depth, True)
        else:
            return
        self._chunks = []

    def on_close(self, tag, depth):
        if self._field is not None and self._field[1] >= depth:
            name, _depth, verbatim = self._field
            if not self._link[name]:
                self._link[name] = "".join(self._chunks)
            self._field = None
        if self._link is not None and self._link_depth >= depth:
            self.stubs.append(self._link)
            self._link = None

    def on_text(self, data):
        if self._field is None:
            return
        if self._field[2]:
            self._chunks.append(data)
        elif data.strip():
            self._chunks.append(data.strip())


def _stub(raw: dict) -> dict | None:
    resolved = _resolve_job_url(raw["href"])
    if resolved is None:
        return None
    url, source_id = resolved
    title = raw["title"].strip()
    if not title:
        return None
    match = _DESCRIPTION_RE.match(raw["description"])
    # No match means nothing is invented: the offer stays "Unknown".
    workload = match.group("workload").strip() if match else ""
    company = match.group("company").strip() if match else ""
    canton_name = match.group("location").strip() if match else ""
    stub = {
        "id": source_id,
        "title": title,
        "company": company or "Unknown",
        "location": canton_name,
        "url": url,
        "employment_type": workload or None,
    }
    stamp = parse_erstmals_aktiviert(raw["stamp"])
    if stamp:
        stub["date_posted"] = stamp
    return stub


def _page_stubs(html: str, page: int) -> list[dict]:
    """The page's own-ad stubs, or a verdict on why there are none.

    Order of verdicts, each one a measured failure mode:
      1. no counter at all: the fragment is not the portal's;
      2. an unreadable counter: same;
      3. own offer links of which none parses: unreadable either way;
      4. only for fragments with NO own link: beyond the last page the counter
         covers is the end of pagination, and partner-only inside the range is a
         legitimate empty page — the rehabilitation path, no failure;
      5. anything else: a changed structure, even mid-sweep after good pages.
    """
    parser = _Fragment()
    parser.feed(html)
    parser.close()
    stubs = [stub for raw in parser.stubs if (stub := _stub(raw))]
    if stubs:
        return stubs

    if parser.announced is None:
        raise StructureUnknown(
            f"fragment without div[{COUNT_ATTRIBUTE}]: unknown structure"
        )
    try:
        if not _COUNT_RE.fullmatch(parser.announced):
            raise ValueError("not a decimal counter")
        # A counter of thousands of digits passes the regex and still makes
        # int() raise (CPython's 4300-digit limit); it is just as unreadable.
        announced = int(parser.announced)
    except ValueError:
        shown = parser.announced[:64]
        raise StructureUnknown(
            f"unreadable advertisement counter ({shown!r})"
        ) from None
    if parser.own_links:
        raise StructureUnknown(
            f"page {page} carries {parser.own_links} offer links and none parses"
        )
    # Integer ceil, never float: a counter of hundreds of digits overflows.
    last_expected_page = (announced + PAGE_SIZE - 1) // PAGE_SIZE
    if page > last_expected_page:
        return []
    if parser.partner_links:
        return []
    raise StructureUnknown(
        f"the portal announces {announced} offers and page {page} "
        f"(of {last_expected_page} expected) carries none recognisable"
    )


class _Detail(TagStackParser):
    """The detail page's schema.org/JobPosting microdata.

    The description keeps the producer's shape: text joined and then collapsed
    to single spaces, which is what `get_text(separator="\\n")` followed by
    `strip_html_tags` produces.
    """

    def __init__(self):
        super().__init__()
        self.description_chunks: list[str] = []
        self.description_seen = False
        self.date_posted = ""
        self.locality = ""
        self._section_depth = 0
        self._description_depth = 0
        self._locality_depth = 0
        self._locality_chunks: list[str] = []
        self._in_job_location = 0

    def on_open(self, tag, classes, attrs, depth):
        if not self._section_depth:
            if (
                tag == "section"
                and "ad-detail" in classes
                and attrs.get("itemtype") == "http://schema.org/JobPosting"
            ):
                self._section_depth = depth
            return
        prop = attrs.get("itemprop")
        if prop == "description" and not self._description_depth:
            self._description_depth = depth
            self.description_seen = True
        elif prop == "jobLocation" and not self._in_job_location:
            self._in_job_location = depth
        elif prop == "addressLocality" and self._in_job_location and not self.locality:
            self._take_locality(attrs, depth)

    def on_void(self, tag, classes, attrs):
        if not self._section_depth:
            return
        prop = attrs.get("itemprop")
        if prop == "datePosted" and not self.date_posted:
            self.date_posted = _s(attrs.get("content"))
        elif prop == "addressLocality" and self._in_job_location and not self.locality:
            # The locality is a <meta content="...">, so it never reaches the
            # element stack: reading it only on open tags found nothing. The
            # value is kept VERBATIM, like the producer's `.get("content")`.
            self.locality = _s(attrs.get("content"))

    def _take_locality(self, attrs: dict, depth: int) -> None:
        """`content` when the element carries one, otherwise its text."""
        self.locality = _s(attrs.get("content"))
        if not self.locality:
            self._locality_depth = depth
            self._locality_chunks = []

    def on_close(self, tag, depth):
        if self._locality_depth and self._locality_depth >= depth:
            self.locality = "".join(self._locality_chunks)
            self._locality_depth = 0
        if self._description_depth and self._description_depth >= depth:
            self._description_depth = -1
        if self._in_job_location and self._in_job_location >= depth:
            self._in_job_location = 0
        if self._section_depth and self._section_depth >= depth:
            self._section_depth = 0

    def on_text(self, data):
        if self._description_depth > 0 and data.strip():
            self.description_chunks.append(data.strip())
        elif self._locality_depth and data.strip():
            self._locality_chunks.append(data.strip())

    @property
    def detail(self) -> dict:
        value = {}
        # Present-but-empty is kept: the producer stores "" whenever the
        # element exists, and an absent key means something else downstream.
        if self.description_seen:
            value["description"] = _collapse(" ".join(self.description_chunks))
        if self.date_posted:
            value["detail_date_posted"] = self.date_posted
        if self.locality:
            value["address_locality"] = self.locality
        return value


def _detail(html: str) -> dict:
    parser = _Detail()
    parser.feed(html)
    parser.close()
    return parser.detail


def _content(raw: dict) -> dict:
    title = _s(raw.get("title")).strip()
    description = _s(raw.get("description"))
    # Listing = canton ("Neuenburg"); detail = locality ("Fontaines"). Both are
    # kept so the canton helper still sees the canton name.
    canton_name = _s(raw.get("location")).strip()
    locality = _s(raw.get("address_locality")).strip()
    if locality and canton_name:
        location = f"{locality}, {canton_name}"
    else:
        location = locality or canton_name or "Switzerland"
    content = {
        "title": title,
        # Listing only: the company is identity and only the listing is stable.
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
    employment_type = _s(raw.get("employment_type")).strip()
    if employment_type:
        content["employment_type"] = employment_type
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


async def _read(http, url: str, byte_limit: int):
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with http.stream(
                "GET",
                url,
                headers=HEADERS,
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


class GastrojobProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ()

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError("gastrojob scope takes no query parameters")
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        by_identity: dict[str, dict] = {}
        pages = seen = 0
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
                stubs = _page_stubs(
                    await _read(http, listing_url(page), MAX_RESPONSE_BYTES), page
                )
            except StructureUnknown as exc:
                # A 200 we cannot read is a VISIBLE failure, not an empty page:
                # reading it as "0 offers" is what killed this source once.
                logger.error("%s: %s", SOURCE_NAME, exc)
                if first_failure is None:
                    first_failure = ProviderResponseError(f"{SOURCE_NAME}: {exc}")
                error = error or "unknown_gastrojob_structure"
                break
            except Exception as exc:
                if first_failure is None:
                    first_failure = exc
                error = error or type(exc).__name__
                break
            pages += 1
            seen += len(stubs)
            if not stubs:
                exhausted = True
                break
            for stub in stubs:
                by_identity.setdefault(stub["id"], stub)
            if page == budget:
                exhausted = True

        if not by_identity:
            if first_failure is not None:
                raise first_failure
            raise ProviderResponseError(f"{SOURCE_NAME}: sweep produced no offers")

        details = 0
        for stub in by_identity.values():
            if details >= MAX_DETAIL_FETCHES:
                error = error or "detail_budget"
                break
            if loop.time() - started >= SWEEP_BUDGET_S:
                error = error or "time_budget"
                break
            await _pause(PAGE_PAUSE_S)
            details += 1
            try:
                stub.update(_detail(await _read(http, stub["url"], MAX_DETAIL_BYTES)))
            except Exception as exc:
                # The listing stub is already a minimal offer, as in the scraper.
                logger.warning(
                    "%s: detail fetch failed for %r (%s)",
                    SOURCE_NAME,
                    stub["id"],
                    type(exc).__name__,
                )

        listings = []
        for stub in by_identity.values():
            payload = dict(stub)
            published = payload.get("date_posted") or payload.get("detail_date_posted")
            if published:
                payload["_core_published_at"] = published
            listings.append(
                RawListing(
                    external_id="id:" + stub["id"], url=stub["url"], payload=payload
                )
            )
        return FetchResult(
            tuple(listings),
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

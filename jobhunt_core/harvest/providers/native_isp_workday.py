"""Mosaic Ecole on the ISP group's Workday portal.

The only board of the school watchlist that is an API rather than a page:
Workday exposes a public JSON endpoint and the producer posts a search for the
school. Everything that follows is copied from the retiring scraper
(`backend/scrapers/swiss_schools_isp.py`), including the whole `externalPath`
guard chain, because every link in it was added after a measured defect:

- `externalPath` is the only thing standing between the API and a URL we
  persist as clickable. A live probe showed `?job=123` passing a type check and
  being stored as a "valid" URL with no vacancy behind it, so the SHAPE is part
  of the contract: a relative path under /job/ with at least one non-empty
  segment, no scheme, authority, query, fragment, backslash, control character,
  traversal or whitespace — raw OR hidden behind percent-encoding, inspected
  two levels deep with strict UTF-8 and failing closed on deeper nesting.
- A degenerate item degrades THAT item, never the page: a non-string
  `locationsText` used to raise and take the whole batch down.
- The school filter runs BEFORE those per-item guards on purpose: the Workday
  tenant is SHARED with other schools, so validating other schools' rows would
  switch off a healthy source whose zero matches are legitimate.

`published_at` stays absent on purpose (ticket 2A / ADR-10): Workday publishes
`postedOn` as a relative bucket ("Posted 30+ Days Ago"), never a date.
Approximating it would invent precision that does not exist. **Do not "fix"
it.**
"""

import asyncio
import json
import logging
import random
import re
from urllib.parse import unquote_to_bytes

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
from jobhunt_core.harvest.providers.search_metadata import swiss_canton
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "swiss_schools_isp"
TENANT = "internationalschools"
SITE = "ISPCareers"
API_URL = f"https://{TENANT}.wd3.myworkdayjobs.com/wday/cxs/{TENANT}/{SITE}/jobs"
CAREER_SITE = f"https://{TENANT}.wd3.myworkdayjobs.com/en-US/{SITE}"
SCHOOL_FILTER = "mosaic"
SCHOOL_ID = "mosaic_geneva"
SCHOOL_NAME = "Mosaic Ecole"
SCHOOL_CITY = "Geneva"
TAGS = ("education", "international school")
PAGE_SIZE = 20  # = SwissSchoolsISPScraper.PAGE_SIZE
MAX_PAGES = 10  # = SwissSchoolsISPScraper.MAX_PAGES
PAGE_PAUSE_S = 2.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
REQUEST_TIMEOUT_S = 20
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
JITTER_RATIO = 0.5

# URI scheme per RFC 3986: any scheme makes the value an absolute URL, not an
# offer path.
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
# Percent-encoding syntax (RFC 3986 §2.1): every '%' forms EXACTLY one hex
# triplet. "x%", "x%2" and "x%GG" do not.
_WELL_FORMED_PERCENT_RE = re.compile(r"(?:[^%]|%[0-9A-Fa-f]{2})*")
# Two levels of decoding FOR INSPECTION, and only two: the second catches
# double encoding ("%252E%252E" → "%2E%2E" → ".."). The real server decodes
# once; the extra level is defence against evasion, not semantics.
_INSPECTION_DECODE_LEVELS = 2
# A %HH triplet surviving both inspection levels means nesting three deep or
# more. Raising the ceiling would only move the problem one step along, so this
# fails closed.
_PERCENT_TRIPLET_RE = re.compile(rb"%[0-9A-Fa-f]{2}")

logger = logging.getLogger(__name__)


def _decoded_segment_issue(decoded: bytes) -> str | None:
    """Why DECODED bytes are not data of a path, or None.

    Works on bytes, byte by byte, as the server does, and then requires valid
    UTF-8: the real API answers 400 to encoded surrogates (%ED%A0%80) and to
    overlong forms (%C0%AF).
    """
    if any(byte < 0x20 or byte == 0x7F for byte in decoded):
        return "encodes control characters"
    if 0x5C in decoded:  # '\'
        return "encodes a backslash ('%5C')"
    if 0x2F in decoded:  # '/'
        return "encodes a path separator ('%2F')"
    if decoded in (b".", b".."):
        return "encodes a traversal segment ('.' or '..')"
    try:
        decoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "encodes bytes that are not valid UTF-8"
    return None


def _percent_encoding_issue(path: str) -> str | None:
    """Why the percent-encoding of `path` invalidates it, or None.

    The ORIGINAL path is what gets persisted: nothing here is transformed.
    """
    if _WELL_FORMED_PERCENT_RE.fullmatch(path) is None:
        return "malformed percent-encoding ('%' without a hex triplet)"
    for segment in path.split("/"):
        decoded = segment.encode("utf-8")
        for _ in range(_INSPECTION_DECODE_LEVELS):
            if b"%" not in decoded:
                break
            decoded = unquote_to_bytes(decoded)
            issue = _decoded_segment_issue(decoded)
            if issue is not None:
                return issue
        if _PERCENT_TRIPLET_RE.search(decoded):
            return "excessive nested encoding (%HH after two levels)"
    return None


def external_path_issue(path: str) -> str | None:
    """Why `path` is not the shape of an offer path, or None.

    Expected: a relative path under /job/ with at least one non-empty segment;
    no query, fragment, scheme or authority; and no backslash, separator,
    traversal or control character, raw or percent-encoded.
    """
    if not path.startswith("/job/"):
        if path.startswith("//"):
            return "starts with '//' (authority, not a relative path)"
        if _URI_SCHEME_RE.match(path):
            return "carries a URI scheme, not a relative path"
        return "does not start with /job/"
    if "?" in path:
        return "contains a query ('?')"
    if "#" in path:
        return "contains a fragment ('#')"
    if "\\" in path:
        return "contains a backslash ('\\')"
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path):
        return "contains control characters"
    # Lone UTF-16 surrogates: json.loads accepts '"\\ud800"' and produces a str
    # that cannot be encoded to UTF-8, so the inspection below would raise.
    if any(0xD800 <= ord(char) <= 0xDFFF for char in path):
        return "contains lone UTF-16 surrogates"
    if any(char.isspace() for char in path):
        return "contains inner whitespace"
    segments = path.split("/")[2:]
    if any(segment == "" for segment in segments):
        return "no offer segment after /job/ (empty segment)"
    if any(segment in (".", "..") for segment in segments):
        return "contains a traversal segment ('.' or '..')"
    if "%" in path:
        return _percent_encoding_issue(path)
    return None


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _source_id(raw: dict) -> str:
    bullet = raw.get("bulletFields")
    if isinstance(bullet, list) and bullet and isinstance(bullet[0], str) and bullet[0]:
        return bullet[0]
    # Only source_id material: a degenerate bulletFields is repaired with the
    # path, which the guards have already validated, and the offer is emitted.
    return _text(raw.get("externalPath"))


def _content(raw: dict) -> dict:
    location = _text(raw.get("locationsText")) or f"{SCHOOL_CITY}, CH"
    content = {
        "title": _text(raw.get("title")),
        "company": SCHOOL_NAME,
        "description": None,
        "location": location,
        "remote": False,
        "tags": [*TAGS, SCHOOL_ID],
        "salary": None,
        "language": "en",
    }
    canton = swiss_canton(location)
    if canton:
        content["canton"] = canton
    return content


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(SOURCE_NAME, lambda raw: (_text(raw.get("title")), SCHOOL_NAME))


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds + random.uniform(0, seconds * JITTER_RATIO))


async def _search(http, offset: int) -> dict:
    payload = {
        "appliedFacets": {},
        "limit": PAGE_SIZE,
        "offset": offset,
        "searchText": SCHOOL_FILTER,
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await http.post(
                API_URL,
                json=payload,
                headers={**BROWSER_HEADERS, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_S,
            )
            if response.status_code in RETRY_STATUSES:
                raise httpx.HTTPStatusError(
                    "retryable response", request=response.request, response=response
                )
            response.raise_for_status()
            body = response.content
            break
        except httpx.HTTPError as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                exc.response.status_code in RETRY_STATUSES
            )
            if not retryable or attempt == MAX_RETRIES:
                raise
            await _pause(RETRY_BACKOFF_S * (2**attempt))
    else:  # pragma: no cover - the loop either breaks or raises
        raise AssertionError("unreachable")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ProviderResponseError(f"{SOURCE_NAME}: response exceeds byte budget")
    try:
        data = json.loads(body.decode("utf-8", errors="strict"))
    except (ValueError, RecursionError) as exc:
        # A 200 with an unreadable body is the API broken (redeploy, a WAF
        # serving HTML), not an empty board.
        raise ProviderResponseError(f"{SOURCE_NAME}: unreadable JSON body") from exc
    if not isinstance(data, dict):
        raise ProviderResponseError(f"{SOURCE_NAME}: JSON body is not an object")
    if not isinstance(data.get("jobPostings"), list):
        raise ProviderResponseError(f"{SOURCE_NAME}: jobPostings is not a list")
    return data


class ISPWorkdayProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ()

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError(f"{SOURCE_NAME} scope takes no query parameters")
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        by_identity: dict[str, dict] = {}
        pages = seen = degraded = 0

        for page in range(budget):
            if pages:
                await _pause(PAGE_PAUSE_S)
            data = await _search(http, page * PAGE_SIZE)
            pages += 1
            postings = data["jobPostings"]
            seen += len(postings)
            parseable = 0
            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                parseable += 1
                location_text = posting.get("locationsText")
                if location_text is not None and not isinstance(location_text, str):
                    # A degenerate item degrades THAT item, never the page.
                    degraded += 1
                    logger.warning("%s: locationsText is not a string", SOURCE_NAME)
                    continue
                # The school filter comes FIRST: the tenant is shared, and
                # validating other schools' rows would switch off a healthy
                # source whose zero matches are legitimate.
                if SCHOOL_FILTER not in (location_text or "").lower():
                    continue
                title = posting.get("title")
                path = posting.get("externalPath")
                if not (isinstance(title, str) and title.strip()) or not (
                    isinstance(path, str) and path.strip()
                ):
                    degraded += 1
                    logger.warning("%s: matching offer has no title/path", SOURCE_NAME)
                    continue
                path = path.strip()
                issue = external_path_issue(path)
                if issue is not None:
                    degraded += 1
                    logger.warning("%s: externalPath %s", SOURCE_NAME, issue)
                    continue
                posting["externalPath"] = path
                identity = _source_id(posting)
                by_identity.setdefault(identity, posting)
            if postings and not parseable:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: {len(postings)} postings and none is an object"
                )
            if len(postings) < PAGE_SIZE:
                break

        listings = tuple(
            RawListing(
                external_id=f"{SCHOOL_ID}:{identity}",
                url=f"{CAREER_SITE}{posting['externalPath']}",
                payload=posting,
            )
            for identity, posting in by_identity.items()
        )
        if not listings:
            # VERIFIED empty: the API answered its contract and this school has
            # nothing open. On a shared tenant that is the normal state.
            logger.info(
                "%s: tenant answered with no vacancies for the school", SOURCE_NAME
            )
        return FetchResult(
            listings,
            {
                "pages": pages,
                "items_seen": seen,
                "vacancies": len(listings),
                "degraded_items": degraded,
                "verified_empty": not listings,
            },
            pages_fetched=pages,
            # The type's invariant: an error means an incomplete sweep. A
            # degraded item is visible rather than silent, which is the point.
            complete=not degraded,
            error="degraded_isp_items" if degraded else None,
        )

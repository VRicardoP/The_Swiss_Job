"""TES.com's Switzerland listing — the embedded Next.js payload of the scraper.

Rules copied, not imported: the core never loads the BFF runtime.

- The offers travel inside `__NEXT_DATA__`, under
  `props.pageProps.initialData`. The scraper reads them from the tRPC query
  cache instead; on page 1 both carry **the same object** (verified over the
  sealed capture), and `initialData` is the one that survives on later pages.
- A page that parses to nothing, an unreadable payload or a payload without
  that block is a VISIBLE failure, never "0 offers" — the exact defect the
  scraper was fixed for (G1/P2-5).

Measured ceiling of the portal, recorded rather than hidden: **`?page=N` is
ignored server-side**. Page 2 renders page 1's data (`currentPage: 1`), so the
eight offers that `numFound` announces beyond the first twenty are unreachable
without reverse-engineering the client-side route. The sweep therefore asks for
page 2 ONCE: if the portal starts honouring the parameter the sweep follows it,
and if it does not, the shortfall is logged and counted instead of being
silently lost. The run is still COMPLETE — a permanent portal limitation must
not keep `last_complete_at` frozen and turn the staleness alert into noise.

The scraper's `PAGE_SIZE = 1` comment ("server enforces limit=1 per page") is
stale: the portal serves twenty per page today.
"""

import asyncio
import logging
import random

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
from jobhunt_core.harvest.providers.next_data import next_data
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.providers.search_metadata import swiss_canton
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "tes"
BASE_URL = "https://www.tes.com"
LISTING_URL = f"{BASE_URL}/jobs/browse/switzerland"
MAX_PAGES = 35  # = TESScraper.MAX_PAGES
PAGE_PAUSE_S = 2.0  # = TESScraper.RATE_LIMIT_SECONDS
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TAGS = 15  # = BaseJobProvider.MAX_TAGS
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
JITTER_RATIO = 0.5

logger = logging.getLogger(__name__)


def _s(value) -> str:
    return value if isinstance(value, str) else ""


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _first(value) -> str:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _identity(job: dict) -> str:
    value = job.get("id")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return ""
    value = str(value).strip()
    return value if value and len(value) <= 200 else ""


def _page_payload(html: str) -> dict:
    """`props.pageProps.initialData`, or a failure that names what is missing."""
    data = next_data(html, SOURCE_NAME)
    page_props = _object(_object(data.get("props")).get("pageProps"))
    payload = page_props.get("initialData")
    if not isinstance(payload, dict):
        # Fall back to the tRPC cache the scraper reads, so a payload shape the
        # portal reverts to is still understood.
        for entry in (
            _object(_object(page_props.get("trpcState")).get("json")).get("queries", [])
            or []
        ):
            candidate = _object(_object(entry).get("state")).get("data")
            if isinstance(candidate, dict) and "jobs" in candidate:
                payload = candidate
                break
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ProviderResponseError(
            f"{SOURCE_NAME}: __NEXT_DATA__ carries no jobs payload"
        )
    return payload


def _content(raw: dict) -> dict:
    title = _s(raw.get("title")).strip()
    description = strip_html_tags(_s(raw.get("shortDescription")))
    location = _s(raw.get("displayLocation")).strip() or "Switzerland"
    employer = _object(raw.get("employer"))
    content = {
        "title": title,
        "company": _s(employer.get("name")).strip() or "Unknown",
        "description": description,
        "location": location,
        "remote": False,
        "tags": extract_job_skills(title, description)[:MAX_TAGS],
        # The portal publishes a free-text range ("£45,000 - £55,000"), never a
        # pair of amounts: it travels as text and no number is invented.
        "salary": _s(_object(raw.get("salary")).get("range")).replace("\xa0", " ")
        or None,
    }
    canton = swiss_canton(location)
    if canton:
        content["canton"] = canton
    logo = _object(employer.get("images")).get("logo")
    if isinstance(logo, str):
        content["logo"] = logo
    employment_type = _first(raw.get("contractTypes"))
    if employment_type:
        content["employment_type"] = employment_type
    contract_term = _first(raw.get("contractTerms"))
    if contract_term:
        content["contract_type"] = contract_term
    return content


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (
            _s(raw.get("title")).strip(),
            _s(_object(raw.get("employer")).get("name")).strip() or "Unknown",
        ),
    )


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds + random.uniform(0, seconds * JITTER_RATIO))


async def _read(http, page: int) -> str:
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with http.stream(
                "GET",
                LISTING_URL,
                params={"page": page},
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


class TESProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ()

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError("tes scope takes no query parameters")
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        by_identity: dict[str, dict] = {}
        pages = invalid = 0
        announced = 0
        ignored_pagination = False

        for page in range(1, budget + 1):
            if pages:
                await _pause(PAGE_PAUSE_S)
            payload = _page_payload(await _read(http, page))
            pages += 1
            announced = max(announced, _count(payload.get("numFound")))
            served = _count(payload.get("currentPage"))
            if page > 1 and served and served != page:
                # The portal ignored `?page=`: asking again would re-read the
                # same rows. Said out loud, not retried.
                ignored_pagination = True
                break
            fresh = 0
            for job in payload["jobs"]:
                if not isinstance(job, dict):
                    invalid += 1
                    continue
                identity = _identity(job)
                url = _s(job.get("canonicalUrl")).strip()
                if (
                    not identity
                    or not url.startswith("/")
                    or not _s(job.get("title")).strip()
                ):
                    invalid += 1
                    continue
                if identity not in by_identity:
                    by_identity[identity] = {**job, "_core_url": BASE_URL + url}
                    fresh += 1
            total_pages = _count(payload.get("totalPages"))
            if not fresh or (total_pages and page >= total_pages):
                break

        if not by_identity:
            raise ProviderResponseError(f"{SOURCE_NAME}: listing carries no offers")
        if invalid:
            logger.warning("%s: %d unusable rows", SOURCE_NAME, invalid)
        shortfall = max(0, announced - len(by_identity))
        if shortfall:
            logger.warning(
                "%s: the portal announces %d offers and the listing only exposes "
                "%d%s. The rest is not reachable from the rendered payload.",
                SOURCE_NAME,
                announced,
                len(by_identity),
                " (?page= is ignored server-side)" if ignored_pagination else "",
            )
        listings = tuple(
            RawListing(external_id="id:" + identity, url=job["_core_url"], payload=job)
            for identity, job in by_identity.items()
        )
        return FetchResult(
            listings,
            {
                "pages": pages,
                "announced": announced,
                "harvested": len(by_identity),
                "shortfall": shortfall,
                "pagination_ignored": ignored_pagination,
            },
            pages_fetched=pages,
            # A permanent portal limitation must not freeze last_complete_at.
            complete=not invalid,
            error="invalid_tes_rows" if invalid else None,
        )


def _count(value) -> int:
    return value if type(value) is int and value >= 0 else 0

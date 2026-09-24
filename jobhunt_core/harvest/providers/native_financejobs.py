"""Financejobs.ch native producer — the __NEXT_DATA__ parse of the retiring scraper.

Rules copied, not imported: the core never loads the BFF runtime. Each one
exists because it broke before (see backend/scrapers/financejobs.py):

- Next.js has already moved `jobsSSR` once. Both known paths are tried and a
  structure we cannot read is a VISIBLE failure, never "0 offers" — that exact
  silence was the original defect.
- The portal id is a decimal (canonicalised: "00042" and 42 are one offer) or a
  canonical lowercase UUID in `jcJobId`. Anything else has no usable URL and the
  offer is skipped rather than given a fabricated identity.
- Title and company are stripped of HTML BEFORE being validated: "  <b></b> "
  must not become a stub whose title normalises to empty, and a blank company
  must degrade to the same value `None` does, or the same posting gets two
  identities.
- A non-empty page from which no listing survives is a structure failure too.

Salary stays in the raw payload: the canonical boundary carries no amount until
a validated enrichment maps currency and period, exactly as the JSON sources do.
"""

import asyncio
import json
import re

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

SOURCE_NAME = "financejobs"
BASE_URL = "https://www.financejobs.ch"
LISTING_URL = f"{BASE_URL}/de/jobs"
JOB_URL = f"{BASE_URL}/de/job/"
MAX_PAGES = 10  # = FinancejobsScraper.MAX_PAGES
PAGE_PAUSE_S = 2.0  # = FinancejobsScraper.RATE_LIMIT_SECONDS
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SWEEP_BUDGET_S = 300

# Known paths from `props` down to the jobsSSR block, newest first.
_JOBS_SSR_PATHS = (("pageProps", "jobsSSR"), ("initialProps", "pageProps", "jobsSSR"))
_NEXT_DATA = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
# ASCII decimals only: \d would match unicode digits the portal never emits.
_DECIMAL_ID = re.compile(r"^[0-9]+$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# An id that would not fit the stored URL cannot be persisted: treat it as unusable.
_MAX_ID_LEN = 2048 - len(JOB_URL)


def _s(value):
    """Only a real string survives; a null field degrades THIS offer, not the page."""
    return value if isinstance(value, str) else ""


def _job_url_id(job):
    job_id = job.get("jobId")
    if isinstance(job_id, int) and not isinstance(job_id, bool) and job_id >= 0:
        canonical = str(job_id)
        return canonical if len(canonical) <= _MAX_ID_LEN else ""
    if isinstance(job_id, str) and _DECIMAL_ID.fullmatch(job_id):
        canonical = job_id.lstrip("0") or "0"
        return canonical if len(canonical) <= _MAX_ID_LEN else ""
    jc_job_id = job.get("jcJobId")
    if isinstance(jc_job_id, str) and _UUID.fullmatch(jc_job_id):
        return jc_job_id
    return ""


def _jobs_ssr(data):
    """The jobsSSR block, or None when no known shape exists (a failure)."""
    for path in _JOBS_SSR_PATHS:
        node = data.get("props", {})
        for key in path:
            if not isinstance(node, dict):
                break
            node = node.get(key)
        if isinstance(node, dict):
            return node
    return None


def _listing(job):
    if not isinstance(job, dict):
        return None
    job_id = _job_url_id(job)
    title = strip_html_tags(_s(job.get("title"))).strip()
    if not job_id or not title:
        return None
    return RawListing("id:" + job_id, JOB_URL + job_id, job)


def _content(raw):
    title = strip_html_tags(_s(raw.get("title"))).strip()
    company = strip_html_tags(_s(raw.get("companyName"))).strip() or "Unknown"
    description = strip_html_tags(_s(raw.get("description")) or _s(raw.get("summary")))
    location = _s(raw.get("location")).strip() or "Switzerland"
    content = {
        "title": title,
        "company": company,
        "description": description,
        "location": location,
        "remote": False,
        "tags": extract_job_skills(title, description)[:15],
        "salary": None,
    }
    canton = swiss_canton(location)
    if canton:
        content["canton"] = canton
    return content


def register_handlers():
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (
            strip_html_tags(_s(raw.get("title"))).strip(),
            strip_html_tags(_s(raw.get("companyName"))).strip() or "Unknown",
        ),
    )


async def _page(http, page):
    async with http.stream(
        "GET",
        LISTING_URL,
        params={"page": page},
        timeout=25,
        follow_redirects=True,
        headers=BROWSER_HEADERS,
    ) as response:
        response.raise_for_status()
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ProviderResponseError("financejobs response exceeds byte budget")
            chunks.append(chunk)
    match = _NEXT_DATA.search(b"".join(chunks).decode("utf-8", "replace"))
    if match is None:
        raise ProviderResponseError("financejobs: no __NEXT_DATA__")
    try:
        data = json.loads(match.group(1))
    except (ValueError, RecursionError) as exc:
        # ValueError, not JSONDecodeError: a >4300-digit number raises the base.
        raise ProviderResponseError("financejobs: unreadable __NEXT_DATA__") from exc
    if not isinstance(data, dict):
        raise ProviderResponseError("financejobs: __NEXT_DATA__ is not an object")
    block = _jobs_ssr(data)
    if block is None:
        raise ProviderResponseError("financejobs: unknown __NEXT_DATA__ shape")
    jobs = block.get("jobs")
    if not isinstance(jobs, list):
        raise ProviderResponseError("financejobs: jobsSSR.jobs is not a list")
    return jobs


class FinancejobsProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ()

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError("financejobs scope takes no query parameters")
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        listings, invalid, pages, seen = [], 0, 0, 0
        exhausted, error = False, None
        loop = asyncio.get_running_loop()
        started = loop.time()
        for page in range(1, budget + 1):
            if loop.time() - started >= SWEEP_BUDGET_S:
                error = "time_budget"
                break
            try:
                jobs = await _page(http, page)
            except Exception as exc:
                if not listings:
                    raise
                error = type(exc).__name__
                break
            pages += 1
            seen += len(jobs)
            if not jobs:
                exhausted = True
                break
            for job in jobs:
                listing = _listing(job)
                if listing is None:
                    invalid += 1
                else:
                    listings.append(listing)
            if page < budget:
                await asyncio.sleep(PAGE_PAUSE_S)
        if seen and not listings:
            # The page carried offers and none survived: the portal changed
            # shape. Reporting an empty harvest here is the original defect.
            raise ProviderResponseError(
                "financejobs: nonempty feed has no usable identities"
            )
        error = error or ("invalid_financejobs_items" if invalid else None)
        exhausted = exhausted or (MAX_PAGES_PARAM in params and pages >= budget)
        return FetchResult(
            tuple(listings),
            {"pages": pages, "items_seen": seen},
            pages_fetched=pages,
            complete=exhausted and error is None,
            error=error,
        )

"""Bounded Jobgether producer; no browser impersonation or retry of access blocks.

The legacy identity removes the volatile ObjectId prefix but retains company,
title and canonical slug. Retain that identity at handover and the REAL URL in
each raw listing. Three pages is the former request budget, not proof that the
whole source was consumed: reaching the cap with more/unknown pages is partial.
Live access returned 403 during preparation; this provider is not activated.
"""

import asyncio
import hashlib
import math
import re
import time

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.providers.rss_text import extract_job_skills
from jobhunt_core.harvest.providers.browser_headers import (
    BROWSER_HEADERS,
    MAX_PAGES_PARAM,
    page_budget,
)
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "jobgether"
API_URL = "https://jobgether.com/astroapi/offer/search"
OFFER_URL = "https://jobgether.com/offer/"
MAX_PAGES = 3
PAGE_PAUSE_S = 3
SWEEP_BUDGET_S = 120
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_VOLATILE_ID = re.compile(r"^[0-9a-f]{24}-")
_NON_REMOTE = {"no remote", "not remote", "on-site", "onsite", "office"}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _object(value):
    return value if isinstance(value, dict) else {}


def _positive_number(value):
    return (type(value) is int and value > 0) or (
        type(value) is float and math.isfinite(value) and value > 0
    )


def _salary(raw):
    value = _object(raw.get("salary"))
    average = value.get("average")
    if not _positive_number(average):
        return None
    amounts = []
    for key in ("min", "max"):
        amount = value.get(key)
        if _positive_number(amount):
            amounts.append(str(int(amount)))
    if not amounts:
        return None
    return ("-".join(amounts) + " " + _text(value.get("currency"))).strip()


def _content(raw):
    title = _text(raw.get("title"))
    skills = raw.get("skills")
    skills = skills if isinstance(skills, list) else []
    tags, seen = [], set()
    for value in [_text(_object(s).get("name")) for s in skills] + extract_job_skills(
        title, ""
    ):
        if value and value.lower() not in seen:
            seen.add(value.lower())
            tags.append(value)
    remote = _text(raw.get("remoteOfferType")).lower()
    return {
        "title": title,
        "company": _text(_object(raw.get("companyData")).get("name")),
        "location": _text(raw.get("requiredLocations")),
        "description": "",
        "tags": tags[:15],
        "salary": _salary(raw),
        "remote": bool(remote) and remote not in _NON_REMOTE,
    }


def register_handlers():
    register_normalizer(SOURCE_NAME, _content)

    def identity(raw):
        return raw.get("title"), _object(raw.get("companyData")).get("name")

    register_extractor(SOURCE_NAME, identity)


def _listing(raw):
    if not isinstance(raw, dict):
        return None
    slug = _text(raw.get("slug"))
    title = _text(raw.get("title"))
    # A dot belongs in real slugs (next.js, .net, psy.d): the retiring
    # producer keeps them and rejecting them silently lost 4% of the feed
    # (live probe: 6 of 150). Traversal and anything that could change the
    # resolved URL stay rejected -- the slug is interpolated into it.
    if not title or not re.fullmatch(r"[\w.-]{1,900}", slug) or ".." in slug:
        return None
    company = _text(_object(raw.get("companyData")).get("name"))
    identity = (
        f"{title.lower()}|{company.lower()}|{OFFER_URL}{_VOLATILE_ID.sub('', slug)}"
    )
    try:
        external_id = hashlib.md5(identity.encode()).hexdigest()
    except UnicodeError:
        return None
    return RawListing(external_id, OFFER_URL + slug, raw)


async def _page(http, query, page, timeout):
    async with asyncio.timeout(timeout):
        async with http.stream(
            "GET",
            API_URL,
            params={"keyword": query, "page": page},
            headers=BROWSER_HEADERS,
            timeout=25,
            follow_redirects=True,
        ) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ProviderResponseError(
                        "Jobgether response byte budget exceeded"
                    )
                chunks.append(chunk)
    try:
        body = httpx.Response(200, content=b"".join(chunks)).json()
    except (ValueError, UnicodeError) as exc:
        raise ProviderResponseError("Jobgether invalid JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ProviderResponseError("Jobgether invalid envelope")
    total = body.get("maxPages")
    if (
        isinstance(total, str)
        and total.isascii()
        and total.isdigit()
        and len(total) <= 9
    ):
        total = int(total)
    if total is not None and (type(total) is not int or total < 0):
        raise ProviderResponseError("Jobgether invalid page count")
    rows = body["data"]
    if (not rows and total is not None and page < total) or (rows and total == 0):
        raise ProviderResponseError("Jobgether page/count contradiction")
    return rows, not rows or (total is not None and page >= total)


class JobgetherProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ("query",)

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or set(params) - {"query", MAX_PAGES_PARAM}:
            raise ProviderConfigError("Jobgether accepts only query")
        query = params.get("query", "")
        if not isinstance(query, str) or len(query) > 200:
            raise ProviderConfigError("Jobgether query must be a bounded string")
        # Same three-page budget the retiring producer uses (jobgether.py:57).
        budget = page_budget(params, MAX_PAGES) or MAX_PAGES
        started = time.monotonic()
        listings, invalid, pages, seen = [], 0, 0, 0
        error, exhausted = None, False
        for page in range(1, budget + 1):
            remaining = SWEEP_BUDGET_S - (time.monotonic() - started)
            if remaining <= 0:
                error = "time_budget"
                break
            try:
                rows, exhausted = await _page(http, query, page, min(25, remaining))
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
            seen += len(rows)
            for row in rows:
                listing = _listing(row)
                if listing is None:
                    invalid += 1
                else:
                    listings.append(listing)
            if exhausted:
                break
            if page < budget:
                remaining = SWEEP_BUDGET_S - (time.monotonic() - started)
                if remaining > 0:
                    await asyncio.sleep(min(PAGE_PAUSE_S, remaining))
        urls_by_id = {}
        for listing in listings:
            urls_by_id.setdefault(listing.external_id, set()).add(listing.url)
        ambiguous = {key for key, urls in urls_by_id.items() if len(urls) > 1}
        if ambiguous:
            # The portal republishes one opening as several postings sharing
            # title, company and canonical slug. Refusing to pick a winner is
            # the contract and stays. But it happens on EVERY sweep (live probe
            # 2026-09-21: 3 of 139 identities, 8 of 150 listings), and the
            # retiring producer loses the very same rows to ix_jobs_url. Calling
            # it a failed harvest would keep `last_complete_at` NULL forever and
            # turn `cosecha_sin_completar` into permanent noise (G9 P2-C). The
            # count travels in the cursor instead, readable without lying.
            listings = [
                listing for listing in listings if listing.external_id not in ambiguous
            ]
        if seen and not listings:
            raise ProviderResponseError(
                "Jobgether nonempty feed has no usable identities"
            )
        error = error or ("invalid_jobgether_items" if invalid else None)
        if not exhausted and MAX_PAGES_PARAM not in params:
            # Undeclared: hitting the internal safety cap is still partial.
            error = error or "page_budget"
        exhausted = exhausted or (MAX_PAGES_PARAM in params and pages >= budget)
        return FetchResult(
            tuple(listings),
            {"pages": pages, "items_seen": seen, "ambiguous": len(ambiguous)},
            pages_fetched=pages,
            complete=exhausted and error is None,
            error=error,
        )

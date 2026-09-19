"""The Hub remote listings plus bounded per-offer detail, raw retained.

A failed detail is not emitted as a sparse revision: unlike the old local
upsert, the core stores immutable complete canonical revisions. Keep the
previous good incarnation and report a partial harvest instead of erasing it.
"""
import asyncio
import re
import time

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import BaseProvider, ProviderConfigError, ProviderResponseError
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "thehub"
API_URL = "https://api.thehub.io/v2/jobs"
DETAIL_URL = "https://api.thehub.io/jobs/single/"
PUBLIC_URL = "https://thehub.io/jobs/"
MAX_PAGES = 20
PAUSE_S = 0.5
SWEEP_BUDGET_S = 600
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_OBJECT_ID = re.compile(r"^[0-9a-f]{24}$")


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _id(value):
    value = _text(value)
    return value if _OBJECT_ID.fullmatch(value) else ""


def _content(raw):
    title = _text(raw.get("title"))
    company = raw.get("company")
    location = raw.get("location")
    company = _text(company.get("name")) if isinstance(company, dict) else ""
    location = _text(location.get("address") or location.get("locality")) if isinstance(location, dict) else ""
    description = strip_html_tags(_text(raw.get("description")))
    return {"title": title, "company": company, "location": location, "description": description,
            "remote": bool(raw.get("isRemote", False)), "tags": extract_job_skills(title, description)[:15]}


def register_handlers():
    register_normalizer(SOURCE_NAME, _content)
    def identity(raw):
        content = _content(raw)
        return content["title"], content["company"]
    register_extractor(SOURCE_NAME, identity)


async def _json(http, url, remaining, params=None):
    if remaining <= 0:
        raise TimeoutError()
    async with asyncio.timeout(min(25, remaining)):
        async with http.stream("GET", url, params=params, timeout=25, follow_redirects=True,
                               headers={"User-Agent": "SwissJobHunter/1.0"}) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ProviderResponseError("TheHub response byte budget exceeded")
                chunks.append(chunk)
    try:
        return httpx.Response(200, content=b"".join(chunks)).json()
    except (ValueError, UnicodeError) as exc:
        raise ProviderResponseError("TheHub invalid JSON") from exc


def _page(body, page):
    if not isinstance(body, dict) or not isinstance(body.get("docs"), list):
        raise ProviderResponseError("TheHub invalid listing envelope")
    total = body.get("pages")
    if isinstance(total, str) and total.isascii() and total.isdecimal() and len(total) <= 6:
        total = int(total)
    if type(total) is not int or total < 0:
        raise ProviderResponseError("TheHub invalid page count")
    rows = body["docs"]
    if (not rows and page < total) or (rows and page > total):
        raise ProviderResponseError("TheHub page contradicts page count")
    return rows, page >= total


class TheHubProvider(BaseProvider):
    name = SOURCE_NAME

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or params:
            raise ProviderConfigError("TheHub remote scope has no query parameters")
        started = time.monotonic()
        listings, pages, seen = [], 0, 0
        exhausted, error = False, None
        for page in range(1, MAX_PAGES + 1):
            remaining = SWEEP_BUDGET_S - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                body = await _json(http, API_URL, remaining, {"isRemote": "true", "page": page})
                rows, exhausted = _page(body, page)
            except (httpx.HTTPError, ProviderResponseError, TimeoutError) as exc:
                if not listings:
                    raise
                error = type(exc).__name__
                break
            pages += 1
            seen += len(rows)
            for index, row in enumerate(rows):
                job_id = _id(row.get("id")) if isinstance(row, dict) else ""
                if not job_id:
                    error = "invalid_thehub_items"
                    continue
                remaining = SWEEP_BUDGET_S - (time.monotonic() - started)
                if remaining <= 0:
                    exhausted, error = False, "budget_exhausted"
                    break
                try:
                    detail = await _json(http, DETAIL_URL + job_id, remaining)
                    if (not isinstance(detail, dict) or _id(detail.get("id")) != job_id
                            or not isinstance(detail.get("description"), str)):
                        raise ProviderResponseError("TheHub invalid/mismatched detail")
                    payload = {**row, **detail}
                    listings.append(RawListing(job_id, PUBLIC_URL + job_id, payload))
                except (httpx.HTTPError, ProviderResponseError, TimeoutError) as exc:
                    error = type(exc).__name__
                if index + 1 < len(rows):
                    await asyncio.sleep(PAUSE_S)
            if exhausted or error == "budget_exhausted":
                break
            if page < MAX_PAGES:
                await asyncio.sleep(PAUSE_S)
        if seen and not listings:
            raise ProviderResponseError("TheHub nonempty feed has no usable detailed offers")
        return FetchResult(tuple(listings), {"items_seen": seen, "pages": pages},
                           pages_fetched=pages, complete=exhausted and error is None, error=error)

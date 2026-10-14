"""Adzuna's credentialed, bounded JSON search used by ReactPortfolio."""

import asyncio
import json
import math
from json import JSONDecodeError
from urllib.parse import urlsplit

import httpx

from jobhunt_core.config import settings
from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.providers.jobicy_metadata import (
    extract_tech_skills,
    normalize_location,
)
from jobhunt_core.harvest.providers.rss_text import strip_html_tags
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "adzuna"
ENDPOINT = "https://api.adzuna.com/v1/api/jobs"
COUNTRIES = ("gb", "us", "de")
PAGES = (1, 2)
RESULTS_PER_PAGE = 50
QUERY = "remote+developer"
QUERY_OR = "remote+software+developer+engineer+python+react+fullstack"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
PAGE_DELAY_SECONDS = 0.5
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}


class _RetryablePageError(RuntimeError):
    """Sanitized internal marker: never carries the credentialed request URL."""


def _dict(value):
    return value if isinstance(value, dict) else {}


def _title(raw) -> str:
    value = raw.get("title") if isinstance(raw, dict) else None
    return strip_html_tags(value) if isinstance(value, str) else ""


def _company(raw) -> str:
    value = _dict(raw.get("company")).get("display_name")
    return value.strip() if isinstance(value, str) else ""


def _category(raw) -> str:
    value = _dict(raw.get("category")).get("label")
    return value if isinstance(value, str) and value else "Other"


def _location(raw) -> str:
    location = _dict(raw.get("location"))
    area = location.get("area")
    value = area[0] if isinstance(area, list) and area else location.get("display_name")
    if not value:
        return "Worldwide"
    return normalize_location(value) or "Unknown"


def _positive_number(value):
    if type(value) is int:
        return value if value > 0 else None
    if type(value) is float:
        return value if math.isfinite(value) and value > 0 else None
    return None


def _employment_type(raw) -> str | None:
    values = [raw.get("contract_type"), raw.get("contract_time")]
    parts = [value for value in values if isinstance(value, str) and value]
    return " ".join(parts) or None


def _content(raw):
    title = _title(raw)
    description = raw.get("description")
    description = description if isinstance(description, str) else ""
    return {
        "title": title,
        "company": _company(raw),
        "description": strip_html_tags(description),
        "location": _location(raw),
        "tags": extract_tech_skills(title, description),
        "remote": False,
        "salary": None,
        "salary_min": _positive_number(raw.get("salary_min")),
        "salary_max": _positive_number(raw.get("salary_max")),
        "employment_type": _employment_type(raw),
    }


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(SOURCE_NAME, lambda raw: (_title(raw), _company(raw)))


def _identity(raw) -> str:
    value = raw.get("id") if isinstance(raw, dict) else None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value).strip()


def _url(raw) -> str:
    value = raw.get("redirect_url") if isinstance(raw, dict) else None
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return value


def _listing(raw, country, source_order):
    identity, url, title = _identity(raw), _url(raw), _title(raw)
    if not identity or not url or not title:
        return None
    payload = {
        **raw,
        "_country": country,
        "_core_category": _category(raw),
        "_core_order": source_order,
    }
    for field in ("salary_min", "salary_max"):
        if field in payload:
            payload[field] = _positive_number(payload[field])
    return RawListing(
        external_id=identity,
        url=url,
        payload=payload,
    )


async def _body(http, url: str, params: dict) -> dict:
    try:
        async with http.stream(
            "GET",
            url,
            params=params,
            timeout=30,
            follow_redirects=False,
            headers={"User-Agent": "SwissJobHunter/1.0", "Accept": "application/json"},
        ) as response:
            if response.status_code in RETRY_STATUSES:
                raise _RetryablePageError(f"http_{response.status_code}")
            if response.status_code != 200:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: HTTP {response.status_code}"
                )
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ProviderResponseError(
                        f"{SOURCE_NAME}: response exceeds byte budget"
                    )
                chunks.append(chunk)
    except _RetryablePageError:
        raise
    except ProviderResponseError:
        raise
    except httpx.HTTPError:
        raise _RetryablePageError("transport") from None
    try:
        value = json.loads(b"".join(chunks))
    except (JSONDecodeError, UnicodeError):
        raise _RetryablePageError("invalid_json") from None
    if not isinstance(value, dict):
        raise ProviderResponseError(f"{SOURCE_NAME}: invalid response envelope")
    return value


async def _page(http, country: str, page: int) -> list:
    params = {
        "app_id": settings.CORE_ADZUNA_APP_ID,
        "app_key": settings.CORE_ADZUNA_APP_KEY,
        "results_per_page": RESULTS_PER_PAGE,
        "what": QUERY,
        "what_or": QUERY_OR,
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            body = await _body(
                http,
                f"{ENDPOINT}/{country}/search/{page}",
                params,
            )
            rows = body.get("results")
            if not isinstance(rows, list):
                raise ProviderResponseError(f"{SOURCE_NAME}: invalid jobs collection")
            return rows
        except _RetryablePageError as exc:
            if attempt == MAX_RETRIES:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: page request failed ({exc})"
                ) from None
            await asyncio.sleep(min(2**attempt, 300))
    raise AssertionError("unreachable")


class AdzunaProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ("country",)

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if (
            not isinstance(params, dict)
            or set(params) != {"country"}
            or params.get("country") not in COUNTRIES
        ):
            raise ProviderConfigError("Adzuna scope requires one verified country")
        if not settings.CORE_ADZUNA_APP_ID or not settings.CORE_ADZUNA_APP_KEY:
            raise ProviderConfigError("Adzuna credentials are not configured")

        country = params["country"]
        rows, pages, fetch_error = [], 0, None
        for page in PAGES:
            try:
                page_rows = await _page(http, country, page)
            except ProviderResponseError:
                if not rows:
                    raise
                fetch_error = "page_fetch_failed"
                break
            pages += 1
            country_offset = COUNTRIES.index(country) * len(PAGES) * RESULTS_PER_PAGE
            page_offset = (page - 1) * RESULTS_PER_PAGE
            rows.extend(
                (raw, country_offset + page_offset + index)
                for index, raw in enumerate(page_rows)
            )
            await asyncio.sleep(PAGE_DELAY_SECONDS)

        listings = tuple(
            item
            for raw, source_order in rows
            if (item := _listing(raw, country, source_order)) is not None
        )
        invalid = len(rows) - len(listings)
        if rows and not listings:
            raise ProviderResponseError(
                f"{SOURCE_NAME}: nonempty feed has no usable jobs"
            )
        error = fetch_error or ("invalid_adzuna_items" if invalid else None)
        return FetchResult(
            listings,
            {"items_seen": len(rows)},
            pages_fetched=pages,
            complete=error is None,
            error=error,
        )

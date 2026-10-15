"""JSearch transport shared by the scheduled harvest and live Core search."""

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
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
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "jsearch"
ENDPOINT = "https://jsearch.p.rapidapi.com/search"
SOURCE_URL_PREFIX = "https://jsearch.p.rapidapi.com/job/"
RAPIDAPI_HOST = "jsearch.p.rapidapi.com"
DEFAULT_QUERY = "software developer"
DEFAULT_COUNTRY = "us"
DEFAULT_DATE_POSTED = "month"
DEFAULT_PAGE = 1
DEFAULT_NUM_PAGES = 10
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}


class _RetryableSearchError(RuntimeError):
    """Sanitized marker that never carries request headers or URL values."""


@dataclass(frozen=True)
class SearchParams:
    query: str
    page: int = 1
    num_pages: int = 1
    country: str = "us"
    date_posted: str = "all"
    employment_type: str | None = None
    remote_only: bool = False


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def _finite_number(value):
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    return None


def _location(raw: dict) -> str:
    parts = (_text(raw, "job_city"), _text(raw, "job_state"), _text(raw, "job_country"))
    return ", ".join(part for part in parts if part) or "Remote"


def _content(raw: dict) -> dict:
    description = raw.get("job_description")
    return {
        "title": _text(raw, "job_title"),
        "company": _text(raw, "employer_name"),
        "description": description if isinstance(description, str) else "",
        "location": _location(raw),
        "tags": [],
        "remote": bool(raw.get("job_is_remote", False)),
        "salary": None,
        "salary_min": _finite_number(raw.get("job_min_salary")),
        "salary_max": _finite_number(raw.get("job_max_salary")),
        "salary_currency": _text(raw, "job_salary_currency") or None,
        "employment_type": _text(raw, "job_employment_type") or None,
        "logo": _text(raw, "employer_logo") or None,
    }


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (_text(raw, "job_title"), _text(raw, "employer_name")),
    )


def _provider_id(raw: dict) -> str:
    value = raw.get("job_id")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value).strip()


def _identity(raw: dict) -> str:
    value = _provider_id(raw)
    if len(value) <= 200:
        return value
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _url(raw: dict) -> str:
    value = raw.get("job_apply_link")
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


def _listing(raw: dict, source_order: int) -> RawListing | None:
    provider_id = _provider_id(raw)
    identity, apply_url, title = _identity(raw), _url(raw), _text(raw, "job_title")
    if not identity or not apply_url or not title:
        return None
    payload = {**raw, "_core_order": source_order}
    for field in ("job_min_salary", "job_max_salary"):
        if field in payload:
            payload[field] = _finite_number(payload[field])
    source_url = SOURCE_URL_PREFIX + hashlib.sha256(provider_id.encode()).hexdigest()
    return RawListing(
        external_id=identity,
        url=source_url,
        apply_url=apply_url,
        payload=payload,
    )


def _request_params(params: SearchParams) -> dict[str, str | int]:
    values: dict[str, str | int] = {
        "query": params.query,
        "page": params.page,
        "num_pages": params.num_pages,
        "country": params.country,
        "date_posted": params.date_posted,
    }
    if params.employment_type:
        values["employment_types"] = params.employment_type
    if params.remote_only:
        values["remote_jobs_only"] = "true"
    return values


async def search_raw(http: httpx.AsyncClient, params: SearchParams) -> list[dict]:
    """Fetch one JSearch request with a bounded, sanitized response contract."""
    if not settings.CORE_JSEARCH_RAPIDAPI_KEY:
        raise ProviderConfigError("JSearch credential is not configured")
    headers = {
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key": settings.CORE_JSEARCH_RAPIDAPI_KEY,
        "Accept": "application/json",
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with http.stream(
                "GET",
                ENDPOINT,
                params=_request_params(params),
                headers=headers,
                timeout=30,
                follow_redirects=False,
            ) as response:
                if response.status_code in RETRY_STATUSES:
                    raise _RetryableSearchError(f"http_{response.status_code}")
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
            try:
                body = json.loads(b"".join(chunks))
            except (JSONDecodeError, UnicodeError):
                raise _RetryableSearchError("invalid_json") from None
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise ProviderResponseError(f"{SOURCE_NAME}: invalid response envelope")
            if not all(isinstance(row, dict) for row in body["data"]):
                raise ProviderResponseError(f"{SOURCE_NAME}: invalid jobs collection")
            return body["data"]
        except _RetryableSearchError as exc:
            if attempt == MAX_RETRIES:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: search request failed ({exc})"
                ) from None
            await asyncio.sleep(min(2**attempt, 300))
        except ProviderResponseError:
            raise
        except httpx.HTTPError:
            if attempt == MAX_RETRIES:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: search request failed (transport)"
                ) from None
            await asyncio.sleep(min(2**attempt, 300))
    raise AssertionError("unreachable")


def live_job(raw: dict) -> dict:
    """Exact public shape of ReactPortfolio's historical live-search result."""
    description = raw.get("job_description")
    return {
        "job_id": raw.get("job_id"),
        "title": raw.get("job_title"),
        "employer": raw.get("employer_name"),
        "employer_logo": raw.get("employer_logo"),
        "location": _location(raw),
        "is_remote": bool(raw.get("job_is_remote", False)),
        "employment_type": raw.get("job_employment_type"),
        "publisher": raw.get("job_publisher"),
        "apply_link": raw.get("job_apply_link"),
        "posted_at": raw.get("job_posted_at_datetime_utc"),
        "min_salary": _finite_number(raw.get("job_min_salary")),
        "max_salary": _finite_number(raw.get("job_max_salary")),
        "salary_currency": raw.get("job_salary_currency"),
        "description_snippet": (
            description[:300] + "..."
            if isinstance(description, str) and description
            else ""
        ),
    }


class JSearchProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ("query", "country", "date_posted", "page", "num_pages")

    def __init__(self, search_params: SearchParams | None = None):
        self._search_params = search_params
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        expected = {
            "query": DEFAULT_QUERY,
            "country": DEFAULT_COUNTRY,
            "date_posted": DEFAULT_DATE_POSTED,
            "page": DEFAULT_PAGE,
            "num_pages": DEFAULT_NUM_PAGES,
        }
        if not isinstance(params, dict) or params != expected:
            raise ProviderConfigError(
                "JSearch scope differs from the verified contract"
            )
        if self._search_params is None:
            search_params = SearchParams(
                query=params["query"],
                country=params["country"],
                date_posted=params["date_posted"],
                page=params["page"],
                num_pages=params["num_pages"],
            )
        else:
            search_params = self._search_params
        rows = await search_raw(http, search_params)
        listings_list, seen_ids, invalid = [], set(), 0
        for index, raw in enumerate(rows):
            item = _listing(raw, index)
            if item is None:
                invalid += 1
                continue
            if item.external_id in seen_ids:
                continue
            seen_ids.add(item.external_id)
            listings_list.append(item)
        listings = tuple(listings_list)
        if rows and not listings:
            raise ProviderResponseError(
                f"{SOURCE_NAME}: nonempty feed has no usable jobs"
            )
        return FetchResult(
            listings,
            {"items_seen": len(rows), "last_query": search_params.query},
            pages_fetched=search_params.num_pages,
            complete=invalid == 0,
            error="invalid_jsearch_items" if invalid else None,
        )

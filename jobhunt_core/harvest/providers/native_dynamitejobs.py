"""DynamiteJobs' bounded public-category sweep and detail enrichment."""

import asyncio
import json
import logging
import math
import random
import re
from datetime import datetime, timezone
from json import JSONDecodeError

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    SOFT_BLOCK_STATUS,
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
    SoftBlocked,
)
from jobhunt_core.harvest.providers.browser_headers import BROWSER_HEADERS
from jobhunt_core.harvest.providers.html_response import job_posting_description
from jobhunt_core.harvest.providers.jobicy_metadata import COUNTRIES
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "dynamitejobs"
BASE_URL = "https://dynamitejobs.com"
CATEGORY_SLUGS = (
    "remote-admin-va-jobs",
    "remote-business-development-jobs",
    "remote-community-manager-jobs",
    "remote-crypto-web3-blockchain-jobs",
    "remote-customer-support-jobs",
    "remote-data-analyst-jobs",
    "remote-design-jobs",
    "remote-development-jobs",
    "remote-e-commerce-jobs",
    "remote-finance-accounting-jobs",
    "remote-hr-recruiter-jobs",
    "remote-management-operations-jobs",
    "remote-marketing-jobs",
    "remote-multimedia-jobs",
    "remote-paid-ads-jobs",
    "remote-product-jobs",
    "remote-seo-jobs",
    "remote-teacher-coach-jobs",
    "remote-technical-support-jobs",
    "remote-writing-editing-jobs",
)
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DETAIL_BYTES = 2 * 1024 * 1024
MAX_RECENT_JOBS = 300
MAX_DETAIL_FETCHES = 20
MAX_DESCRIPTION_LENGTH = 500
REQUEST_TIMEOUT_S = 30
REQUEST_PAUSE_S = 0.5
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}
_PINIA_STATE_RE = re.compile(r'window\.__PINIA_STATE__\s*=\s*("(?:[^"\\]|\\.)*")', re.S)
_PATH_SEGMENT_RE = re.compile(r"[\w.-]{1,300}")
_REGION_LABELS = {
    "europe": "Europe",
    "northamerica": "North America",
    "latinamerica": "Latin America",
    "southamerica": "South America",
    "asia": "Asia",
    "africa": "Africa",
    "oceania": "Oceania",
    "australia": "Australia",
    "anywhere": "Worldwide",
}
_SALARY_ANNUAL_FACTOR = {"yearly": 1, "monthly": 12, "hourly": 2080}

logger = logging.getLogger(__name__)


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value):
    if type(value) is int and value > 0:
        return value
    if type(value) is float and math.isfinite(value) and value > 0:
        return value
    return None


def _finite(value) -> bool:
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


def _identity(raw: dict) -> str:
    value = raw.get("id")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    value = str(value).strip()
    return value if len(value) <= 200 else ""


def _public_url(raw: dict) -> str:
    company = _text(_object(raw.get("company")).get("username"))
    slug = _text(raw.get("slug"))
    if (
        not _PATH_SEGMENT_RE.fullmatch(company)
        or not _PATH_SEGMENT_RE.fullmatch(slug)
        or ".." in company
        or ".." in slug
    ):
        return ""
    return f"{BASE_URL}/company/{company}/remote-job/{slug}"


def _skill_names(raw: dict) -> list[str]:
    values = raw.get("skills")
    if not isinstance(values, list):
        return []
    names = []
    for value in values:
        name = (
            _text(_object(value).get("name"))
            if isinstance(value, dict)
            else _text(value)
        )
        if name:
            names.append(name)
    return names


def _format_location(value) -> str:
    slug = _text(value)
    if not slug:
        return ""
    if slug in _REGION_LABELS:
        return _REGION_LABELS[slug]
    if slug.isupper():
        return COUNTRIES.get(slug.lower(), slug)
    return slug.title()


def _locations(raw: dict) -> list[str]:
    values = raw.get("locationSlugs")
    if not isinstance(values, list):
        return []
    return [label for value in values if (label := _format_location(value))]


def _published_at(raw: dict) -> str:
    value = raw.get("publishedAt")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return ""
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _salary(raw: dict):
    value = _object(raw.get("salary"))
    factor = _SALARY_ANNUAL_FACTOR.get(value.get("type"))
    if value.get("public") is not True or factor is None:
        return None, None, None
    low, high = _number(value.get("from")), _number(value.get("to"))
    return (
        low * factor if low is not None else None,
        high * factor if high is not None else None,
        _text(value.get("currency")) or None,
    )


def _content(raw: dict) -> dict:
    locations = _locations(raw)
    low, high, currency = _salary(raw)
    job_type = _object(_object(raw.get("type")).get("name"))
    return {
        "title": _text(raw.get("title")),
        "company": _text(_object(raw.get("company")).get("name")),
        "description": _text(raw.get("_core_description")),
        "location": ", ".join(locations) or "Worldwide",
        "tags": _skill_names(raw)[:10],
        "remote": True,
        "salary": None,
        "salary_min": low,
        "salary_max": high,
        "salary_currency": currency,
        "employment_type": _text(job_type.get("display")) or None,
    }


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (
            _text(raw.get("title")),
            _text(_object(raw.get("company")).get("name")),
        ),
    )


def _is_live(raw: dict, now_ms: float) -> bool:
    flags = _object(raw.get("flags"))
    expires = raw.get("expiresAt")
    return (
        raw.get("status") == "published"
        and not flags.get("isDeleted")
        and flags.get("isPageVisible", True) is not False
        and not (_finite(expires) and expires < now_ms)
    )


def _category_jobs(html: str) -> list[dict]:
    match = _PINIA_STATE_RE.search(html)
    if not match:
        raise ValueError("embedded Pinia state is missing")
    try:
        state = json.loads(json.loads(match.group(1)))
    except (JSONDecodeError, TypeError) as exc:
        raise ValueError("embedded Pinia state is invalid") from exc
    jobs = (
        _object(state.get("categoryJobs")).get("jobs")
        if isinstance(state, dict)
        else None
    )
    if not isinstance(jobs, list):
        raise ValueError("category jobs collection is invalid")
    return jobs


def _description(html: str) -> str:
    return job_posting_description(html, max_length=MAX_DESCRIPTION_LENGTH)


async def _pause() -> None:
    await asyncio.sleep(random.uniform(REQUEST_PAUSE_S, REQUEST_PAUSE_S * 2))


async def _read(http, url: str, byte_limit: int, parser=None):
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with http.stream(
                "GET",
                url,
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
            text = b"".join(chunks).decode("utf-8")
            return parser(text) if parser else text
        except ProviderResponseError:
            raise
        except (httpx.HTTPError, UnicodeError, ValueError) as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                exc.response.status_code in RETRY_STATUSES
            )
            if not retryable or attempt == MAX_RETRIES:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: request failed ({type(exc).__name__})"
                ) from None
            await asyncio.sleep(min(2**attempt, 30))
    raise AssertionError("unreachable")


def _cached_descriptions(cursor) -> dict[str, str]:
    if cursor is None:
        return {}
    if not isinstance(cursor, dict):
        # DT-148 A18: el cursor es CACHÉ que escribe este mismo código, no
        # configuración del scope. Tratarlo como `ProviderConfigError` dejaba el
        # scope fallando en CADA ventana sin contabilizar el fallo (el runner
        # relanza ese error antes de registrarlo), el dispatcher lo reencolaba
        # siempre y nadie podía reiniciarlo sin tocar un parámetro semántico.
        # Un cursor ilegible se DESCARTA con un grito y se parte de cero.
        logger.error("%s: cursor ilegible, se descarta y se parte de cero", SOURCE_NAME)
        return {}
    values = cursor.get("descriptions", {})
    if not isinstance(values, dict) or any(
        not isinstance(key, str)
        or not key
        or not isinstance(value, str)
        or not value
        or len(value) > MAX_DESCRIPTION_LENGTH
        for key, value in values.items()
    ):
        logger.error(
            "%s: caché de descripciones ilegible, se descarta y se vuelve a "
            "pedir el detalle",
            SOURCE_NAME,
        )
        return {}
    return values


async def _fill_descriptions(http, retained, cached):
    """Completa las descripciones que falten, dentro del tope de detalles.

    Devuelve `(descriptions, attempts, fetched, soft_block)`. Fuera de
    `fetch_new` por la misma razón que en jobscout24: es otra
    responsabilidad, y dentro llevaba la función a grado E (DT-148 A8).
    """
    retained_ids = {_identity(raw) for raw in retained}
    descriptions = {key: value for key, value in cached.items() if key in retained_ids}
    attempts = fetched = 0
    soft_block = None
    for raw in retained:
        identity = _identity(raw)
        if identity in descriptions or attempts >= MAX_DETAIL_FETCHES:
            continue
        attempts += 1
        await _pause()
        try:
            description = await _read(
                http, _public_url(raw), MAX_DETAIL_BYTES, _description
            )
        except SoftBlocked:
            # Se deja de pedir detalles AHORA y el bloqueo viaja al runner
            # (antes: cuatro reintentos por detalle y hasta 20 detalles,
            # contra un portal que nos pide parar) — DT-148 A8.
            logger.error(
                "%s: desafío anti-bot en el detalle de %r — se detiene el "
                "pase de detalles",
                SOURCE_NAME,
                identity,
            )
            soft_block = SOFT_BLOCK_STATUS
            break
        except (httpx.HTTPError, ProviderResponseError):
            logger.warning("%s: detail fetch failed for %r", SOURCE_NAME, identity)
            continue
        if description:
            descriptions[identity] = description
            fetched += 1
    return descriptions, attempts, fetched, soft_block


class DynamiteJobsProvider(BaseProvider):
    name = SOURCE_NAME

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or params:
            raise ProviderConfigError("DynamiteJobs scope has no query parameters")
        cached = _cached_descriptions(cursor)
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        rows, seen, invalid = [], set(), 0

        for index, slug in enumerate(CATEGORY_SLUGS):
            if index:
                await _pause()
            category = await _read(
                http,
                f"{BASE_URL}/category/{slug}",
                MAX_RESPONSE_BYTES,
                _category_jobs,
            )
            for raw in category:
                if not isinstance(raw, dict):
                    invalid += 1
                    continue
                identity = _identity(raw)
                if not identity:
                    invalid += 1
                    continue
                if identity in seen:
                    continue
                seen.add(identity)
                if not _is_live(raw, now_ms):
                    continue
                if not _public_url(raw) or not _text(raw.get("title")):
                    invalid += 1
                    continue
                rows.append(raw)

        retained = rows[:MAX_RECENT_JOBS]
        descriptions, attempts, fetched, soft_block = await _fill_descriptions(
            http, retained, cached
        )

        recent_order = {
            _identity(raw): index
            for index, raw in enumerate(
                sorted(retained, key=lambda raw: _published_at(raw), reverse=True)
            )
        }
        next_order = len(recent_order)
        listings = []
        for raw in rows:
            identity = _identity(raw)
            locations = _locations(raw)
            tags = _skill_names(raw)
            order = recent_order.get(identity)
            if order is None:
                order, next_order = next_order, next_order + 1
            listings.append(
                RawListing(
                    external_id=identity,
                    url=_public_url(raw),
                    payload={
                        **raw,
                        "_core_description": descriptions.get(identity, ""),
                        "_core_published_at": _published_at(raw),
                        "_core_order": order,
                        "_core_statistics_location": (
                            locations[0] if locations else "Worldwide"
                        ),
                        "_core_statistics_tags": [tag.lower() for tag in tags],
                    },
                )
            )
        if invalid and not listings:
            raise ProviderResponseError(
                f"{SOURCE_NAME}: nonempty categories have no usable identities"
            )
        if invalid:
            logger.warning("%s: %d invalid items", SOURCE_NAME, invalid)
        return FetchResult(
            tuple(listings),
            {
                "items_seen": len(seen),
                "recent_items": len(retained),
                "details_cached": len(descriptions),
                "detail_attempts": attempts,
                "details_fetched": fetched,
                "descriptions": descriptions,
            },
            pages_fetched=len(CATEGORY_SLUGS),
            complete=not invalid and soft_block is None,
            error=("invalid_dynamitejobs_items" if invalid else None)
            or ("soft_block" if soft_block is not None else None),
            blocked_with=soft_block,
        )

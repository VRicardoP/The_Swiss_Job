"""SwissDevJobs' light feed plus bounded, cursor-backed descriptions."""

import asyncio
import json
import logging
from json import JSONDecodeError

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.providers.jobicy_metadata import normalize_location
from jobhunt_core.harvest.providers.rss_text import strip_html_tags
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "swissdevjobs"
FEED_URL = "https://swissdevjobs.ch/api/jobsLight"
DETAIL_URL = "https://swissdevjobs.ch/jobs/"
LOGO_URL = "https://static.swissdevjobs.ch/logo-images/"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_DETAIL_BYTES = 2 * 1024 * 1024
MAX_RECENT_JOBS = 300
MAX_DETAIL_FETCHES = 20
MAX_DESCRIPTION_LENGTH = 500
DETAIL_PAUSE_SECONDS = 0.5
DETAIL_MARKER = "window.__detailedJob="

logger = logging.getLogger(__name__)


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return value


def _identity(raw) -> str:
    slug = _text(raw.get("jobUrl"))
    if slug:
        return slug
    value = raw.get("_id")
    return (
        str(value).strip()
        if isinstance(value, (str, int)) and not isinstance(value, bool)
        else ""
    )


def _public_url(raw) -> str:
    slug = _text(raw.get("jobUrl"))
    return DETAIL_URL + slug if slug else ""


def _location(raw) -> str:
    value = raw.get("cityCategory") or raw.get("actualCity")
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.strip().lower() == "remote"
    ):
        return "Switzerland"
    return normalize_location(value) or "Switzerland"


def _tags(raw) -> list:
    value = raw.get("technologies")
    return value if isinstance(value, list) else []


def _content(raw):
    salary_min = _positive_number(raw.get("annualSalaryFrom"))
    salary_max = _positive_number(raw.get("annualSalaryTo"))
    logo = _text(raw.get("logoImg"))
    return {
        "title": raw.get("name"),
        "company": raw.get("company"),
        "description": raw.get("_core_description"),
        "location": _location(raw),
        "tags": _tags(raw)[:10],
        "remote": raw.get("workplace") == "remote"
        or raw.get("remoteType") == "anywhere",
        "salary": None,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": "CHF" if salary_min or salary_max else None,
        "employment_type": raw.get("jobType"),
        "logo": LOGO_URL + logo if logo else None,
    }


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)

    def identity(raw):
        content = _content(raw)
        return content["title"], content["company"]

    register_extractor(SOURCE_NAME, identity)


async def _read(http, url: str, byte_limit: int) -> bytes:
    async with http.stream(
        "GET",
        url,
        timeout=25,
        follow_redirects=True,
        headers={"User-Agent": "SwissJobHunter/1.0"},
    ) as response:
        response.raise_for_status()
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > byte_limit:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: response exceeds byte budget"
                )
            chunks.append(chunk)
    return b"".join(chunks)


async def _feed(http) -> list:
    body = await _read(http, FEED_URL, MAX_RESPONSE_BYTES)
    try:
        rows = json.loads(body)
    except (JSONDecodeError, UnicodeError) as exc:
        raise ProviderResponseError(f"{SOURCE_NAME}: invalid JSON") from exc
    if not isinstance(rows, list):
        raise ProviderResponseError(f"{SOURCE_NAME}: invalid jobs collection")
    return rows


def _description(html: str) -> str:
    marker_at = html.find(DETAIL_MARKER)
    if marker_at < 0:
        return ""
    try:
        payload, _ = json.JSONDecoder().raw_decode(
            html[marker_at + len(DETAIL_MARKER) :].lstrip()
        )
    except (JSONDecodeError, UnicodeError):
        return ""
    value = payload.get("description") if isinstance(payload, dict) else None
    return (
        strip_html_tags(value)[:MAX_DESCRIPTION_LENGTH]
        if isinstance(value, str)
        else ""
    )


async def _fetch_description(http, url: str) -> str:
    body = await _read(http, url, MAX_DETAIL_BYTES)
    try:
        return _description(body.decode("utf-8"))
    except UnicodeError:
        return ""


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
    if "descriptions" not in cursor:
        return {}
    values = cursor["descriptions"]
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


class SwissDevJobsProvider(BaseProvider):
    name = SOURCE_NAME

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or params:
            raise ProviderConfigError("SwissDevJobs scope has no query parameters")

        cached_descriptions = _cached_descriptions(cursor)
        rows = await _feed(http)
        valid, invalid = [], 0
        for raw in rows:
            if isinstance(raw, dict) and raw.get("isPaused"):
                continue
            if not isinstance(raw, dict) or not _identity(raw) or not _public_url(raw):
                invalid += 1
                continue
            valid.append(raw)

        retained = valid[:MAX_RECENT_JOBS]
        active_ids = {_identity(raw) for raw in retained}
        descriptions = {
            key: value
            for key, value in cached_descriptions.items()
            if key in active_ids
        }
        attempts = fetched = 0
        for raw in retained:
            job_id = _identity(raw)
            if job_id in descriptions or attempts >= MAX_DETAIL_FETCHES:
                continue
            attempts += 1
            await asyncio.sleep(DETAIL_PAUSE_SECONDS)
            try:
                description = await _fetch_description(http, _public_url(raw))
            except (httpx.HTTPError, ProviderResponseError):
                logger.warning("%s: detail fetch failed for %r", SOURCE_NAME, job_id)
                continue
            if description:
                descriptions[job_id] = description
                fetched += 1

        listings = tuple(
            RawListing(
                external_id=_identity(raw),
                url=_public_url(raw),
                payload={
                    **raw,
                    "_core_description": descriptions.get(_identity(raw), ""),
                },
            )
            for raw in valid
        )
        if invalid and not listings:
            raise ProviderResponseError(
                f"{SOURCE_NAME}: nonempty feed has no usable identities"
            )
        if invalid:
            logger.warning("%s: %d invalid items", SOURCE_NAME, invalid)
        return FetchResult(
            listings,
            {
                "items_seen": len(rows),
                "invalid": invalid,
                "details_cached": len(descriptions),
                "detail_attempts": attempts,
                "details_fetched": fetched,
                "descriptions": descriptions,
            },
            pages_fetched=1,
            # DT-148 E1: un item ilegible NO congela `last_complete_at`. Antes
            # bastaba uno para marcar el barrido incompleto, y un anuncio
            # permanente dejaba el scope «parcial para siempre» — lo que además
            # impedía que el kill-switch reseteara su contador de bloqueos. El
            # recuento viaja en el cursor y `health` vigila la PROPORCIÓN.
            complete=True,
        )

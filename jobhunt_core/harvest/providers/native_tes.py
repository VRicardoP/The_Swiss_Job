"""TES.com's Swiss vacancies, read from the jobs sitemap TES publishes.

Since 2026-11-17 every TES page a client without JavaScript asks for — the
Switzerland listing this provider used to parse, its tRPC search endpoint and
each vacancy page — answers a "Client Challenge". The one resource left open
is the sitemap `robots.txt` declares (probe of 2026-11-24, authorised by the
owner): `aws-sitemap-jobs-active.xml`, ~7,300 active vacancies worldwide with
`<loc>` and `<lastmod>`. The challenge is not bypassed; only that file is read.

What the sitemap gives, and what it does not:

- The Swiss vacancies are the URLs ending in `-switzerland-<id>` (34 on the
  probe day). The id is the same number the old payload carried, so the
  identity `id:<n>` of every offer already stored is preserved.
- The title comes from the URL slug ("teacher-of-mathematics" → "Teacher of
  mathematics"). Capitalisation is lost, and a slug sometimes carries the town
  or school ("...-aiglon-college-chesi-res"): it stays in the title rather
  than being guessed apart.
- No employer, description, salary or contract: the normaliser reports
  "Unknown" and empty text, as for any listing that lacks them.
- `<lastmod>` travels as `advert.startDate`, the date field admission and the
  catalogue already read for this source. It is the portal's last edit, the
  closest date the sitemap offers.

A response that is not that sitemap (an HTML challenge, a renamed file) is a
VISIBLE failure, never "0 offers". A sitemap without Swiss vacancies is a
legitimate, complete empty harvest.
"""

import asyncio
import logging
import random
import re

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
)
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.providers.search_metadata import swiss_canton
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "tes"
BASE_URL = "https://www.tes.com"
SITEMAP_URL = f"{BASE_URL}/aws-sitemap-jobs-active.xml"
VACANCY_PREFIX = f"{BASE_URL}/jobs/vacancy/"
DEFAULT_LOCATION = "Switzerland"
# The sitemap measured 1.7 MB on 2026-11-24; the budget leaves room to grow.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TAGS = 15  # = BaseJobProvider.MAX_TAGS
REQUEST_TIMEOUT_S = 60
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
JITTER_RATIO = 0.5

_URL_RE = re.compile(r"<url>(.*?)</url>", re.S)
_LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>")
_LASTMOD_RE = re.compile(r"<lastmod>\s*([^<]+?)\s*</lastmod>")
_SWISS_SLUG_RE = re.compile(r"^([a-z0-9-]+?)-switzerland-(\d{1,12})$")

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


def _title(slug: str) -> str:
    words = slug.replace("-", " ").split()
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def _vacancy(entry: str) -> dict | None:
    """Payload of one Swiss `<url>` entry, or None for any other vacancy."""
    loc = _LOC_RE.search(entry)
    if not loc or not loc.group(1).startswith(VACANCY_PREFIX):
        return None
    url = loc.group(1)
    match = _SWISS_SLUG_RE.match(url[len(VACANCY_PREFIX) :])
    if not match:
        return None
    lastmod = _LASTMOD_RE.search(entry)
    payload = {
        "id": int(match.group(2)),
        "title": _title(match.group(1)),
        "canonicalUrl": url[len(BASE_URL) :],
        "displayLocation": DEFAULT_LOCATION,
        "_core_url": url,
    }
    if lastmod:
        payload["advert"] = {"startDate": lastmod.group(1)}
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


async def _read(http) -> str:
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with http.stream(
                "GET",
                SITEMAP_URL,
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
        # `max_pages` is still accepted so a scope provisioned for the old
        # listing keeps working; one sitemap is one request whatever it says.
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError("tes scope takes no query parameters")
        entries = _URL_RE.findall(await _read(http))
        if not entries:
            raise ProviderResponseError(
                f"{SOURCE_NAME}: the jobs sitemap carries no <url> entries"
            )
        by_identity: dict[str, dict] = {}
        for entry in entries:
            vacancy = _vacancy(entry)
            if vacancy is not None:
                by_identity.setdefault(str(vacancy["id"]), vacancy)
        listings = tuple(
            RawListing(external_id="id:" + identity, url=job["_core_url"], payload=job)
            for identity, job in by_identity.items()
        )
        return FetchResult(
            listings,
            {
                "sitemap_entries": len(entries),
                "harvested": len(listings),
                "items_seen": len(listings),
                "invalid": 0,
            },
            pages_fetched=1,
            complete=True,
        )

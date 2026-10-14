"""Job-Room's bounded public ICT search."""

import json
from json import JSONDecodeError
from urllib.parse import quote

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.providers.jobicy_metadata import extract_tech_skills
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "jobroom"
ENDPOINT = "https://www.job-room.ch/jobadservice/api/jobAdvertisements/_search"
PUBLIC_BASE_URL = "https://www.job-room.ch/job-search"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
PAGE_SIZE = 300
ONLINE_SINCE_DAYS = 7
PROFESSION_CODES = (
    {"type": "CHISCO3", "value": "250"},
    {"type": "CHISCO3", "value": "251"},
    {"type": "CHISCO3", "value": "252"},
    {"type": "CHISCO3", "value": "351"},
    {"type": "CHISCO3", "value": "352"},
)
ANONYMOUS_COMPANY_LABEL = "Confidential"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _ad(raw):
    return _dict(raw.get("jobAdvertisement")) if isinstance(raw, dict) else {}


def _content_root(raw):
    return _dict(_ad(raw).get("jobContent"))


def _description(raw):
    descriptions = _content_root(raw).get("jobDescriptions")
    return (
        _dict(descriptions[0])
        if isinstance(descriptions, list) and descriptions
        else {}
    )


def _identity(raw):
    value = _ad(raw).get("id")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value).strip()


def _company(raw):
    ad = _ad(raw)
    if _dict(ad.get("publication")).get("companyAnonymous"):
        return ANONYMOUS_COMPANY_LABEL
    return _dict(_content_root(raw).get("company")).get("name")


def _location(raw):
    location = _dict(_content_root(raw).get("location"))
    city, canton = location.get("city"), location.get("cantonCode")
    if city and canton:
        return f"{city}, {canton}"
    return city or canton or "Switzerland"


def _workload(raw):
    employment = _dict(_content_root(raw).get("employment"))
    low = employment.get("workloadPercentageMin")
    high = employment.get("workloadPercentageMax")
    if low is None and high is None:
        return ""
    if low is None:
        return f"{high}%"
    if high is None or low == high:
        return f"{low}%"
    return f"{low}-{high}%"


def _skills(raw):
    description = _description(raw)
    return extract_tech_skills(
        description.get("title", ""),
        description.get("description", "") or "",
        word_boundaries=True,
    )


def _languages(raw):
    values = _content_root(raw).get("languageSkills")
    if not isinstance(values, list):
        return []
    return [
        value["languageIsoCode"]
        for value in values
        if isinstance(value, dict)
        and isinstance(value.get("languageIsoCode"), str)
        and value["languageIsoCode"]
    ]


def _content(raw):
    description = _description(raw)
    employment = _dict(_content_root(raw).get("employment"))
    statistics_tags = (
        raw.get("_core_statistics_tags") if isinstance(raw, dict) else None
    )
    skills = statistics_tags if isinstance(statistics_tags, list) else _skills(raw)
    return {
        "title": description.get("title"),
        "company": _company(raw),
        "description": description.get("description"),
        "location": _location(raw),
        "tags": sorted(set([*skills, *_languages(raw)]))[:10],
        "remote": "HOME_WORK" in (employment.get("workForms") or []),
        "salary": None,
        "employment_type": _workload(raw),
    }


def register_handlers():
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (_description(raw).get("title"), _company(raw)),
    )


def _search_body():
    return {
        "workloadPercentageMin": 0,
        "workloadPercentageMax": 100,
        "onlineSince": ONLINE_SINCE_DAYS,
        "displayRestricted": False,
        "permanent": None,
        "professionCodes": list(PROFESSION_CODES),
        "keywords": [],
        "cantonCodes": [],
        "communalCodes": [],
    }


async def _rows(http):
    async with http.stream(
        "POST",
        ENDPOINT,
        params={"page": 0, "size": PAGE_SIZE, "sort": "date_desc"},
        json=_search_body(),
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": "SwissJobHunter/1.0", "Accept": "application/json"},
    ) as response:
        response.raise_for_status()
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: response exceeds byte budget"
                )
            chunks.append(chunk)
    try:
        rows = json.loads(b"".join(chunks))
    except (JSONDecodeError, UnicodeError) as exc:
        raise ProviderResponseError(f"{SOURCE_NAME}: invalid JSON") from exc
    if not isinstance(rows, list):
        raise ProviderResponseError(f"{SOURCE_NAME}: invalid jobs collection")
    return rows


def _listing(raw):
    identity = _identity(raw)
    title = _description(raw).get("title")
    if not identity or not isinstance(title, str) or not title:
        return None
    return RawListing(
        external_id=identity,
        url=f"{PUBLIC_BASE_URL}/{quote(identity, safe='')}",
        payload={**raw, "_core_statistics_tags": _skills(raw)},
    )


class JobRoomProvider(BaseProvider):
    name = SOURCE_NAME

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or params:
            raise ProviderConfigError("Job-Room scope has no query parameters")
        rows = await _rows(http)
        listings = tuple(item for row in rows if (item := _listing(row)) is not None)
        invalid = len(rows) - len(listings)
        if rows and not listings:
            raise ProviderResponseError(
                f"{SOURCE_NAME}: nonempty feed has no usable jobs"
            )
        return FetchResult(
            listings,
            {"items_seen": len(rows)},
            pages_fetched=1,
            complete=not invalid,
            error="invalid_jobroom_items" if invalid else None,
        )

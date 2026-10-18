"""Native public JSON producers for the F cutover (disabled until provisioned).

Each request refreshes the provider's published feed, not an invented complete
historical archive. Remotive uses the existing 200-item feed; Jobicy uses a
50-item feed PER tag/geo scope; RemoteOK has one global feed; Himalayas keeps
the legacy three pages of 20; SwissTechJobs keeps its two-page WordPress loop;
ICTJobs keeps its five pages of 20 with embedded taxonomies.
These adapters preserve the original object for the sink; parsing canonical
content is separate. They do not import the BFF, access its DB, or enable any
source.

Identity prefers the upstream id; a URL fallback is explicitly weaker when a
portal changes URLs and must be checked in that source's cutover parity test.
No title/company hash and no early-stop on already seen items.
"""

import asyncio
import logging
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

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
from jobhunt_core.harvest.providers.rss_text import extract_job_skills
from jobhunt_core.harvest.providers.search_metadata import search_metadata
from jobhunt_core.harvest.types import FetchResult, RawListing

logger = logging.getLogger(__name__)

ENDPOINTS = {
    "remotive": "https://remotive.com/api/remote-jobs",
    "workingnomads": "https://www.workingnomads.com/api/exposed_jobs/",
    "jobicy": "https://jobicy.com/api/v2/remote-jobs",
    "remoteok": "https://remoteok.com/api",
    "himalayas": "https://himalayas.app/jobs/api",
    "swisstechjobs": "https://www.swisstechjobs.com/wp-json/wp/v2/job-listings",
    "ictjobs": "https://ictjobs.ch/wp-json/wp/v2/posts",
}
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
HIMALAYAS_PAGE_SIZE = 20
HIMALAYAS_MAX_PAGES = 3
HIMALAYAS_PAGE_DELAY_SECONDS = 0.5
WORDPRESS_PAGE_SIZE = 100
WORDPRESS_MAX_PAGES = 2
WORDPRESS_PAGE_DELAY_SECONDS = 0.5
ICTJOBS_PAGE_SIZE = 20
ICTJOBS_MAX_PAGES = 5

# Same title-only exclusions as the previous Working Nomads producer.
WORKINGNOMADS_TECH_EXCLUDE = (
    "software engineer",
    "backend engineer",
    "frontend engineer",
    "full stack",
    "fullstack",
    "devops",
    "sre",
    "site reliability",
    "ml engineer",
    "data engineer",
    "cloud engineer",
    "platform engineer",
    "mobile developer",
    "ios developer",
    "android developer",
    "blockchain",
    "cybersecurity",
    "security engineer",
    "embedded",
    "firmware",
    "hardware engineer",
    "network engineer",
    "infrastructure",
)


class _PlainText(HTMLParser):
    def __init__(self):
        # Legacy keeps entities encoded and separates inline tags; retain
        # that text so changing producer does not silently change embeddings.
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden:
            self.parts.append(" ")

    def handle_entityref(self, name):
        self.handle_data("&" + name + ";")

    def handle_charref(self, name):
        self.handle_data("&#" + name + ";")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value):
    if not isinstance(value, str):
        return None
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split()) or None


def _remoteok_salary(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    value = value * 1000 if value < 1000 else value
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _positive_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return value


def _swisstechjobs_content(raw):
    title_value = raw.get("title")
    content_value = raw.get("content")
    meta = raw.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    title = (
        _plain(title_value.get("rendered")) if isinstance(title_value, dict) else None
    )
    description = (
        _plain(content_value.get("rendered"))
        if isinstance(content_value, dict)
        else None
    )
    salary = meta.get("_job_salary")
    return {
        "title": title,
        "company": meta.get("_company_name"),
        "description": description,
        "location": meta.get("_job_location") or "Switzerland",
        "tags": extract_tech_skills(title, description)[:10],
        "remote": bool(meta.get("_remote_position", 0)),
        "salary": salary,
        "salary_currency": meta.get("_job_salary_currency") or "CHF",
    }


def _ictjobs_terms(raw):
    terms = {"tags": [], "location": None, "prof_group": None, "employment_type": None}
    embedded = raw.get("_embedded")
    for group in embedded.get("wp:term", []) if isinstance(embedded, dict) else []:
        if not isinstance(group, list):
            continue
        for term in group:
            if not isinstance(term, dict):
                continue
            taxonomy, name = term.get("taxonomy"), term.get("name")
            if not isinstance(name, str) or not name:
                continue
            if taxonomy == "post_tag":
                terms["tags"].append(name)
            elif taxonomy in {"ctx_work_location", "ctx_flake_location"}:
                terms["location"] = terms["location"] or name
            elif taxonomy == "ctx_prof_group":
                terms["prof_group"] = terms["prof_group"] or name
            elif taxonomy == "ctx_employment_type":
                terms["employment_type"] = terms["employment_type"] or name
    return terms


def _ictjobs_content(raw):
    title_value = raw.get("title")
    title = (
        _plain(title_value.get("rendered")) if isinstance(title_value, dict) else None
    ) or ""
    acf = raw.get("acf")
    acf = acf if isinstance(acf, dict) else {}
    intro = _plain(acf.get("intro")) or ""
    description = _plain(acf.get("description")) or ""
    full_text = f"{intro} {description}".strip()
    terms = _ictjobs_terms(raw)
    # Preserve the legacy producer's set-derived selection before its 10-tag cap.
    tags = list(set([*terms["tags"], *extract_tech_skills(title, full_text)]))
    salary_min, salary_max = acf.get("salary_min", "0"), acf.get("salary_max", "0")
    try:
        salary_min = int(salary_min) if salary_min and salary_min != "0" else None
        salary_max = int(salary_max) if salary_max and salary_max != "0" else None
    except (TypeError, ValueError):
        salary_min = salary_max = None
    return {
        "title": title,
        "company": "",
        "description": full_text,
        "location": acf.get("location") or terms["location"] or "Switzerland",
        "tags": tags[:10],
        "remote": acf.get("has_home_office", False),
        "salary": None,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": "CHF" if salary_min or salary_max else None,
        "employment_type": terms["employment_type"],
        "prof_group": terms["prof_group"],
    }


def _merge_tags(tags, extracted):
    merged, seen = [], set()
    for tag in [*tags, *extracted]:
        if not isinstance(tag, (str, int, float)) or isinstance(tag, bool):
            continue
        tag = str(tag).strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            merged.append(tag)
    return merged


def _base_content(title, company, description, location, tags):
    return {
        "title": title,
        "company": company,
        "description": description,
        "location": location,
        "tags": tags,
        "remote": True,
        # Preserve the existing canonical boundary; unverified portal salary
        # stays in raw. Source-specific verified salary is added below.
        "salary": None,
    }


def _jobicy_content(raw):
    title = raw.get("jobTitle")
    description = _plain(raw.get("jobDescription"))
    extracted = [
        *extract_tech_skills(title, description),
        *extract_job_skills(title if isinstance(title, str) else "", description or ""),
    ]
    location = normalize_location(raw.get("jobGeo") or raw.get("country"))
    content = _base_content(
        title,
        raw.get("companyName"),
        description,
        location,
        _merge_tags([], extracted)[:15],
    )
    content.update(search_metadata(title, description, location, raw.get("jobType")))
    return content


def _remoteok_content(raw):
    title = raw.get("position") or raw.get("title")
    description = _plain(raw.get("description"))
    tags = raw.get("tags")
    tags = tags if isinstance(tags, list) else []
    content = _base_content(
        title,
        raw.get("company"),
        description,
        normalize_location(raw.get("location")) or "Worldwide",
        tags,
    )
    salary_min = _remoteok_salary(raw.get("salary_min"))
    salary_max = _remoteok_salary(raw.get("salary_max"))
    content.update(
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency="USD" if salary_min or salary_max else None,
    )
    return content


def _himalayas_content(raw):
    restrictions = raw.get("locationRestrictions")
    location = (
        restrictions[0] if isinstance(restrictions, list) and restrictions else None
    )
    tags = raw.get("categories")
    tags = tags if isinstance(tags, list) else []
    content = _base_content(
        raw.get("title"),
        raw.get("companyName"),
        raw.get("excerpt"),
        normalize_location(location) or "Worldwide",
        tags,
    )
    salary_min = _positive_number(raw.get("minSalary"))
    salary_max = _positive_number(raw.get("maxSalary"))
    content.update(
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=(
            raw.get("currency")
            if (salary_min or salary_max) and isinstance(raw.get("currency"), str)
            else None
        ),
        employment_type=raw.get("employmentType"),
        logo=raw.get("companyLogo"),
    )
    return content


def _generic_content(name, raw):
    title = raw.get("title")
    description = _plain(raw.get("description"))
    tags = raw.get("tags")
    if name == "workingnomads" and isinstance(tags, str):
        tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
    tags = tags if isinstance(tags, list) else []
    category = raw.get("category_name") if name == "workingnomads" else None
    if isinstance(category, str) and category.strip():
        tags = [category.strip(), *tags]
    extracted = extract_job_skills(
        title if isinstance(title, str) else "", description or ""
    )
    location = raw.get(
        "candidate_required_location" if name == "remotive" else "location"
    )
    if name == "workingnomads" and (
        not isinstance(location, str) or not location.strip()
    ):
        location = "Remote / Worldwide"
    content = _base_content(
        title,
        raw.get("company_name"),
        description,
        location,
        _merge_tags(tags, extracted)[:15],
    )
    if name == "workingnomads":
        content.update(search_metadata(title, description, location, None))
    return content


_CONTENT_MAPPERS = {
    "himalayas": _himalayas_content,
    "ictjobs": _ictjobs_content,
    "jobicy": _jobicy_content,
    "remoteok": _remoteok_content,
    "swisstechjobs": _swisstechjobs_content,
}


def _content(name, raw):
    mapper = _CONTENT_MAPPERS.get(name)
    return mapper(raw) if mapper else _generic_content(name, raw)


def register_handlers():
    for name in ENDPOINTS:
        register_normalizer(name, lambda raw, source=name: _content(source, raw))
        title, company = (
            ("jobTitle", "companyName")
            if name == "jobicy"
            else ("position", "company")
            if name == "remoteok"
            else ("title", "companyName")
            if name == "himalayas"
            else ("title", "company_name")
        )
        if name in {"swisstechjobs", "ictjobs"}:
            register_extractor(
                name,
                lambda raw, source=name: (
                    _plain(raw.get("title", {}).get("rendered"))
                    if isinstance(raw.get("title"), dict)
                    else None,
                    (
                        raw.get("meta", {}).get("_company_name")
                        if isinstance(raw.get("meta"), dict)
                        else None
                    )
                    if source == "swisstechjobs"
                    else "",
                ),
            )
        else:
            register_extractor(
                name, lambda raw, t=title, c=company: (raw.get(t), raw.get(c))
            )


def _valid_url(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
    except ValueError:
        return None
    return value


def _listing(name, raw):
    if not isinstance(raw, dict):
        return None
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    acf = raw.get("acf") if isinstance(raw.get("acf"), dict) else {}
    apply_url = (
        _valid_url(raw.get("apply_url"))
        if name == "remoteok"
        else _valid_url(raw.get("applicationLink"))
        if name == "himalayas"
        else _valid_url(meta.get("_application"))
        if name == "swisstechjobs"
        else _valid_url(acf.get("direct_link"))
        if name == "ictjobs" and acf.get("use_direct_link")
        else None
    )
    url = _valid_url(
        raw.get("applicationLink")
        if name == "himalayas"
        else raw.get("link")
        if name in {"swisstechjobs", "ictjobs"}
        else raw.get("url")
    )
    if url is None and name == "remoteok" and raw.get("slug"):
        url = _valid_url(f"https://remoteok.com/remote-jobs/{raw['slug']}")
    url = url or apply_url
    if url is None:
        return None
    identity = raw.get("guid" if name == "himalayas" else "id")
    if (
        isinstance(identity, bool)
        or not isinstance(identity, (str, int))
        or not str(identity).strip()
    ):
        external_id = "url:" + url
    else:
        external_id = "id:" + str(identity).strip()
    return RawListing(
        external_id=external_id, url=url, payload=raw, apply_url=apply_url
    )


def _validate_params(name, params):
    allowed = (
        {"tag", "geo"}
        if name == "jobicy"
        else {"query"}
        if name in {"remotive", "workingnomads"}
        else set()
    )
    if not isinstance(params, dict) or set(params) - allowed:
        raise ProviderConfigError(f"Invalid parameters for {name}")
    if any(not isinstance(value, str) or len(value) > 200 for value in params.values()):
        raise ProviderConfigError(
            "Source filters must be strings of at most 200 characters"
        )


def _query_for(name, params):
    query = {}
    if name == "remotive":
        query = {"limit": 200}
        if params.get("query"):
            query["search"] = params["query"]
    elif name == "jobicy":
        query = {"count": 50, **{k: v for k, v in params.items() if v}}
    return query


async def _fetch_body(name, http, query):
    """Descarga acotada en bytes; devuelve el JSON decodificado."""
    async with http.stream(
        "GET",
        ENDPOINTS[name],
        params=query,
        timeout=25,
        headers={"User-Agent": "SwissJobHunter/1.0"},
        follow_redirects=True,
    ) as response:
        if (
            name in {"swisstechjobs", "ictjobs"}
            and query.get("page", 1) > 1
            and response.status_code == 400
        ):
            try:
                error = httpx.Response(200, content=await response.aread()).json()
            except (ValueError, UnicodeError):
                error = None
            if (
                isinstance(error, dict)
                and error.get("code") == "rest_post_invalid_page_number"
                and isinstance(error.get("data"), dict)
                and error["data"].get("status") == 400
            ):
                return []
        response.raise_for_status()
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ProviderResponseError(f"{name}: response exceeds byte budget")
            chunks.append(chunk)
        try:
            return httpx.Response(200, content=b"".join(chunks)).json()
        except (ValueError, UnicodeError) as exc:
            raise ProviderResponseError(f"{name}: invalid JSON") from exc


def _rows_of(name, body):
    if name == "remoteok":
        rows = (
            body[1:]
            if isinstance(body, list) and body and isinstance(body[0], dict)
            else None
        )
    else:
        rows = (
            body
            if name in {"workingnomads", "swisstechjobs", "ictjobs"}
            else body.get("jobs")
            if isinstance(body, dict)
            else None
        )
    if not isinstance(rows, list):
        raise ProviderResponseError(f"{name}: invalid jobs collection")
    return rows


def _workingnomads_filter(name, listings, query):
    """Excluye títulos tech y lo que no contenga la consulta; devuelve (aceptadas, filtradas)."""
    accepted, filtered = [], 0
    for listing in listings:
        content = _content(name, listing.payload)
        title = content["title"] if isinstance(content["title"], str) else ""
        query_text = (title + " " + (content["description"] or "")).lower()
        if (
            any(word in title.lower() for word in WORKINGNOMADS_TECH_EXCLUDE)
            or query not in query_text
        ):
            filtered += 1
        else:
            accepted.append(listing)
    return tuple(accepted), filtered


class NativeJSONProvider(BaseProvider):
    SEMANTIC_PARAMS = ("query", "tag", "geo")

    def __init__(self, name):
        if name not in ENDPOINTS:
            raise ProviderConfigError(f"Unsupported native JSON source: {name}")
        self.name = name
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        _validate_params(self.name, params)
        queries = (
            tuple(
                {"limit": HIMALAYAS_PAGE_SIZE, "offset": page * HIMALAYAS_PAGE_SIZE}
                for page in range(HIMALAYAS_MAX_PAGES)
            )
            if self.name == "himalayas"
            else tuple(
                {"page": page, "per_page": WORDPRESS_PAGE_SIZE}
                for page in range(1, WORDPRESS_MAX_PAGES + 1)
            )
            if self.name == "swisstechjobs"
            else tuple(
                {"page": page, "per_page": ICTJOBS_PAGE_SIZE, "_embed": ""}
                for page in range(1, ICTJOBS_MAX_PAGES + 1)
            )
            if self.name == "ictjobs"
            else (_query_for(self.name, params),)
        )
        rows, pages, fetch_error = [], 0, None
        for index, query in enumerate(queries):
            try:
                body = await _fetch_body(self.name, http, query)
                page_rows = _rows_of(self.name, body)
            except Exception:
                if not rows:
                    raise
                logger.warning("%s: page %d fetch failed", self.name, index + 1)
                fetch_error = "page_fetch_failed"
                break
            pages += 1
            rows.extend(page_rows)
            if not page_rows:
                break
            if index < len(queries) - 1:
                if self.name == "himalayas":
                    await asyncio.sleep(HIMALAYAS_PAGE_DELAY_SECONDS)
                elif self.name in {"swisstechjobs", "ictjobs"}:
                    await asyncio.sleep(WORDPRESS_PAGE_DELAY_SECONDS)
        listings = tuple(
            item for row in rows if (item := _listing(self.name, row)) is not None
        )
        if rows and not listings:
            raise ProviderResponseError(
                f"{self.name}: nonempty feed has no usable identities"
            )
        invalid = len(rows) - len(listings)
        if invalid:
            logger.warning(
                "%s: %d items without usable identity/URL", self.name, invalid
            )
        filtered = 0
        if self.name == "workingnomads":
            listings, filtered = _workingnomads_filter(
                self.name, listings, params.get("query", "").lower()
            )
        error = fetch_error or ("invalid_json_items" if invalid else None)
        return FetchResult(
            listings,
            {"items_seen": len(rows), "filtered": filtered},
            pages_fetched=pages,
            complete=not invalid and fetch_error is None,
            error=error,
        )

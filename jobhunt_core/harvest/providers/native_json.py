"""Native public JSON producers for the F cutover (disabled until provisioned).

Each request refreshes the provider's published feed, not an invented complete
historical archive. Remotive uses the existing 200-item feed; Jobicy uses a
50-item feed PER tag/geo scope (the five old queries need five scopes). These
adapters preserve the original object for the sink; parsing canonical content
is separate. They do not import the BFF, access its DB, or enable any source.

Identity prefers the upstream id; a URL fallback is explicitly weaker when a
portal changes URLs and must be checked in that source's cutover parity test.
No title/company hash and no early-stop on already seen items.
"""

import logging
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import BaseProvider, ProviderConfigError, ProviderResponseError
from jobhunt_core.harvest.providers.rss_text import extract_job_skills
from jobhunt_core.harvest.types import FetchResult, RawListing

logger = logging.getLogger(__name__)

ENDPOINTS = {
    "remotive": "https://remotive.com/api/remote-jobs",
    "workingnomads": "https://www.workingnomads.com/api/exposed_jobs/",
    "jobicy": "https://jobicy.com/api/v2/remote-jobs",
}
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

# Same title-only exclusions as the previous Working Nomads producer.
WORKINGNOMADS_TECH_EXCLUDE = (
    "software engineer", "backend engineer", "frontend engineer", "full stack",
    "fullstack", "devops", "sre", "site reliability", "ml engineer",
    "data engineer", "cloud engineer", "platform engineer", "mobile developer",
    "ios developer", "android developer", "blockchain", "cybersecurity",
    "security engineer", "embedded", "firmware", "hardware engineer",
    "network engineer", "infrastructure",
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


def _content(name, raw):
    jobicy = name == "jobicy"
    title = raw.get("jobTitle" if jobicy else "title")
    description = _plain(raw.get("jobDescription" if jobicy else "description"))
    tags = raw.get("tags")
    if name == "workingnomads" and isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags = tags if isinstance(tags, list) and not jobicy else []
    category = raw.get("category_name") if name == "workingnomads" else None
    if isinstance(category, str) and category.strip():
        tags = [category.strip(), *tags]
    extracted = extract_job_skills(title if isinstance(title, str) else "", description or "")
    merged, seen = [], set()
    for tag in [*tags, *extracted]:
        if not isinstance(tag, (str, int, float)) or isinstance(tag, bool):
            continue
        tag = str(tag).strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            merged.append(tag)
    location = (raw.get("jobGeo") or raw.get("country")) if jobicy else raw.get(
        "candidate_required_location" if name == "remotive" else "location"
    )
    if name == "workingnomads" and (not isinstance(location, str) or not location.strip()):
        location = "Remote / Worldwide"
    return {
        "title": title,
        "company": raw.get("companyName" if jobicy else "company_name"),
        "description": description,
        "location": location,
        "tags": merged[:15],
        "remote": True,
        # Preserve the existing canonical boundary; portal salary stays in raw
        # until a validated enrichment step maps its amount/currency/period.
        "salary": None,
    }


def register_handlers():
    for name in ENDPOINTS:
        register_normalizer(name, lambda raw, source=name: _content(source, raw))
        title, company = ("jobTitle", "companyName") if name == "jobicy" else ("title", "company_name")
        register_extractor(name, lambda raw, t=title, c=company: (raw.get(t), raw.get(c)))


def _listing(raw):
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    identity = raw.get("id")
    if isinstance(identity, bool) or not isinstance(identity, (str, int)) or not str(identity).strip():
        external_id = "url:" + url
    else:
        external_id = "id:" + str(identity).strip()
    return RawListing(external_id=external_id, url=url, payload=raw)


class NativeJSONProvider(BaseProvider):
    SEMANTIC_PARAMS = ("query", "tag", "geo")

    def __init__(self, name):
        if name not in ENDPOINTS:
            raise ProviderConfigError(f"Unsupported native JSON source: {name}")
        self.name = name
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        allowed = {"query"} if self.name != "jobicy" else {"tag", "geo"}
        if not isinstance(params, dict) or set(params) - allowed:
            raise ProviderConfigError(f"Invalid parameters for {self.name}")
        if any(not isinstance(value, str) or len(value) > 200 for value in params.values()):
            raise ProviderConfigError("Source filters must be strings of at most 200 characters")
        query = {}
        if self.name == "remotive":
            query = {"limit": 200}
            if params.get("query"):
                query["search"] = params["query"]
        elif self.name == "jobicy":
            query = {"count": 50, **{k: v for k, v in params.items() if v}}
        async with http.stream("GET", ENDPOINTS[self.name], params=query, timeout=25,
                               headers={"User-Agent": "SwissJobHunter/1.0"}, follow_redirects=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ProviderResponseError(f"{self.name}: response exceeds byte budget")
                chunks.append(chunk)
            try:
                body = httpx.Response(200, content=b"".join(chunks)).json()
            except (ValueError, UnicodeError) as exc:
                raise ProviderResponseError(f"{self.name}: invalid JSON") from exc
        rows = body if self.name == "workingnomads" else body.get("jobs") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise ProviderResponseError(f"{self.name}: invalid jobs collection")
        listings = tuple(item for row in rows if (item := _listing(row)) is not None)
        if rows and not listings:
            raise ProviderResponseError(f"{self.name}: nonempty feed has no usable identities")
        invalid = len(rows) - len(listings)
        if invalid:
            logger.warning("%s: %d items without usable identity/URL", self.name, invalid)
        filtered = 0
        if self.name == "workingnomads":
            accepted = []
            query = params.get("query", "").lower()
            for listing in listings:
                content = _content(self.name, listing.payload)
                title = content["title"] if isinstance(content["title"], str) else ""
                query_text = (title + " " + (content["description"] or "")).lower()
                if (
                    any(word in title.lower() for word in WORKINGNOMADS_TECH_EXCLUDE)
                    or query not in query_text
                ):
                    filtered += 1
                else:
                    accepted.append(listing)
            listings = tuple(accepted)
        return FetchResult(listings, {"items_seen": len(rows), "filtered": filtered},
                           complete=not invalid, error="invalid_json_items" if invalid else None)

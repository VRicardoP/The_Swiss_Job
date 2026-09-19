"""Native RSS providers, ported from the existing BFF adapters for F.

Fixed public endpoints, bounded responses, no legacy imports, no source activation.
Raw payload is the serialized XML item (all fields/attributes retained, not
byte-identical XML formatting) plus the original portal date for admission.
Legacy query/technical exclusions and canonical content rules are preserved.
Only the task scheduler retries; malformed or partially unreadable feeds are
visible failures, never a healthy disappearance of offers.
"""

import hashlib
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import BaseProvider, ProviderConfigError, ProviderResponseError
from jobhunt_core.harvest.providers.rss_text import extract_job_skills, strip_html_tags
from jobhunt_core.harvest.types import FetchResult, RawListing

ENDPOINTS = {
    "weworkremotely": "https://weworkremotely.com/remote-jobs.rss",
    "euremotejobs": "https://euremotejobs.com/feed/",
    "jobspresso": "https://jobspresso.co/feed/?post_type=job_listing",
    "globaljobs": "https://www.globaljobs.org/jobs/feed.rss",
    "zebis": "https://www.zebis.ch/stellen/stelleninserate/rss",
}
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
# Copied unchanged from the corresponding legacy providers for cutover parity.
EU_TECH_EXCLUDE = {
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
}
JOBSPRESSO_TECH_EXCLUDE = {
    "software engineer",
    "backend engineer",
    "frontend engineer",
    "full stack",
    "fullstack",
    "devops",
    "sre",
    "ml engineer",
    "data engineer",
    "cloud engineer",
    "mobile developer",
    "ios developer",
    "android developer",
    "blockchain",
    "cybersecurity",
    "security engineer",
    "embedded",
}
_INTL_CITIES = [
    "Geneva",
    "New York",
    "Vienna",
    "Brussels",
    "Paris",
    "Nairobi",
    "Washington",
    "London",
    "Rome",
    "The Hague",
    "Bonn",
    "Bangkok",
    "Bangkok",
    "Addis Ababa",
    "Cairo",
    "Dakar",
    "Beirut",
    "Amman",
]


class _NoDTD(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ProviderResponseError("RSS with DTD is unsupported")


def _xml(raw):
    try:
        return ET.fromstring(raw, parser=ET.XMLParser(target=_NoDTD()))
    except (ET.ParseError, ValueError) as exc:
        raise ProviderResponseError("unreadable RSS XML") from exc


def _zebis_url(raw):
    """Same host repair as legacy: take only a valid /stellen/<slug> path.

    Zebis published a broken 0.0.0.0:3000 base. Never follow that authority;
    repair to the fixed public host. Keep the original XML for audit.
    """
    if not raw or "\\" in raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if (parts.scheme not in {"http", "https"} or parts.username is not None or
            not re.fullmatch(r"/stellen/[^/?#\x00-\x1f\x7f]+", parts.path)):
        return None
    return "https://www.zebis.ch" + parts.path


def _zebis_employer(description):
    match = re.search(r"<(?:p|div)>\s*<strong>([^<]+)</strong>", description)
    name = match.group(1).strip() if match else ""
    return name if len(name) > 3 and not name[0].isdigit() else "Unknown"


def _content(source, raw):
    item = _xml(raw["item_xml"])
    title = (item.findtext("title") or "").strip()
    description = strip_html_tags(item.findtext("description") or "")
    company = ""
    if source == "zebis":
        company = _zebis_employer(item.findtext("description") or "")
    elif source == "weworkremotely":
        if ": " in title:
            company, title = title.split(": ", 1)
    elif " at " in title:
        title, company = title.rsplit(" at ", 1)
    elif source == "euremotejobs" and ": " in title and len(title.split(": ", 1)[0]) < 50:
        company, title = title.split(": ", 1)
    elif source == "globaljobs":
        for separator in (" — ", " | "):
            if separator in title:
                title, company = title.split(separator, 1)
                break
    title, company = title.strip(), company.strip()
    location = "Remote / Worldwide"
    if source == "zebis":
        location = "Switzerland"
    elif source == "weworkremotely":
        location = (item.findtext("region") or "").strip() or location
    elif source == "euremotejobs":
        location = "Remote / Europe"
    elif source == "jobspresso":
        location = ((item.findtext("{job_listing}job_location") or
                     item.findtext("job_location") or location).strip())
    elif source == "globaljobs":
        text = description.lower()
        if "home-based" in text or "home based" in text:
            location = "Remote / Home-based"
        elif "remote" not in text[:300]:
            location = next((city for city in _INTL_CITIES if city.lower() in text[:500]), "International")
    tags = extract_job_skills(title, description)
    category = (item.findtext("category") or "").strip()
    if source not in {"weworkremotely", "zebis"} and category and category.lower() not in [tag.lower() for tag in tags]:
        tags = [category] + tags
    remote = source in {"weworkremotely", "euremotejobs"} or "remote" in location.lower() or "home-based" in location.lower()
    return {"title": title, "company": company, "description": description,
            "location": location, "remote": remote, "tags": tags[:15]}


def register_handlers():
    for name in ENDPOINTS:
        register_normalizer(name, lambda raw, source=name: _content(source, raw))
        def extract(raw, source=name):
            content = _content(source, raw)
            return content["title"], content["company"]
        register_extractor(name, extract)


def _listing(item, source):
    guid = (item.findtext("guid") or "").strip()
    url = (item.findtext("link") or "").strip() or guid
    if source == "zebis":
        url = _zebis_url(url) or _zebis_url(guid) or ""
        # Its GUID is also a URL with the broken host. Use the repaired URL
        # so the portal fixing its own host cannot create a second identity.
        guid = ""
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
            return None
    except ValueError:
        return None
    # Bound identity length, without depending on mutable title/company.
    identity = ("guid:" if guid else "url:") + hashlib.sha256((guid or url).encode()).hexdigest()
    return RawListing(identity, url, {"item_xml": ET.tostring(item, encoding="unicode"),
                                      "pubDate": item.findtext("pubDate")})


class NativeRSSProvider(BaseProvider):
    SEMANTIC_PARAMS = ("query",)

    def __init__(self, name):
        if name not in ENDPOINTS:
            raise ProviderConfigError("Unsupported native RSS source")
        self.name = name
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if (not isinstance(params, dict) or set(params) - {"query"} or
                not isinstance(params.get("query", ""), str) or len(params.get("query", "")) > 200):
            raise ProviderConfigError("RSS scope accepts only a query string of at most 200 characters")
        async with http.stream("GET", ENDPOINTS[self.name], timeout=25,
                               headers={"User-Agent": "SwissJobHunter/1.0"},
                               follow_redirects=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ProviderResponseError("RSS exceeds byte budget")
                chunks.append(chunk)
        root = _xml(b"".join(chunks))
        channel = root.find("channel") if root.tag == "rss" else None
        if channel is None:
            raise ProviderResponseError("RSS without channel")
        items = channel.findall("item")
        listings, invalid, filtered = [], 0, 0
        query = params.get("query", "").lower()
        excludes = (EU_TECH_EXCLUDE if self.name == "euremotejobs" else
                    JOBSPRESSO_TECH_EXCLUDE if self.name == "jobspresso" else ())
        for item in items:
            listing = _listing(item, self.name)
            if listing is None:
                invalid += 1
                continue
            content = _content(self.name, listing.payload)
            query_text = content["title"] + " " + content["description"]
            if self.name in {"weworkremotely", "zebis"}:
                query_text += " " + content["company"]
            if any(word in content["title"].lower() for word in excludes) or query not in query_text.lower():
                filtered += 1
                continue
            listings.append(listing)
        if items and invalid == len(items):
            raise ProviderResponseError("nonempty RSS has no usable identities")
        return FetchResult(tuple(listings), {"items_seen": len(items), "filtered": filtered},
                           complete=not invalid, error="invalid_rss_items" if invalid else None)


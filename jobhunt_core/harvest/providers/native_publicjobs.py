"""Publicjobs' SvelteKit feed, decoded without importing the legacy backend.

The raw listing is the portal object after one-level reference resolution,
not a lossy canonical projection. Unknown envelope/reference shapes are errors;
a malformed neighbor makes the run partial, not silently complete.
"""
import asyncio
import hashlib
from urllib.parse import unquote, urlsplit

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import BaseProvider, ProviderConfigError, ProviderResponseError
from jobhunt_core.harvest.providers.rss_text import extract_job_skills
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "publicjobs"
BASE_URL = "https://www.publicjobs.ch"
DATA_URL = BASE_URL + "/jobs/__data.json"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _ref(data, value):
    if type(value) is not int or not 0 <= value < len(data):
        raise ProviderResponseError("Publicjobs invalid SvelteKit reference")
    return data[value]


def _decode(body):
    try:
        data = body["nodes"][0]["data"]
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise ProviderResponseError("Publicjobs invalid data table")
        search = _ref(data, data[0]["jobSearch"])
        indices = _ref(data, search["data"])
        if not isinstance(indices, list):
            raise ProviderResponseError("Publicjobs invalid jobs array")
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderResponseError("Publicjobs invalid envelope") from exc
    rows, invalid = [], 0
    for index in indices:
        try:
            obj = _ref(data, index)
            if not isinstance(obj, dict):
                raise ProviderResponseError("Publicjobs invalid job object")
            # Svelte uses negative sentinels for undefined/null special values.
            # Retain them as None, never interpret them as Python negative indexes.
            decoded = {key: (None if value < 0 else _ref(data, value))
                       if type(value) is int else value for key, value in obj.items()}
            rows.append(decoded)
        except ProviderResponseError:
            invalid += 1
    return rows, invalid, len(indices)


def _listing(raw):
    path = _text(raw.get("path"))
    try:
        path.encode("utf-8")
        parsed = urlsplit(path)
        decoded = unquote(path)
        if (not path.startswith("/") or path.startswith("//") or parsed.netloc or parsed.scheme
                or "\\" in decoded or any(ord(c) < 32 for c in decoded)
                or ".." in unquote(parsed.path).split("/")):
            return None
    except (ValueError, UnicodeError):
        return None
    return RawListing("path:" + hashlib.sha256(path.encode()).hexdigest(), BASE_URL + path, raw)


def _content(raw):
    title = _text(raw.get("title"))
    return {"title": title, "company": _text(raw.get("contactCompany")) or "Unknown",
            "description": "", "location": _text(raw.get("workingAddressCity"))
                or _text(raw.get("workingAddressRegion")) or "Switzerland",
            "remote": False, "tags": extract_job_skills(title, "")[:15]}


def register_handlers():
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(SOURCE_NAME, lambda raw: (_text(raw.get("title")),
                                               _text(raw.get("contactCompany")) or "Unknown"))


class PublicJobsProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ("query",)

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if (not isinstance(params, dict) or set(params) - {"query"}
                or not isinstance(params.get("query", ""), str) or len(params.get("query", "")) > 200):
            raise ProviderConfigError("Invalid publicjobs query")
        async with asyncio.timeout(25):
            async with http.stream("GET", DATA_URL, timeout=20, follow_redirects=True,
                                   headers={"User-Agent": "SwissJobHunter/1.0"}) as response:
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ProviderResponseError("Publicjobs byte budget exceeded")
                    chunks.append(chunk)
        try:
            body = httpx.Response(200, content=b"".join(chunks)).json()
        except (ValueError, UnicodeError) as exc:
            raise ProviderResponseError("Publicjobs invalid JSON") from exc
        rows, invalid, seen = _decode(body)
        listings, usable = [], 0
        query = params.get("query", "").lower()
        for row in rows:
            listing = _listing(row)
            if listing is None or not _text(row.get("title")):
                invalid += 1
                continue
            usable += 1
            if query and query not in f"{_text(row.get('title'))} {_text(row.get('contactCompany'))}".lower():
                continue
            listings.append(listing)
        if seen and not usable:
            raise ProviderResponseError("Publicjobs nonempty feed has no usable jobs")
        return FetchResult(tuple(listings), {"items_seen": seen}, pages_fetched=1,
                           complete=not invalid, error="invalid_publicjobs_items" if invalid else None)

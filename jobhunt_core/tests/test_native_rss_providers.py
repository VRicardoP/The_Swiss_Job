"""RSS source handover: failure != empty, stable identity and legacy content."""

import asyncio
import xml.etree.ElementTree as ET

import httpx
import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.harvest.provider import ProviderConfigError, ProviderResponseError

SOURCES = ("weworkremotely", "euremotejobs", "jobspresso", "globaljobs")


def fetch(source, body, params=None, status=200):
    from jobhunt_core.harvest.providers.native_rss import NativeRSSProvider
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, content=body)
        )) as client:
            return await NativeRSSProvider(source).fetch_new(params or {}, None, client)
    return asyncio.run(run())


def feed(title="Editor at Acme", extra="", guid="stable-123"):
    return f'''<rss version="2.0"><channel><item>
      <title>{title}</title><link>https://example.test/job</link><guid>{guid}</guid>
      <description><![CDATA[<p>English writer in Geneva</p>]]></description>
      <pubDate>Fri, 18 Sep 2026 12:00:00 +0000</pubDate>{extra}
    </item></channel></rss>'''.encode()


@pytest.mark.parametrize("source", SOURCES)
def test_feed_raw_fields_dates_and_stable_identity(source):
    one = fetch(source, feed(extra='<unknown attr="kept">raw</unknown>'))
    two = fetch(source, feed(title="Senior Editor at Acme"))
    assert len(one.listings) == 1 and one.complete
    assert one.listings[0].external_id == two.listings[0].external_id
    raw = one.listings[0].payload
    assert ET.fromstring(raw["item_xml"]).find("unknown").attrib == {"attr": "kept"}
    assert raw["pubDate"] == "Fri, 18 Sep 2026 12:00:00 +0000"
    content = normalize_offer(source, raw)
    assert content["description"] == "English writer in Geneva"
    assert "english" in content["tags"]


@pytest.mark.parametrize("source,title,company,role,location,remote", [
    ("weworkremotely", "Acme: Editor", "Acme", "Editor", "Remote / Worldwide", True),
    ("euremotejobs", "Acme: Editor", "Acme", "Editor", "Remote / Europe", True),
    ("jobspresso", "Editor at Acme", "Acme", "Editor", "Remote / Worldwide", True),
    ("globaljobs", "Editor — Acme", "Acme", "Editor", "Geneva", False),
])
def test_legacy_canonical_content(source, title, company, role, location, remote):
    result = fetch(source, feed(title))
    content = normalize_offer(source, result.listings[0].payload)
    assert (content["company"], content["title"], content["location"], content["remote"]) == (company, role, location, remote)


@pytest.mark.parametrize("source", ["euremotejobs", "jobspresso"])
def test_legacy_technical_exclusion_is_not_a_broken_empty_feed(source):
    result = fetch(source, feed("Software Engineer at Acme"))
    assert result.complete and not result.listings
    assert result.next_cursor["filtered"] == 1


@pytest.mark.parametrize("body", [b"", b"<html/>", b"<rss/>", b"<rss><channel>",
    b'<rss><channel><item><title>Lost URL</title></item></channel></rss>',
    b'<!DOCTYPE rss [<!ENTITY name "expanded">]><rss><channel/></rss>'])
def test_unknown_or_unsafe_structure_is_not_empty(body):
    with pytest.raises(ProviderResponseError):
        fetch("weworkremotely", body)


def test_empty_feed_and_query_empty_are_valid():
    assert fetch("weworkremotely", b"<rss><channel/></rss>").listings == ()
    assert fetch("weworkremotely", feed(), {"query": "teacher"}).listings == ()
    assert len(fetch("weworkremotely", feed(), {"query": "english"}).listings) == 1


def test_config_and_http_errors_propagate():
    with pytest.raises(ProviderConfigError):
        fetch("weworkremotely", feed(), {"endpoint": "http://localhost/private"})
    with pytest.raises(httpx.HTTPStatusError):
        fetch("weworkremotely", feed(), status=429)


def test_guid_fallback_and_missing_identity_neighbor():
    body = feed(guid="").replace(b"</channel>", b"<item><title>bad</title></item></channel>")
    result = fetch("weworkremotely", body)
    assert len(result.listings) == 1
    assert result.listings[0].external_id.startswith("url:")
    assert result.error == "invalid_rss_items" and not result.complete


def test_registered_in_worker_without_legacy_imports():
    from jobhunt_core.harvest.providers import get_provider
    from jobhunt_core.harvest.registry import ensure_handler
    for source in SOURCES:
        assert get_provider(source).name == source
        assert ensure_handler(source)


def test_byte_budget_and_non_utf8_dtd_are_rejected(monkeypatch):
    from jobhunt_core.harvest.providers import native_rss
    dtd = '<!DOCTYPE rss [<!ENTITY name "expanded">]><rss><channel/></rss>'
    with pytest.raises(ProviderResponseError):
        fetch("globaljobs", dtd.encode("utf-16"))
    monkeypatch.setattr(native_rss, "MAX_RESPONSE_BYTES", 8)
    with pytest.raises(ProviderResponseError, match="byte budget"):
        fetch("globaljobs", feed())


def test_location_metadata_and_long_guid_are_preserved():
    result = fetch("jobspresso", feed(extra="<job_location>Geneva</job_location>", guid="x" * 300))
    row = result.listings[0]
    assert len(row.external_id) <= 200
    assert ET.fromstring(row.payload["item_xml"]).findtext("guid") == "x" * 300
    content = normalize_offer("jobspresso", row.payload)
    assert content["location"] == "Geneva" and content["remote"] is False

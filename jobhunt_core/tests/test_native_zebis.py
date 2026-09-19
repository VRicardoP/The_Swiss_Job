"""Zebis handover preserves the established repair of the portal's broken host."""
import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.harvest.provider import ProviderResponseError
from jobhunt_core.tests.test_native_rss_providers import fetch


def body(link, guid="", title="Lehrperson 80%", description="English teacher", category=""):
    from xml.etree import ElementTree as ET
    root = ET.Element("rss")
    item = ET.SubElement(ET.SubElement(root, "channel"), "item")
    for key, value in (("link", link), ("guid", guid), ("title", title),
                       ("description", description), ("category", category),
                       ("pubDate", "Fri, 18 Sep 2026 12:00:00 +0000")):
        ET.SubElement(item, key).text = value
    return ET.tostring(root)


def test_broken_host_is_repaired_and_host_fix_does_not_change_identity():
    broken = "https://0.0.0.0:3000/stellen/lehrperson-1"
    valid = "https://www.zebis.ch/stellen/lehrperson-1"
    old = fetch("zebis", body(broken, broken)).listings[0]
    new = fetch("zebis", body(valid, valid)).listings[0]
    assert old.url == new.url == valid
    assert old.external_id == new.external_id
    content = normalize_offer("zebis", old.payload)
    assert content["company"] == "Unknown" and content["location"] == "Switzerland"
    assert content["remote"] is False and "english" in content["tags"]


@pytest.mark.parametrize("url", ["", "mailto:test@example.org", "https://evil.example/",
    "https://evil.example/jobs/x", "https://evil.example/stellen/a/b",
    "https://user@evil.example/stellen/a", "https://evil.example\\@www.zebis.ch/stellen/a",
    "https://[evil/stellen/a"])
def test_invalid_offer_url_is_visible_not_healthy_empty(url):
    with pytest.raises(ProviderResponseError):
        fetch("zebis", body(url))


def test_guid_fallback_and_legacy_content_not_generic_title_split():
    result = fetch("zebis", body("https://[broken", "https://0.0.0.0:3000/stellen/role",
        title="English teacher at school", description="<p><strong>Academy</strong></p>English",
        category="not a legacy skill"))
    row = result.listings[0]
    assert row.url == "https://www.zebis.ch/stellen/role"
    content = normalize_offer("zebis", row.payload)
    assert content["title"] == "English teacher at school"
    assert content["company"] == "Academy"
    assert "not a legacy skill" not in content["tags"]
    assert not fetch("zebis", body(row.url), {"query":"not found"}).listings

"""Do not change existing embedding text merely by changing the producer."""

from jobhunt_core.tests.test_native_json_providers import fetch
from jobhunt_core.harvest.normalize import normalize_offer


def test_html_entities_and_inline_boundaries_match_existing_source():
    row = {
        "id": 1,
        "url": "https://example.org/1",
        "title": "Editor",
        "description": "<p>A &amp; B</p><b>one</b><i>two</i> &#38;",
    }
    result, _ = fetch("remotive", {"jobs": [row]})
    assert (
        normalize_offer("remotive", result.listings[0].payload)["description"]
        == "A &amp; B one two &#38;"
    )

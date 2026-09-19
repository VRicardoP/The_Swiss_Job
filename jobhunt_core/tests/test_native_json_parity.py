"""Admission/content contracts that must survive the native-source handover."""

import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.tests.test_native_json_providers import fetch


def test_invalid_neighbor_is_partial_error_not_complete():
    result, _ = fetch("remotive", {"jobs": [None, {
        "id": 1, "url": "https://example.org/job", "title": "English teacher",
    }]})
    assert len(result.listings) == 1
    assert result.complete is False and result.error == "invalid_json_items"


@pytest.mark.parametrize("source", ["remotive", "workingnomads", "jobicy"])
def test_legacy_extracted_skills_survive(source):
    row = {"id": 1, "url": "https://example.org/job", "title": "English teacher",
           "jobTitle": "English teacher", "description": "TEFL and Spanish",
           "jobDescription": "TEFL and Spanish", "tags": ["ENGLISH", "Writing"],
           "category_name": "Education"}
    result, _ = fetch(source, [row] if source == "workingnomads" else {"jobs": [row]})
    content = normalize_offer(source, result.listings[0].payload)
    assert {"english", "spanish", "tefl"} <= {tag.lower() for tag in content["tags"]}
    if source == "workingnomads":
        assert content["tags"][0] == "Education"
        assert content["location"] == "Remote / Worldwide"
    assert len([tag for tag in content["tags"] if tag.lower() == "english"]) == 1


def test_workingnomads_technical_exclusion_and_query_are_preserved():
    rows = [{"id": i, "url": f"https://example.org/{i}", "title": title,
             "description": "English"} for i, title in enumerate(
                 ["Backend Engineer", "English Teacher", "Spanish Editor"], 1)]
    result, _ = fetch("workingnomads", rows, {"query": "teacher"})
    assert [row.payload["title"] for row in result.listings] == ["English Teacher"]
    assert result.complete and result.next_cursor["filtered"] == 2
    result, _ = fetch("workingnomads", rows)
    assert len(result.listings) == 2


def test_workingnomads_query_changes_scope_fingerprint():
    from jobhunt_core.harvest.providers.native_json import NativeJSONProvider
    p = NativeJSONProvider("workingnomads")
    assert p.params_fingerprint({"query": "teacher"}) != p.params_fingerprint({"query": "editor"})

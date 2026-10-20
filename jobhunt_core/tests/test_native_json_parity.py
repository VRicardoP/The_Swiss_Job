"""Admission/content contracts that must survive the native-source handover."""

import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.tests.test_native_json_providers import fetch


def test_invalid_neighbor_is_partial_error_not_complete():
    result, _ = fetch(
        "remotive",
        {
            "jobs": [
                None,
                {
                    "id": 1,
                    "url": "https://example.org/job",
                    "title": "English teacher",
                },
            ]
        },
    )
    assert len(result.listings) == 1
    # DT-148 E1: un item sin identidad no marca el barrido incompleto.
    assert result.complete is True and result.error is None
    assert result.next_cursor["invalid"] == 1


@pytest.mark.parametrize("source", ["remotive", "workingnomads", "jobicy", "remoteok"])
def test_legacy_extracted_skills_survive(source):
    row = {
        "id": 1,
        "url": "https://example.org/job",
        "title": "English teacher",
        "jobTitle": "English teacher",
        "description": "TEFL and Spanish",
        "jobDescription": "TEFL and Spanish",
        "tags": ["ENGLISH", "Writing"],
        "category_name": "Education",
    }
    body = (
        [row]
        if source == "workingnomads"
        else [{"legal": "metadata"}, row]
        if source == "remoteok"
        else {"jobs": [row]}
    )
    if source == "remoteok":
        row["position"] = row["title"]
        row["company"] = "School"
    result, _ = fetch(source, body)
    content = normalize_offer(source, result.listings[0].payload)
    if source == "remoteok":
        assert content["tags"] == ["ENGLISH", "Writing"]
    else:
        assert {"english", "spanish", "tefl"} <= {
            tag.lower() for tag in content["tags"]
        }
    if source == "workingnomads":
        assert content["tags"][0] == "Education"
        assert content["location"] == "Remote / Worldwide"
    assert len([tag for tag in content["tags"] if tag.lower() == "english"]) == 1


def test_workingnomads_technical_exclusion_and_query_are_preserved():
    rows = [
        {
            "id": i,
            "url": f"https://example.org/{i}",
            "title": title,
            "description": "English",
        }
        for i, title in enumerate(
            ["Backend Engineer", "English Teacher", "Spanish Editor"], 1
        )
    ]
    result, _ = fetch("workingnomads", rows, {"query": "teacher"})
    assert [row.payload["title"] for row in result.listings] == ["English Teacher"]
    assert result.complete and result.next_cursor["filtered"] == 2
    result, _ = fetch("workingnomads", rows)
    assert len(result.listings) == 2


def test_workingnomads_query_changes_scope_fingerprint():
    from jobhunt_core.harvest.providers.native_json import NativeJSONProvider

    p = NativeJSONProvider("workingnomads")
    assert p.params_fingerprint({"query": "teacher"}) != p.params_fingerprint(
        {"query": "editor"}
    )

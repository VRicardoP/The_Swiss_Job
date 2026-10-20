"""Job-Room preserves ReactPortfolio's bounded public ICT search contract."""

import asyncio
import json

import httpx
import pytest


def row(identity="ad-1", **changes):
    advertisement = {
        "id": identity,
        "status": "PUBLISHED_PUBLIC",
        "jobContent": {
            "jobDescriptions": [
                {
                    "languageIsoCode": "de",
                    "title": "Backend Engineer",
                    "description": (
                        "Python JavaScript TypeScript React Node.js SQL Docker "
                        "Kubernetes AWS Terraform Redis Linux"
                    ),
                }
            ],
            "company": {"name": "Tech AG"},
            "employment": {
                "workloadPercentageMin": 80,
                "workloadPercentageMax": 100,
                "workForms": ["HOME_WORK"],
            },
            "location": {"city": "Zürich", "cantonCode": "ZH"},
            "languageSkills": [
                {"languageIsoCode": "de"},
                {"languageIsoCode": "en"},
            ],
        },
        "publication": {"startDate": "2026-10-13", "companyAnonymous": False},
    }
    advertisement.update(changes)
    return {"jobAdvertisement": advertisement, "favouriteItem": None}


def fetch(body, *, params=None):
    from jobhunt_core.harvest.providers import native_jobroom as module

    requests = []

    async def run():
        def transport(request):
            requests.append(request)
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await module.JobRoomProvider().fetch_new(
                {} if params is None else params, None, client
            )
        return result, requests

    return asyncio.run(run())


def test_request_identity_content_and_statistics_tags_are_exact():
    from jobhunt_core.harvest.normalize import normalize_offer
    from jobhunt_core.harvest.providers.native_jobroom import (
        ENDPOINT,
        PROFESSION_CODES,
    )

    original = row()
    result, requests = fetch([original])

    assert result.complete and result.error is None and result.pages_fetched == 1
    assert result.next_cursor == {"items_seen": 1, "invalid": 0}
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url.copy_with(query=None)) == ENDPOINT
    assert dict(request.url.params) == {
        "page": "0",
        "size": "300",
        "sort": "date_desc",
    }
    assert request.headers["accept"] == "application/json"
    assert json.loads(request.content) == {
        "workloadPercentageMin": 0,
        "workloadPercentageMax": 100,
        "onlineSince": 7,
        "displayRestricted": False,
        "permanent": None,
        "professionCodes": list(PROFESSION_CODES),
        "keywords": [],
        "cantonCodes": [],
        "communalCodes": [],
    }

    listing = result.listings[0]
    assert listing.external_id == "ad-1"
    assert listing.url == "https://www.job-room.ch/job-search/ad-1"
    assert listing.payload["jobAdvertisement"] == original["jobAdvertisement"]
    assert listing.payload["favouriteItem"] is None
    assert listing.payload["_core_statistics_tags"] == [
        "backend",
        "python",
        "javascript",
        "typescript",
        "react",
        "node.js",
        "sql",
        "redis",
        "docker",
        "kubernetes",
        "aws",
        "linux",
        "terraform",
    ]
    assert normalize_offer("jobroom", listing.payload) == {
        "title": "Backend Engineer",
        "company": "Tech AG",
        "description": (
            "Python JavaScript TypeScript React Node.js SQL Docker Kubernetes "
            "AWS Terraform Redis Linux"
        ),
        "tags": [
            "aws",
            "backend",
            "de",
            "docker",
            "en",
            "javascript",
            "kubernetes",
            "linux",
            "node.js",
            "python",
        ],
        "salary": None,
        "location": "Zürich, ZH",
        "remote": True,
        "employment_type": "80-100%",
    }


def test_anonymous_company_location_and_single_workload_bound():
    from jobhunt_core.harvest.normalize import normalize_offer

    value = row()
    ad = value["jobAdvertisement"]
    ad["publication"]["companyAnonymous"] = True
    ad["jobContent"]["location"] = {"city": "", "cantonCode": "GE"}
    ad["jobContent"]["employment"] = {
        "workloadPercentageMin": None,
        "workloadPercentageMax": 80,
        "workForms": [],
    }
    content = normalize_offer("jobroom", fetch([value])[0].listings[0].payload)

    assert content["company"] == "Confidential"
    assert content["location"] == "GE"
    assert content["employment_type"] == "80%"
    assert content["remote"] is False


def test_invalid_neighbor_is_counted_but_valid_row_survives():
    result, _requests = fetch([row(), None])
    assert len(result.listings) == 1
    # DT-148 E1.
    assert result.complete and result.error is None
    assert result.next_cursor["invalid"] == 1


def test_empty_board_is_a_success():
    result, _requests = fetch([])
    assert result.complete and result.listings == () and result.error is None


@pytest.mark.parametrize("body", [None, {}, {"jobs": []}, "changed"])
def test_invalid_feed_envelope_never_becomes_empty_success(body):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch(body)


def test_nonempty_all_invalid_feed_fails_closed():
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch([None, {"jobAdvertisement": {"id": ""}}])


def test_upstream_identity_cannot_change_the_public_url_structure():
    result, _requests = fetch([row("../admin?next=/")])

    assert result.listings[0].external_id == "../admin?next=/"
    assert result.listings[0].url == (
        "https://www.job-room.ch/job-search/..%2Fadmin%3Fnext%3D%2F"
    )


def test_invalid_filters_fail_before_network():
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch([], params={"query": "python"})


def test_nested_publication_date_and_provider_are_wired():
    from jobhunt_core.harvest.admission import publication_date
    from jobhunt_core.harvest.providers import get_provider

    assert get_provider("jobroom").name == "jobroom"
    assert publication_date("jobroom", row()).isoformat() == "2026-10-13T00:00:00+00:00"


def test_word_boundaries_preserve_jobroom_without_changing_shared_default():
    from jobhunt_core.harvest.providers.jobicy_metadata import extract_tech_skills

    text = "Angebot für digitale R&D Systeme; C++ C# Go R"
    assert extract_tech_skills("", text, word_boundaries=True) == [
        "go",
        "c++",
        "c#",
        "r",
    ]
    assert "git" in extract_tech_skills("", "digital")

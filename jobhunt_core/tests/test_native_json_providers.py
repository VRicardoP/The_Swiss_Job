"""Native producers must preserve raw data and distinguish failure from empty.

No source is enabled by importing a provider. Cutover still requires a scoped
canary and parity before disabling its previous writer.
"""

import asyncio
import copy

import httpx
import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.harvest.provider import ProviderConfigError, ProviderResponseError


def provider(name):
    from jobhunt_core.harvest.providers.native_json import NativeJSONProvider

    return NativeJSONProvider(name)


def fetch(name, payload, params=None, status=200):
    requests = []

    async def run():
        def transport(request):
            requests.append(request)
            return httpx.Response(status, json=payload)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await provider(name).fetch_new(params or {}, {}, client)
            return result

    return asyncio.run(run()), requests


def fetch_pages(name, payloads, statuses=None):
    requests = []
    statuses = statuses or [200] * len(payloads)

    async def run():
        def transport(request):
            index = len(requests)
            requests.append(request)
            return httpx.Response(statuses[index], json=payloads[index])

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            return await provider(name).fetch_new({}, {}, client)

    return asyncio.run(run()), requests


def envelope(name, rows):
    if name == "workingnomads":
        return rows
    if name == "remoteok":
        return [{"legal": "metadata"}, *rows]
    return {"jobs": rows}


@pytest.mark.parametrize(
    "name,key,title,company",
    [
        ("remotive", "jobs", "title", "company_name"),
        ("workingnomads", None, "title", "company_name"),
        ("jobicy", "jobs", "jobTitle", "companyName"),
    ],
)
def test_raw_preserved_identity_does_not_depend_on_title(name, key, title, company):
    raw = {
        "id": 123,
        title: "Editor",
        company: "Acme",
        "url": "https://jobs.example/123",
        "description": "<p>First &amp; second</p>",
        "jobDescription": "<p>First &amp; second</p>",
        "unknown_upstream_field": {"retained": True},
    }
    original = copy.deepcopy(raw)
    result, _ = fetch(name, {key: [raw]} if key else [raw])
    listing = result.listings[0]
    assert listing.payload == original
    assert raw == original
    assert result.complete
    # Keep the established legacy source text, not a newly decoded variant.
    assert normalize_offer(name, listing.payload)["description"] == "First &amp; second"
    changed = {**raw, title: "Senior Editor", "url": "https://jobs.example/renamed-123"}
    second, _ = fetch(name, {key: [changed]} if key else [changed])
    assert second.listings[0].external_id == listing.external_id


def test_remoteok_raw_identity_apply_url_and_normalization_are_preserved():
    raw = {
        "id": 123,
        "position": "Data Engineer",
        "title": "ignored",
        "company": "DataCorp",
        "location": "",
        "tags": [*[f"tag-{index}" for index in range(20)], "tag-0"],
        "date": "2026-10-13T12:00:00Z",
        "salary_min": 80,
        "salary_max": 120000,
        "apply_url": "https://apply.example/123",
        "url": "https://remoteok.com/remote-jobs/123",
        "description": "<b>Great</b> job",
        "unknown_upstream_field": {"retained": True},
    }
    result, _ = fetch("remoteok", envelope("remoteok", [raw]))
    listing = result.listings[0]
    content = normalize_offer("remoteok", listing.payload)

    assert listing.external_id == "id:123"
    assert listing.url == raw["url"]
    assert listing.apply_url == raw["apply_url"]
    assert listing.payload == raw
    assert content == {
        "title": "Data Engineer",
        "company": "DataCorp",
        "description": "Great job",
        "tags": [*[f"tag-{index}" for index in range(20)], "tag-0"],
        "salary": None,
        "location": "Worldwide",
        "remote": True,
        "salary_currency": "USD",
        "salary_min": 80000,
        "salary_max": 120000,
    }


def test_himalayas_three_page_contract_preserves_public_fields(monkeypatch):
    from jobhunt_core.harvest.providers import native_json

    monkeypatch.setattr(native_json, "HIMALAYAS_PAGE_DELAY_SECONDS", 0)
    raw = {
        "guid": "job-1",
        "title": "Backend Engineer",
        "companyName": "Acme",
        "companyLogo": "https://cdn.example/acme.png",
        "applicationLink": "https://jobs.example/job-1",
        "categories": ["Software Engineering", "Python", "Python"],
        "locationRestrictions": ["Germany", "Austria"],
        "employmentType": "Full Time",
        "pubDate": 1791950000,
        "minSalary": 70000.5,
        "maxSalary": 95000,
        "currency": "EUR",
        "excerpt": "<p>Raw <strong>HTML</strong></p>",
        "unknown_upstream_field": {"retained": True},
    }
    pages = [
        {"jobs": [raw]},
        {
            "jobs": [
                {
                    **raw,
                    "guid": "job-2",
                    "applicationLink": "https://jobs.example/job-2",
                }
            ]
        },
        {
            "jobs": [
                {
                    **raw,
                    "guid": "job-3",
                    "applicationLink": "https://jobs.example/job-3",
                }
            ]
        },
    ]

    result, requests = fetch_pages("himalayas", pages)
    listing = result.listings[0]
    content = normalize_offer("himalayas", listing.payload)

    assert [request.url.params.get("offset") for request in requests] == [
        "0",
        "20",
        "40",
    ]
    assert all(request.url.params.get("limit") == "20" for request in requests)
    assert result.complete and result.pages_fetched == 3
    assert result.next_cursor == {"items_seen": 3, "filtered": 0, "invalid": 0}
    assert [item.external_id for item in result.listings] == [
        "id:job-1",
        "id:job-2",
        "id:job-3",
    ]
    assert listing.url == listing.apply_url == raw["applicationLink"]
    assert listing.payload == raw
    assert content == {
        "title": "Backend Engineer",
        "company": "Acme",
        "description": "<p>Raw <strong>HTML</strong></p>",
        "tags": ["Software Engineering", "Python", "Python"],
        "salary": None,
        "location": "Germany",
        "remote": True,
        "salary_currency": "EUR",
        "employment_type": "Full Time",
        "logo": "https://cdn.example/acme.png",
        "salary_min": 70000.5,
        "salary_max": 95000,
    }


def test_himalayas_stops_on_empty_page(monkeypatch):
    from jobhunt_core.harvest.providers import native_json

    monkeypatch.setattr(native_json, "HIMALAYAS_PAGE_DELAY_SECONDS", 0)
    result, requests = fetch_pages(
        "himalayas",
        [
            {
                "jobs": [
                    {
                        "guid": "one",
                        "title": "Editor",
                        "applicationLink": "https://jobs.example/one",
                    }
                ]
            },
            {"jobs": []},
        ],
    )
    assert result.complete and result.pages_fetched == len(requests) == 2
    assert len(result.listings) == 1


def test_himalayas_preserves_empty_logo_string(monkeypatch):
    from jobhunt_core.harvest.providers import native_json

    monkeypatch.setattr(native_json, "HIMALAYAS_PAGE_DELAY_SECONDS", 0)
    result, _requests = fetch_pages(
        "himalayas",
        [
            {
                "jobs": [
                    {
                        "guid": "one",
                        "title": "Editor",
                        "applicationLink": "https://jobs.example/one",
                        "companyLogo": "",
                    }
                ]
            },
            {"jobs": []},
        ],
    )
    assert normalize_offer("himalayas", result.listings[0].payload)["logo"] == ""


def test_himalayas_later_page_failure_is_partial(monkeypatch):
    from jobhunt_core.harvest.providers import native_json

    monkeypatch.setattr(native_json, "HIMALAYAS_PAGE_DELAY_SECONDS", 0)
    result, requests = fetch_pages(
        "himalayas",
        [
            {
                "jobs": [
                    {
                        "guid": "one",
                        "title": "Editor",
                        "applicationLink": "https://jobs.example/one",
                    }
                ]
            },
            {"error": "busy"},
        ],
        statuses=[200, 503],
    )
    assert len(requests) == 2 and len(result.listings) == 1
    assert result.pages_fetched == 1
    assert not result.complete and result.error == "page_fetch_failed"


def test_swisstechjobs_wordpress_contract_and_terminal_page(monkeypatch):
    from jobhunt_core.harvest.providers import native_json

    monkeypatch.setattr(native_json, "WORDPRESS_PAGE_DELAY_SECONDS", 0)
    raw = {
        "id": 48213,
        "date": "2026-10-08T00:00:00",
        "link": "https://www.swisstechjobs.com/job/python-engineer/",
        "title": {"rendered": "Python Engineer &#8211; <strong>Zurich</strong>"},
        "content": {"rendered": "<p>Build services with <b>Python</b> and AWS.</p>"},
        "meta": {
            "_company_name": "Alpine Cloud AG",
            "_job_location": "Zürich",
            "_remote_position": "0",
            "_application": "https://apply.example/48213",
            "_job_salary": "110000-130000",
            "_job_salary_currency": "",
        },
        "unknown_upstream_field": {"retained": True},
    }
    terminal = {
        "code": "rest_post_invalid_page_number",
        "message": "end",
        "data": {"status": 400},
    }

    result, requests = fetch_pages("swisstechjobs", [[raw], terminal], [200, 400])
    listing = result.listings[0]
    content = normalize_offer("swisstechjobs", listing.payload)

    assert [request.url.params.get("page") for request in requests] == ["1", "2"]
    assert all(request.url.params.get("per_page") == "100" for request in requests)
    assert result.complete and result.pages_fetched == 2
    assert result.next_cursor == {"items_seen": 1, "filtered": 0, "invalid": 0}
    assert listing.external_id == "id:48213"
    assert listing.url == raw["link"]
    assert listing.apply_url == raw["meta"]["_application"]
    assert listing.payload == raw
    assert content == {
        "title": "Python Engineer &#8211; Zurich",
        "company": "Alpine Cloud AG",
        "description": "Build services with Python and AWS.",
        # Legacy substring matching also finds the one-letter R tag.
        "tags": ["python", "r", "aws"],
        "salary": "110000-130000",
        "location": "Zürich",
        "remote": True,
        "salary_currency": "CHF",
    }


def test_swisstechjobs_rejects_unrelated_400_as_failure(monkeypatch):
    from jobhunt_core.harvest.providers import native_json

    monkeypatch.setattr(native_json, "WORDPRESS_PAGE_DELAY_SECONDS", 0)
    result, requests = fetch_pages(
        "swisstechjobs",
        [
            [{"id": 1, "link": "https://example.org/1", "title": {"rendered": "Dev"}}],
            {"code": "other", "data": {"status": 400}},
        ],
        [200, 400],
    )
    assert len(requests) == 2 and len(result.listings) == 1
    assert not result.complete and result.error == "page_fetch_failed"


def test_ictjobs_five_page_contract_preserves_acf_and_taxonomies(monkeypatch):
    from jobhunt_core.harvest.providers import native_json

    monkeypatch.setattr(native_json, "WORDPRESS_PAGE_DELAY_SECONDS", 0)
    raw = {
        "id": 91527,
        "date": "2026-10-14T00:23:00",
        "link": "https://ictjobs.ch/devops-engineer/",
        "title": {"rendered": "DevOps Engineer &#8211; 80–100%"},
        "acf": {
            "intro": "<p>Join our platform team.</p>",
            "description": "<p>Build with <b>Docker</b> and Azure.</p>",
            "location": "",
            "has_home_office": True,
            "direct_link": "https://apply.example/91527",
            "use_direct_link": True,
            "salary_min": "90000",
            "salary_max": "110000",
        },
        "_embedded": {
            "wp:term": [
                [{"taxonomy": "post_tag", "name": "Cloud"}],
                [{"taxonomy": "ctx_work_location", "name": "Bern"}],
                [{"taxonomy": "ctx_prof_group", "name": "System Engineering"}],
                [{"taxonomy": "ctx_employment_type", "name": "Festanstellung"}],
            ]
        },
        "unknown_upstream_field": {"retained": True},
    }
    pages = [
        [{**raw, "id": page, "link": f"https://ictjobs.ch/job-{page}/"}]
        for page in range(1, 6)
    ]

    result, requests = fetch_pages("ictjobs", pages)
    listing = result.listings[0]
    content = normalize_offer("ictjobs", listing.payload)

    assert [request.url.params.get("page") for request in requests] == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]
    assert all(request.url.params.get("per_page") == "20" for request in requests)
    assert all(request.url.params.get("_embed") == "" for request in requests)
    assert result.complete and result.pages_fetched == 5
    assert result.next_cursor == {"items_seen": 5, "filtered": 0, "invalid": 0}
    assert listing.external_id == "id:1"
    assert listing.url == "https://ictjobs.ch/job-1/"
    assert listing.apply_url == raw["acf"]["direct_link"]
    assert listing.payload["unknown_upstream_field"] == {"retained": True}
    assert content["title"] == "DevOps Engineer &#8211; 80–100%"
    # Canonical normalization stores an absent company as null; the
    # compatibility response restores the legacy empty string.
    assert content["company"] is None
    assert (
        content["description"] == "Join our platform team. Build with Docker and Azure."
    )
    assert content["location"] == "Bern"
    assert content["remote"] is True
    assert content["salary_min"] == 90000
    assert content["salary_max"] == 110000
    assert content["salary_currency"] == "CHF"
    assert content["employment_type"] == "Festanstellung"
    assert content["prof_group"] == "System Engineering"
    assert {"Cloud", "devops", "docker", "azure"}.issubset(content["tags"])
    assert len(content["tags"]) <= 10


def test_ictjobs_preserves_joint_salary_parse_failure():
    raw = {
        "id": 1,
        "link": "https://ictjobs.ch/job-1/",
        "title": {"rendered": "Engineer"},
        "acf": {"salary_min": "90000", "salary_max": "120'000"},
    }
    result, _requests = fetch_pages("ictjobs", [[raw], []])
    content = normalize_offer("ictjobs", result.listings[0].payload)
    assert content.get("salary_min") is content.get("salary_max") is None
    assert content.get("salary_currency") is None


def test_ictjobs_first_page_400_is_not_a_terminal_empty_page():
    with pytest.raises(httpx.HTTPStatusError):
        fetch(
            "ictjobs",
            {
                "code": "rest_post_invalid_page_number",
                "data": {"status": 400},
            },
            status=400,
        )


@pytest.mark.parametrize(
    "name,payload",
    [
        ("remotive", {}),
        ("remotive", {"jobs": None}),
        ("remotive", {"jobs": {}}),
        ("workingnomads", {}),
        ("jobicy", []),
        ("jobicy", {"jobs": "changed"}),
        ("remoteok", []),
        ("remoteok", {}),
        ("remoteok", ["metadata"]),
        ("himalayas", []),
        ("himalayas", {"jobs": "changed"}),
        ("swisstechjobs", {}),
        ("ictjobs", {}),
    ],
)
def test_invalid_envelope_is_error_not_empty(name, payload):
    with pytest.raises(ProviderResponseError):
        fetch(name, payload)


@pytest.mark.parametrize(
    "name,payload",
    [
        ("remotive", {"jobs": []}),
        ("workingnomads", []),
        ("jobicy", {"jobs": []}),
        ("remoteok", [{"legal": "metadata"}]),
        ("himalayas", {"jobs": []}),
        ("swisstechjobs", []),
        ("ictjobs", []),
    ],
)
def test_explicit_empty_is_success(name, payload):
    result, _ = fetch(name, payload)
    assert result.complete and result.listings == ()


def test_unrecognized_items_cannot_claim_empty_success():
    with pytest.raises(ProviderResponseError):
        fetch(
            "remotive",
            {"jobs": [None, {"id": 1, "renamed_url": "https://example.org/a"}]},
        )


def test_bad_item_does_not_drop_valid_neighbor():
    result, _ = fetch(
        "remotive",
        {"jobs": [None, {"id": 2, "url": "https://example.org/a", "title": "Editor"}]},
    )
    assert len(result.listings) == 1


def test_http_failure_propagates():
    with pytest.raises(httpx.HTTPStatusError):
        fetch("remotive", {"jobs": []}, status=429)


def test_missing_id_uses_url_not_mutable_title():
    one, _ = fetch("workingnomads", [{"url": "https://example.org/a", "title": "A"}])
    two, _ = fetch("workingnomads", [{"url": "https://example.org/a", "title": "B"}])
    assert one.listings[0].external_id == two.listings[0].external_id


def test_jobicy_tags_are_separate_scopes_not_silent_deduped_fetches():
    _, requests = fetch("jobicy", {"jobs": []}, {"tag": "content", "geo": "europe"})
    assert len(requests) == 1
    assert requests[0].url.params["tag"] == "content"
    assert requests[0].url.params["geo"] == "europe"


def test_jobicy_list_job_type_preserves_search_metadata():
    result, _ = fetch(
        "jobicy",
        {
            "jobs": [
                {
                    "id": 1,
                    "url": "https://example.org/a",
                    "jobTitle": "Senior Python Developer",
                    "jobDescription": "Write React documentation in English",
                    "jobGeo": "US",
                    "jobType": ["Full-time", 4, {"invalid": True}],
                }
            ]
        },
    )
    content = normalize_offer("jobicy", result.listings[0].payload)
    assert content["contract_type"] == "full_time"
    assert content["seniority"] == "senior"
    assert content["location"] == "United States"
    assert content["tags"][:4] == ["developer", "python", "r", "react"]
    assert content["language"] == "en"


@pytest.mark.parametrize(
    "raw,expected",
    [("anywhere", "Worldwide"), ("UK", "United Kingdom"), ("EMEA", "Emea")],
)
def test_jobicy_location_preserves_legacy_public_labels(raw, expected):
    result, _ = fetch(
        "jobicy",
        {
            "jobs": [
                {
                    "id": 1,
                    "url": "https://example.org/a",
                    "jobTitle": "Editor",
                    "jobGeo": raw,
                }
            ]
        },
    )
    assert normalize_offer("jobicy", result.listings[0].payload)["location"] == expected


def test_scope_rejects_unknown_params_before_network():
    with pytest.raises(ProviderConfigError):
        fetch("remotive", {"jobs": []}, {"endpoint": "http://localhost/private"})


def test_remoteok_rejects_filters_before_network():
    with pytest.raises(ProviderConfigError):
        fetch("remoteok", [{"legal": "metadata"}], {"query": "python"})


def test_himalayas_rejects_filters_before_network():
    with pytest.raises(ProviderConfigError):
        fetch("himalayas", {"jobs": []}, {"query": "python"})


def test_swisstechjobs_rejects_filters_before_network():
    with pytest.raises(ProviderConfigError):
        fetch("swisstechjobs", [], {"query": "python"})


def test_ictjobs_rejects_filters_before_network():
    with pytest.raises(ProviderConfigError):
        fetch("ictjobs", [], {"query": "python"})


def test_remoteok_builds_url_from_slug_when_source_url_is_missing():
    result, _ = fetch(
        "remoteok",
        envelope(
            "remoteok",
            [{"id": 4, "position": "QA", "slug": "qa-acme"}],
        ),
    )
    assert result.listings[0].url == "https://remoteok.com/remote-jobs/qa-acme"


def test_keyword_is_part_of_scope_identity():
    p = provider("remotive")
    assert p.params_fingerprint({"query": "editor"}) != p.params_fingerprint(
        {"query": "teacher"}
    )


def test_html_and_tags_are_defensive():
    result, _ = fetch(
        "workingnomads",
        [
            {
                "id": 1,
                "url": "https://example.org/a",
                "title": "Editor",
                "description": "<script>secret()</script><p>Work</p><p>here</p>",
                "tags": "writing, editing",
            }
        ],
    )
    content = normalize_offer("workingnomads", result.listings[0].payload)
    assert content["description"] == "Work here"
    assert content["tags"] == ["writing", "editing"]


def test_native_registry_does_not_require_legacy_modules():
    from jobhunt_core.harvest.providers import get_provider
    from jobhunt_core.harvest.registry import ensure_handler

    for name in (
        "remotive",
        "workingnomads",
        "jobicy",
        "remoteok",
        "himalayas",
        "swisstechjobs",
        "ictjobs",
    ):
        assert get_provider(name).name == name
        assert ensure_handler(name)

"""SvelteKit decode errors must not masquerade as an empty Swiss feed."""
import asyncio
import json

import httpx
import pytest


def envelope(rows=None):
    rows = rows if rows is not None else [{"title": "Python Lehrer", "path": "/jobs/lehrer",
        "contactCompany": "Schule", "workingAddressCity": "Bern",
        "publicFrom": "2026-09-19T10:00:00Z"}]
    data = [{"jobSearch": 1}, {"data": 2}, []]
    for row in rows:
        data[2].append(len(data))
        refs = {}
        data.append(refs)
        for key, value in row.items():
            refs[key] = len(data)
            data.append(value)
    return {"nodes": [{"data": data}]}


def fetch(body, params=None):
    from jobhunt_core.harvest.providers.native_publicjobs import PublicJobsProvider
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=json.dumps(body).encode())
        )) as client:
            return await PublicJobsProvider().fetch_new(params or {}, None, client)
    return asyncio.run(run())


def test_decode_normalize_identity_and_original_fields():
    from jobhunt_core.harvest.normalize import normalize_offer
    result = fetch(envelope())
    assert result.complete and len(result.listings) == 1
    row = result.listings[0]
    assert row.url == "https://www.publicjobs.ch/jobs/lehrer"
    assert row.payload["publicFrom"] == "2026-09-19T10:00:00Z"
    content = normalize_offer("publicjobs", row.payload)
    assert content["title"] == "Python Lehrer" and content["company"] == "Schule"
    assert content["location"] == "Bern" and content["remote"] is False
    changed = envelope([{"title": "Lehrer neu", "path": "/jobs/lehrer"}])
    assert fetch(changed).listings[0].external_id == row.external_id


@pytest.mark.parametrize("body", [None, [], {}, {"nodes": []},
    {"nodes": [{"data": []}]}, {"nodes": [{"data": [1]}]},
    {"nodes": [{"data": [{"jobSearch": -1}]}]},
    {"nodes": [{"data": [{"jobSearch": True}, {"data": 2}, []]}]},
    {"nodes": [{"data": [{"jobSearch": 1}, {"data": -1}, []]}]},
    {"nodes": [{"data": [{"jobSearch": 1}, {"data": 2}, "bad"]}]}])
def test_bad_envelope_fails_closed(body):
    from jobhunt_core.harvest.provider import ProviderResponseError
    with pytest.raises(ProviderResponseError):
        fetch(body)


@pytest.mark.parametrize("bad", [-1, True, 9999, "3"])
def test_invalid_job_reference_keeps_neighbor_but_is_partial(bad):
    body = envelope()
    body["nodes"][0]["data"][2].append(bad)
    result = fetch(body)
    assert len(result.listings) == 1 and not result.complete and result.error


@pytest.mark.parametrize("path", ["//evil.test/jobs", "https://evil.test/jobs", "/../admin", "/jobs/\ud800", "", None])
def test_invalid_path_not_fabricated(path):
    body = envelope([{"title": "valid", "path": "/jobs/valid"}, {"title": "bad", "path": path}])
    result = fetch(body)
    assert len(result.listings) == 1 and not result.complete


def test_empty_is_complete_and_filter_is_not_upstream_param():
    assert fetch(envelope([])).complete
    result = fetch(envelope(), {"query": "NONMATCH"})
    assert result.complete and not result.listings


def test_registry_and_admission():
    from jobhunt_core.harvest.providers import get_provider
    from jobhunt_core.harvest.admission import admission_window
    assert get_provider("publicjobs").name == "publicjobs"
    assert admission_window("publicjobs", {"admission_window_days": 7}).days == 7

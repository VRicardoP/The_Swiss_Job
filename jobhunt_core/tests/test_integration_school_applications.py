"""Private school drafts/state preserve both existing state vocabularies."""

import asyncio
import uuid

from jobhunt_core.tests.test_integration_api_schools import _create, _request, SCOPES
from jobhunt_core.tests.test_integration_api_saved_searches import (
    db,
    _seed_profile,
    _rows,
    pytestmark,
)


def test_school_draft_state_etag_replay_and_erase(db):
    f, made = db
    token, tenant, pid = _seed_profile(f, made, SCOPES)
    other, _, opid = _seed_profile(f, made, SCOPES)
    monitor = _create(f, token).json()
    base = f"/v1/profiles/{pid}/school-applications"
    body = {
        "monitor_id": monitor["id"],
        "source_ref": "legacy-job",
        "context": {"notes": "private"},
    }
    created = _request(f, token, base, "POST", body, "create")
    assert created.status_code == 201, created.text
    path = base + "/" + created.json()["id"]
    assert _request(f, token, base, "POST", body, "create").json() == created.json()
    assert _request(f, token, base, "POST", body, "duplicate").status_code == 409
    assert _request(f, other, path).status_code == 404
    assert (
        _request(
            f, other, f"/v1/profiles/{opid}/school-applications/" + created.json()["id"]
        ).status_code
        == 404
    )
    assert _request(f, token, base + "?source_ref=legacy-job").json()["items"] == [
        created.json()
    ]
    drafted = _request(
        f,
        token,
        path,
        "PATCH",
        {"draft_content": "private draft"},
        "draft",
        created.headers["etag"],
    )
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["status"] == "drafted"
    assert drafted.json()["context"] == {"notes": "private"}
    assert (
        _request(
            f,
            token,
            path,
            "PATCH",
            {"status": "sent"},
            "stale",
            created.headers["etag"],
        ).status_code
        == 412
    )
    sent = _request(
        f, token, path, "PATCH", {"status": "sent"}, "sent", drafted.headers["etag"]
    )
    assert sent.status_code == 200
    rewritten = _request(
        f,
        token,
        path,
        "PATCH",
        {"draft_content": "revised draft"},
        "rewrite",
        sent.headers["etag"],
    )
    assert rewritten.status_code == 200 and rewritten.json()["status"] == "sent"
    assert (
        _request(
            f, token, path, "PATCH", {"status": None}, "null", rewritten.headers["etag"]
        ).status_code
        == 400
    )
    receipts = _rows(
        f,
        "SELECT response FROM idempotency_records WHERE response->>'subject_profile_id'=:p",
        p=str(pid),
    )
    assert len(receipts) == 4
    assert all(
        "draft" not in str(r[0]) and "private" not in str(r[0]) for r in receipts
    )

    async def erase():
        from jobhunt_core.shadow.projector import erase_shadow_profile

        async with f() as s:
            await erase_shadow_profile(s, "user-1", tenant)
            await s.commit()

    asyncio.run(erase())
    assert (
        _rows(f, "SELECT id FROM school_applications WHERE profile_id=:p", p=pid) == []
    )
    assert _request(f, token, base, "POST", body, "create").status_code == 404
    assert _request(f, token, "/v1/schools/" + monitor["id"]).status_code == 200


def test_school_application_rejects_foreign_monitor_and_job(db):
    f, made = db
    token, _, pid = _seed_profile(f, made, SCOPES)
    other, _, _ = _seed_profile(f, made, SCOPES)
    foreign = _create(f, other).json()
    base = f"/v1/profiles/{pid}/school-applications"
    bad = _request(
        f,
        token,
        base,
        "POST",
        {"monitor_id": foreign["id"], "source_ref": "x"},
        "foreign",
    )
    assert bad.status_code == 404, bad.text
    own = _create(f, token).json()
    bad = _request(
        f,
        token,
        base,
        "POST",
        {
            "monitor_id": own["id"],
            "source_ref": "x",
            "school_job_id": str(uuid.uuid4()),
        },
        "missing",
    )
    assert bad.status_code == 404, bad.text
    assert (
        _rows(f, "SELECT id FROM school_applications WHERE profile_id=:p", p=pid) == []
    )

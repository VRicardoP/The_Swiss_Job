"""E.15 against real PostgreSQL. No scraping, email, or production database."""

import asyncio
import json
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from jobhunt_core.tests import test_integration_api as tia
from jobhunt_core.tests.test_integration_api_saved_searches import (
    db,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    _rows,
    _seed_profile,
    pytestmark,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
)

SCOPES = ["schools:read", "schools:write"]
SETTINGS = {
    "name": "Example School",
    "group_tier": "B",
    "monitoring_mode": "manual_only",
    "policy": "portal_only",
    "contact_name": "Private contact",
    "is_active": False,
}


def _request(
    f, token, path="/v1/schools", method="GET", body=None, key=None, etag=None
):
    headers = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    if etag is not None:
        headers["If-Match"] = etag
    return tia._api(
        f,
        path,
        token=token,
        method=method,
        headers=headers,
        content=json.dumps(body, ensure_ascii=True) if body is not None else None,
    )


def _create(f, token, slug=None, **settings):
    return _request(
        f,
        token,
        method="POST",
        key=str(uuid.uuid4()),
        body={
            "external_ref": slug or "school-" + uuid.uuid4().hex[:12],
            "settings": {**SETTINGS, **settings},
        },
    )


def test_school_identity_shared_but_contacts_and_configuration_private(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    a, _, ap = _seed_profile(f, made, SCOPES)
    b, _, bp = _seed_profile(f, made, SCOPES)
    slug = "school-" + uuid.uuid4().hex[:12]
    ra, rb = (
        _create(f, a, slug),
        _create(f, b, slug, contact_name="Other contact", is_active=True),
    )
    assert ra.status_code == rb.status_code == 201, (ra.text, rb.text)
    assert ra.json()["school_id"] == rb.json()["school_id"]
    assert ra.json()["id"] != rb.json()["id"]
    assert _request(f, a).json()["items"] == [ra.json()]
    assert _request(f, b).json()["items"] == [rb.json()]
    for method in ("GET", "PUT", "DELETE"):
        response = _request(
            f,
            b,
            "/v1/schools/" + ra.json()["id"],
            method,
            ra.json()["settings"] if method == "PUT" else None,
            str(uuid.uuid4()),
            ra.headers["etag"],
        )
        assert response.status_code == 404, response.text
    for pid, token in ((ap, b), (bp, a)):
        assert (
            _request(f, token, f"/v1/profiles/{pid}/school-preferences").status_code
            == 404
        )


def test_country_edit_cannot_change_shared_school_identity(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    a, _, _ = _seed_profile(f, made, SCOPES)
    b, _, _ = _seed_profile(f, made, SCOPES)
    slug = "school-" + uuid.uuid4().hex[:12]
    first, other = _create(f, a, slug), _create(f, b, slug)
    original = first.json()
    changed = _request(
        f,
        a,
        "/v1/schools/" + original["id"],
        "PUT",
        {**original["settings"], "country": "DE"},
        "country-edit",
        first.headers["etag"],
    )
    assert changed.status_code == 409, changed.text
    assert _request(f, a, "/v1/schools/" + original["id"]).json() == original
    assert _request(f, b, "/v1/schools/" + other.json()["id"]).json() == other.json()


def test_school_replay_etag_and_deleted_receipt_do_not_resurrect(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, _ = _seed_profile(f, made, SCOPES)
    key = "school-" + uuid.uuid4().hex
    body = {"external_ref": key[:24], "settings": SETTINGS}
    first = _request(f, token, method="POST", body=body, key=key)
    assert first.status_code == 201, first.text
    replay = _request(f, token, method="POST", body=body, key=key)
    assert replay.json() == first.json()
    receipts = _rows(f, "SELECT response FROM idempotency_records WHERE key=:k", k=key)
    assert receipts[0][0]["body"] == {"id": first.json()["id"]}
    path = "/v1/schools/" + first.json()["id"]
    changed = {**first.json()["settings"], "notes": "changed"}
    assert (
        _request(f, token, path, "PUT", changed, "no-precondition").status_code == 428
    )
    edited = _request(f, token, path, "PUT", changed, "edit", first.headers["etag"])
    assert edited.status_code == 200 and edited.json()["version"] == 2, edited.text
    stale = _request(f, token, path, "PUT", changed, "stale", first.headers["etag"])
    assert stale.status_code == 412, stale.text
    assert (
        _request(
            f, token, path, "DELETE", key="delete", etag=edited.headers["etag"]
        ).status_code
        == 204
    )
    assert (
        _request(
            f, token, path, "DELETE", key="delete", etag=edited.headers["etag"]
        ).status_code
        == 204
    )
    assert _request(f, token, method="POST", body=body, key=key).status_code == 404
    assert _request(f, token, path).status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "bad\x00name"},
        {"notes": "bad\ud800notes"},
        {"policy": "unknown"},
        {"settings": []},
        {"jobs_page_url": "javascript:alert(1)"},
        {"group_tier": None},
    ],
)
def test_school_bad_boundary_never_writes(db, bad):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, _ = _seed_profile(f, made, SCOPES)
    response = _create(f, token, **bad)
    assert response.status_code == 400, response.text
    assert _request(f, token).json()["items"] == []


def test_school_scope_cursor_and_nonvacuous_pagination(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, tenant, _ = _seed_profile(f, made, SCOPES)
    assert _request(f, None).status_code == 401
    _, _, readonly = tia._issue(f, made, tenant, ["schools:read"])
    assert _create(f, readonly).status_code == 403
    for _ in range(4):
        assert _create(f, token).status_code == 201
    first = _request(f, token, "/v1/schools?limit=2").json()
    second = _request(
        f, token, "/v1/schools?limit=2&cursor=" + first["next_cursor"]
    ).json()
    assert len(first["items"]) == len(second["items"]) == 2
    assert second["next_cursor"] is None
    assert len({r["id"] for r in first["items"] + second["items"]}) == 4
    assert _request(f, token, "/v1/schools?cursor=broken").status_code == 400


def test_school_preferences_roundtrip_and_profile_erasure(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, tenant, pid = _seed_profile(f, made, SCOPES)
    path = f"/v1/profiles/{pid}/school-preferences"
    initial = _request(f, token, path)
    assert initial.json() == {"enabled": False}
    result = _request(
        f, token, path, "PUT", {"enabled": True}, "enable", initial.headers["etag"]
    )
    assert result.status_code == 200, result.text
    assert _request(f, token, path).json() == {"enabled": True}
    stale = _request(
        f,
        token,
        path,
        "PUT",
        {"enabled": False},
        "disable-stale",
        initial.headers["etag"],
    )
    assert stale.status_code == 412

    async def erase():
        from jobhunt_core.shadow.projector import erase_shadow_profile

        async with f() as s:
            assert await erase_shadow_profile(s, "user-1", tenant) == pid
            await s.commit()

    asyncio.run(erase())
    assert (
        _rows(f, "SELECT * FROM school_profile_preferences WHERE profile_id=:p", p=pid)
        == []
    )
    assert (
        _request(f, token, path, "PUT", {"enabled": True}, "enable").status_code == 404
    )


def test_school_db_rejects_unlinked_unexplained_and_cross_consumer_job(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    a, _, ap = _seed_profile(f, made, SCOPES)
    _, _, bp = _seed_profile(f, made, SCOPES)
    monitor = _create(f, a).json()
    ac = _rows(f, "SELECT consumer_id FROM profiles WHERE id=:p", p=ap)[0][0]
    bc = _rows(f, "SELECT consumer_id FROM profiles WHERE id=:p", p=bp)[0][0]

    async def insert(cid, reason):
        async with f() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO school_job_details(id,monitor_id,consumer_id,source_ref,metadata,quarantine_reason) "
                    "VALUES (:id,:m,:c,'unlinked','{}',:reason)"
                ),
                {
                    "id": uuid.uuid4(),
                    "m": uuid.UUID(monitor["id"]),
                    "c": cid,
                    "reason": reason,
                },
            )
            await s.commit()

    with pytest.raises(IntegrityError):
        asyncio.run(insert(ac, None))
    with pytest.raises(IntegrityError):
        asyncio.run(insert(bc, "no_url"))
    asyncio.run(insert(ac, "no_url"))
    response = _request(
        f, a, "/v1/schools/" + monitor["id"], "DELETE", key="protected", etag="*"
    )
    assert response.status_code == 409, response.text

"""Owner erase must remove cached personal responses, not just primary tables."""

import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.shadow.projector import erase_shadow_profile
from jobhunt_core.tests.test_integration_api_saved_searches import _seed_profile
from jobhunt_core.tests.test_integration_api_applications import db
from jobhunt_core.tests import test_integration_api as api


@pytest.mark.parametrize(
    "spelling", ["canonical", "uppercase", "compact", "braces", "urn"]
)
@pytest.mark.parametrize("legacy", [False, True])
def test_erased_cv_cannot_be_replayed_from_idempotency(db, spelling, legacy):
    factory, created = db
    token, consumer, pid = _seed_profile(
        factory, created, ["profiles:read", "profiles:write"]
    )
    path_id = (
        str(pid)
        if spelling == "canonical"
        else (
            str(pid).upper()
            if spelling == "uppercase"
            else (
                "{" + str(pid) + "}"
                if spelling == "braces"
                else "urn:uuid:" + str(pid) if spelling == "urn" else pid.hex
            )
        )
    )
    path = f"/v1/profiles/{path_id}"
    headers = {"Idempotency-Key": "owner-erase-" + uuid.uuid4().hex}
    marker = "SYNTHETIC-PRIVATE-CV-" + uuid.uuid4().hex
    body = {"title": "Synthetic test engineer", "cv_text": marker, "skills": ["Python"]}
    first = api._api(
        factory, path, token=token, method="PUT", json_body=body, headers=headers
    )
    assert first.status_code == 200

    async def erase():
        async with factory() as s:
            if legacy:
                await s.execute(
                    sa.text(
                        "UPDATE idempotency_records SET response=response-'subject_profile_id' WHERE key=:key"
                    ),
                    {"key": headers["Idempotency-Key"]},
                )
            await erase_shadow_profile(s, "user-1", consumer)
            await s.commit()
            residual = await s.scalar(
                sa.text(
                    "SELECT count(*) FROM idempotency_records i "
                    "JOIN consumers c ON c.id=i.consumer_id WHERE c.name=:c AND i.response::text LIKE :marker"
                ),
                {"c": consumer, "marker": "%" + marker + "%"},
            )
            return residual

    residual = asyncio.run(erase())
    replay = api._api(
        factory, path, token=token, method="PUT", json_body=body, headers=headers
    )
    assert (residual, replay.status_code) == (0, 404)
    assert marker not in replay.text


@pytest.mark.parametrize("resource", ["applications", "saved-searches"])
@pytest.mark.parametrize("legacy", [False, True])
def test_erasure_removes_personal_receipts_but_not_another_owner(db, resource, legacy):
    from jobhunt_core import profiles

    factory, created = db
    scope = resource.replace("-", "_")
    token, consumer, pid = _seed_profile(
        factory, created, [scope + ":read", scope + ":write"]
    )
    marker = "SYNTHETIC-PRIVATE-" + uuid.uuid4().hex

    async def second_profile():
        async with factory() as s:
            cid = await profiles.ensure_consumer(s, consumer)
            other = await profiles.upsert_profile(s, cid, "user-2")
            await s.commit()
            return other

    other = asyncio.run(second_profile())
    path = "/v1/" + resource

    def body(owner):
        values = {"profile_id": str(owner)}
        values.update(
            {
                "title": "Synthetic job",
                "notes": marker,
                "url": f"https://receipt-test.invalid/{owner}/{marker}",
            }
            if resource == "applications"
            else {"name": marker}
        )
        if resource == "applications":
            created["shadow_urls"].append(values["url"])
        return values

    first_headers = {"Idempotency-Key": "erased-" + uuid.uuid4().hex}
    other_headers = {"Idempotency-Key": "preserved-" + uuid.uuid4().hex}
    first = api._api(
        factory,
        path,
        token=token,
        method="POST",
        json_body=body(pid),
        headers=first_headers,
    )
    preserved = api._api(
        factory,
        path,
        token=token,
        method="POST",
        json_body=body(other),
        headers=other_headers,
    )
    assert first.status_code == preserved.status_code == 201

    async def erase():
        async with factory() as s:
            if legacy:
                await s.execute(
                    sa.text(
                        "UPDATE idempotency_records SET response=response-'subject_profile_id' "
                        "WHERE key=:key"
                    ),
                    {"key": first_headers["Idempotency-Key"]},
                )
            await erase_shadow_profile(s, "user-1", consumer)
            await s.commit()
            return await s.scalar(
                sa.text("SELECT count(*) FROM idempotency_records WHERE key=:key"),
                {"key": first_headers["Idempotency-Key"]},
            )

    assert asyncio.run(erase()) == 0
    denied = api._api(
        factory,
        path,
        token=token,
        method="POST",
        json_body=body(pid),
        headers=first_headers,
    )
    replay = api._api(
        factory,
        path,
        token=token,
        method="POST",
        json_body=body(other),
        headers=other_headers,
    )
    assert denied.status_code == 404
    assert marker not in denied.text
    assert replay.status_code == 201 and replay.json() == preserved.json()


@pytest.mark.parametrize("resource", ["applications", "saved-searches"])
@pytest.mark.parametrize("legacy", [False, True])
def test_delete_replay_is_preserved_and_new_receipts_are_erased(db, resource, legacy):
    factory, created = db
    scope = resource.replace("-", "_")
    token, consumer, pid = _seed_profile(
        factory, created, [scope + ":read", scope + ":write"]
    )
    body = {
        "profile_id": str(pid),
        **(
            {"title": "Synthetic job", "url": f"https://receipt-test.invalid/{pid}"}
            if resource == "applications"
            else {"name": "Synthetic search"}
        ),
    }
    if resource == "applications":
        created["shadow_urls"].append(body["url"])
    first = api._api(
        factory,
        "/v1/" + resource,
        token=token,
        method="POST",
        json_body=body,
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert first.status_code == 201
    path = "/v1/" + resource + "/" + first.json()["id"]
    key = uuid.uuid4().hex
    headers = {"Idempotency-Key": key, "If-Match": first.headers["etag"]}
    deleted = api._api(factory, path, token=token, method="DELETE", headers=headers)
    assert deleted.status_code == 204

    async def remove_metadata():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE idempotency_records SET response=response-'subject_profile_id' WHERE key=:key"
                ),
                {"key": key},
            )
            await s.commit()

    if legacy:
        asyncio.run(remove_metadata())
    assert (
        api._api(
            factory, path, token=token, method="DELETE", headers=headers
        ).status_code
        == 204
    )

    async def erase():
        async with factory() as s:
            await erase_shadow_profile(s, "user-1", consumer)
            await s.commit()
            return await s.scalar(
                sa.text("SELECT count(*) FROM idempotency_records WHERE key=:key"),
                {"key": key},
            )

    count = asyncio.run(erase())
    if not legacy:
        assert count == 0
        assert (
            api._api(
                factory, path, token=token, method="DELETE", headers=headers
            ).status_code
            == 404
        )
    else:
        # Historical empty DELETE receipts have no recoverable subject after the
        # resource disappeared. They retain no response content and expire normally.
        assert count == 1


def test_cached_replay_waits_for_owner_erasure_before_reading_receipt(db):
    from httpx import ASGITransport, AsyncClient
    from jobhunt_core.api import deps
    from jobhunt_core.api.main import app

    factory, created = db
    token, consumer, pid = _seed_profile(factory, created, ["profiles:write"])
    path = f"/v1/profiles/{pid}"
    key = uuid.uuid4().hex
    body = {"title": "Synthetic race", "cv_text": "SYNTHETIC-CACHED-PRIVATE"}
    assert (
        api._api(
            factory,
            path,
            token=token,
            method="PUT",
            json_body=body,
            headers={"Idempotency-Key": key},
        ).status_code
        == 200
    )

    async def run():
        request_pid = asyncio.Future()

        async def override_session():
            async with factory() as s:
                backend = await s.scalar(sa.text("SELECT pg_backend_pid()"))
                request_pid.set_result(backend)
                yield s

        app.dependency_overrides[deps.get_session] = override_session
        try:
            async with factory() as eraser:
                await eraser.execute(
                    sa.text("SELECT id FROM profiles WHERE id=:p FOR UPDATE"),
                    {"p": pid},
                )
                eraser_pid = await eraser.scalar(sa.text("SELECT pg_backend_pid()"))
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    task = asyncio.create_task(
                        client.put(
                            path,
                            json=body,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Idempotency-Key": key,
                            },
                        )
                    )
                    try:
                        writer_pid = await asyncio.wait_for(request_pid, 5)
                        async with factory() as observer:
                            async with asyncio.timeout(5):
                                while True:
                                    blockers = await observer.scalar(
                                        sa.text("SELECT pg_blocking_pids(:p)"),
                                        {"p": writer_pid},
                                    )
                                    if eraser_pid in blockers:
                                        break
                                    assert (
                                        not task.done()
                                    ), "cached response escaped the owner lock"
                                    await asyncio.sleep(0.01)
                        await erase_shadow_profile(eraser, "user-1", consumer)
                        await eraser.commit()
                        response = await asyncio.wait_for(task, 5)
                        assert response.status_code == 404
                        assert body["cv_text"] not in response.text
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
        finally:
            app.dependency_overrides.clear()

    asyncio.run(run())

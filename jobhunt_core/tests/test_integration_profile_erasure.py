"""Live erasure: owned, transactional, retryable, and irreversible to stale writers."""

import asyncio
import uuid
import json

import pytest
import sqlalchemy as sa

from jobhunt_core import profiles
from jobhunt_core.erasure import erase_external_identity, erase_owned_profile
from jobhunt_core.tests import test_integration_api as tia
from jobhunt_core.tests.test_integration_api_saved_searches import (
    db,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    _rows,
    _seed_profile,
    pytestmark,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
)


def test_portfolio_erasure_scrubs_material_manifest_without_other_user_loss(db):  # noqa: F811  (la fixture, no una redefinición)

    f, made = db

    async def run():
        async with f() as session:
            cid = await profiles.ensure_consumer(session, "portfolio")
            pid = await profiles.upsert_profile(session, cid, "synthetic-erased")
            gone = str(("synthetic-erased", "url", "private-note"))
            kept = str(("synthetic-kept", "url", "other-note"))
            manifest = {
                "verdict": "divergent",
                "tables": {"applications": {"missing": [gone, kept], "extra": []}},
                "staged": [
                    {
                        "external_ref": "synthetic-erased",
                        "durable": {"notes": "private-note"},
                    },
                    {
                        "external_ref": "synthetic-kept",
                        "durable": {"notes": "other-note"},
                    },
                ],
                "divergences": [gone + kept],
                "events": {"expected": {gone: 1, kept: 1}, "actual": {}},
                "provenance": {"applications": [str(uuid.uuid4())]},
            }
            mid = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO portfolio_migration_manifest (id, verdict, manifest) "
                    "VALUES (:id, :verdict, CAST(:manifest AS jsonb))"
                ),
                {
                    "id": mid,
                    "verdict": manifest["verdict"],
                    "manifest": json.dumps(manifest),
                },
            )
            await erase_owned_profile(session, cid, pid)
            row = (
                await session.execute(
                    sa.text(
                        "SELECT manifest,status,verdict FROM portfolio_migration_manifest WHERE id=:id"
                    ),
                    {"id": mid},
                )
            ).one()

            assert "private-note" not in json.dumps(row.manifest)
            assert row.manifest["tables"]["applications"]["missing"] == [kept]
            assert row.manifest["staged"][0]["durable"]["notes"] == "other-note"
            assert row.manifest["provenance"] == manifest["provenance"]
            assert (row.status, row.verdict) == ("unknown", "redacted")
            await session.rollback()

    asyncio.run(run())


def test_owned_erasure_retry_and_cross_tenant(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed_profile(f, made, scopes=["profiles:write"])
    other, _, other_pid = _seed_profile(f, made, scopes=["profiles:write"])
    assert (
        tia._api(f, f"/v1/profiles/{pid}", token=other, method="DELETE").status_code
        == 404
    )
    assert _rows(f, "SELECT id FROM profiles WHERE id=:p", p=pid)
    a = tia._api(f, f"/v1/profiles/{pid}", token=token, method="DELETE")
    b = tia._api(f, f"/v1/profiles/{pid}", token=token, method="DELETE")
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json() == b.json()
    assert a.json()["backup_erasure"] == "not_confirmed"
    assert not _rows(f, "SELECT id FROM profiles WHERE id=:p", p=pid)
    assert _rows(f, "SELECT id FROM profiles WHERE id=:p", p=other_pid)
    assert (
        tia._api(f, f"/v1/profiles/{pid}", token=other, method="DELETE").status_code
        == 404
    )


def test_swissjob_erasure_no_longer_needs_the_capture_staging(db):  # noqa: F811  (la fixture, no una redefinición)
    """D3: core0063 retiró `shadow_change_log`. Borrar un perfil de SwissJob,
    con perfil o sin él, ya no puede depender de esa tabla: antes los dos
    caminos la bloqueaban y fallaban con «relation does not exist»."""
    f, made = db

    async def run():
        async with f() as s:
            assert (
                await s.scalar(sa.text("SELECT to_regclass('shadow_change_log')"))
                is None
            )
            cid = await profiles.ensure_consumer(s, "swissjob-shadow")
            pid = await profiles.upsert_profile(s, cid, str(uuid.uuid4()))
            assert await erase_owned_profile(s, cid, pid)
            assert await erase_external_identity(s, cid, str(uuid.uuid4()))
            await s.rollback()  # synthetic identities, no real user affected

    asyncio.run(run())


def test_retired_cdc_replica_cannot_acknowledge(db):  # noqa: F811  (la fixture, no una redefinición)
    """D3: `swissjob-cdc` dejó de ser una réplica. La validación va antes de
    buscar el recibo, así que un tenant cualquiera distingue los dos casos:
    la réplica retirada es 400 y la viva llega a buscar su recibo (404)."""
    f, made = db
    token, _, _ = _seed_profile(f, made, scopes=["profiles:write"])
    url = f"/v1/profile-erasures/{uuid.uuid4()}/acks/"
    retired = tia._api(f, url + "swissjob-cdc", token=token, method="POST")
    assert retired.status_code == 400, retired.text
    assert retired.json()["code"] == "invalid_replica"
    live = tia._api(f, url + "swissjob-live", token=token, method="POST")
    assert live.status_code == 404, live.text


def test_erasure_inventory_owned_and_paginated(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed_profile(f, made, scopes=["profiles:write"])
    other, _, _ = _seed_profile(f, made, scopes=["profiles:write"])
    assert (
        tia._api(f, f"/v1/profiles/{pid}", token=token, method="DELETE").status_code
        == 200
    )
    response = tia._api(f, "/v1/profile-erasures", token=token)
    assert response.status_code == 200, response.text
    assert [r["profile_id"] for r in response.json()["items"]] == [str(pid)]
    assert tia._api(f, "/v1/profile-erasures", token=other).json()["items"] == []
    assert (
        tia._api(f, "/v1/profile-erasures?after=" + str(pid), token=token).json()[
            "items"
        ]
        == []
    )


def test_offline_restore_reapplies_sealed_inventory(db):  # noqa: F811  (la fixture, no una redefinición)
    from jobhunt_core.erasure_restore import snapshot, reconcile

    f, made = db
    _, _, pid = _seed_profile(f, made)
    cid = _rows(f, "SELECT consumer_id FROM profiles WHERE id=:p", p=pid)[0][0]

    async def run():
        async with f() as s:
            await erase_owned_profile(s, cid, pid)
            bundle = await snapshot(s)
            await s.rollback()  # reproduces the account state in a pre-erasure backup
        async with f() as restored:
            assert await reconcile(restored, bundle) == 1
            await restored.commit()
        async with f() as repeated:
            assert await reconcile(repeated, bundle) == 1
            await repeated.commit()
        async with f() as rejected:
            bundle["items"][0]["external_ref"] = "tampered"
            with pytest.raises(ValueError, match="seal"):
                await reconcile(rejected, bundle)

    asyncio.run(run())
    assert not _rows(f, "SELECT id FROM profiles WHERE id=:p", p=pid)


@pytest.mark.parametrize("direct", [False, True])
def test_repeatable_read_cannot_recreate_erased_identity(db, direct):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    _, _, pid = _seed_profile(f, made)
    cid = _rows(f, "SELECT consumer_id FROM profiles WHERE id=:p", p=pid)[0][0]

    async def run():
        async with f() as stale, f() as eraser:
            await stale.execute(
                sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            )
            await stale.execute(
                sa.text("SELECT count(*) FROM profile_erasure_receipts")
            )
            await erase_owned_profile(eraser, cid, pid)
            await eraser.commit()
            with pytest.raises(sa.exc.DBAPIError, match="serialize"):
                if direct:
                    await stale.execute(
                        sa.text(
                            "INSERT INTO profiles(id, consumer_id, external_ref) "
                            "VALUES (:pid,:cid,'user-1')"
                        ),
                        {"pid": uuid.uuid4(), "cid": cid},
                    )
                else:
                    await profiles.upsert_profile(stale, cid, "user-1")
            await stale.rollback()

    asyncio.run(run())


def test_receipt_and_deletion_rollback_together(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    _, _, pid = _seed_profile(f, made)
    cid = _rows(f, "SELECT consumer_id FROM profiles WHERE id=:p", p=pid)[0][0]

    async def run():
        async with f() as s:
            await erase_owned_profile(s, cid, pid)
            await s.rollback()

    asyncio.run(run())
    assert _rows(f, "SELECT id FROM profiles WHERE id=:p", p=pid)
    assert not _rows(
        f, "SELECT profile_id FROM profile_erasure_receipts WHERE profile_id=:p", p=pid
    )


def test_erasure_serializes_with_inflight_enrollment(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    _, _, pid = _seed_profile(f, made)
    cid = _rows(f, "SELECT consumer_id FROM profiles WHERE id=:p", p=pid)[0][0]

    async def run():
        async with f() as writer, f() as eraser:
            await profiles.upsert_profile(writer, cid, "user-1")
            await eraser.execute(sa.text("SET LOCAL lock_timeout='150ms'"))
            with pytest.raises(sa.exc.DBAPIError, match="lock"):
                await erase_owned_profile(eraser, cid, pid)
            await eraser.rollback()
            await writer.commit()
            assert await erase_owned_profile(eraser, cid, pid)
            await eraser.commit()

    asyncio.run(run())


def test_private_erasure_inventory_is_exclusive_and_private(tmp_path):
    from jobhunt_core.erasure_restore import private_read, private_write

    tmp_path.chmod(0o700)
    artifact = tmp_path / "erasures.json"
    body = {"version": 1, "items": []}
    private_write(artifact, body)
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert private_read(artifact) == body
    with pytest.raises(FileExistsError):
        private_write(artifact, body)
    artifact.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        private_read(artifact)

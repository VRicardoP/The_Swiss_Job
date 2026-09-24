"""Live erasure: owned, transactional, retryable, and irreversible to stale CDC."""
import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core import profiles
from jobhunt_core.erasure import ProfileErasedError, erase_owned_profile
from jobhunt_core.tests import test_integration_api as tia
from jobhunt_core.tests.test_integration_api_saved_searches import db, _rows, _seed_profile, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_portfolio_erasure_scrubs_material_manifest_without_other_user_loss(db):  # noqa: F811  (la fixture, no una redefinición)
    from jobhunt_core.import_portfolio_manifest import persist_manifest
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
                "staged": [{"external_ref": "synthetic-erased", "durable": {"notes": "private-note"}},
                           {"external_ref": "synthetic-kept", "durable": {"notes": "other-note"}}],
                "divergences": [gone + kept],
                "events": {"expected": {gone: 1, kept: 1}, "actual": {}},
                "provenance": {"applications": [str(uuid.uuid4())]},
            }
            mid = await persist_manifest(session, manifest)
            await erase_owned_profile(session, cid, pid)
            row = (await session.execute(sa.text(
                "SELECT manifest,status,verdict FROM portfolio_migration_manifest WHERE id=:id"
            ), {"id": mid})).one()
            import json
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
    assert tia._api(f, f"/v1/profiles/{pid}", token=other, method="DELETE").status_code == 404
    assert _rows(f, "SELECT id FROM profiles WHERE id=:p", p=pid)
    a = tia._api(f, f"/v1/profiles/{pid}", token=token, method="DELETE")
    b = tia._api(f, f"/v1/profiles/{pid}", token=token, method="DELETE")
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json() == b.json()
    assert a.json()["backup_erasure"] == "not_confirmed"
    assert not _rows(f, "SELECT id FROM profiles WHERE id=:p", p=pid)
    assert _rows(f, "SELECT id FROM profiles WHERE id=:p", p=other_pid)
    assert tia._api(f, f"/v1/profiles/{pid}", token=other, method="DELETE").status_code == 404


def test_erasure_fences_enrollment_direct_insert_and_cdc(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    _, _, pid = _seed_profile(f, made)
    cid = _rows(f, "SELECT consumer_id FROM profiles WHERE id=:p", p=pid)[0][0]

    async def run():
        async with f() as s:
            await erase_owned_profile(s, cid, pid)
            await s.commit()
        async with f() as s:
            with pytest.raises(ProfileErasedError):
                await profiles.upsert_profile(s, cid, "user-1")
            # The Python guard doesn't abort the transaction or CDC batch.
            from jobhunt_core.shadow.projector import _upsert_profile_pks
            from types import SimpleNamespace
            assert await _upsert_profile_pks(s, cid, {
                "old-pk": SimpleNamespace(fields={"user_id": "user-1"}),
            }) == {}
            await s.rollback()
        async with f() as s:
            with pytest.raises(sa.exc.IntegrityError, match="profile has been erased"):
                await s.execute(sa.text(
                    "INSERT INTO profiles(id, consumer_id, external_ref) VALUES (:p,:c,'user-1')"
                ), {"p": uuid.uuid4(), "c": cid})
    asyncio.run(run())


def test_cdc_buffer_scrub_and_future_capture(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db

    async def run():
        async with f() as s:
            cid = await profiles.ensure_consumer(s, "swissjob-shadow")
            ref = str(uuid.uuid4())
            pid = await profiles.upsert_profile(s, cid, ref)
            pk = str(uuid.uuid4())
            base = 900000000 + uuid.uuid4().int % 100000000
            sql = sa.text("INSERT INTO shadow_change_log "
                "(lsn,seq_in_tx,src_table,op,pk,payload) VALUES "
                "(:l,0,'user_profiles','I',:pk,CAST(:body AS jsonb))")
            import json
            payload = json.dumps({"user_id": ref, "cv_text": "private synthetic CV"})
            await s.execute(sql, {"l": base, "pk": pk, "body": payload})
            await erase_owned_profile(s, cid, pid)
            # A later full capture and an omitted-user_id update are scrubbed.
            await s.execute(sql, {"l": base + 1, "pk": pk, "body": payload})
            await s.execute(sql, {"l": base + 2, "pk": pk,
                                 "body": '{"cv_text":"late synthetic CV"}'})
            rows = (await s.execute(sa.text(
                "SELECT payload FROM shadow_change_log WHERE pk=:pk"
            ), {"pk": pk})).scalars().all()
            assert rows == [{}, {}, {}]
            await s.rollback()  # synthetic CDC fixture, no real user affected
    asyncio.run(run())


def test_erasure_inventory_owned_and_paginated(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed_profile(f, made, scopes=["profiles:write"])
    other, _, _ = _seed_profile(f, made, scopes=["profiles:write"])
    assert tia._api(f, f"/v1/profiles/{pid}", token=token, method="DELETE").status_code == 200
    response = tia._api(f, "/v1/profile-erasures", token=token)
    assert response.status_code == 200, response.text
    assert [r["profile_id"] for r in response.json()["items"]] == [str(pid)]
    assert tia._api(f, "/v1/profile-erasures", token=other).json()["items"] == []
    assert tia._api(f, "/v1/profile-erasures?after=" + str(pid), token=token).json()["items"] == []


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


def test_unlinked_user_is_fenced_before_first_projection(db):  # noqa: F811  (la fixture, no una redefinición)
    from jobhunt_core.erasure import erase_external_identity
    f, made = db
    async def run():
        async with f() as s:
            cid = await profiles.ensure_consumer(s, "swissjob-shadow")
            ref = str(uuid.uuid4())
            receipt = await erase_external_identity(s, cid, ref)
            assert await erase_external_identity(s, cid, ref) == receipt
            with pytest.raises(ProfileErasedError):
                await profiles.upsert_profile(s, cid, ref)
            from jobhunt_core.shadow.projector import erase_shadow_profile
            late_ref = str(uuid.uuid4())
            assert await erase_shadow_profile(s, late_ref) is None
            with pytest.raises(ProfileErasedError):
                await profiles.upsert_profile(s, cid, late_ref)
            await s.rollback()
    asyncio.run(run())


@pytest.mark.parametrize("direct", [False, True])
def test_repeatable_read_cannot_recreate_erased_identity(db, direct):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    _, _, pid = _seed_profile(f, made)
    cid = _rows(f, "SELECT consumer_id FROM profiles WHERE id=:p", p=pid)[0][0]
    async def run():
        async with f() as stale, f() as eraser:
            await stale.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            await stale.execute(sa.text("SELECT count(*) FROM profile_erasure_receipts"))
            await erase_owned_profile(eraser, cid, pid)
            await eraser.commit()
            with pytest.raises(sa.exc.DBAPIError, match="serialize"):
                if direct:
                    await stale.execute(sa.text(
                        "INSERT INTO profiles(id, consumer_id, external_ref) "
                        "VALUES (:pid,:cid,'user-1')"
                    ), {"pid": uuid.uuid4(), "cid": cid})
                else:
                    await profiles.upsert_profile(stale, cid, "user-1")
            await stale.rollback()
    asyncio.run(run())


def test_repeatable_read_capture_cannot_restore_erased_payload(db):  # noqa: F811  (la fixture, no una redefinición)
    from jobhunt_core.erasure import erase_external_identity
    import json
    f, made = db

    async def run():
        ref = str(uuid.uuid4())
        async with f() as seed:
            cid = await profiles.ensure_consumer(seed, "swissjob-shadow")
            await seed.commit()
        async with f() as stale, f() as eraser:
            await stale.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            await stale.execute(sa.text("SELECT count(*) FROM profile_erasure_receipts"))
            await erase_external_identity(eraser, cid, ref)
            await eraser.commit()
            with pytest.raises(sa.exc.DBAPIError, match="serialize"):
                await stale.execute(sa.text(
                    "INSERT INTO shadow_change_log(lsn,seq_in_tx,src_table,op,pk,payload) "
                    "VALUES (:lsn,0,'user_profiles','I',:pk,CAST(:payload AS jsonb))"
                ), {"lsn": 900000000 + uuid.uuid4().int % 100000000,
                    "pk": str(uuid.uuid4()),
                    "payload": json.dumps({"user_id": ref, "cv_text": "synthetic secret"})})
            await stale.rollback()
        async with f() as cleanup:
            await cleanup.execute(sa.text(
                "DELETE FROM profile_erasure_receipts WHERE consumer_id=:cid AND external_ref=:ref"
            ), {"cid": cid, "ref": ref})
            await cleanup.commit()
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
    assert not _rows(f, "SELECT profile_id FROM profile_erasure_receipts WHERE profile_id=:p", p=pid)


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

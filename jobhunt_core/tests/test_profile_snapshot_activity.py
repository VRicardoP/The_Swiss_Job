"""Shadow metrics must use live account activity after profile handover."""

import asyncio
import json
import uuid

import sqlalchemy as sa

from jobhunt_core import profiles
from jobhunt_core.shadow import projector
from jobhunt_core.tests.test_integration_api import db, _issue
from jobhunt_core.tests.test_profile_snapshot_delivery import _put, _snapshot


def test_live_activity_overrides_opposite_applied_cdc_state(db):
    factory, created = db
    cid, _, token = _issue(
        factory, created, projector.SHADOW_CONSUMER, ["profiles:write"]
    )
    uid = str(uuid.uuid4())
    lsn = uuid.uuid4().int % 2**60

    async def setup():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, uid)
            await s.execute(
                sa.text(
                    "INSERT INTO shadow_change_log(lsn,seq_in_tx,src_table,op,pk,payload,applied_at) "
                    "VALUES(:l,0,'users','U',:u,CAST(:j AS jsonb),clock_timestamp())"
                ),
                {"l": lsn, "u": uid, "j": json.dumps({"is_active": False})},
            )
            await s.commit()
            assert uid in await projector.inactive_user_refs(s, [uid])
            return pid

    async def check(expected):
        async with factory() as s:
            assert (uid in await projector.inactive_user_refs(s, [uid])) is expected

    async def cleanup():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "DELETE FROM shadow_change_log WHERE pk=:u AND src_table='users'"
                ),
                {"u": uid},
            )
            await s.commit()

    try:
        pid = asyncio.run(setup())
        assert _put(factory, pid, token, _snapshot(active=True)).status_code == 200
        asyncio.run(check(False))
        assert _put(factory, pid, token, _snapshot(2, active=False)).status_code == 200
        asyncio.run(check(True))
        assert _put(factory, pid, token, _snapshot(3, active=True)).status_code == 200
        asyncio.run(check(False))
    finally:
        asyncio.run(cleanup())

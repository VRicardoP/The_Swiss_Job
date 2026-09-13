"""Private erasure inventory for an OFFLINE restore, never a backup-erased claim.

Snapshot the current receipt inventory before restoring an older database.
Reconcile the restored database, then BFF replicas, BEFORE opening networking.
Only minimal identities are exported, not CVs or credentials. Files are private
and exclusive-create using the established cutover artifact helpers.
"""
import argparse
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from jobhunt_core.database import task_session_factory
from jobhunt_core.document_cutover import private_read, private_write
from jobhunt_core.erasure import erase_owned_profile, lock_identity


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


async def snapshot(session):
    rows = (await session.execute(sa.text(
        "SELECT r.profile_id, c.name AS consumer, r.external_ref, "
        "r.source_profile_pks, r.erased_at FROM profile_erasure_receipts r "
        "JOIN consumers c ON c.id=r.consumer_id ORDER BY c.name,r.external_ref"
    ))).mappings().all()
    items = [{**r, "profile_id": str(r["profile_id"]),
              "erased_at": r["erased_at"].isoformat()} for r in rows]
    body = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "items": items}
    return {**body, "sha256": digest(body)}


async def reconcile(session, bundle):
    body = {k: v for k, v in bundle.items() if k != "sha256"}
    if body.get("version") != 1 or digest(body) != bundle.get("sha256"):
        raise ValueError("invalid erasure inventory seal")
    if not isinstance(body.get("items"), list):
        raise ValueError("invalid erasure inventory")
    # Validate the entire artifact before the first mutation.
    seen = set()
    for row in body["items"]:
        uuid.UUID(row["profile_id"])
        date = datetime.fromisoformat(row["erased_at"])
        if date.tzinfo is None or not isinstance(row["source_profile_pks"], list):
            raise ValueError("invalid erasure receipt")
        if not all(isinstance(p, str) for p in row["source_profile_pks"]):
            raise ValueError("invalid capture identities")
        key = (row["consumer"], row["external_ref"])
        if key in seen or not all(isinstance(v, str) and v for v in key):
            raise ValueError("invalid or repeated erasure identity")
        seen.add(key)
    for row in body["items"]:
        cid = await session.scalar(sa.text("SELECT id FROM consumers WHERE name=:name"),
                                   {"name": row["consumer"]})
        if cid is None:
            raise ValueError("receipt consumer absent from restored database")
        pid = uuid.UUID(row["profile_id"])
        params = {"cid": cid, "ref": row["external_ref"], "pid": pid}
        await lock_identity(session, cid, row["external_ref"])
        restored = await session.scalar(sa.text(
            "SELECT id FROM profiles WHERE consumer_id=:cid AND external_ref=:ref"
        ), params)
        if restored is not None and restored != pid:
            raise ValueError("restored profile identity differs from erasure receipt")
        if restored is not None:
            await erase_owned_profile(session, cid, pid)
        await session.execute(sa.text(
            "INSERT INTO profile_erasure_receipts "
            "(profile_id,consumer_id,external_ref,source_profile_pks,erased_at) "
            "VALUES(:pid,:cid,:ref,:pks,:at) ON CONFLICT DO NOTHING"
        ), {**params, "pks": row["source_profile_pks"],
            "at": datetime.fromisoformat(row["erased_at"])})
        receipt = (await session.execute(sa.text(
            "SELECT consumer_id, external_ref FROM profile_erasure_receipts WHERE profile_id=:pid"
        ), {"pid": pid})).one()
        if receipt.consumer_id != cid or receipt.external_ref != row["external_ref"]:
            raise ValueError("persisted erasure receipt identity mismatch")
        await session.execute(sa.text(
            "UPDATE profile_erasure_receipts SET source_profile_pks=ARRAY("
            "SELECT DISTINCT unnest(source_profile_pks || CAST(:pks AS text[]))) "
            "WHERE profile_id=:pid"
        ), {"pid": pid, "pks": row["source_profile_pks"]})
        if row["consumer"] == "swissjob-shadow":
            await session.execute(sa.text(
                "UPDATE shadow_change_log SET payload='{}'::jsonb "
                "WHERE src_table='user_profiles' AND "
                "(payload->>'user_id'=:ref OR pk=ANY(:pks))"
            ), {"ref": row["external_ref"], "pks": row["source_profile_pks"]})
        if await session.scalar(sa.text("SELECT 1 FROM profiles WHERE id=:pid"), {"pid": pid}):
            raise ValueError("restored profile remains present")
    return len(body["items"])


async def run(args):
    async with task_session_factory() as factory:
        async with factory() as session:
            if args.command == "snapshot":
                bundle = await snapshot(session)
                private_write(args.inventory, bundle)
                return {"receipts": len(bundle["items"]), "sha256": bundle["sha256"]}
            count = await reconcile(session, private_read(args.inventory))
            if args.apply:
                await session.commit()
            else:
                await session.rollback()
            return {"receipts": count, "applied": args.apply}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["snapshot", "reconcile"])
    parser.add_argument("inventory")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args))))
    except Exception as exc:
        # Connection errors may include DSNs. Operational logs expose only type.
        raise SystemExit(f"erasure reconciliation failed: {type(exc).__name__}") from None

"""At-least-once delivery AFTER local deletion commits, independent of harvest.

Only an explicit owned core receipt confirms core live deletion. A 404, timeout,
malformed body or lost response stays pending. No backup/global completion claim.
"""
import asyncio
import logging
import uuid
from datetime import datetime

from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert

from database import async_session
from models.profile_erasure import ProfileErasure
from services.profiles.core_client import default_client_factory

logger = logging.getLogger(__name__)


def clear_erased_caches(profile_id):
    if profile_id is not None:
        from services.profiles.core_client import clear_profile_cache
        from services.matching.core_client import clear_feed_cache
        clear_profile_cache(profile_id)
        clear_feed_cache(profile_id)


async def queue_erasure(db, user_id, core_profile_id):
    """Same transaction and user root lock as account deletion."""
    await db.execute(insert(ProfileErasure).values(
        user_id=user_id, core_profile_id=core_profile_id,
    ).on_conflict_do_nothing(index_elements=[ProfileErasure.user_id]))


async def deliver_erasure(db, user_id, client_factory=None):
    row = (await db.execute(select(ProfileErasure).where(
        ProfileErasure.user_id == user_id,
    ))).scalar_one_or_none()
    if row is None or row.core_confirmed_at:
        await db.commit()
        return
    pid = row.core_profile_id
    await db.execute(update(ProfileErasure).where(
        ProfileErasure.user_id == user_id,
    ).values(last_attempt_at=func.now()))
    await db.commit()  # Never retain locks during HTTP.
    try:
        async with (client_factory or default_client_factory)() as client:
            response = await client.delete(f"/profiles/{pid}" if pid is not None
                                           else f"/profile-erasures/by-user/{user_id}")
        if response.status_code != 200:
            raise ValueError("core_unconfirmed")
        receipt = response.json()
        if (not isinstance(receipt, dict) or receipt.get("status") != "erased"
                or receipt.get("scope") != "core_live_database"
                or (pid is not None and uuid.UUID(receipt.get("profile_id", "")) != pid)):
            raise ValueError("invalid_receipt")
        confirmed_pid = uuid.UUID(receipt["profile_id"])
        erased_at = datetime.fromisoformat(receipt["erased_at"].replace("Z", "+00:00"))
        if erased_at.tzinfo is None:
            raise ValueError("invalid_receipt")
        await db.execute(update(ProfileErasure).where(
            ProfileErasure.user_id == user_id,
            ProfileErasure.core_confirmed_at.is_(None),
        ).values(core_profile_id=confirmed_pid, core_confirmed_at=erased_at, last_error=None))
        await db.commit()
    except Exception as exc:
        await db.rollback()
        # No exception messages, URLs, tokens or response bodies in logs/storage.
        await db.execute(update(ProfileErasure).where(
            ProfileErasure.user_id == user_id,
            ProfileErasure.core_confirmed_at.is_(None),
        ).values(last_error=type(exc).__name__))
        await db.commit()
        logger.warning("profile erasure delivery remains pending (%s)", type(exc).__name__)


async def drain_erasures(session_factory=async_session):
    async with session_factory() as db:
        ids = (await db.execute(select(ProfileErasure.user_id).where(
            # Unlinked accounts also require a fence against their first CDC event.
            ProfileErasure.core_confirmed_at.is_(None),
        ).order_by(ProfileErasure.last_attempt_at.asc().nulls_first(),
                   ProfileErasure.user_id).limit(20))).scalars().all()
    for uid in ids:
        try:
            async with session_factory() as db:
                await deliver_erasure(db, uid)
        except Exception as exc:
            logger.warning("profile erasure retry deferred (%s)", type(exc).__name__)
    return len(ids)



async def reconcile_replica(session_factory=async_session, client_factory=None):
    """Purge this BFF's own copy using authenticated consumer-owned receipts.

    Re-scan receipts rather than trusting a checkpoint restored from an older
    backup. This includes already confirmed requests: an old user row must
    never survive merely because its previous receipt says 'done'.
    """
    cursor = None
    total = 0
    async with (client_factory or default_client_factory)() as client:
        while True:
            response = await client.get("/profile-erasures",
                                        params={"after": cursor} if cursor else {})
            if response.status_code != 200:
                raise ValueError("unconfirmed_erasure_inventory")
            body = response.json()
            if (not isinstance(body, dict) or body.get("consumer") != "swissjob-shadow"
                    or not isinstance(body.get("items"), list)):
                raise ValueError("invalid_erasure_inventory")
            if len(body["items"]) > 100:
                raise ValueError("invalid_erasure_inventory")
            previous = uuid.UUID(cursor) if cursor else None
            for receipt in body["items"]:
                pid = uuid.UUID(receipt["profile_id"])
                uid = uuid.UUID(receipt["external_ref"])
                if previous is not None and pid <= previous:
                    raise ValueError("invalid_erasure_order")
                previous = pid
                erased_at = datetime.fromisoformat(receipt["erased_at"].replace("Z", "+00:00"))
                if erased_at.tzinfo is None:
                    raise ValueError("invalid_erasure_receipt")
                async with session_factory() as db:
                    from models.user import User
                    from models.integration_inbox import IntegrationInbox
                    from models.jobhunt_profile_map import JobhuntProfileMap
                    from sqlalchemy import delete
                    user = (await db.execute(select(User).where(
                        User.id == uid).with_for_update())).scalar_one_or_none()
                    bound = await db.scalar(select(JobhuntProfileMap.core_profile_id).where(
                        JobhuntProfileMap.user_id == uid))
                    if bound is not None and bound != pid:
                        raise ValueError("erasure_identity_conflict")
                    await queue_erasure(db, uid, pid)
                    await db.execute(delete(IntegrationInbox).where(
                        IntegrationInbox.subject_profile_id == pid))
                    if user is not None:
                        await db.delete(user)
                        total += 1
                    await db.execute(update(ProfileErasure).where(
                        ProfileErasure.user_id == uid,
                    ).values(core_confirmed_at=erased_at, last_error=None))
                    await db.commit()
                clear_erased_caches(pid)
                from config import settings
                if settings.CORE_ERASURE_REPLICA_ID:
                    ack = await client.post(
                        f"/profile-erasures/{pid}/acks/{settings.CORE_ERASURE_REPLICA_ID}")
                    if ack.status_code != 204:
                        raise ValueError("erasure_ack_unconfirmed")
            next_cursor = body.get("next_cursor")
            if next_cursor is None:
                break
            if not body["items"] or str(previous) != next_cursor:
                raise ValueError("invalid_erasure_cursor")
            cursor = next_cursor
    return total


async def run_erasure_delivery():
    while True:
        try:
            from config import settings
            if settings.CORE_CONSUMER_KEY:
                await drain_erasures()
                if settings.CORE_ERASURE_REPLICA_ID:
                    await reconcile_replica()
        except Exception as exc:
            logger.warning("profile erasure drain deferred (%s)", type(exc).__name__)
        await asyncio.sleep(30)


if __name__ == "__main__":
    # Offline-restore barrier: do not start ingress/workers until this succeeds.
    # Credentials remain in the existing environment, never command arguments.
    import argparse
    import json
    from config import settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true",
                        help="Run only erasure reconciliation, without app/harvest/exclusion writers")
    args = parser.parse_args()
    if not settings.CORE_ERASURE_REPLICA_ID or not settings.CORE_CONSUMER_KEY:
        raise SystemExit("replica identity and core credential are required")
    try:
        if args.watch:
            asyncio.run(run_erasure_delivery())
        else:
            print(json.dumps({"purged_accounts": asyncio.run(reconcile_replica())}))
    except Exception as exc:
        raise SystemExit(f"erasure reconciliation failed: {type(exc).__name__}") from None

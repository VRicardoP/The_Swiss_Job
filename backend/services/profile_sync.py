"""Deliver committed full profile snapshots; never perform HTTP in a mutation tx.

Triggers capture every retained writer (CRUD, CV tasks and activity changes).
This worker is independent of harvest and provisions a missing Core identity idempotently.
It coalesces backlog to the latest snapshot; core fences delayed older requests.
"""

import asyncio
import logging
import uuid

from sqlalchemy import func, select, update

from config import settings
from database import async_session
from models.profile_sync_state import ProfileSyncState
from models.user import User
from services.matching.identity import resolve_core_profile_id, set_profile_link
from services.profiles.core_client import default_client_factory

logger = logging.getLogger(__name__)


async def lock_profile_writer(db, user_id):
    """Lock the account root before a profile row to match erasure order."""
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())


async def profile_sync_status(db, user_id):
    row = (
        await db.execute(
            select(
                ProfileSyncState.version,
                ProfileSyncState.delivered_version,
                ProfileSyncState.last_error,
                ProfileSyncState.last_attempt_at,
            ).where(ProfileSyncState.user_id == user_id)
        )
    ).one_or_none()
    return {
        "enabled": settings.CORE_PROFILE_SYNC_ENABLED,
        "pending": row is not None and row.version > row.delivered_version,
        "version": row.version if row else 0,
        "delivered_version": row.delivered_version if row else 0,
        "last_error": row.last_error if row else None,
        "last_attempt_at": row.last_attempt_at if row else None,
    }


async def _ensure_core_profile_id(db, user_id, client_factory):
    profile_id = await resolve_core_profile_id(db, user_id)
    if profile_id is not None:
        return profile_id

    external_ref = str(user_id)
    async with client_factory() as client:
        response = await client.post(
            "/profiles",
            json={"external_ref": external_ref},
            headers={"Idempotency-Key": f"profile-enrollment:{external_ref}"},
        )
    if response.status_code != 200:
        raise ValueError(f"core_http_{response.status_code}")
    payload = response.json()
    try:
        profile_id = uuid.UUID(payload["profile_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_enrollment") from exc
    if payload.get("external_ref") != external_ref:
        raise ValueError("invalid_enrollment")
    await set_profile_link(
        db, user_id, profile_id, updated_by="profile_snapshot_delivery"
    )
    return profile_id


async def sync_profile_snapshot(db, user_id, client_factory=None):
    if not settings.CORE_PROFILE_SYNC_ENABLED:
        return {"status": "disabled"}
    sent_version = None
    try:
        row = (
            await db.execute(
                select(
                    ProfileSyncState.version,
                    ProfileSyncState.delivered_version,
                    ProfileSyncState.content,
                    ProfileSyncState.active,
                ).where(ProfileSyncState.user_id == user_id)
            )
        ).one_or_none()
        if row is None or row.content is None or row.version == row.delivered_version:
            await db.commit()
            return {"status": "idle"}
        sent_version = row.version
        body = {"version": sent_version, "active": row.active, "content": row.content}
        factory = client_factory or default_client_factory
        if factory is default_client_factory and not settings.CORE_CONSUMER_KEY:
            raise ValueError("not_configured")
        pid = await _ensure_core_profile_id(db, user_id, factory)
        await db.execute(
            update(ProfileSyncState)
            .where(
                ProfileSyncState.user_id == user_id,
            )
            .values(last_attempt_at=func.clock_timestamp())
        )
        await db.commit()
        async with factory() as client:
            response = await client.put(f"/profiles/{pid}/source-snapshot", json=body)
        if response.status_code != 200:
            raise ValueError(f"core_http_{response.status_code}")
        payload = response.json()
        ack = payload.get("version") if isinstance(payload, dict) else None
        if type(ack) is not int or ack < sent_version:
            raise ValueError("invalid_ack")
        if ack > sent_version:
            raise ValueError("core_version_ahead")
        await db.execute(
            update(ProfileSyncState)
            .where(
                ProfileSyncState.user_id == user_id,
                ProfileSyncState.version == sent_version,
            )
            .values(delivered_version=sent_version, last_error=None)
        )
        await db.commit()
        return {"status": "ok", "version": sent_version}
    except Exception as exc:
        # Never log/persist exception bodies, CVs, HTTP credentials or URLs.
        safe_errors = {
            "not_configured",
            "invalid_enrollment",
            "invalid_ack",
            "core_version_ahead",
        }
        error = (
            str(exc)
            if type(exc) is ValueError and str(exc) in safe_errors
            else type(exc).__name__
        )
        if (
            type(exc) is ValueError
            and str(exc).startswith("core_http_")
            and str(exc)[10:].isdigit()
        ):
            error = str(exc)
        try:
            await db.rollback()
            if sent_version is not None:
                await db.execute(
                    update(ProfileSyncState)
                    .where(
                        ProfileSyncState.user_id == user_id,
                        ProfileSyncState.version == sent_version,
                        ProfileSyncState.delivered_version < sent_version,
                    )
                    .values(last_error=error, last_attempt_at=func.clock_timestamp())
                )
                await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass
            logger.warning(
                "profile_sync: diagnostics unavailable; snapshot remains pending"
            )
        logger.warning("profile_sync: pending delivery (%s)", error)
        return {"status": "pending", "error": error}


async def drain_profile_snapshots(session_factory=async_session):
    if not settings.CORE_PROFILE_SYNC_ENABLED:
        return 0
    async with session_factory() as db:
        ids = (
            (
                await db.execute(
                    select(ProfileSyncState.user_id)
                    .where(
                        ProfileSyncState.content.is_not(None),
                        ProfileSyncState.delivered_version < ProfileSyncState.version,
                    )
                    .order_by(
                        ProfileSyncState.last_attempt_at.asc().nulls_first(),
                        ProfileSyncState.user_id,
                    )
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
    for uid in ids:
        async with session_factory() as db:
            await sync_profile_snapshot(db, uid)
    return len(ids)


async def run_profile_delivery():
    while True:
        try:
            await drain_profile_snapshots()
        except Exception as exc:
            logger.warning(
                "profile_sync: drain failed (%s); retry in 30s", type(exc).__name__
            )
        await asyncio.sleep(30)

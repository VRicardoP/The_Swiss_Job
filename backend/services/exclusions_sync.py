"""Versioned, durable projection of BFF exclusion rules to the core.

The mutation and its latest snapshot commit together. HTTP delivery occurs
afterwards, outside the transaction. A startup/periodic drain retries after
network failures and process restarts, independently of harvest schedulers.
Multiple BFF processes may deliver the same snapshot: the core fences versions.
"""

import asyncio
import logging
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models.exclusion_sync_state import ExclusionSyncState
from models.job_filter import JobFilter
from models.user import User
from services.matching.identity import resolve_core_profile_id
from services.profiles.core_client import default_client_factory
from services.routing import CAPABILITY_MATCHING, legacy_owns, resolve_mode

logger = logging.getLogger(__name__)


async def lock_filter_writer(db: AsyncSession, user_id: uuid.UUID) -> None:
    """All filter edits acquire user before reading/mutating filters."""
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())


async def queue_exclusions(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Called BEFORE the mutation commit, with the user lock held."""
    await db.flush()
    rows = (
        await db.execute(
            select(JobFilter.filter_type, JobFilter.pattern)
            .where(
                JobFilter.user_id == user_id,
                JobFilter.is_active.is_(True),
            )
            .order_by(JobFilter.filter_type, JobFilter.pattern)
        )
    ).all()
    payload = [{"kind": r.filter_type, "pattern": r.pattern} for r in rows]
    stmt = insert(ExclusionSyncState).values(
        user_id=user_id, version=1, exclusions=payload
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[ExclusionSyncState.user_id],
            set_={
                "version": ExclusionSyncState.version + 1,
                "exclusions": stmt.excluded.exclusions,
                "last_error": None,
                "updated_at": func.now(),
            },
        )
    )


async def exclusion_sync_status(db, user_id):
    row = (
        await db.execute(
            select(
                ExclusionSyncState.version,
                ExclusionSyncState.delivered_version,
                ExclusionSyncState.last_error,
            ).where(ExclusionSyncState.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        return {"pending": False, "version": 0, "delivered_version": 0}
    return {
        "pending": row.delivered_version < row.version,
        "version": row.version,
        "delivered_version": row.delivered_version,
        "last_error": row.last_error,
    }


async def sync_exclusions_to_core(
    db: AsyncSession, user_id: uuid.UUID, client_factory=None
) -> dict:
    """Deliver a COMMITTED snapshot; errors retain the durable pending row."""
    sent_version = None
    try:
        row = (
            await db.execute(
                select(
                    ExclusionSyncState.version,
                    ExclusionSyncState.exclusions,
                    ExclusionSyncState.delivered_version,
                ).where(ExclusionSyncState.user_id == user_id)
            )
        ).one_or_none()
        if row is None or row.delivered_version == row.version:
            await db.commit()
            return {"status": "idle"}
        sent_version = row.version
        payload = {"version": sent_version, "exclusions": row.exclusions}
        mode = await resolve_mode(db, CAPABILITY_MATCHING, user_id)
        pid = (
            await resolve_core_profile_id(db, user_id)
            if not legacy_owns(mode)
            else None
        )
        await db.execute(
            update(ExclusionSyncState)
            .where(
                ExclusionSyncState.user_id == user_id,
            )
            .values(last_attempt_at=func.now())
        )
        await db.commit()  # no database lock/transaction while waiting for HTTP
        if legacy_owns(mode):
            return {"status": "local", "mode": mode}
        if pid is None:
            raise ValueError("sin_vinculo")
        async with (client_factory or default_client_factory)() as client:
            response = await client.put(f"/profiles/{pid}/exclusions", json=payload)
        if response.status_code != 200:
            raise ValueError(f"core_http_{response.status_code}")
        ack = response.json().get("version")
        if type(ack) is not int or ack < sent_version:
            raise ValueError("invalid_ack")
        if ack > sent_version:
            # A newer local snapshot is untouched by the conditional error write.
            # After a DB restore, expose the mismatch instead of retrying silently.
            raise ValueError("core_version_ahead")
        await db.execute(
            update(ExclusionSyncState)
            .where(
                ExclusionSyncState.user_id == user_id,
                ExclusionSyncState.version == sent_version,
            )
            .values(delivered_version=sent_version, last_error=None)
        )
        await db.commit()
        return {
            "status": "ok",
            "version": sent_version,
            "declaradas": len(payload["exclusions"]),
        }
    except Exception as exc:
        # Do not persist/log exception text (HTTP failures can contain credentials).
        error = (
            str(exc)
            if isinstance(exc, ValueError)
            and str(exc)
            in {
                "sin_vinculo",
                "invalid_ack",
                "core_version_ahead",
            }
            else type(exc).__name__
        )
        try:
            await db.rollback()
            if sent_version is not None:
                await db.execute(
                    update(ExclusionSyncState)
                    .where(
                        ExclusionSyncState.user_id == user_id,
                        ExclusionSyncState.version == sent_version,
                        ExclusionSyncState.delivered_version < sent_version,
                    )
                    .values(last_error=error, last_attempt_at=func.now())
                )
                await db.commit()
        except Exception:
            # The original mutation and pending row are already committed.
            # An unavailable diagnostics DB must not turn that success into HTTP 500.
            try:
                await db.rollback()
            except Exception:
                pass
            logger.warning("exclusions_sync: could not persist delivery diagnostics")
        logger.warning("exclusions_sync: pending delivery (%s)", error)
        return {"status": "pending", "error": error}


async def drain_pending_exclusions(session_factory=async_session) -> int:
    async with session_factory() as db:
        ids = (
            (
                await db.execute(
                    select(ExclusionSyncState.user_id)
                    .where(
                        ExclusionSyncState.delivered_version
                        < ExclusionSyncState.version,
                    )
                    .order_by(
                        ExclusionSyncState.last_attempt_at.asc().nulls_first(),
                        ExclusionSyncState.user_id,
                    )
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
    for uid in ids:
        async with session_factory() as db:
            await sync_exclusions_to_core(db, uid)
    return len(ids)


async def run_exclusion_delivery() -> None:
    """Runs even when the harvest scheduler is quiesced; no external fetch."""
    while True:
        try:
            await drain_pending_exclusions()
        except Exception:
            logger.exception("exclusions_sync: drain failed; retry in 30s")
        await asyncio.sleep(30)

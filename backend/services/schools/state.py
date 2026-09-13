"""Fresh authority for school state; read canaries never create a second writer."""

from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from config import settings
from models.jobhunt_routing import JobhuntRouting, CONSUMER_SWISSJOB, PROFILE_WILDCARD
from .port import CoreUnavailableError


async def block_school_writes(request: Request):
    if settings.SCHOOL_WRITES_FROZEN and request.method not in (
        "GET",
        "HEAD",
        "OPTIONS",
    ):
        raise CoreUnavailableError("school writes are frozen")


async def read_school_mode(db, user_id=None, *, write=False):
    if write and settings.SCHOOL_WRITES_FROZEN:
        raise CoreUnavailableError("school writes are frozen")
    pid = user_id or PROFILE_WILDCARD
    try:
        if write:
            # Absent wildcard is fenced too. No LLM runs in these write methods.
            await db.execute(text("LOCK TABLE jobhunt_routing IN SHARE MODE"))
        rows = (
            await db.execute(
                select(JobhuntRouting.profile_id, JobhuntRouting.mode).where(
                    JobhuntRouting.consumer_id == CONSUMER_SWISSJOB,
                    JobhuntRouting.capability == "schools",
                    JobhuntRouting.profile_id.in_([pid, PROFILE_WILDCARD]),
                )
            )
        ).all()
    except SQLAlchemyError:
        raise CoreUnavailableError("school routing unavailable") from None
    modes = dict(rows)
    mode = modes.get(pid, modes.get(PROFILE_WILDCARD, "local"))
    if mode not in ("local", "shadow", "core_read", "core_primary", "rollback_pending"):
        raise CoreUnavailableError("unknown school routing mode")
    return mode


async def state_on_core(db, user_id=None, *, write=False):
    return await read_school_mode(db, user_id, write=write) in (
        "core_primary",
        "rollback_pending",
    )


async def resolve_school_state(db, user_id, *, write=False):
    if await state_on_core(db, user_id, write=write):
        from .watchlist_state import CoreWatchlist

        return CoreWatchlist(db)
    from services.applications.local import LocalApplications

    return LocalApplications(db)

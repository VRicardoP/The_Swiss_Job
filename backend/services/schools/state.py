"""Fixed Core authority for school preferences and watchlist state."""

from fastapi import Request

from config import settings
from .port import CoreUnavailableError


async def block_school_writes(request: Request):
    if settings.SCHOOL_WRITES_FROZEN and request.method not in (
        "GET",
        "HEAD",
        "OPTIONS",
    ):
        raise CoreUnavailableError("school writes are frozen")


async def state_on_core(db, user_id=None, *, write=False):
    if write and settings.SCHOOL_WRITES_FROZEN:
        raise CoreUnavailableError("school writes are frozen")
    return True


async def resolve_school_state(db, user_id, *, write=False):
    await state_on_core(db, user_id, write=write)
    from .watchlist_state import CoreWatchlist

    return CoreWatchlist(db)

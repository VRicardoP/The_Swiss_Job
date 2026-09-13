"""Watchlist preference authority, independent of the CV/profile reader."""

from sqlalchemy import select

from models.user import User
from models.user_profile import UserProfile
from services.matching.identity import resolve_core_profile_id
from .http_client import SchoolClient
from .port import CoreUnavailableError
from .state import state_on_core


async def preference(db, user_id, local_value, *, enabled=None):
    if not await state_on_core(db, user_id, write=enabled is not None):
        return local_value if enabled is None else enabled
    pid = await resolve_core_profile_id(db, user_id)
    if pid is None:
        raise CoreUnavailableError("school preference owner is not enrolled")
    return await SchoolClient().preferences(pid, enabled)


async def enabled_users(db, *, extra_condition=None):
    # Usually two profiles; no local preference predicate may hide an enabled
    # core profile. An unavailable authority aborts instead of sending stale mail.
    query = (
        select(User, UserProfile.watchlist_schools_enabled)
        .join(UserProfile, UserProfile.user_id == User.id)
        .where(User.is_active.is_(True))
    )
    if extra_condition is not None:
        query = query.where(extra_condition)
    users = []
    for user, local_value in (await db.execute(query)).all():
        if await preference(db, user.id, local_value):
            users.append(user)
    return users

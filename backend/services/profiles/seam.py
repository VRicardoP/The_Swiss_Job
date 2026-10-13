"""Fixed local authority for the editable SwissJob profile."""

from sqlalchemy.ext.asyncio import AsyncSession

from .local import LocalProfile
from .port import ProfilePort


async def resolve_profiles(db: AsyncSession, user_id=None) -> ProfilePort:
    return LocalProfile(db)

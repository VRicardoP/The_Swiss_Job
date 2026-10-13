"""Fixed local authority for applications and the MatchResult state machine."""

from sqlalchemy.ext.asyncio import AsyncSession

from .local import LocalApplications
from .port import ApplicationsPort


async def resolve_applications(db: AsyncSession, user_id=None) -> ApplicationsPort:
    return LocalApplications(db)

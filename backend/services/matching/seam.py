"""Fixed Core authority for match feeds."""

from sqlalchemy.ext.asyncio import AsyncSession

from .core_client import CoreMatching
from .port import MatchingPort


async def resolve_matching(db: AsyncSession, user_id=None) -> MatchingPort:
    return CoreMatching(db)

"""Fixed Core authority for monitored schools."""

from sqlalchemy.ext.asyncio import AsyncSession

from .core_client import CoreSchools
from .port import SchoolsPort


async def resolve_schools(db: AsyncSession, user_id=None) -> SchoolsPort:
    return CoreSchools(enabled=True)

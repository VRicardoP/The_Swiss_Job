"""Fixed Core authority for catalogue reads."""

from sqlalchemy.ext.asyncio import AsyncSession

from .core_client import CoreCatalog
from .port import CatalogPort


async def resolve_catalog(db: AsyncSession, profile_id=None) -> CatalogPort:
    return CoreCatalog(db=db)

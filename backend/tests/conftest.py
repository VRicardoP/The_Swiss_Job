import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from config import settings
from core.rate_limit import limiter
from database import Base, get_db
from main import app

# Disable rate limiting in tests
limiter.enabled = False

# Use a separate test database to avoid wiping production data
_base_url = settings.DATABASE_URL.rsplit("/", 1)[0]
_test_db_url = _base_url + "/swissjobhunter_test"

test_engine = create_async_engine(_test_db_url, poolclass=NullPool)
TestSessionLocal = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest.fixture(scope="session", autouse=True)
async def _ensure_test_db():
    """Create test database + pgvector extension if they don't exist."""
    from sqlalchemy.ext.asyncio import create_async_engine as _cae

    admin_engine = _cae(_base_url + "/swissjobhunter", isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text(
                "SELECT 1 FROM pg_database WHERE datname = 'swissjobhunter_test'"
            )
        )
        if not exists:
            await conn.execute(text("CREATE DATABASE swissjobhunter_test"))
    await admin_engine.dispose()

    # Enable pgvector in the test DB
    vec_engine = _cae(_test_db_url, isolation_level="AUTOCOMMIT")
    async with vec_engine.connect() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await vec_engine.dispose()


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
async def setup_db():
    import models  # noqa: F401 — register metadata

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add tsvector column + trigger + GIN index (not created by create_all)
        await conn.execute(
            text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS search_vector tsvector")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_jobs_search_vector "
                "ON jobs USING GIN (search_vector)"
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS tsvector_update_jobs ON jobs"))
        await conn.execute(
            text(
                "CREATE TRIGGER tsvector_update_jobs "
                "BEFORE INSERT OR UPDATE OF title, description, company "
                "ON jobs FOR EACH ROW EXECUTE FUNCTION "
                "tsvector_update_trigger("
                "search_vector, 'pg_catalog.simple', "
                "title, description, company)"
            )
        )
    yield
    # Clean data but keep tables (don't drop_all which destroys the schema)
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f"TRUNCATE TABLE {table.name} CASCADE"))


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


def random_email() -> str:
    return f"test-{uuid.uuid4().hex[:8]}@example.com"

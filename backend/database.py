from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


@asynccontextmanager
async def task_session():
    """Create a fresh engine + session for Celery tasks.

    Each asyncio.run() call in a Celery worker creates a new event loop.
    The module-level engine is bound to the first loop and cannot be reused.
    This creates a disposable engine per task invocation.
    """
    task_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_size=settings.DB_TASK_POOL_SIZE,
        max_overflow=settings.DB_TASK_MAX_OVERFLOW,
    )
    factory = async_sessionmaker(
        task_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with factory() as session:
            yield session
    finally:
        await task_engine.dispose()

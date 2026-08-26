import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import models  # noqa: F401 — registra las 15 tablas en Base.metadata
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

# DDL que `create_all` no cubre: columna tsvector, su índice GIN y el trigger
# que la mantiene.
_EXTRA_DDL = (
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS search_vector tsvector",
    "CREATE INDEX IF NOT EXISTS ix_jobs_search_vector ON jobs USING GIN (search_vector)",
    "DROP TRIGGER IF EXISTS tsvector_update_jobs ON jobs",
    "CREATE TRIGGER tsvector_update_jobs "
    "BEFORE INSERT OR UPDATE OF title, description, company "
    "ON jobs FOR EACH ROW EXECUTE FUNCTION "
    "tsvector_update_trigger("
    "search_vector, 'pg_catalog.simple', title, description, company)",
)

# Orden inverso de dependencias — el mismo que usaba el TRUNCATE tabla a tabla.
_TABLE_NAMES = tuple(t.name for t in reversed(Base.metadata.sorted_tables))
_TRUNCATE_ALL = "TRUNCATE TABLE " + ", ".join(_TABLE_NAMES) + " CASCADE"

# Sonda de «qué tablas tienen filas». `EXISTS` es TRANSACCIONAL y EXACTO: ve
# todo lo commiteado en el instante en que corre, y corre en la MISMA
# transacción que el TRUNCATE que decide. Deliberadamente NO se usa
# `pg_stat_user_tables`: sus estadísticas van con retraso y una tabla sucia que
# llegara tarde contaminaría el test siguiente — la clase de ceguera que este
# proyecto ya ha pagado.
_DIRTY_PROBE = " UNION ALL ".join(
    f"SELECT '{name}' AS t WHERE EXISTS (SELECT 1 FROM {name})" for name in _TABLE_NAMES
)


@pytest.fixture(scope="session", autouse=True)
async def _ensure_test_db():
    """Crea la BD de test, la extensión pgvector y el ESQUEMA — una sola vez.

    El esquema que producen `create_all` + `_EXTRA_DDL` es IDÉNTICO en cada
    test (ambos son idempotentes), así que rehacerlo en los 2.267 no añade
    ninguna capacidad de refutación: si estuviera mal, estaría mal también la
    primera vez. Lo que sí se rehace por test es dejar la base vacía, que es lo
    único que aísla un test del siguiente (ver `setup_db`).
    """
    from sqlalchemy.ext.asyncio import create_async_engine as _cae

    admin_engine = _cae(_base_url + "/swissjobhunter", isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = 'swissjobhunter_test'")
        )
        if not exists:
            await conn.execute(text("CREATE DATABASE swissjobhunter_test"))
    await admin_engine.dispose()

    # Enable pgvector in the test DB
    vec_engine = _cae(_test_db_url, isolation_level="AUTOCOMMIT")
    async with vec_engine.connect() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await vec_engine.dispose()

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _EXTRA_DDL:
            await conn.execute(text(stmt))
        # Punto de partida limpio: la base puede traer residuo de una pasada
        # anterior abortada, y al primer test no le limpia nadie.
        await conn.execute(text(_TRUNCATE_ALL))
    yield


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
async def setup_db(_ensure_test_db):
    """Deja la base vacía después de cada test.

    Solo se truncan las tablas que TIENEN filas. El `TRUNCATE` de antes no
    llevaba `RESTART IDENTITY`, así que sobre una tabla ya vacía no tiene
    ningún efecto observable: truncarla y no truncarla son indistinguibles
    para cualquier test. Lo que sí cuesta es el fichero nuevo (`relfilenode`)
    que `TRUNCATE` asigna a cada heap y a cada uno de los 43 índices.

    La guarda permanente de que esto NO deja residuo está en
    `tests/test_g9_aislamiento_fixture.py`.
    """
    yield
    async with test_engine.begin() as conn:
        dirty = [row[0] for row in (await conn.execute(text(_DIRTY_PROBE))).all()]
        if dirty:
            await conn.execute(text("TRUNCATE TABLE " + ", ".join(dirty) + " CASCADE"))


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


@pytest.fixture
async def redis_client():
    """Provide a Redis connection for SSE tests (same host as app cache)."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    yield client
    await client.aclose()


@pytest.fixture
async def sse_manager(redis_client):
    """Provide a started SSEManager with small queue for overflow tests."""
    from services.sse_manager import SSEManager

    mgr = SSEManager(redis_client, queue_maxsize=10)
    await mgr.start()
    yield mgr
    await mgr.stop()

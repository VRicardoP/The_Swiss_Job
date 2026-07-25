"""Motor/sesión/Base del core — SIEMPRE dentro del esquema propio (A-01)."""

from contextlib import asynccontextmanager

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from jobhunt_core.config import settings


class Base(DeclarativeBase):
    # Todo modelo del core nace en el esquema propio (defensa además del rol
    # de mínimo privilegio). Las tablas [A] llegan en A-02.
    metadata = MetaData(schema=settings.CORE_DB_SCHEMA)


def create_core_engine(**kwargs):
    """Engine del core con el search_path obligatorio.

    search_path = jobhunt, public (rev. externa 3ª #1): pgvector (tipo
    `vector` y sus operadores, p.ej. <=>) vive en `public`; sin él en el
    search_path, A-02 no puede crear ni operar columnas vector(384). La
    protección frente a las TABLAS legacy de public NO es la invisibilidad
    del esquema sino las ACL (cero privilegios, verificado exhaustivamente
    en cada corrida del migrate, incluidas ACL de columna).
    """
    return create_async_engine(
        settings.CORE_DATABASE_URL,
        connect_args={
            "server_settings": {"search_path": f"{settings.CORE_DB_SCHEMA}, public"}
        },
        **kwargs,
    )


engine = create_core_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def task_session_factory():
    """Engine + session factory DESECHABLES para tareas Celery (rev. A-04 2ª #1).

    Cada asyncio.run() de una tarea crea un event loop NUEVO: el engine global
    (y su pool asyncpg) queda ligado al PRIMER loop del proceso worker, y la
    segunda tarea muere con 'Future attached to a different loop' +
    InterfaceError. Engine propio por invocación, dispose en el MISMO loop;
    NullPool: sin conexiones que sobrevivan al loop (mismo patrón que
    backend.database.task_session del legacy).

    NOTA (2º análisis B-02, P3): quien YA corre dentro de un ÚNICO
    asyncio.run (el proyector de la sombra) no debe pagar un engine NullPool
    por llamada — las impls de las tareas (_run_pending_impl,
    _run_profile_impl) aceptan `session_factory=` inyectada; este context
    manager queda para el camino Celery standalone (loop nuevo por tarea).
    """
    task_engine = create_core_engine(poolclass=NullPool)
    try:
        yield async_sessionmaker(task_engine, expire_on_commit=False)
    finally:
        await task_engine.dispose()

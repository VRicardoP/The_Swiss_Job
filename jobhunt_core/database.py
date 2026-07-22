"""Motor/sesión/Base del core — SIEMPRE dentro del esquema propio (A-01)."""

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from jobhunt_core.config import settings


class Base(DeclarativeBase):
    # Todo modelo del core nace en el esquema propio (defensa además del rol
    # de mínimo privilegio). Las tablas [A] llegan en A-02.
    metadata = MetaData(schema=settings.CORE_DB_SCHEMA)


engine = create_async_engine(
    settings.CORE_DATABASE_URL,
    # search_path = jobhunt, public (rev. externa 3ª #1): pgvector (tipo
    # `vector` y sus operadores, p.ej. <=>) vive en `public`; sin él en el
    # search_path, A-02 no puede crear ni operar columnas vector(384). La
    # protección frente a las TABLAS legacy de public NO es la invisibilidad
    # del esquema sino las ACL (cero privilegios, verificado exhaustivamente
    # en cada corrida del migrate, incluidas ACL de columna).
    connect_args={
        "server_settings": {"search_path": f"{settings.CORE_DB_SCHEMA}, public"}
    },
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

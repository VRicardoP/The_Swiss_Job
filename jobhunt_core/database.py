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
    # search_path fijado a nivel de conexión: el core no resuelve tablas fuera
    # de su esquema ni aunque una query olvide el prefijo.
    connect_args={"server_settings": {"search_path": settings.CORE_DB_SCHEMA}},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

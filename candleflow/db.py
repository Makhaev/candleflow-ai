import os

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from candleflow.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

# Worker, seed and Alembic use the synchronous driver. HTTP uses asynchronous asyncpg I/O.
async_options = (
    {"poolclass": NullPool}
    if os.getenv("TEST_DATABASE_URL")
    else {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow, "pool_timeout": 10}
)
async_engine = create_async_engine(
    engine.url.set(drivername="postgresql+asyncpg"), pool_pre_ping=True, hide_parameters=True, **async_options
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

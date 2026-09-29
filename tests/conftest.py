
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

from app.core.db.base import Base
import app.core.db.models  # noqa: F401 – registers all models with Base.metadata
from app.core.db.db import get_db, get_security_session
from app.main import app


# ---------------------------------------------------------------------------
# Container & engine  (session-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container():
    """One PostgreSQL 18 + pgvector container for the entire test session."""
    with PostgresContainer("pgvector/pgvector:pg18") as pg:
        yield pg


@pytest_asyncio.fixture(scope="session")
async def test_engine(postgres_container: PostgresContainer):
    """Session-scoped async engine.  Creates the schema once per session."""
    raw_url = postgres_container.get_connection_url()
    # Ensure we use the asyncpg driver
    async_url = (
        raw_url
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )

    engine = create_async_engine(async_url, echo=False)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Per-test isolation via nested transactions (SAVEPOINT → ROLLBACK)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_connection(test_engine) -> AsyncGenerator[AsyncConnection, None]:
    """Opens one connection and begins an outer transaction for the test."""
    async with test_engine.connect() as conn:
        await conn.begin()
        yield conn
        await conn.rollback()  # discard everything the test wrote


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    """
    Binds a session to the already-open test connection so that all ORM
    operations participate in the outer transaction (and thus get rolled back
    after the test).
    """
    session_factory = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient with the real DB dependency replaced by the test session."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    # Security counters (rate limits, OTP lockout) join the same test
    # transaction, so they reset with the per-test rollback.
    async def _override_get_security_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_security_session] = _override_get_security_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def isolated_client(test_engine):
    """Client whose requests each get their own committing sessions.

    The shared savepoint session in the `client` fixture cannot be used by
    concurrent requests, so this mimics production: one session per request,
    real commits. State is cleaned up in teardown.
    """
    session_factory = async_sessionmaker(
        bind=test_engine, expire_on_commit=False, autoflush=False, autocommit=False
    )

    async def _request_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _security_session():
        async with session_factory() as session:
            try:
                yield session
            finally:
                try:
                    await session.commit()
                except Exception:
                    await session.rollback()

    app.dependency_overrides[get_db] = _request_session
    app.dependency_overrides[get_security_session] = _security_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        async with test_engine.begin() as conn:
            await conn.execute(text("DELETE FROM rate_limits"))
            await conn.execute(text("DELETE FROM users"))


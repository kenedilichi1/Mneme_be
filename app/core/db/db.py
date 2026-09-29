from collections.abc import AsyncGenerator

import logging

from pgvector.sqlalchemy import Vector  # noqa: F401 – re-exported for model use
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# Dedicated session factory for rate-limit / OTP-lockout counters. It commits
# independently of the request transaction so attempt counters survive failed
# requests (a 4xx raised from a route would roll back the request session).
# This is the documented exception to "get_db owns the commit" (MNE-23).
SecuritySessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session used in FastAPI route dependencies.

    Transaction policy (MNE-23): get_db owns the commit. Services and
    repositories flush but never commit; a raised exception rolls the whole
    request back. The only sanctioned exceptions are:

    1. ``get_security_session`` (below) — rate-limit and OTP-lockout counters
       must survive failed requests, so they commit on their own session.
    2. ``DocumentRepository.commit`` in ``DocumentService`` — called out
       explicitly at each call site: the oversized-upload path must
       persist ``status="failed"`` despite raising 413; the normal confirm
       path must commit *before* publishing to RabbitMQ, because the worker
       skips (and acks) messages whose document row is not committed yet;
       and delete_document must commit the row removal *before* removing
       the storage object (MNE-21).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_security_session() -> AsyncGenerator[AsyncSession, None]:
    """Session for rate-limit hits and OTP lockout counters.

    Commits unconditionally — even when the request ends in an error — so a
    failed verify still counts toward the lockout (MNE-11 / MNE-23).
    """
    async with SecuritySessionLocal() as session:
        try:
            yield session
        finally:
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception(
                    "Could not persist security counters; counter update lost"
                )

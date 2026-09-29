"""Rate limiting backed by Postgres so limits are shared by every process.

Replaces the old in-memory slowapi limiter, which reset on restart, was not
shared between workers, and keyed on the raw connecting IP (every user behind
a proxy looked like the same client).
"""

import math
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db.db import get_security_session

SecuritySessionDep = Annotated[AsyncSession, Depends(get_security_session)]

# Count the hit and, in the same statement, drop rows whose window ended long
# ago so the table stays bounded.
_CLAIM_SQL = text(
    """
    WITH claim AS (
        INSERT INTO rate_limits (key, window_started_at, hit_count)
        VALUES (:key, now(), 1)
        ON CONFLICT (key) DO UPDATE SET
            hit_count = CASE
                WHEN rate_limits.window_started_at < now() - make_interval(secs => :window)
                THEN 1
                ELSE rate_limits.hit_count + 1
            END,
            window_started_at = CASE
                WHEN rate_limits.window_started_at < now() - make_interval(secs => :window)
                THEN now()
                ELSE rate_limits.window_started_at
            END
        RETURNING hit_count, window_started_at
    ), gc AS (
        DELETE FROM rate_limits
        WHERE window_started_at < now() - interval '1 day'
    )
    SELECT hit_count, window_started_at FROM claim
    """
)


def get_client_ip(request: Request) -> str:
    """Client IP, honouring X-Forwarded-For only when explicitly trusted.

    With a trusted proxy the rightmost entry is the client as appended by that
    proxy; entries further left are client-controlled and spoofable.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[-1].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def enforce_rate_limit(
    key: str, limit: int, window_seconds: int, session: AsyncSession
) -> None:
    """Atomically record one hit for `key`; raise 429 when over `limit`."""
    result = await session.execute(
        _CLAIM_SQL, {"key": key, "window": window_seconds}
    )
    row = result.mappings().one_or_none()
    if row is None:
        return

    hit_count = row["hit_count"]
    if hit_count <= limit:
        return

    window_started = row["window_started_at"]
    now = datetime.now(timezone.utc)
    retry_after = max(1, math.ceil(window_seconds - (now - window_started).total_seconds()))
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Rate limit exceeded",
        headers={"Retry-After": str(retry_after)},
    )


def rate_limit(name: str, limit: int, window_seconds: int = 60):
    """Dependency factory: one fixed-window limit per client IP."""

    async def dependency(
        request: Request, session: SecuritySessionDep
    ) -> None:
        ip = get_client_ip(request)
        await enforce_rate_limit(
            f"{name}:ip:{ip}", limit, window_seconds, session
        )

    return dependency

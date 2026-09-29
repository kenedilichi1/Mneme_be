"""Per-email OTP lockout counters (MNE-11).

Failed verifies are counted per email across every OTP, in a single atomic
UPDATE so parallel requests cannot race the counter. The claim runs on the
security session (committed independently of the request transaction), so a
rejected verify still counts.
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

_CLAIM_SQL = text(
    """
    UPDATE users
    SET failed_otp_attempts = CASE
            WHEN otp_attempts_window_started_at IS NULL
              OR otp_attempts_window_started_at < now() - make_interval(mins => :lockout_minutes)
            THEN 1
            ELSE failed_otp_attempts + 1
        END,
        otp_attempts_window_started_at = CASE
            WHEN otp_attempts_window_started_at IS NULL
              OR otp_attempts_window_started_at < now() - make_interval(mins => :lockout_minutes)
            THEN now()
            ELSE otp_attempts_window_started_at
        END
    WHERE email = :email
      AND (
            otp_attempts_window_started_at IS NULL
         OR otp_attempts_window_started_at < now() - make_interval(mins => :lockout_minutes)
         OR failed_otp_attempts < :max_attempts
      )
    RETURNING failed_otp_attempts, otp_attempts_window_started_at
    """
)

_RESET_SQL = text(
    """
    UPDATE users
    SET failed_otp_attempts = 0,
        otp_attempts_window_started_at = NULL
    WHERE email = :email
    """
)

_RETRY_AFTER_SQL = text(
    """
    SELECT GREATEST(
        CEIL(EXTRACT(EPOCH FROM (
            otp_attempts_window_started_at
            + make_interval(mins => :lockout_minutes) - now()
        ))),
        1
    ) AS retry_after
    FROM users WHERE email = :email
    """
)


async def claim_attempt(session: AsyncSession, email: str) -> tuple[int, datetime] | None:
    """Consume one verify-attempt slot for this email.

    Returns (attempts_so_far, window_started_at), or None when the lockout
    window is exhausted (the caller must reject without checking any code).
    """
    params = {
        "email": email,
        "max_attempts": settings.OTP_MAX_FAILED_ATTEMPTS,
        "lockout_minutes": settings.OTP_LOCKOUT_MINUTES,
    }
    row = (await session.execute(_CLAIM_SQL, params)).mappings().one_or_none()
    if row is None:
        return None
    return row["failed_otp_attempts"], row["otp_attempts_window_started_at"]


async def reset_attempts(session: AsyncSession, email: str) -> None:
    """Clear the counter after a successful verification."""
    await session.execute(_RESET_SQL, {"email": email})


async def lockout_retry_after(session: AsyncSession, email: str) -> int:
    """Seconds until the lockout window expires (for the Retry-After header)."""
    row = (
        await session.execute(
            _RETRY_AFTER_SQL,
            {"email": email, "lockout_minutes": settings.OTP_LOCKOUT_MINUTES},
        )
    ).one_or_none()
    if row is None:
        return settings.OTP_LOCKOUT_MINUTES * 60
    return int(row[0])

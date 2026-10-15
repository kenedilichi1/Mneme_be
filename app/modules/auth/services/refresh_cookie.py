"""Refresh-token cookie for browser clients.

Delivering the refresh token as an httpOnly cookie keeps it out of JS reach:
an XSS bug can still steal the short-lived access token from memory, but it
cannot mint a new session once that token expires.

Attributes:
- ``HttpOnly``   — unreadable from JavaScript.
- ``SameSite``   — ``refresh_cookie_samesite`` (default ``lax``) blocks
  cross-site POSTs, which is the CSRF defence for /refresh and /logout.
- ``Path``       — scoped to the auth routes so it never rides along on API
  calls (documents, chat, search) and has the smallest exposure surface.
- host-only     — no ``Domain`` attribute, so subdomains don't receive it.
- ``Secure``    — always in production, and whenever SameSite is ``none``
  (browsers reject SameSite=None without Secure).
"""

from fastapi import Response

from app.core.config import settings

REFRESH_COOKIE_PATH = "/api/v1/auth"


def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
        secure=settings.is_production
        or settings.refresh_cookie_samesite == "none",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
        secure=settings.is_production
        or settings.refresh_cookie_samesite == "none",
    )

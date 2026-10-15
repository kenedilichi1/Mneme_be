from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.config import settings
from app.core.email import normalize_email
from app.core.rate_limit import (
    SecuritySessionDep,
    enforce_rate_limit,
    rate_limit,
)
from app.core.responses import COMMON_ERRORS, SuccessEnvelope, error_responses, ok
from app.modules.auth.dependencies.auth_dependency import AuthServiceDep, CurrentUserDep
from app.modules.auth.schemas.auth_schema import (
    RefreshRequest,
    RequestOtp,
    TokenResponse,
    VerifyOtpRequest,
)
from app.modules.auth.services.refresh_cookie import (
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.modules.user.schemas.user_schema import UserPublic

auth_router = APIRouter()

_RATE_WINDOW_SECONDS = 60


@auth_router.post(
    "/request-otp",
    status_code=202,
    response_model=SuccessEnvelope[dict[str, str]],
    responses=COMMON_ERRORS,
)
async def request_otp(
    body: RequestOtp,
    auth_service: AuthServiceDep,
    security_session: SecuritySessionDep,
    _ip_limit: Annotated[None, Depends(rate_limit("request_otp", settings.OTP_REQUEST_LIMIT_PER_MINUTE))],
) -> dict[str, str]:
    # Per-email limit: rotating IPs must not multiply the guess budget
    await enforce_rate_limit(
        f"request_otp:email:{normalize_email(body.email)}",
        settings.OTP_REQUEST_LIMIT_PER_MINUTE,
        _RATE_WINDOW_SECONDS,
        security_session,
    )
    await auth_service.request_otp(body.email)
    return ok(None, "OTP sent")


@auth_router.post(
    "/verify-otp",
    response_model=SuccessEnvelope[TokenResponse],
    responses=COMMON_ERRORS
    | error_responses(
        (400, "Invalid, expired or missing OTP"),
        (404, "User not found"),
        (429, "Rate limit or OTP lockout exceeded"),
    ),
)
async def verify_otp(
    body: VerifyOtpRequest,
    response: Response,
    auth_service: AuthServiceDep,
    security_session: SecuritySessionDep,
    _ip_limit: Annotated[None, Depends(rate_limit("verify_otp", settings.OTP_VERIFY_LIMIT_PER_MINUTE))],
):
    await enforce_rate_limit(
        f"verify_otp:email:{normalize_email(body.email)}",
        settings.OTP_VERIFY_LIMIT_PER_MINUTE,
        _RATE_WINDOW_SECONDS,
        security_session,
    )
    tokens = await auth_service.verify_otp(body.email, body.otp, security_session)
    # Always set the httpOnly cookie; the body copy exists only for native
    # clients that asked for it (browser JS must never see this token).
    set_refresh_cookie(response, tokens["refresh_token"])
    if not body.deliver_refresh_token:
        tokens["refresh_token"] = None
    return ok(tokens, "Login successful")


@auth_router.post(
    "/refresh",
    response_model=SuccessEnvelope[TokenResponse],
    responses=COMMON_ERRORS
    | error_responses(
        (401, "Missing, invalid, expired, revoked or reused refresh token")
    ),
)
async def refresh(
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    security_session: SecuritySessionDep,
    body: RefreshRequest = RefreshRequest(),
):
    # Browser: token comes from the httpOnly cookie (body omitted entirely).
    # Native: token comes in the body and goes back in the body.
    raw_token = body.refresh_token or request.cookies.get(
        settings.refresh_cookie_name
    )
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token",
        )
    tokens = await auth_service.refresh_session(raw_token, security_session)
    # Rotate the cookie even for body-mode clients that also hold one.
    set_refresh_cookie(response, tokens["refresh_token"])
    if body.refresh_token is None:
        tokens["refresh_token"] = None
    return ok(tokens, "Token refreshed")


@auth_router.post(
    "/logout",
    response_model=SuccessEnvelope[dict[str, str]],
    responses=COMMON_ERRORS,
)
async def logout(
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    body: RefreshRequest = RefreshRequest(),
):
    raw_token = body.refresh_token or request.cookies.get(
        settings.refresh_cookie_name
    )
    # Idempotent: no/unknown token still succeeds (and clears the cookie).
    if raw_token:
        await auth_service.logout(raw_token)
    clear_refresh_cookie(response)
    return ok(None, "Logged out")


@auth_router.get(
    "/me",
    response_model=SuccessEnvelope[UserPublic],
    responses=COMMON_ERRORS | error_responses((401, "Not authenticated")),
)
async def me(current_user: CurrentUserDep):
    return ok(current_user, "Profile retrieved")

from typing import Annotated

from fastapi import APIRouter, Depends

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
    return ok(tokens, "Login successful")


@auth_router.post(
    "/refresh",
    response_model=SuccessEnvelope[TokenResponse],
    responses=COMMON_ERRORS
    | error_responses((401, "Invalid, expired, revoked or reused refresh token")),
)
async def refresh(
    body: RefreshRequest,
    auth_service: AuthServiceDep,
    security_session: SecuritySessionDep,
):
    tokens = await auth_service.refresh_session(body.refresh_token, security_session)
    return ok(tokens, "Token refreshed")


@auth_router.post(
    "/logout",
    response_model=SuccessEnvelope[dict[str, str]],
    responses=COMMON_ERRORS,
)
async def logout(body: RefreshRequest, auth_service: AuthServiceDep):
    await auth_service.logout(body.refresh_token)
    return ok(None, "Logged out")


@auth_router.get(
    "/me",
    response_model=SuccessEnvelope[UserPublic],
    responses=COMMON_ERRORS | error_responses((401, "Not authenticated")),
)
async def me(current_user: CurrentUserDep):
    return ok(current_user, "Profile retrieved")

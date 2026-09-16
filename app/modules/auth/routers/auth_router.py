from fastapi import APIRouter

from app.modules.auth.dependencies.auth_dependency import AuthServiceDep, CurrentUserDep
from app.modules.auth.schemas.auth_schema import (
    LoginRequest,
    SignupRequest,
    TokenResponse,
    VerifyOtpRequest,
)
from app.modules.user.schemas.user_schema import UserPublic

auth_router = APIRouter()


@auth_router.post("/signup", status_code=202)
async def signup(request: SignupRequest, auth_service: AuthServiceDep) -> dict[str, str]:
    await auth_service.request_otp(request.email)
    return {"message": "OTP sent"}


@auth_router.post("/login", status_code=202)
async def login(request: LoginRequest, auth_service: AuthServiceDep) -> dict[str, str]:
    await auth_service.request_otp(request.email)
    return {"message": "OTP sent"}


@auth_router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp(request: VerifyOtpRequest, auth_service: AuthServiceDep):
    return await auth_service.verify_otp(request.email, request.otp)


@auth_router.get("/me", response_model=UserPublic)
async def me(current_user: CurrentUserDep):
    return current_user

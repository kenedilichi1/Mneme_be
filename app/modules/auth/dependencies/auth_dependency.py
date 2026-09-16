import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import SessionDep
from app.core.email import EmailService
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.auth_service import AuthService
from app.modules.auth.services.secrets import decode_access_token
from app.modules.user.dependencies import UserServiceDep
from app.modules.user.models.user_model import User


bearer_scheme = HTTPBearer()


def get_otp_code_repository(db: SessionDep) -> OtpCodeRepository:
    return OtpCodeRepository(db)


def get_email_service() -> EmailService:
    return EmailService()


def get_auth_service(
    otp_code_repo: Annotated[OtpCodeRepository, Depends(get_otp_code_repository)],
    user_service: UserServiceDep,
    email_service: Annotated[EmailService, Depends(get_email_service)],
) -> AuthService:
    return AuthService(otp_code_repo, user_service, email_service)


OtpCodeRepositoryDep = Annotated[OtpCodeRepository, Depends(get_otp_code_repository)]
EmailServiceDep = Annotated[EmailService, Depends(get_email_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    user_service: UserServiceDep,
) -> User:
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    try:
        parsed_id = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = await user_service.get_user_by_id(parsed_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]

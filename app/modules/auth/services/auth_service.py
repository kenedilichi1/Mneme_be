from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.core.email import EmailService
from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.secrets import (
    create_access_token,
    generate_otp_code,
    hash_otp_code,
    otp_expiry,
    verify_otp_code,
)
from app.modules.user.schemas.user_schema import CreateUser
from app.modules.user.services.user_service import UserService


class AuthService:
    def __init__(
        self,
        otp_code_repo: OtpCodeRepository,
        user_service: UserService,
        email_service: EmailService,
    ) -> None:
        self.otp_code_repo = otp_code_repo
        self.user_service = user_service
        self.email_service = email_service

    async def request_otp(self, user_email: str) -> None:
        user = await self.user_service.get_by_email(email=user_email)

        if user is None:
            user = await self.user_service.create_user(
                payload=CreateUser(email=user_email)
            )

        otp_code = generate_otp_code()
        otp = OtpCode(
            user_id=user.id,
            code_hash=hash_otp_code(otp_code),
            expires_at=otp_expiry(),
        )

        await self.otp_code_repo.create(otp)
        await self.email_service.send_otp_email(user_email, otp_code)

    async def verify_otp(self, user_email: str, otp_code: str) -> dict[str, str]:
        user = await self.user_service.get_by_email(email=user_email)

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        otp = await self.otp_code_repo.get_otp_code(user.id)

        if otp is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OTP not found",
            )

        if otp.expires_at < datetime.now(timezone.utc):
            await self.otp_code_repo.delete_otp_code(user.id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OTP expired",
            )

        if not verify_otp_code(otp_code, otp.code_hash):
            otp.attempts += 1
            if otp.attempts >= 3:
                await self.otp_code_repo.delete_otp_code(user.id)
                await self.otp_code_repo.db.commit()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Too many attempts",
                )
            await self.otp_code_repo.db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid OTP",
            )

        await self.otp_code_repo.delete_otp_code(user.id)

        return {
            "access_token": create_access_token(str(user.id)),
            "token_type": "bearer",
        }

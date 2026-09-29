from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.email import EmailService, normalize_email
from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.models.refresh_token_model import RefreshToken
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.repositories.refresh_token_repository import RefreshTokenRepository
from app.modules.auth.services.otp_lockout import claim_attempt, lockout_retry_after, reset_attempts
from app.modules.auth.services.secrets import (
    create_access_token,
    generate_otp_code,
    generate_refresh_token,
    hash_otp_code,
    hash_refresh_token,
    otp_expiry,
    verify_otp_code,
)
from app.modules.user.services.user_service import UserService


class AuthService:
    def __init__(
        self,
        otp_code_repo: OtpCodeRepository,
        user_service: UserService,
        email_service: EmailService,
        refresh_token_repo: RefreshTokenRepository,
    ) -> None:
        self.otp_code_repo = otp_code_repo
        self.user_service = user_service
        self.email_service = email_service
        self.refresh_token_repo = refresh_token_repo

    async def request_otp(self, user_email: str) -> None:
        # Identity is case-insensitive: normalize once, then everything below
        # (user row, OTP, lockout counter) keys off the stored canonical form.
        user_email = normalize_email(user_email)
        user = await self.user_service.get_or_create_by_email(user_email)

        # A user has at most one live OTP: issuing a new code invalidates the
        # older ones instead of giving the requester a fresh guess budget.
        await self.otp_code_repo.delete_otp_code(user.id)

        otp_code = generate_otp_code()
        otp = OtpCode(
            user_id=user.id,
            code_hash=hash_otp_code(otp_code),
            expires_at=otp_expiry(),
        )

        await self.otp_code_repo.create(otp)
        await self.email_service.send_otp_email(user.email, otp_code)

    async def verify_otp(
        self, user_email: str, otp_code: str, security_session: AsyncSession
    ) -> dict[str, str]:
        user = await self.user_service.get_user_by_email(user_email)

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Claim a verify-attempt slot BEFORE checking any code: one atomic
        # UPDATE per attempt, shared across all OTPs for this email. A None
        # result means the lockout window is exhausted — reject outright so
        # parallel requests can never exceed the limit.
        claim = await claim_attempt(security_session, user.email)
        if claim is None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed attempts. Please try again later.",
                headers={
                    "Retry-After": str(
                        await lockout_retry_after(security_session, user.email)
                    )
                },
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
            # The attempt was already counted by claim_attempt
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid OTP",
            )

        await reset_attempts(security_session, user.email)
        await self.otp_code_repo.delete_otp_code(user.id)

        refresh_token = await self._issue_refresh_token(user)
        return {
            "access_token": create_access_token(str(user.id)),
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    async def refresh_session(
        self, raw_refresh_token: str, security_session: AsyncSession
    ) -> dict[str, str]:
        """Rotate a refresh token: revoke the old one, issue a fresh pair."""
        token = await self.refresh_token_repo.get_by_hash(
            hash_refresh_token(raw_refresh_token)
        )
        if token is None:
            raise self._invalid_refresh_token()

        if token.expires_at < datetime.now(timezone.utc):
            raise self._invalid_refresh_token()

        # Single atomic claim: two concurrent refreshes with the same token
        # cannot both rotate it.
        claimed = await self.refresh_token_repo.claim_for_rotation(token.id)
        if not claimed:
            # Reuse of a rotated/logged-out token — assume theft and revoke the
            # whole family. The 401 that follows would roll back the request
            # session, so the revocation runs on the security session instead.
            await RefreshTokenRepository(security_session).revoke_all_for_user(
                token.user_id
            )
            raise self._invalid_refresh_token()

        user = await self.user_service.get_user_by_id(token.user_id)
        if user is None:
            raise self._invalid_refresh_token()

        new_refresh_token = await self._issue_refresh_token(user)
        return {
            "access_token": create_access_token(str(user.id)),
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        }

    async def logout(self, raw_refresh_token: str) -> dict[str, str]:
        """Revoke a refresh token. Idempotent: unknown tokens still succeed."""
        token = await self.refresh_token_repo.get_by_hash(
            hash_refresh_token(raw_refresh_token)
        )
        if token is not None:
            await self.refresh_token_repo.revoke(token)
        return {"message": "Logged out"}

    async def _issue_refresh_token(self, user) -> str:
        await self.refresh_token_repo.delete_expired_for_user(user.id)
        raw_token = generate_refresh_token()
        await self.refresh_token_repo.create(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_refresh_token(raw_token),
                expires_at=datetime.now(timezone.utc)
                + timedelta(days=settings.refresh_token_expire_days),
            )
        )
        return raw_token

    @staticmethod
    def _invalid_refresh_token() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

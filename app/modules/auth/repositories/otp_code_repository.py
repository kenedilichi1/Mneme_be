from typing import Any
import uuid

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.auth.models.otp_code_model import OtpCode
class OtpCodeRepository:
    def __init__(self, db:AsyncSession):
        self.db = db

    async def create(self,otp_code:OtpCode ):
        self.db.add(otp_code)
        await self.db.flush()
        await self.db.refresh(otp_code)

        return otp_code

    async def get_otp_code(self, user_id: uuid.UUID) -> OtpCode | None:
        result = await self.db.execute(
            select(OtpCode)
            .where(OtpCode.user_id == user_id)
            .order_by(OtpCode.created_at.desc())
            .limit(1)
        )
        otp = result.scalar_one_or_none()

        return otp

    async def delete_otp_code(self, user_id: uuid.UUID):
        await self.db.execute(
            delete(OtpCode)
            .where(OtpCode.user_id == user_id)
        )
       
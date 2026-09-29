import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models.refresh_token_model import RefreshToken
from app.utils.timezone import utcnow


class RefreshTokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, token: RefreshToken) -> RefreshToken:
        self.db.add(token)
        await self.db.flush()
        await self.db.refresh(token)
        return token

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def claim_for_rotation(self, token_id: uuid.UUID) -> bool:
        """Atomically mark a live token as revoked; False if already revoked.

        Single UPDATE ... WHERE revoked_at IS NULL so two concurrent refreshes
        with the same token cannot both succeed.
        """
        result = await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        return result.rowcount == 1

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        """Revoke every live token of a user (used on rotation reuse)."""
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )

    async def revoke(self, token: RefreshToken) -> None:
        if token.revoked_at is None:
            token.revoked_at = utcnow()
            await self.db.flush()

    async def delete_expired_for_user(self, user_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.expires_at < func.now(),
            )
        )

    async def count_live_for_user(self, user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > func.now(),
            )
        )
        return result.scalar_one()

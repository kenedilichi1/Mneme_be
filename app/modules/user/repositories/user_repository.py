import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import normalize_email
from app.modules.user.models.user_model import User

class UserRepository:
    def __init__(self, db:AsyncSession):
        self.db = db

    async def create(self, user:User):
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)

        return user

    async def update(self, user: User) -> User:
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def rollback(self) -> None:
        """Abort the current transaction after a lost signup race.

        A unique-violation leaves the session unusable; rolling back lets the
        caller re-query for the row the winning request committed.
        """
        await self.db.rollback()

    async def get_by_email(self, email:str):
        # lower() on both sides: matches the unique index on lower(email), so
        # lookups behave exactly like the database's uniqueness rule.
        result = await self.db.execute(
            select(User).where(func.lower(User.email) == normalize_email(email))
        )

        return result.scalar_one_or_none()

    async def get_by_id(self, user_id:uuid.UUID):
        result= await self.db.execute(
            select(User).where(User.id == user_id)
        )

        return result.scalar_one_or_none()
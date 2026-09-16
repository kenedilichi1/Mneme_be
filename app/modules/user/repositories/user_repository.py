import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

    async def get_by_email(self, email:str):
        result = await self.db.execute(
            select(User).where(User.email == email)
        )

        return result.scalar_one_or_none()

    async def get_by_id(self, user_id:uuid.UUID):
        result= await self.db.execute(
            select(User).where(User.id == user_id)
        )

        return result.scalar_one_or_none()
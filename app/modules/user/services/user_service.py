import uuid

from app.modules.user.models.user_model import User
from app.modules.user.repositories.user_repository import UserRepository
from app.modules.user.schemas.user_schema import CreateUser
from datetime import datetime, timezone


class UserService:
    def __init__(self, user_repository: UserRepository):
        self.user_repository = user_repository

    async def get_by_email(self, email: str) -> User | None:
        return await self.user_repository.get_by_email(email)

    async def create_user(self, payload: CreateUser) -> User:
        user = User(
            email=payload.email,
            first_name=payload.first_name,
            last_name= payload.last_name,
            created_at= datetime.now(timezone.utc),
            updated_at = datetime.now(timezone.utc)
        )

        return await self.user_repository.create(user)
    
    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.user_repository.get_by_id(user_id)

    async def get_user_by_email(self, user_email: str) -> User | None:
        return await self.user_repository.get_by_email(user_email)

    async def update(
        self,
        user: User,
    ) -> User:
        return await self.user_repository.update(user)
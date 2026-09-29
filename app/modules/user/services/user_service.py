import uuid

from sqlalchemy.exc import IntegrityError

from app.core.email import normalize_email
from app.modules.user.models.user_model import User
from app.modules.user.repositories.user_repository import UserRepository
from app.modules.user.schemas.user_schema import CreateUser
from datetime import datetime, timezone


class UserService:
    def __init__(self, user_repository: UserRepository):
        self.user_repository = user_repository

    async def create_user(self, payload: CreateUser) -> User:
        user = User(
            email=normalize_email(payload.email),
            first_name=payload.first_name,
            last_name= payload.last_name,
            created_at= datetime.now(timezone.utc),
            updated_at = datetime.now(timezone.utc)
        )

        return await self.user_repository.create(user)
    
    async def get_or_create_by_email(self, user_email: str) -> User:
        """Find the user for an address, registering it if it is new.

        Two concurrent signups can both miss the SELECT and race the INSERT
        (possibly with different casings); the loser sees the unique index on
        lower(email), rolls back, and picks up the winner's committed row
        instead of surfacing a 500.
        """
        email = normalize_email(user_email)
        user = await self.user_repository.get_by_email(email)
        if user is not None:
            return user

        try:
            return await self.create_user(CreateUser(email=email))
        except IntegrityError:
            await self.user_repository.rollback()
            user = await self.user_repository.get_by_email(email)
            if user is None:
                raise
            return user

    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.user_repository.get_by_id(user_id)

    async def get_user_by_email(self, user_email: str) -> User | None:
        return await self.user_repository.get_by_email(email=user_email)


    async def update(
        self,
        user: User,
    ) -> User:
        return await self.user_repository.update(user)

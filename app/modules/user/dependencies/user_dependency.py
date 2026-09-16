from typing import Annotated

from fastapi import Depends

from app.api.dependencies import SessionDep
from app.modules.user.repositories.user_repository import UserRepository
from app.modules.user.services.user_service import UserService


def get_user_repository(db: SessionDep) -> UserRepository:
    return UserRepository(db)


def get_user_service(
    user_repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserService:
    return UserService(user_repository)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]

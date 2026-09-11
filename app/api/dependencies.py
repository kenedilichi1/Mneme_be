from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

# Dependency for database session
SessionDep = Annotated[AsyncSession, Depends(get_db)]

# Future dependency for current user
# CurrentUserDep = Annotated[User, Depends(get_current_user)]

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.db import get_db

SessionDep = Annotated[AsyncSession, Depends(get_db)]

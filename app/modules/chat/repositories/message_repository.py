import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.chat.models.message_model import Message


class MessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations=citations or [],
        )
        self.db.add(message)
        await self.db.flush()
        await self.db.refresh(message)
        return message

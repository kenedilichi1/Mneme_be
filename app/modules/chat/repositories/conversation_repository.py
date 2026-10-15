import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.message_model import Message
from app.utils.timezone import utcnow


class ConversationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self, user_id: uuid.UUID, document_id: uuid.UUID, title: str | None = None
    ) -> Conversation:
        conversation = Conversation(
            user_id=user_id, document_id=document_id, title=title
        )
        self.db.add(conversation)
        await self.db.flush()
        await self.db.refresh(conversation)
        return conversation

    async def get_by_id(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_document(
        self,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Conversation]:
        result = await self.db.execute(
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.document_id == document_id,
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def latest_messages(
        self, conversation_id: uuid.UUID, limit: int
    ) -> list[Message]:
        """Most recent `limit` messages, returned oldest-first for prompt replay."""
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def list_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Message]:
        """Full message history for a conversation, oldest-first."""
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def touch(self, conversation: Conversation) -> None:
        """Bump `updated_at` so the session sorts to the top of history lists."""
        conversation.updated_at = utcnow()
        await self.db.flush()

    async def commit(self) -> None:
        """Explicit mid-request commit — sanctioned exception to get_db owning
        the commit (transaction policy in app/core/db/db.py, MNE-23).

        Justification: the chat endpoint streams its response. The turn's
        messages must be durable the moment they are produced — a client
        disconnect or later stream failure must not lose history (the product's
        core durability promise). Committed rows survive the request teardown
        rollback that follows an interrupted stream."""
        await self.db.commit()

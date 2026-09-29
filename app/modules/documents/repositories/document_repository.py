import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models.document_model import Document


class DocumentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, document: Document) -> Document:
        self.db.add(document)
        await self.db.flush()
        await self.db.refresh(document)
        return document

    async def get_by_id(self, document_id: uuid.UUID, user_id: uuid.UUID) -> Document | None:
        result = await self.db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_all_by_user(
        self, user_id: uuid.UUID, limit: int = 20, offset: int = 0
    ) -> list[Document]:
        result = await self.db.execute(
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def update(self, document: Document) -> Document:
        await self.db.flush()
        await self.db.refresh(document)
        return document

    async def commit(self) -> None:
        """Explicit mid-request commit — sanctioned exception to get_db owning
        the commit; see the transaction policy in app/core/db/db.py (MNE-23).
        Only DocumentService may call this (confirm_upload ×2, delete_document),
        each with its own justification at the call site."""
        await self.db.commit()

    async def delete(self, document: Document) -> None:
        await self.db.delete(document)
        await self.db.flush()

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base_class import BaseModel
from app.utils.timezone import utcnow


class Conversation(BaseModel):
    """A Q&A session scoped to one user (and, for now, one document).

    ``document_id`` is nullable per the ERD: a NULL value will mean a
    library-level conversation once cross-library Q&A lands. Every row
    belongs to exactly one user — multi-tenant isolation is enforced by
    ``user_id`` filters in the repository, never by ``id`` alone.
    """

    __tablename__ = "conversations"
    __table_args__ = (
        # History listings are always "my conversations for this document"
        Index(
            "ix_conversations_user_document_updated",
            "user_id",
            "document_id",
            "updated_at",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String

from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base_class import BaseModel
from app.modules.documents.models.document_status import DocumentStatus
from app.utils.timezone import utcnow


class Document(BaseModel):
    __tablename__ = "documents"
    __table_args__ = (
        # Membership of `status` (MNE-22); allowed *paths* live in
        # models.document_status.ALLOWED_TRANSITIONS
        CheckConstraint(
            "status IN ('pending_upload', 'uploaded', 'processing', 'processed', 'failed')",
            name="ck_documents_status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    file_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=DocumentStatus.PENDING_UPLOAD.value
    )
    tags: Mapped[List[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


    user: Mapped["User"] = relationship(back_populates="documents")
    chunks: Mapped[List["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

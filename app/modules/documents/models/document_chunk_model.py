import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import Computed, Index, String, Integer, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, deferred
from pgvector.sqlalchemy import Vector

from app.core.db.base_class import BaseModel
from app.utils.timezone import utcnow


class DocumentChunk(BaseModel):
    __tablename__ = "document_chunks"
    __table_args__ = (
        # Approximate nearest-neighbour index for vector search (MNE-19)
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # Full-text index over the stored tsvector (MNE-19)
        Index(
            "ix_document_chunks_content_tsv",
            "content_tsv",
            postgresql_using="gin",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(

        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    content: Mapped[str] = mapped_column(String, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(768), nullable=True
    )
    # Generated + stored: kept in sync with `content` by Postgres, indexed with
    # GIN for full-text search. Deferred because it is only queried via raw
    # SQL — the asyncpg driver has no tsvector codec for ORM row loading.
    # Nullable because PostgreSQL leaves generated columns nullable unless
    # NOT NULL is declared explicitly (matches the alembic migration).
    content_tsv = deferred(
        mapped_column(
            TSVECTOR,
            Computed("to_tsvector('english', content)", persisted=True),
            nullable=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")
    user: Mapped["User"] = relationship()

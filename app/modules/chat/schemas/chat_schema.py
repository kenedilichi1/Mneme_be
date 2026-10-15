from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    # Omit to start a new session; pass the id to continue an existing one
    conversation_id: UUID | None = None


class Citation(BaseModel):
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    page_number: int | None = None
    section_title: str | None = None
    snippet: str


class ConversationPublic(BaseModel):
    id: UUID
    document_id: UUID | None = None
    title: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessagePublic(BaseModel):
    id: UUID
    role: str
    content: str
    citations: list[Citation] = []
    created_at: datetime

    model_config = {"from_attributes": True}

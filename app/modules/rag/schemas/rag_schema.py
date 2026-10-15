import uuid
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict

class ChunkSearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    content: str
    token_count: int
    score: float

    model_config = ConfigDict(from_attributes=True)


SearchMode = Literal["hybrid", "vector", "text"]
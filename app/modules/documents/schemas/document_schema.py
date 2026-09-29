from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class AllowedFileType(str, Enum):
    PDF = "application/pdf"
    EPUB = "application/epub+zip"


MIME_TO_EXTENSION = {
    AllowedFileType.PDF.value: "pdf",
    AllowedFileType.EPUB.value: "epub",
}


class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=200)
    original_filename: str = Field(..., min_length=1, max_length=255)
    file_size: int = Field(..., gt=0)
    file_type: AllowedFileType


class DocumentPublic(BaseModel):
    id: UUID
    title: str
    author: str | None = None
    original_filename: str
    file_size: int
    page_count: int | None = None
    file_type: str
    status: str
    tags: list[str] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentWithUploadUrl(BaseModel):
    document: DocumentPublic
    upload_url: str
    upload_method: str = "PUT"
    # Must be sent exactly as given; the URL's signature covers them
    upload_headers: dict[str, str]


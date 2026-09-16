from uuid import UUID

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=200)
    original_filename: str = Field(..., min_length=1)
    file_size: int = Field(..., gt=0)
    file_type: str = Field(..., min_length=1)


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
    created_at: str

    model_config = {"from_attributes": True}


class DocumentWithUploadUrl(BaseModel):
    document: DocumentPublic
    upload_url: str


class PresignedUrlResponse(BaseModel):
    document_id: UUID
    presigned_url: str


class DocumentConfirmUpload(BaseModel):
    pass

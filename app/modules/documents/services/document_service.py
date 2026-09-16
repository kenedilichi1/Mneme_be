import uuid

from fastapi import HTTPException, status

from app.core.storage.storage_service import StorageService
from app.modules.documents.models.document_model import Document
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.documents.schemas.document_schema import DocumentCreate


class DocumentService:
    def __init__(
        self,
        document_repository: DocumentRepository,
        storage_service: StorageService,
    ):
        self.document_repository = document_repository
        self.storage_service = storage_service

    async def create_document(
        self, user_id: uuid.UUID, payload: DocumentCreate
    ) -> tuple[Document, str]:
        storage_key = f"{user_id}/{uuid.uuid4()}.{payload.file_type.split('/')[-1]}"
        document = Document(
            user_id=user_id,
            title=payload.title,
            author=payload.author,
            original_filename=payload.original_filename,
            file_size=payload.file_size,
            file_type=payload.file_type,
            storage_key=storage_key,
            status="pending_upload",
        )
        document = await self.document_repository.create(document)
        upload_url = self.storage_service.generate_upload_url(user_id, payload.file_type)
        return document, upload_url

    async def get_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> Document:
        document = await self.document_repository.get_by_id(document_id, user_id)
        if document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            )
        return document

    async def list_documents(self, user_id: uuid.UUID) -> list[Document]:
        return await self.document_repository.get_all_by_user(user_id)

    async def confirm_upload(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> Document:
        document = await self.get_document(document_id, user_id)

        if document.status != "pending_upload":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document is not in pending_upload status",
            )

        document.status = "uploaded"
        return await self.document_repository.update(document)

    async def delete_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        document = await self.get_document(document_id, user_id)
        if document.storage_key:
            self.storage_service.delete_file(document.storage_key)
        await self.document_repository.delete(document)

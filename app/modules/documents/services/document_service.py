import asyncio
import logging
import uuid

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.queue.events import (
    DOCUMENT_PROCESSING_QUEUE_NAME,
    DocumentEvent,
    DocumentEventType,
)
from app.core.queue.queue import RabbitMQ
from app.core.storage.storage_service import StorageService
from app.modules.documents.models.document_model import Document
from app.modules.documents.models.document_status import DocumentStatus, transition
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.documents.schemas.document_schema import (
    MIME_TO_EXTENSION,
    DocumentCreate,
)

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(
        self,
        document_repository: DocumentRepository,
        storage_service: StorageService,
        rabbitmq: RabbitMQ,
    ):
        self.document_repository = document_repository
        self.storage_service = storage_service
        self.rabbitmq = rabbitmq

    async def create_document(
        self, user_id: uuid.UUID, payload: DocumentCreate
    ) -> tuple[Document, str]:
        if payload.file_size > settings.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File exceeds the {settings.MAX_UPLOAD_BYTES} byte limit",
            )

        ext = MIME_TO_EXTENSION.get(payload.file_type.value, "bin")
        storage_key = f"{user_id}/{uuid.uuid4()}.{ext}"
        document = Document(
            user_id=user_id,
            title=payload.title,
            author=payload.author,
            original_filename=payload.original_filename,
            file_size=payload.file_size,
            file_type=payload.file_type.value,
            storage_key=storage_key,
            status="pending_upload",
        )
        document = await self.document_repository.create(document)
        upload_url = await asyncio.to_thread(
            self.storage_service.generate_upload_url, storage_key, document.file_type
        )
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

    async def list_documents(
        self, user_id: uuid.UUID, limit: int = 20, offset: int = 0
    ) -> list[Document]:
        return await self.document_repository.get_all_by_user(
            user_id, limit=limit, offset=offset
        )

    async def confirm_upload(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> Document:
        document = await self.get_document(document_id, user_id)

        # "uploaded" means a previous confirm committed but failed to queue; allow a retry
        if document.status not in (
            DocumentStatus.PENDING_UPLOAD,
            DocumentStatus.UPLOADED,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Document cannot be confirmed in status '{document.status}'",
            )

        if document.status == DocumentStatus.PENDING_UPLOAD:
            size = await asyncio.to_thread(
                self.storage_service.get_file_size, document.storage_key
            )
            if size is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File has not been uploaded",
                )
            if size > settings.MAX_UPLOAD_BYTES:
                await asyncio.to_thread(
                    self.storage_service.delete_file, document.storage_key
                )
                transition(document, DocumentStatus.FAILED)
                await self.document_repository.update(document)
                # Sanctioned exception (MNE-23): get_db would roll this back
                # when the 413 propagates, so the failure mark commits now.
                await self.document_repository.commit()
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"File exceeds the {settings.MAX_UPLOAD_BYTES} byte limit",
                )

            document.file_size = size
            transition(document, DocumentStatus.UPLOADED)
            document = await self.document_repository.update(document)
            # Sanctioned exception (MNE-23): the worker skips-and-acks events
            # whose document row is not visible yet, so the state must be
            # committed before the publish — get_db only commits after the
            # response, i.e. after this publish.
            await self.document_repository.commit()

        event = DocumentEvent(
            event_type=DocumentEventType.DOCUMENT_UPLOADED,
            document_id=str(document.id),
            user_id=str(user_id),
            storage_key=document.storage_key,
        )
        try:
            await self.rabbitmq.publish(DOCUMENT_PROCESSING_QUEUE_NAME, event.to_dict())
        except Exception:
            logger.exception("Failed to queue document %s for processing", document.id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Upload confirmed but processing could not be queued; retry confirm",
            )

        return document

    async def delete_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        document = await self.get_document(document_id, user_id)
        storage_key = document.storage_key

        # MNE-21: the row goes first. If this fails, nothing is lost and the
        # user can simply retry; the file is never removed before the row.
        await self.document_repository.delete(document)
        # Sanctioned exception (MNE-23): the row must be *committed* before
        # the file is touched — a rolled-back delete must not have removed
        # the storage object behind a live row.
        await self.document_repository.commit()

        if storage_key:
            try:
                await asyncio.to_thread(
                    self.storage_service.delete_file, storage_key
                )
            except Exception:
                # The row is already gone; a leftover object is an orphan for
                # the cleanup job, not a failed deletion for the user.
                logger.warning(
                    "Deleted document %s but failed to remove storage object %s",
                    document_id,
                    storage_key,
                    exc_info=True,
                )


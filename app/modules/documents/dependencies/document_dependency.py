from typing import Annotated

from fastapi import Depends

from app.api.dependencies import SessionDep
from app.core.storage.storage_service import StorageService, storage_service
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.documents.services.document_service import DocumentService


def get_document_repository(db: SessionDep) -> DocumentRepository:
    return DocumentRepository(db)


def get_storage_service() -> StorageService:
    return storage_service


def get_document_service(
    document_repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
) -> DocumentService:
    return DocumentService(document_repository, storage_service)


DocumentRepositoryDep = Annotated[DocumentRepository, Depends(get_document_repository)]
StorageServiceDep = Annotated[StorageService, Depends(get_storage_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]

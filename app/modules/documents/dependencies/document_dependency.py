from typing import Annotated

from fastapi import Depends

from app.api.dependencies import SessionDep
from app.core.queue.queue import RabbitMQ, rabbitmq
from app.core.storage.storage_service import StorageService, storage_service
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.documents.services.document_service import DocumentService


def get_document_repository(db: SessionDep) -> DocumentRepository:
    return DocumentRepository(db)


def get_storage_service() -> StorageService:
    return storage_service


def get_rabbitmq() -> RabbitMQ:
    return rabbitmq


def get_document_service(
    document_repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    rabbitmq: Annotated[RabbitMQ, Depends(get_rabbitmq)],
) -> DocumentService:
    return DocumentService(document_repository, storage_service, rabbitmq)


DocumentRepositoryDep = Annotated[DocumentRepository, Depends(get_document_repository)]
StorageServiceDep = Annotated[StorageService, Depends(get_storage_service)]
RabbitMQDep = Annotated[RabbitMQ, Depends(get_rabbitmq)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]

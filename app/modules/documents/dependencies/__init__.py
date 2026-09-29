from app.modules.documents.dependencies.document_dependency import (
    DocumentRepositoryDep,
    DocumentServiceDep,
    RabbitMQDep,
    StorageServiceDep,
    get_document_repository,
    get_document_service,
    get_rabbitmq,
    get_storage_service,
)

__all__ = [
    "DocumentRepositoryDep",
    "DocumentServiceDep",
    "RabbitMQDep",
    "StorageServiceDep",
    "get_document_repository",
    "get_document_service",
    "get_rabbitmq",
    "get_storage_service",
]

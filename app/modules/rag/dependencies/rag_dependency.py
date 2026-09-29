from typing import Annotated

from fastapi import Depends

from app.api.dependencies import SessionDep
from app.modules.documents.repositories.document_chunk_repository import (
    DocumentChunkRepository,
)
from app.modules.documents.services.embedding_service import (
    EmbeddingService,
)
from app.modules.rag.services.vector_search_service import VectorSearchService


def get_document_chunk_repository(db: SessionDep) -> DocumentChunkRepository:
    return DocumentChunkRepository(db)


def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()


def get_vector_search_service(
    chunk_repository: Annotated[
        DocumentChunkRepository, Depends(get_document_chunk_repository)
    ],
    embedding_service: Annotated[
        EmbeddingService, Depends(get_embedding_service)
    ],
) -> VectorSearchService:
    return VectorSearchService(
        chunk_repository=chunk_repository,
        embedding_service=embedding_service,
    )


DocumentChunkRepositoryDep = Annotated[
    DocumentChunkRepository, Depends(get_document_chunk_repository)
]
EmbeddingServiceDep = Annotated[
    EmbeddingService, Depends(get_embedding_service)
]
VectorSearchServiceDep = Annotated[
    VectorSearchService, Depends(get_vector_search_service)
]

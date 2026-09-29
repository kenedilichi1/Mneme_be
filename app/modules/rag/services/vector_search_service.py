import uuid
from typing import List, Optional, Literal

from pydantic import BaseModel, ConfigDict

from app.modules.documents.models.document_chunk_model import DocumentChunk
from app.modules.documents.repositories.document_chunk_repository import (
    DocumentChunkRepository,
)
from app.modules.documents.services.embedding_service import EmbeddingService


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


class VectorSearchService:
    """
    Service for performing semantic vector, keyword full-text, or RRF hybrid search over document chunks.
    Supports single-document queries as well as cross-library queries.
    """

    def __init__(
        self,
        chunk_repository: DocumentChunkRepository,
        embedding_service: EmbeddingService,
    ) -> None:
        self.chunk_repository = chunk_repository
        self.embedding_service = embedding_service

    async def search(
        self,
        query: str,
        user_id: uuid.UUID,
        document_id: Optional[uuid.UUID] = None,
        limit: int = 5,
        mode: SearchMode = "hybrid",
        alpha: float = 0.5,
    ) -> List[ChunkSearchResult]:
        """
        Executes document search using the requested mode ("hybrid", "vector", or "text").
        In hybrid mode, combines dense vector search with PostgreSQL full-text search via RRF.
        """
        if not query.strip():
            return []

        if mode == "text":
            text_results = await self.chunk_repository.full_text_search(
                query_text=query,
                user_id=user_id,
                document_id=document_id,
                limit=limit,
            )
            return [
                ChunkSearchResult(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    score=rank,
                )
                for chunk, rank in text_results
            ]

        # Generate query vector for vector or hybrid mode
        query_embeddings = await self.embedding_service.generate_embeddings(
            [query], is_query=True
        )
        if not query_embeddings:
            return []

        query_vector = query_embeddings[0]

        if mode == "vector":
            vector_results = await self.chunk_repository.vector_search(
                query_vector=query_vector,
                user_id=user_id,
                document_id=document_id,
                limit=limit,
            )
            return [
                ChunkSearchResult(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    score=max(0.0, 1.0 - dist),
                )
                for chunk, dist in vector_results
            ]

        # Default mode: hybrid (Reciprocal Rank Fusion)
        hybrid_results = await self.chunk_repository.hybrid_search(
            query_vector=query_vector,
            query_text=query,
            user_id=user_id,
            document_id=document_id,
            limit=limit,
            alpha=alpha,
        )

        return [
            ChunkSearchResult(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                page_number=chunk.page_number,
                section_title=chunk.section_title,
                content=chunk.content,
                token_count=chunk.token_count,
                score=rrf_score,
            )
            for chunk, rrf_score in hybrid_results
        ]

import uuid
from typing import Optional, List, Tuple
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models.document_chunk_model import DocumentChunk


class DocumentChunkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_many(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        self.db.add_all(chunks)
        await self.db.flush()
        return chunks

    async def get_by_document_id(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        result = await self.db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return list(result.scalars().all())

    async def delete_by_document_id(self, document_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        await self.db.flush()

    async def vector_search(
        self,
        query_vector: List[float],
        user_id: uuid.UUID,
        document_id: Optional[uuid.UUID] = None,
        limit: int = 5,
        distance_threshold: Optional[float] = None,
    ) -> List[Tuple[DocumentChunk, float]]:
        """
        Perform vector similarity search over document_chunks using pgvector cosine distance.
        Enforces strict multi-tenant user_id isolation.
        """
        distance_expr = DocumentChunk.embedding.cosine_distance(query_vector)

        stmt = (
            select(DocumentChunk, distance_expr.label("distance"))
            .where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.embedding.is_not(None),
            )
        )

        if document_id is not None:
            stmt = stmt.where(DocumentChunk.document_id == document_id)

        if distance_threshold is not None:
            stmt = stmt.where(distance_expr <= distance_threshold)

        stmt = stmt.order_by(distance_expr.asc()).limit(limit)

        result = await self.db.execute(stmt)
        rows = result.all()

        return [(row[0], float(row[1])) for row in rows]

    async def full_text_search(
        self,
        query_text: str,
        user_id: uuid.UUID,
        document_id: Optional[uuid.UUID] = None,
        limit: int = 20,
    ) -> List[Tuple[DocumentChunk, float]]:
        """
        PostgreSQL Full-Text Search over document chunks using the stored
        content_tsv column (GIN-indexed) and websearch_to_tsquery.
        """
        if not query_text.strip():
            return []

        tsquery = func.websearch_to_tsquery("english", query_text)
        rank_expr = func.ts_rank_cd(DocumentChunk.content_tsv, tsquery)

        stmt = (
            select(DocumentChunk, rank_expr.label("rank"))
            .where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.content_tsv.op("@@")(tsquery),
            )
        )

        if document_id is not None:
            stmt = stmt.where(DocumentChunk.document_id == document_id)

        stmt = stmt.order_by(rank_expr.desc()).limit(limit)

        result = await self.db.execute(stmt)
        rows = result.all()

        return [(row[0], float(row[1])) for row in rows]

    async def hybrid_search(
        self,
        query_vector: List[float],
        query_text: str,
        user_id: uuid.UUID,
        document_id: Optional[uuid.UUID] = None,
        limit: int = 5,
        alpha: float = 0.5,
        k: int = 60,
    ) -> List[Tuple[DocumentChunk, float]]:
        """
        Perform Hybrid Search combining Dense Vector Search and Full-Text Search
        using Reciprocal Rank Fusion (RRF).
        """
        candidate_limit = max(limit * 3, 30)

        vector_results = await self.vector_search(
            query_vector=query_vector,
            user_id=user_id,
            document_id=document_id,
            limit=candidate_limit,
        )

        text_results = await self.full_text_search(
            query_text=query_text,
            user_id=user_id,
            document_id=document_id,
            limit=candidate_limit,
        )

        rrf_scores: dict[uuid.UUID, float] = {}
        chunk_map: dict[uuid.UUID, DocumentChunk] = {}

        for rank, (chunk, _) in enumerate(vector_results, start=1):
            chunk_map[chunk.id] = chunk
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + (alpha / (k + rank))

        for rank, (chunk, _) in enumerate(text_results, start=1):
            chunk_map[chunk.id] = chunk
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + ((1.0 - alpha) / (k + rank))

        sorted_chunks = sorted(
            rrf_scores.items(), key=lambda item: item[1], reverse=True
        )[:limit]

        return [(chunk_map[chunk_id], score) for chunk_id, score in sorted_chunks]

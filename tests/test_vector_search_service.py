import uuid
from unittest.mock import AsyncMock
import pytest

from app.modules.documents.models.document_chunk_model import DocumentChunk
from app.modules.rag.services.vector_search_service import VectorSearchService


@pytest.fixture
def mock_chunk():
    return DocumentChunk(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        chunk_index=0,
        page_number=5,
        section_title="Chapter 1: Deep Learning",
        content="Neural networks and hybrid search.",
        token_count=10,
        embedding=[0.1] * 768,
    )


@pytest.mark.asyncio
async def test_search_empty_query():
    repo = AsyncMock()
    embedding_svc = AsyncMock()
    service = VectorSearchService(chunk_repository=repo, embedding_service=embedding_svc)

    results = await service.search(query="   ", user_id=uuid.uuid4())
    assert results == []


@pytest.mark.asyncio
async def test_search_hybrid_mode(mock_chunk):
    user_id = mock_chunk.user_id
    repo = AsyncMock()
    repo.hybrid_search.return_value = [(mock_chunk, 0.032)]

    embedding_svc = AsyncMock()
    embedding_svc.generate_embeddings.return_value = [[0.1] * 768]

    service = VectorSearchService(chunk_repository=repo, embedding_service=embedding_svc)

    results = await service.search(
        query="hybrid search test",
        user_id=user_id,
        mode="hybrid",
        limit=5,
    )

    assert len(results) == 1
    assert results[0].chunk_id == mock_chunk.id
    assert results[0].score == 0.032
    repo.hybrid_search.assert_called_once_with(
        query_vector=[0.1] * 768,
        query_text="hybrid search test",
        user_id=user_id,
        document_id=None,
        limit=5,
        alpha=0.5,
    )


@pytest.mark.asyncio
async def test_search_vector_mode(mock_chunk):
    user_id = mock_chunk.user_id
    repo = AsyncMock()
    repo.vector_search.return_value = [(mock_chunk, 0.2)]

    embedding_svc = AsyncMock()
    embedding_svc.generate_embeddings.return_value = [[0.1] * 768]

    service = VectorSearchService(chunk_repository=repo, embedding_service=embedding_svc)

    results = await service.search(
        query="dense search test",
        user_id=user_id,
        mode="vector",
        limit=3,
    )

    assert len(results) == 1
    assert pytest.approx(results[0].score) == 0.8
    repo.vector_search.assert_called_once_with(
        query_vector=[0.1] * 768,
        user_id=user_id,
        document_id=None,
        limit=3,
    )


@pytest.mark.asyncio
async def test_search_text_mode(mock_chunk):
    user_id = mock_chunk.user_id
    repo = AsyncMock()
    repo.full_text_search.return_value = [(mock_chunk, 0.95)]

    embedding_svc = AsyncMock()
    service = VectorSearchService(chunk_repository=repo, embedding_service=embedding_svc)

    results = await service.search(
        query="keyword search test",
        user_id=user_id,
        mode="text",
        limit=4,
    )

    assert len(results) == 1
    assert results[0].score == 0.95
    repo.full_text_search.assert_called_once_with(
        query_text="keyword search test",
        user_id=user_id,
        document_id=None,
        limit=4,
    )
    embedding_svc.generate_embeddings.assert_not_called()

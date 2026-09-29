import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models.document_chunk_model import DocumentChunk
from app.modules.documents.models.document_model import Document
from app.modules.user.models.user_model import User
from app.modules.user.repositories.user_repository import UserRepository

# 768-dim unit vector along the first axis (matches _E0 planting below)
_QUERY_VECTOR = "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"


async def _explain(db: AsyncSession, sql: str, params: dict | None = None) -> str:
    result = await db.execute(text(sql), params or {})
    return "\n".join(str(row[0]) for row in result)


async def _plant_chunk(db: AsyncSession) -> DocumentChunk:
    user = await UserRepository(db).create(User(email=f"idx-{uuid.uuid4()}@ex.com"))
    doc = Document(
        user_id=user.id,
        title="indexed",
        original_filename="indexed.pdf",
        file_size=1,
        file_type="application/pdf",
        storage_key=f"{user.id}/indexed.pdf",
        status="processed",
    )
    db.add(doc)
    await db.flush()
    vector = [0.0] * 768
    vector[0] = 1.0
    chunk = DocumentChunk(
        document_id=doc.id,
        user_id=user.id,
        chunk_index=0,
        content="apple revenue growth analysis",
        token_count=6,
        embedding=vector,
    )
    db.add(chunk)
    await db.flush()
    return chunk


@pytest.mark.asyncio
async def test_hnsw_index_exists_and_serves_vector_ordering(db_session: AsyncSession):
    await _plant_chunk(db_session)
    # Empty tables make the planner prefer seq scans; force index consideration
    await db_session.execute(text("SET enable_seqscan = off"))

    plan = await _explain(
        db_session,
        """
        EXPLAIN
        SELECT id FROM document_chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:q AS vector)
        LIMIT 5
        """,
        {"q": _QUERY_VECTOR},
    )

    assert "ix_document_chunks_embedding_hnsw" in plan
    assert "Seq Scan" not in plan


@pytest.mark.asyncio
async def test_gin_index_serves_stored_tsvector_queries(db_session: AsyncSession):
    await _plant_chunk(db_session)
    await db_session.execute(text("SET enable_seqscan = off"))

    plan = await _explain(
        db_session,
        """
        EXPLAIN
        SELECT id FROM document_chunks
        WHERE content_tsv @@ to_tsquery('english', 'apple & revenue')
        """,
    )

    assert "ix_document_chunks_content_tsv" in plan
    assert "Seq Scan" not in plan


@pytest.mark.asyncio
async def test_stored_tsvector_is_generated_from_content(db_session: AsyncSession):
    chunk = await _plant_chunk(db_session)
    row = (
        await db_session.execute(
            text("SELECT content_tsv @@ to_tsquery('english', 'apple & revenue') "
                 "FROM document_chunks WHERE id = :id"),
            {"id": chunk.id},
        )
    ).scalar_one()
    assert row is True

    # Non-matching content proves the stored column tracks `content`
    row = (
        await db_session.execute(
            text("SELECT content_tsv @@ to_tsquery('english', 'zebra') "
                 "FROM document_chunks WHERE id = :id"),
            {"id": chunk.id},
        )
    ).scalar_one()
    assert row is False

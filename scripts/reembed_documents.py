"""Re-enqueue documents for parsing and re-embedding (MNE-18).

Resets each selected document's status to "uploaded" (so the worker does not
skip it as already processed), commits, then publishes a document.uploaded
event per document. Safe to re-run.

Usage (from the repo root):
    uv run python -m scripts.reembed_documents
    uv run python -m scripts.reembed_documents --document-id <uuid>
    uv run python -m scripts.reembed_documents --user-id <uuid>
"""
import argparse
import uuid
from collections.abc import Awaitable, Callable
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.db import AsyncSessionLocal
from app.core.queue.events import (
    DOCUMENT_PROCESSING_QUEUE_NAME,
    DocumentEvent,
    DocumentEventType,
)
from app.core.queue.queue import rabbitmq
from app.modules.documents.models.document_model import Document
from app.modules.documents.models.document_status import DocumentStatus, transition

Publisher = Callable[[str, dict], Awaitable[None]]


async def collect_documents(
    session: AsyncSession,
    document_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
) -> list[Document]:
    stmt = select(Document)
    if document_id is not None:
        stmt = stmt.where(Document.id == document_id)
    if user_id is not None:
        stmt = stmt.where(Document.user_id == user_id)
    stmt = stmt.order_by(Document.created_at)
    return list((await session.execute(stmt)).scalars())


async def requeue_documents(
    session: AsyncSession,
    publisher: Publisher,
    document_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
) -> int:
    """Reset matching documents to "uploaded" and publish processing events.

    The status update is committed *before* publishing: the worker reads the
    status from the DB and skips documents that are still "processed".
    """
    documents = await collect_documents(session, document_id, user_id)
    for document in documents:
        # Validates the move through the shared lifecycle map (MNE-22);
        # documents already "uploaded" are a no-op (already queued).
        transition(document, DocumentStatus.UPLOADED)
    await session.commit()

    for document in documents:
        event = DocumentEvent(
            event_type=DocumentEventType.DOCUMENT_UPLOADED,
            document_id=str(document.id),
            user_id=str(document.user_id),
            storage_key=document.storage_key,
        )
        await publisher(DOCUMENT_PROCESSING_QUEUE_NAME, event.to_dict())
    return len(documents)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-enqueue documents so they are re-parsed and re-embedded"
    )
    parser.add_argument("--document-id", type=uuid.UUID, default=None)
    parser.add_argument("--user-id", type=uuid.UUID, default=None)
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        count = await requeue_documents(
            session, rabbitmq.publish, args.document_id, args.user_id
        )
    await rabbitmq.close()
    print(f"Requeued {count} document(s) for re-processing")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

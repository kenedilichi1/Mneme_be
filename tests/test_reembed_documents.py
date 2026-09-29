import uuid

import pytest
from sqlalchemy import select

from app.core.queue.events import DOCUMENT_PROCESSING_QUEUE_NAME
from app.modules.documents.models.document_model import Document
from app.modules.user.models.user_model import User
from app.modules.user.repositories.user_repository import UserRepository
from scripts.reembed_documents import requeue_documents


async def _plant_document(db_session, user: User, status: str) -> Document:
    doc = Document(
        user_id=user.id,
        title="doc",
        original_filename="doc.pdf",
        file_size=1,
        file_type="application/pdf",
        storage_key=f"{user.id}/doc.pdf",
        status=status,
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


@pytest.mark.asyncio
async def test_requeue_resets_status_and_publishes_events(db_session):
    user = await UserRepository(db_session).create(User(email="reembed@example.com"))
    target = await _plant_document(db_session, user, status="processed")
    other_user = await UserRepository(db_session).create(
        User(email="reembed-other@example.com")
    )
    untouched = await _plant_document(db_session, other_user, status="processed")

    published: list[tuple[str, dict]] = []

    async def fake_publisher(queue_name: str, message: dict) -> None:
        published.append((queue_name, message))

    count = await requeue_documents(db_session, fake_publisher, user_id=user.id)

    assert count == 1
    assert published == [
        (
            DOCUMENT_PROCESSING_QUEUE_NAME,
            {
                "event_type": "document.uploaded",
                "document_id": str(target.id),
                "user_id": str(user.id),
                "storage_key": target.storage_key,
            },
        )
    ]

    statuses = {
        doc.id: doc.status
        for doc in (await db_session.execute(select(Document))).scalars()
    }
    assert statuses[target.id] == "uploaded"
    assert statuses[untouched.id] == "processed"


@pytest.mark.asyncio
async def test_requeue_single_document_by_id(db_session):
    user = await UserRepository(db_session).create(User(email="reembed-one@example.com"))
    first = await _plant_document(db_session, user, status="processed")
    second = await _plant_document(db_session, user, status="processed")

    published: list[dict] = []

    async def fake_publisher(queue_name: str, message: dict) -> None:
        published.append(message)

    count = await requeue_documents(
        db_session, fake_publisher, document_id=uuid.UUID(str(first.id))
    )

    assert count == 1
    assert published[0]["document_id"] == str(first.id)
    await db_session.refresh(first)
    await db_session.refresh(second)
    assert first.status == "uploaded"
    assert second.status == "processed"

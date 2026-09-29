import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.documents.models.document_model import Document
from app.modules.documents.models.document_status import (
    ALLOWED_TRANSITIONS,
    DocumentStatus,
    InvalidStatusTransition,
    transition,
)
from app.modules.user.models.user_model import User
from app.modules.user.repositories.user_repository import UserRepository


def make_document(status: str) -> Document:
    return Document(
        user_id=uuid.uuid4(),
        title="doc",
        original_filename="doc.pdf",
        file_size=1,
        file_type="application/pdf",
        storage_key="k",
        status=status,
    )


def test_enum_covers_every_status():
    assert {s.value for s in DocumentStatus} == {
        "pending_upload",
        "uploaded",
        "processing",
        "processed",
        "failed",
    }
    # Every status has a transition entry (and no unknown keys)
    assert set(ALLOWED_TRANSITIONS) == set(DocumentStatus)


@pytest.mark.parametrize(
    "source, target",
    [
        ("pending_upload", "uploaded"),
        ("pending_upload", "failed"),
        ("uploaded", "processing"),
        ("processing", "processed"),
        ("processing", "uploaded"),
        ("processing", "failed"),
        ("processed", "uploaded"),
        ("failed", "uploaded"),
    ],
)
def test_allowed_transitions(source, target):
    doc = make_document(source)
    transition(doc, DocumentStatus(target))
    assert doc.status == target


@pytest.mark.parametrize(
    "source, target",
    [
        ("processed", "processing"),
        ("processed", "failed"),
        ("pending_upload", "processing"),
        ("pending_upload", "processed"),
        ("uploaded", "failed"),
        ("failed", "processed"),
        ("failed", "processing"),
    ],
)
def test_forbidden_transitions_raise(source, target):
    doc = make_document(source)
    with pytest.raises(InvalidStatusTransition):
        transition(doc, DocumentStatus(target))
    assert doc.status == source  # unchanged


def test_self_transition_is_noop():
    doc = make_document("processing")
    transition(doc, DocumentStatus.PROCESSING)
    transition(doc, "processing")
    assert doc.status == "processing"


@pytest.mark.asyncio
async def test_db_check_constraint_rejects_unknown_status(db_session):
    user = await UserRepository(db_session).create(User(email="status-check@example.com"))
    db_session.add(
        Document(
            user_id=user.id,
            title="bogus",
            original_filename="bogus.pdf",
            file_size=1,
            file_type="application/pdf",
            storage_key="k",
            status="not_a_status",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

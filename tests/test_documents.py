import json
import pytest
from pydantic import ValidationError

from app.core.queue.events import (
    DOCUMENT_PROCESSING_QUEUE_NAME,
    DocumentEvent,
    DocumentEventType,
)
from app.modules.documents.schemas.document_schema import (
    AllowedFileType,
    DocumentCreate,
    MIME_TO_EXTENSION,
)


def test_document_create_valid_pdf():
    payload = DocumentCreate(
        title="Test Book",
        author="John Doe",
        original_filename="book.pdf",
        file_size=1024,
        file_type=AllowedFileType.PDF,
    )
    assert payload.file_type == AllowedFileType.PDF
    assert MIME_TO_EXTENSION[payload.file_type.value] == "pdf"


def test_document_create_valid_epub():
    payload = DocumentCreate(
        title="Test Book",
        author=None,
        original_filename="book.epub",
        file_size=2048,
        file_type=AllowedFileType.EPUB,
    )
    assert payload.file_type == AllowedFileType.EPUB
    assert MIME_TO_EXTENSION[payload.file_type.value] == "epub"


def test_document_create_rejects_unsupported_file_type():
    with pytest.raises(ValidationError):
        DocumentCreate(
            title="Malicious file",
            original_filename="test.sh",
            file_size=100,
            file_type="application/x-sh",
        )


def test_document_event_json_serialization():
    event = DocumentEvent(
        event_type=DocumentEventType.DOCUMENT_UPLOADED,
        document_id="doc-123",
        user_id="user-456",
        storage_key="user-456/doc-123.pdf",
    )
    event_dict = event.to_dict()
    serialized = json.dumps(event_dict)
    deserialized = json.loads(serialized)

    assert deserialized["event_type"] == "document.uploaded"
    assert deserialized["document_id"] == "doc-123"
    assert deserialized["storage_key"] == "user-456/doc-123.pdf"

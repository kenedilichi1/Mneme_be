import uuid
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.storage.b2_storage import B2Storage, _region_from_endpoint
from app.modules.documents.models.document_model import Document
from app.modules.documents.schemas.document_schema import AllowedFileType, DocumentCreate
from app.modules.documents.services.document_service import DocumentService


def make_document(status: str = "pending_upload") -> Document:
    return Document(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="Book",
        original_filename="book.pdf",
        file_size=1000,
        file_type="application/pdf",
        storage_key="u/book.pdf",
        status=status,
    )


def make_service(document: Document | None = None, file_size: int | None = 1234):
    calls: list[str] = []

    repo = MagicMock()
    repo.create = AsyncMock(side_effect=lambda d: d)
    repo.get_by_id = AsyncMock(return_value=document)
    repo.update = AsyncMock(side_effect=lambda d: d)
    repo.commit = AsyncMock(side_effect=lambda: calls.append("commit"))
    repo.delete = AsyncMock(side_effect=lambda d: calls.append("delete_row"))

    storage = MagicMock()
    storage.generate_upload_url.return_value = "https://upload.example"
    storage.get_file_size.return_value = file_size
    storage.delete_file = MagicMock(side_effect=lambda key: calls.append("delete_file"))

    rabbitmq = MagicMock()
    rabbitmq.publish = AsyncMock(side_effect=lambda *a: calls.append("publish"))

    return DocumentService(repo, storage, rabbitmq), repo, storage, rabbitmq, calls


async def test_create_rejects_file_over_limit():
    service, repo, *_ = make_service()
    payload = DocumentCreate(
        title="Big",
        original_filename="big.pdf",
        file_size=settings.MAX_UPLOAD_BYTES + 1,
        file_type=AllowedFileType.PDF,
    )
    with pytest.raises(HTTPException) as exc:
        await service.create_document(uuid.uuid4(), payload)
    assert exc.value.status_code == 413
    repo.create.assert_not_called()


async def test_create_signs_url_for_key_and_content_type():
    service, _, storage, *_ = make_service()
    payload = DocumentCreate(
        title="Book",
        original_filename="book.pdf",
        file_size=100,
        file_type=AllowedFileType.PDF,
    )
    document, url = await service.create_document(uuid.uuid4(), payload)
    assert url == "https://upload.example"
    storage.generate_upload_url.assert_called_once_with(
        document.storage_key, "application/pdf"
    )


async def test_confirm_rejects_missing_file():
    service, _, _, rabbitmq, _ = make_service(make_document(), file_size=None)
    with pytest.raises(HTTPException) as exc:
        await service.confirm_upload(uuid.uuid4(), uuid.uuid4())
    assert exc.value.status_code == 400
    rabbitmq.publish.assert_not_called()


async def test_confirm_rejects_oversized_file_and_marks_failed():
    document = make_document()
    service, repo, storage, rabbitmq, _ = make_service(
        document, file_size=settings.MAX_UPLOAD_BYTES + 1
    )
    with pytest.raises(HTTPException) as exc:
        await service.confirm_upload(document.id, document.user_id)
    assert exc.value.status_code == 413
    storage.delete_file.assert_called_once_with(document.storage_key)
    assert document.status == "failed"
    repo.commit.assert_awaited_once()
    rabbitmq.publish.assert_not_called()


async def test_confirm_uses_real_size_and_commits_before_publishing():
    document = make_document()
    service, _, _, _, calls = make_service(document, file_size=4321)
    result = await service.confirm_upload(document.id, document.user_id)
    assert result.status == "uploaded"
    assert result.file_size == 4321
    assert calls == ["commit", "publish"]


async def test_confirm_publish_failure_returns_503_and_can_be_retried():
    document = make_document()
    service, _, storage, rabbitmq, _ = make_service(document)
    rabbitmq.publish.side_effect = ConnectionError("broker down")

    with pytest.raises(HTTPException) as exc:
        await service.confirm_upload(document.id, document.user_id)
    assert exc.value.status_code == 503
    assert document.status == "uploaded"

    # Retry re-publishes without re-checking storage
    rabbitmq.publish.side_effect = None
    storage.get_file_size.reset_mock()
    await service.confirm_upload(document.id, document.user_id)
    rabbitmq.publish.assert_awaited()
    storage.get_file_size.assert_not_called()


async def test_confirm_rejects_already_processing_document():
    service, *_ = make_service(make_document(status="processing"))
    with pytest.raises(HTTPException) as exc:
        await service.confirm_upload(uuid.uuid4(), uuid.uuid4())
    assert exc.value.status_code == 400


async def test_delete_commits_row_before_touching_storage():
    document = make_document()
    service, repo, storage, _, calls = make_service(document)

    await service.delete_document(document.id, document.user_id)

    # MNE-21: row removed and committed first; the file goes last
    assert calls == ["delete_row", "commit", "delete_file"]
    repo.delete.assert_awaited_once_with(document)
    storage.delete_file.assert_called_once_with(document.storage_key)


async def test_delete_succeeds_even_if_storage_delete_fails():
    document = make_document()
    service, repo, storage, *_ = make_service(document)
    storage.delete_file.side_effect = ConnectionError("b2 down")

    # The row is already gone; an orphaned file must not fail the request
    await service.delete_document(document.id, document.user_id)

    repo.delete.assert_awaited_once()
    repo.commit.assert_awaited_once()


async def test_delete_missing_document_is_404():
    service, repo, storage, *_ = make_service(None)
    with pytest.raises(HTTPException) as exc:
        await service.delete_document(uuid.uuid4(), uuid.uuid4())
    assert exc.value.status_code == 404
    repo.delete.assert_not_called()
    storage.delete_file.assert_not_called()


def test_region_from_endpoint():
    assert _region_from_endpoint("https://s3.us-west-004.backblazeb2.com") == "us-west-004"
    # Scheme-less endpoints are tolerated (MNE-28)
    assert _region_from_endpoint("s3.us-west-004.backblazeb2.com") == "us-west-004"
    with pytest.raises(ValueError):
        _region_from_endpoint("https://example.com")
    with pytest.raises(ValueError):
        _region_from_endpoint("example.com")


def test_presigned_url_is_scoped_to_key_and_content_type():
    url = B2Storage().generate_upload_url("user/doc.pdf", "application/pdf", 900)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.path.endswith("/user/doc.pdf")
    assert query["X-Amz-Expires"] == ["900"]
    assert "content-type" in query["X-Amz-SignedHeaders"][0]

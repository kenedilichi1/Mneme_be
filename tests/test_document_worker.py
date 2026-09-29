import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.queue.events import DocumentEvent, DocumentEventType
from app.modules.documents.models.document_model import Document
from app.modules.documents.services.docling_parser import ParsedChunk, ParsedDocument
from app.worker import document_worker
from app.worker.document_worker import (
    MAX_ATTEMPTS,
    handle_message,
    process_document,
)


@pytest.mark.asyncio
async def test_process_document_success():
    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()

    event = DocumentEvent(
        event_type=DocumentEventType.DOCUMENT_UPLOADED,
        document_id=str(doc_id),
        user_id=str(user_id),
        storage_key=f"{user_id}/{doc_id}.pdf",
    )

    mock_doc = Document(
        id=doc_id,
        user_id=user_id,
        title="Sample Book",
        author="Author",
        original_filename="sample.pdf",
        file_size=1024,
        file_type="application/pdf",
        storage_key=f"{user_id}/db-key.pdf",
        status="uploaded",
    )

    mock_parsed_doc = ParsedDocument(
        title="Parsed Title",
        author="Author",
        page_count=5,
        file_type="application/pdf",
        text_content="Sample text content",
        chunks=[
            ParsedChunk(
                chunk_index=0,
                content="Chunk 1 text",
                page_number=1,
                section_title="Chapter 1",
                token_count=10,
            ),
            ParsedChunk(
                chunk_index=1,
                content="Chunk 2 text",
                page_number=2,
                section_title="Chapter 2",
                token_count=10,
            ),
        ],
    )

    mock_embeddings = [[0.1] * 768, [0.2] * 768]

    mock_session = AsyncMock()

    mock_doc_repo = AsyncMock()
    mock_doc_repo.get_by_id.return_value = mock_doc
    mock_doc_repo.update.return_value = mock_doc

    mock_chunk_repo = AsyncMock()
    mock_chunk_repo.create_many.return_value = []

    with patch(
        "app.worker.document_worker.AsyncSessionLocal",
        return_value=mock_session,
    ), patch(
        "app.worker.document_worker.DocumentRepository",
        return_value=mock_doc_repo,
    ), patch(
        "app.worker.document_worker.DocumentChunkRepository",
        return_value=mock_chunk_repo,
    ), patch(
        "app.worker.document_worker.storage_service.download_file_to_path"
    ) as mock_download, patch(
        "app.worker.document_worker.docling_parser.parse_document",
        return_value=mock_parsed_doc,
    ), patch(
        "app.worker.document_worker.embedding_service.generate_embeddings",
        new_callable=AsyncMock,
        return_value=mock_embeddings,
    ) as mock_gen_embeds:
        await process_document(event)

        mock_doc_repo.get_by_id.assert_called_once_with(doc_id, user_id)
        mock_download.assert_called_once()
        assert mock_download.call_args[0][0] == f"{user_id}/db-key.pdf"
        mock_gen_embeds.assert_called_once_with(["Chunk 1 text", "Chunk 2 text"])
        mock_chunk_repo.create_many.assert_called_once()

        created_chunks = mock_chunk_repo.create_many.call_args[0][0]
        assert len(created_chunks) == 2
        assert created_chunks[0].content == "Chunk 1 text"
        assert created_chunks[0].embedding == [0.1] * 768
        assert created_chunks[1].content == "Chunk 2 text"
        assert created_chunks[1].embedding == [0.2] * 768

        assert mock_doc.status == "processed"
        assert mock_doc.page_count == 5


def make_event() -> DocumentEvent:
    user_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    return DocumentEvent(
        event_type=DocumentEventType.DOCUMENT_UPLOADED,
        document_id=str(doc_id),
        user_id=str(user_id),
        storage_key=f"{user_id}/{doc_id}.pdf",
    )


def make_message(event: DocumentEvent | None, attempt: int | None = None, redelivered=False):
    import json

    message = MagicMock()
    message.body = (
        json.dumps(event.to_dict()).encode() if event else b"not json"
    )
    message.headers = {"x-attempt": attempt} if attempt else {}
    message.content_type = "application/json"
    message.redelivered = redelivered
    message.ack = AsyncMock()
    return message


@pytest.fixture
def broker():
    """Captures (routing_key, headers) of everything the worker publishes."""
    published: list[tuple[str, dict]] = []

    async def publish(msg, routing_key):
        published.append((routing_key, msg.headers))

    channel = MagicMock()
    channel.default_exchange.publish = AsyncMock(side_effect=publish)
    with patch.object(document_worker.rabbitmq, "channel", channel):
        yield published


@pytest.fixture
def statuses():
    recorded: list[str] = []

    async def fake_set_status(event, status):
        recorded.append(status)

    with patch.object(document_worker, "set_document_status", side_effect=fake_set_status):
        yield recorded


async def test_success_acks(broker, statuses):
    message = make_message(make_event())
    with patch.object(document_worker, "process_document", new_callable=AsyncMock):
        await handle_message(message)
    message.ack.assert_awaited_once()
    assert broker == []


async def test_transient_error_schedules_retry(broker, statuses):
    message = make_message(make_event())
    with patch.object(
        document_worker, "process_document", AsyncMock(side_effect=ConnectionError("db down"))
    ):
        await handle_message(message)

    assert broker[0][0].endswith(".retry.1")
    assert broker[0][1]["x-attempt"] == 2
    assert statuses == ["uploaded"]
    message.ack.assert_awaited_once()


async def test_retry_attempt_uses_next_delay_queue(broker, statuses):
    message = make_message(make_event(), attempt=2)
    with patch.object(
        document_worker, "process_document", AsyncMock(side_effect=TimeoutError())
    ):
        await handle_message(message)
    assert broker[0][0].endswith(".retry.2")
    assert broker[0][1]["x-attempt"] == 3


async def test_last_attempt_dead_letters_and_fails(broker, statuses):
    message = make_message(make_event(), attempt=MAX_ATTEMPTS)
    with patch.object(
        document_worker, "process_document", AsyncMock(side_effect=ConnectionError("still down"))
    ):
        await handle_message(message)

    assert broker[0][0].endswith(".dead")
    assert "still down" in broker[0][1]["x-failure-reason"]
    assert statuses == ["failed"]
    message.ack.assert_awaited_once()


async def test_permanent_error_fails_without_retry(broker, statuses):
    from app.modules.documents.services.docling_parser import UnsupportedDocumentTypeError

    message = make_message(make_event())
    with patch.object(
        document_worker,
        "process_document",
        AsyncMock(side_effect=UnsupportedDocumentTypeError("bad")),
    ):
        await handle_message(message)

    assert broker == []
    assert statuses == ["failed"]
    message.ack.assert_awaited_once()


async def test_missing_file_in_storage_is_permanent(broker, statuses):
    from botocore.exceptions import ClientError

    error = ClientError({"Error": {"Code": "404"}}, "GetObject")
    message = make_message(make_event())
    with patch.object(document_worker, "process_document", AsyncMock(side_effect=error)):
        await handle_message(message)
    assert broker == []
    assert statuses == ["failed"]


async def test_redelivery_counts_as_attempt_without_reprocessing(broker, statuses):
    message = make_message(make_event(), redelivered=True)
    process = AsyncMock()
    with patch.object(document_worker, "process_document", process):
        await handle_message(message)

    process.assert_not_called()
    assert broker[0][0].endswith(".retry.1")
    message.ack.assert_awaited_once()


async def test_crash_loop_is_capped(broker, statuses):
    message = make_message(make_event(), attempt=MAX_ATTEMPTS, redelivered=True)
    with patch.object(document_worker, "process_document", AsyncMock()):
        await handle_message(message)
    assert broker[0][0].endswith(".dead")
    assert statuses == ["failed"]


async def test_malformed_message_is_dead_lettered(broker, statuses):
    message = make_message(None)
    await handle_message(message)
    assert broker[0][0].endswith(".dead")
    assert statuses == []
    message.ack.assert_awaited_once()


async def test_db_error_rolls_back_and_propagates():
    event = make_event()
    doc = Document(
        id=uuid.UUID(event.document_id),
        user_id=uuid.UUID(event.user_id),
        title="t",
        original_filename="a.pdf",
        file_size=1,
        file_type="application/pdf",
        storage_key=event.storage_key,
        status="uploaded",
    )
    parsed = ParsedDocument(
        file_type="application/pdf",
        text_content="x",
        chunks=[ParsedChunk(chunk_index=0, content="x", token_count=1)],
    )
    session = AsyncMock()
    session.__aenter__.return_value = session
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    chunk_repo = AsyncMock()
    chunk_repo.create_many.side_effect = RuntimeError("flush failed")

    with patch.object(document_worker, "AsyncSessionLocal", return_value=session), patch.object(
        document_worker, "DocumentRepository", return_value=doc_repo
    ), patch.object(
        document_worker, "DocumentChunkRepository", return_value=chunk_repo
    ), patch.object(
        document_worker.storage_service, "download_file_to_path"
    ), patch.object(
        document_worker.docling_parser, "parse_document", return_value=parsed
    ), patch.object(
        document_worker.embedding_service,
        "generate_embeddings",
        AsyncMock(return_value=[[0.1] * 768]),
    ):
        with pytest.raises(RuntimeError):
            await process_document(event)

    session.rollback.assert_awaited_once()


async def test_already_processed_document_is_skipped():
    event = make_event()
    doc = MagicMock(status="processed")
    session = AsyncMock()
    session.__aenter__.return_value = session
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    with patch.object(document_worker, "AsyncSessionLocal", return_value=session), patch.object(
        document_worker, "DocumentRepository", return_value=doc_repo
    ), patch.object(document_worker.storage_service, "download_file_to_path") as download:
        await process_document(event)
    download.assert_not_called()
    assert doc.status == "processed"


async def test_document_deleted_mid_processing_stops_cleanly(broker, statuses):
    message = make_message(make_event())
    with patch.object(
        document_worker,
        "process_document",
        AsyncMock(side_effect=document_worker.DocumentDeletedError("row gone")),
    ):
        await handle_message(message)

    # Ack without retry or dead-letter, and without touching a status that
    # no longer exists (MNE-21)
    message.ack.assert_awaited_once()
    assert broker == []
    assert statuses == []


async def test_vanished_row_during_initial_mark_raises_deleted_error():
    from sqlalchemy.orm.exc import NoResultFound

    event = make_event()
    doc = Document(
        id=uuid.UUID(event.document_id),
        user_id=uuid.UUID(event.user_id),
        title="t",
        original_filename="a.pdf",
        file_size=1,
        file_type="application/pdf",
        storage_key=event.storage_key,
        status="uploaded",
    )
    session = AsyncMock()
    session.__aenter__.return_value = session
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    doc_repo.update.side_effect = NoResultFound("row is gone")

    with patch.object(document_worker, "AsyncSessionLocal", return_value=session), patch.object(
        document_worker, "DocumentRepository", return_value=doc_repo
    ), patch.object(document_worker, "DocumentChunkRepository", AsyncMock()):
        with pytest.raises(document_worker.DocumentDeletedError):
            await process_document(event)

    session.rollback.assert_awaited()

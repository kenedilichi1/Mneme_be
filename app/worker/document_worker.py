import asyncio
import json
import logging
import uuid
from pathlib import Path

import aiofiles.os
import aiofiles.tempfile
import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from botocore.exceptions import ClientError
from docling.exceptions import ConversionError, DocumentLoadError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound, StaleDataError

from app.core.config import settings
from app.core.db.db import AsyncSessionLocal
from app.core.queue.events import (
    DOCUMENT_PROCESSING_QUEUE_NAME,
    DocumentEvent,
    DocumentEventType,
)
from app.core.queue.queue import (
    RETRY_DELAYS_SECONDS,
    dead_letter_queue_name,
    declare_processing_queues,
    rabbitmq,
    retry_queue_name,
)
from app.core.storage.storage_service import storage_service
from app.modules.documents.models.document_chunk_model import DocumentChunk
from app.modules.documents.models.document_status import DocumentStatus, transition
from app.modules.documents.repositories.document_chunk_repository import (
    DocumentChunkRepository,
)
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.documents.services.docling_parser import (
    DoclingParserService,
    UnsupportedDocumentTypeError,
)
from app.modules.documents.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

QUEUE_NAME = DOCUMENT_PROCESSING_QUEUE_NAME
ATTEMPT_HEADER = "x-attempt"
MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS) + 1

# Retrying these can't succeed, so the document fails immediately
PERMANENT_ERRORS = (UnsupportedDocumentTypeError, ConversionError, DocumentLoadError)

docling_parser = DoclingParserService()
embedding_service = EmbeddingService()


class MalformedMessageError(ValueError):
    pass


class DocumentDeletedError(Exception):
    """The document row vanished while it was being processed (MNE-21).

    Raised when a refresh or chunk insert finds the document (or user) gone —
    the user deleted it mid-processing. Handled as a clean stop: ack, no
    retry, no status write.
    """


def is_permanent_error(exc: BaseException) -> bool:
    if isinstance(exc, PERMANENT_ERRORS):
        return True
    # File missing from storage
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")
    return False


def parse_event(body: bytes) -> DocumentEvent:
    try:
        data = json.loads(body.decode("utf-8"))
        event = DocumentEvent(
            event_type=DocumentEventType(data["event_type"]),
            document_id=data["document_id"],
            user_id=data["user_id"],
            storage_key=data["storage_key"],
        )
        uuid.UUID(event.document_id)
        uuid.UUID(event.user_id)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise MalformedMessageError(str(exc)) from exc
    return event


async def process_document(event: DocumentEvent) -> None:
    """Parse, embed and store a document. Raises on failure; the caller decides whether to retry."""
    doc_id = uuid.UUID(event.document_id)
    usr_id = uuid.UUID(event.user_id)

    logger.info("Starting processing for document %s (user: %s)", doc_id, usr_id)

    async with AsyncSessionLocal() as session:
        doc_repo = DocumentRepository(session)
        chunk_repo = DocumentChunkRepository(session)

        document = await doc_repo.get_by_id(doc_id, usr_id)
        if not document:
            logger.warning("Document %s not found in database; skipping", doc_id)
            return
        if document.status == DocumentStatus.PROCESSED.value:
            logger.info("Document %s already processed; skipping duplicate message", document.id)
            return

        try:
            transition(document, DocumentStatus.PROCESSING)
            await doc_repo.update(document)
            await session.commit()
        except (NoResultFound, StaleDataError, IntegrityError) as exc:
            # Deleted between the lookup above and this write (MNE-21)
            await session.rollback()
            raise DocumentDeletedError(
                f"document {event.document_id} was deleted mid-processing"
            ) from exc

        ext = Path(document.original_filename).suffix or (
            ".pdf" if document.file_type == "application/pdf" else ".epub"
        )

        temp_path: Path | None = None
        async with aiofiles.tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_file:
            temp_path = Path(tmp_file.name)

        try:
            # 1. Download file from storage (the DB row is the source of truth, not the message)
            await asyncio.to_thread(
                storage_service.download_file_to_path,
                document.storage_key,
                str(temp_path),
            )

            # 2. Parse document with Docling (PDF and EPUB supported)
            parsed_doc = await asyncio.to_thread(
                docling_parser.parse_document,
                temp_path,
                document.file_type,
            )

            # 3. Save metadata updates
            if parsed_doc.page_count is not None:
                document.page_count = parsed_doc.page_count

            # 4. Generate embeddings and save document chunks
            if parsed_doc.chunks:
                # Remove existing chunks if re-processing
                await chunk_repo.delete_by_document_id(document.id)

                chunk_texts = [chunk.content for chunk in parsed_doc.chunks]
                embeddings = await embedding_service.generate_embeddings(chunk_texts)

                db_chunks = [
                    DocumentChunk(
                        document_id=document.id,
                        user_id=document.user_id,
                        chunk_index=chunk.chunk_index,
                        page_number=chunk.page_number,
                        section_title=chunk.section_title,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        embedding_model=embedding_service.model,
                        embedding=vector,
                    )
                    for chunk, vector in zip(parsed_doc.chunks, embeddings)
                ]
                await chunk_repo.create_many(db_chunks)

            # 5. Update document status to processed
            transition(document, DocumentStatus.PROCESSED)
            await doc_repo.update(document)
            await session.commit()

            logger.info(
                "Document %s processed successfully (%d chunks embedded, %s pages)",
                document.id,
                len(parsed_doc.chunks),
                document.page_count,
            )
        except (NoResultFound, StaleDataError) as exc:
            # The document row vanished between the lookup above and now —
            # it was deleted mid-processing. Nothing to retry.
            await session.rollback()
            raise DocumentDeletedError(
                f"document {event.document_id} was deleted mid-processing"
            ) from exc
        except IntegrityError as exc:
            # Chunk inserts FK the document/user; a cascade delete landing
            # mid-processing surfaces as a violated FK. Also nothing to retry.
            await session.rollback()
            raise DocumentDeletedError(
                f"document {event.document_id} was deleted mid-processing"
            ) from exc
        except Exception:
            # A failed flush leaves the session unusable until rolled back
            await session.rollback()
            raise
        finally:
            if temp_path:
                try:
                    await aiofiles.os.remove(temp_path)
                except FileNotFoundError:
                    pass


async def set_document_status(event: DocumentEvent, status: str) -> None:
    """Update status in a fresh session, independent of any failed processing session."""
    async with AsyncSessionLocal() as session:
        doc_repo = DocumentRepository(session)
        document = await doc_repo.get_by_id(
            uuid.UUID(event.document_id), uuid.UUID(event.user_id)
        )
        if document is None:
            return
        transition(document, status)
        await doc_repo.update(document)
        await session.commit()


async def _publish_to(queue_name: str, message: AbstractIncomingMessage, headers: dict) -> None:
    assert rabbitmq.channel is not None
    await rabbitmq.channel.default_exchange.publish(
        aio_pika.Message(
            body=message.body,
            content_type=message.content_type or "application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={**(message.headers or {}), **headers},
        ),
        routing_key=queue_name,
    )


async def dead_letter(message: AbstractIncomingMessage, reason: str) -> None:
    await _publish_to(
        dead_letter_queue_name(QUEUE_NAME), message, {"x-failure-reason": reason[:1000]}
    )


async def retry_or_fail(
    message: AbstractIncomingMessage, event: DocumentEvent, attempt: int, reason: str
) -> None:
    """Schedule the next attempt, or dead-letter and fail the document once attempts run out."""
    if attempt < MAX_ATTEMPTS:
        delay = RETRY_DELAYS_SECONDS[attempt - 1]
        logger.warning(
            "Document %s attempt %d/%d failed (%s); retrying in %ds",
            event.document_id, attempt, MAX_ATTEMPTS, reason, delay,
        )
        await _publish_to(
            retry_queue_name(QUEUE_NAME, attempt), message, {ATTEMPT_HEADER: attempt + 1}
        )
        await set_document_status(event, "uploaded")
    else:
        logger.error(
            "Document %s failed after %d attempts (%s); dead-lettering",
            event.document_id, attempt, reason,
        )
        await dead_letter(message, reason)
        await set_document_status(event, "failed")
    # Ack only after the retry/dead-letter copy is safely published
    await message.ack()


async def handle_message(message: AbstractIncomingMessage) -> None:
    try:
        event = parse_event(message.body)
    except MalformedMessageError as exc:
        logger.error("Malformed message; dead-lettering: %s", exc)
        await dead_letter(message, f"malformed message: {exc}")
        await message.ack()
        return

    attempt = int((message.headers or {}).get(ATTEMPT_HEADER, 1))

    if message.redelivered:
        # The previous delivery was never acked: the worker crashed or lost its connection
        # mid-job. Count it as a failed attempt so a document that crashes the worker
        # can't loop forever.
        await retry_or_fail(message, event, attempt, "redelivered after an unfinished attempt")
        return

    try:
        await process_document(event)
    except DocumentDeletedError:
        # Deleted while processing: the row is gone, so there is no status to
        # update and no point retrying — stop cleanly (MNE-21).
        logger.info(
            "Document %s was deleted mid-processing; acking without retry",
            event.document_id,
        )
        await message.ack()
        return
    except Exception as exc:
        if is_permanent_error(exc):
            logger.exception("Document %s failed permanently", event.document_id)
            await set_document_status(event, "failed")
            await message.ack()
            return
        logger.exception("Document %s processing error", event.document_id)
        await retry_or_fail(message, event, attempt, f"{type(exc).__name__}: {exc}")
        return

    await message.ack()


async def start_worker() -> None:
    await rabbitmq.connect()
    assert rabbitmq.channel is not None

    await rabbitmq.channel.set_qos(prefetch_count=settings.WORKER_PREFETCH_COUNT)
    queue = await declare_processing_queues(rabbitmq.channel, QUEUE_NAME)

    logger.info(
        "Worker started, listening on queue: %s (prefetch=%d, max attempts=%d)",
        QUEUE_NAME, settings.WORKER_PREFETCH_COUNT, MAX_ATTEMPTS,
    )

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            try:
                await handle_message(message)
            except Exception:
                # e.g. DB or broker down while recording the outcome. Requeue; the
                # redelivery is then counted as an attempt.
                logger.exception("Failed to handle message; requeueing")
                if not message.processed:
                    await message.nack(requeue=True)

"""Document lifecycle status: enum, allowed transitions, and the transition helper.

Single source of truth for status *paths* (MNE-22). The database additionally
enforces status *membership* via ck_documents_status; this module enforces
from where to where a document may move.
"""
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.documents.models.document_model import Document


class DocumentStatus(str, Enum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class InvalidStatusTransition(ValueError):
    """Raised when a status change violates ALLOWED_TRANSITIONS."""


# pending_upload → uploaded/failed: confirm_upload (success / oversize 413)
# uploaded → processing: worker picks the event up
# processing → processed/uploaded/failed: worker success / retry / give-up
# processed/failed → uploaded: re-embed requeue (MNE-18) and operator retry
ALLOWED_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.PENDING_UPLOAD: frozenset(
        {DocumentStatus.UPLOADED, DocumentStatus.FAILED}
    ),
    DocumentStatus.UPLOADED: frozenset({DocumentStatus.PROCESSING}),
    DocumentStatus.PROCESSING: frozenset(
        {
            DocumentStatus.PROCESSED,
            DocumentStatus.UPLOADED,
            DocumentStatus.FAILED,
        }
    ),
    DocumentStatus.PROCESSED: frozenset({DocumentStatus.UPLOADED}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.UPLOADED}),
}


def transition(document: "Document", new_status: DocumentStatus | str) -> None:
    """Move `document` to `new_status`, or raise InvalidStatusTransition.

    A self-transition is a no-op (idempotent retries stay legal).
    """
    current = DocumentStatus(document.status)
    target = DocumentStatus(new_status)
    if target is current:
        return
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStatusTransition(
            f"Document status cannot move from {current.value!r} to {target.value!r}"
        )
    document.status = target.value

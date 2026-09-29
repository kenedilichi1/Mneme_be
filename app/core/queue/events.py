from enum import Enum
from app.core.config import settings

DOCUMENT_PROCESSING_QUEUE_NAME = f"document_processing.{settings.ENVIRONMENT}"


class DocumentEventType(str, Enum):
    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_PROCESSED = "document.processed"
    DOCUMENT_FAILED = "document.failed"



class DocumentEvent:
    def __init__(
        self,
        event_type: DocumentEventType,
        document_id: str,
        user_id: str,
        storage_key: str,
    ) -> None:
        self.event_type = event_type
        self.document_id = document_id
        self.user_id = user_id
        self.storage_key = storage_key

    def to_dict(self) -> dict[str, str]:
        return {
            "event_type": self.event_type.value,
            "document_id": self.document_id,
            "user_id": self.user_id,
            "storage_key": self.storage_key,
        }

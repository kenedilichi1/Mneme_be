from typing import Annotated

from fastapi import Depends

from app.api.dependencies import SessionDep
from app.modules.chat.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.chat.repositories.message_repository import MessageRepository
from app.modules.chat.services.chat_service import ChatService
from app.modules.chat.services.gemini_service import (
    GeminiService,
    gemini_service as _gemini_service,
)
from app.modules.documents.dependencies.document_dependency import (
    DocumentRepositoryDep,
)
from app.modules.rag.dependencies.rag_dependency import VectorSearchServiceDep


def get_conversation_repository(db: SessionDep) -> ConversationRepository:
    return ConversationRepository(db)


def get_message_repository(db: SessionDep) -> MessageRepository:
    return MessageRepository(db)


def get_gemini_service() -> GeminiService:
    # Singleton: the SDK client holds connection pools and has no per-request
    # state (mirrors get_storage_service).
    return _gemini_service


def get_chat_service(
    document_repository: DocumentRepositoryDep,
    conversation_repository: Annotated[
        ConversationRepository, Depends(get_conversation_repository)
    ],
    message_repository: Annotated[
        MessageRepository, Depends(get_message_repository)
    ],
    vector_search_service: VectorSearchServiceDep,
    gemini_service: Annotated[GeminiService, Depends(get_gemini_service)],
) -> ChatService:
    return ChatService(
        document_repository=document_repository,
        conversation_repository=conversation_repository,
        message_repository=message_repository,
        vector_search_service=vector_search_service,
        gemini_service=gemini_service,
    )


ConversationRepositoryDep = Annotated[
    ConversationRepository, Depends(get_conversation_repository)
]
MessageRepositoryDep = Annotated[MessageRepository, Depends(get_message_repository)]
GeminiServiceDep = Annotated[GeminiService, Depends(get_gemini_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]

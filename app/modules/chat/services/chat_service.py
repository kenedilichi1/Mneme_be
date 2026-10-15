"""Retriever → LLM orchestration for document Q&A.

One turn = start_turn (validate, retrieve, persist the user message, commit)
→ stream_answer (Gemini deltas) → finish_turn (persist the answer + citations,
commit). Both commits are the sanctioned MNE-23 exceptions documented on
ConversationRepository.commit: the endpoint streams, so history must be
durable before the next event is emitted, not only at request teardown.
"""

import logging
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import HTTPException

from app.core.config import settings
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.message_model import Message
from app.modules.chat.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.chat.repositories.message_repository import MessageRepository
from app.modules.chat.schemas.chat_schema import ChatRequest, Citation
from app.modules.chat.services.gemini_service import GeminiService
from app.modules.documents.models.document_status import DocumentStatus
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.rag.services.vector_search_service import (
    ChunkSearchResult,
    VectorSearchService,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Mneme, a reading assistant that answers questions about a single document.

You are given numbered context passages extracted from that document:

{context}

Rules:
- Answer using ONLY the passages above; ground every claim in them.
- Cite the passages you rely on inline with their bracketed number, e.g. [1] or [2][4].
- If the passages do not contain the answer, say so plainly — never invent content.
- Match the user's language. Be concise; quote only when quoting helps.
"""

TITLE_MAX_CHARS = 120
SNIPPET_MAX_CHARS = 300
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")


@dataclass
class PreparedTurn:
    """Everything validated/gathered before the answer starts streaming."""

    conversation: Conversation
    query: str
    history: list[Message]
    chunks: list[ChunkSearchResult]


def truncate_to_budget(
    chunks: list[ChunkSearchResult], budget_tokens: int
) -> list[ChunkSearchResult]:
    """Keep highest-ranked chunks until the token budget is spent; always keep
    at least the top chunk so the model has *something* to work with."""
    kept: list[ChunkSearchResult] = []
    total = 0
    for chunk in chunks:
        if kept and total + chunk.token_count > budget_tokens:
            break
        kept.append(chunk)
        total += chunk.token_count
    return kept


def extract_citations(
    answer: str, chunks: list[ChunkSearchResult]
) -> list[Citation]:
    """Map the `[n]` markers in the answer back to chunk metadata (in text order)."""
    seen: set[int] = set()
    citations: list[Citation] = []
    for raw in _CITATION_RE.findall(answer):
        index = int(raw) - 1
        if index < 0 or index >= len(chunks) or index in seen:
            continue
        seen.add(index)
        chunk = chunks[index]
        citations.append(
            Citation(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                page_number=chunk.page_number,
                section_title=chunk.section_title,
                snippet=chunk.content[:SNIPPET_MAX_CHARS],
            )
        )
    return citations


def _build_context(chunks: list[ChunkSearchResult]) -> str:
    if not chunks:
        return (
            "(No relevant passages were found in the document for this question.)"
        )
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        meta = []
        if chunk.page_number is not None:
            meta.append(f"page {chunk.page_number}")
        if chunk.section_title:
            meta.append(chunk.section_title)
        location = f" ({', '.join(meta)})" if meta else ""
        blocks.append(f"[{i}]{location}\n{chunk.content}")
    return "\n\n".join(blocks)


class ChatService:
    def __init__(
        self,
        document_repository: DocumentRepository,
        conversation_repository: ConversationRepository,
        message_repository: MessageRepository,
        vector_search_service: VectorSearchService,
        gemini_service: GeminiService,
    ) -> None:
        self.document_repository = document_repository
        self.conversation_repository = conversation_repository
        self.message_repository = message_repository
        self.vector_search_service = vector_search_service
        self.gemini_service = gemini_service

    async def start_turn(
        self, *, user_id: uuid.UUID, document_id: uuid.UUID, payload: ChatRequest
    ) -> PreparedTurn:
        document = await self.document_repository.get_by_id(document_id, user_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        if document.status != DocumentStatus.PROCESSED.value:
            raise HTTPException(
                status_code=409,
                detail="Document is not ready for questions yet",
            )

        conversation = await self._resolve_conversation(
            user_id=user_id, document_id=document_id, payload=payload
        )

        # Fetch history *before* writing this turn's user message so the
        # prompt never replays the question twice.
        history = await self.conversation_repository.latest_messages(
            conversation.id, settings.CHAT_HISTORY_MAX_MESSAGES
        )

        results = await self.vector_search_service.search(
            query=payload.query,
            user_id=user_id,
            document_id=document_id,
            limit=settings.CHAT_RETRIEVAL_LIMIT,
            mode="hybrid",
        )
        chunks = truncate_to_budget(results, settings.CHAT_CONTEXT_TOKEN_BUDGET)

        await self.message_repository.create(
            conversation_id=conversation.id,
            role="user",
            content=payload.query,
        )
        await self.conversation_repository.touch(conversation)
        # Commit before the first byte streams: a disconnect mid-answer must
        # still leave the question in history (MNE-23 exception, see repo).
        await self.conversation_repository.commit()

        return PreparedTurn(
            conversation=conversation,
            query=payload.query,
            history=history,
            chunks=chunks,
        )

    async def _resolve_conversation(
        self, *, user_id: uuid.UUID, document_id: uuid.UUID, payload: ChatRequest
    ) -> Conversation:
        if payload.conversation_id is None:
            # Omitting the id always starts a fresh session for this document.
            return await self.conversation_repository.create(
                user_id=user_id,
                document_id=document_id,
                title=payload.query[:TITLE_MAX_CHARS],
            )
        conversation = await self.conversation_repository.get_by_id(
            payload.conversation_id, user_id
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.document_id != document_id:
            raise HTTPException(
                status_code=409,
                detail="Conversation does not belong to this document",
            )
        return conversation

    async def stream_answer(self, prepared: PreparedTurn) -> AsyncIterator[str]:
        system_instruction = SYSTEM_PROMPT.format(
            context=_build_context(prepared.chunks)
        )
        messages: list[tuple[str, str]] = [
            (message.role, message.content) for message in prepared.history
        ]
        messages.append(("user", prepared.query))
        async for delta in self.gemini_service.stream(
            system_instruction=system_instruction,
            messages=messages,
        ):
            yield delta

    async def finish_turn(
        self, prepared: PreparedTurn, answer: str
    ) -> list[Citation]:
        citations = extract_citations(answer, prepared.chunks)
        await self.message_repository.create(
            conversation_id=prepared.conversation.id,
            role="assistant",
            content=answer,
            citations=[c.model_dump(mode="json") for c in citations],
        )
        await self.conversation_repository.touch(prepared.conversation)
        await self.conversation_repository.commit()
        return citations

    async def list_conversations(
        self,
        *,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Conversation]:
        document = await self.document_repository.get_by_id(document_id, user_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return await self.conversation_repository.list_by_document(
            user_id=user_id,
            document_id=document_id,
            limit=limit,
            offset=offset,
        )

    async def get_messages(
        self, *, user_id: uuid.UUID, document_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> list[Message]:
        conversation = await self.conversation_repository.get_by_id(
            conversation_id, user_id
        )
        if conversation is None or conversation.document_id != document_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return await self.conversation_repository.list_messages(conversation.id)

"""Document Q&A endpoints (SSE).

Event protocol for POST /{document_id}/query — one JSON object per `data:` line:

    event: conversation_id   {"conversation_id": "..."}
    event: delta             {"text": "..."}            (0..n)
    event: citations         {"citations": [...]}       (after the stream)
    event: done              {"conversation_id": "..."}
    event: error             {"message": "..."}         (terminal, replaces done)

Errors detected *before* the first byte (auth, ownership, validation) leave
through the normal JSON error envelope; once streaming starts, failures are
reported as `error` events because the status line is already on the wire.
"""

import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.core.responses import COMMON_ERRORS, SuccessEnvelope, error_responses, ok
from app.modules.auth.dependencies.auth_dependency import CurrentUserDep
from app.modules.chat.dependencies.chat_dependency import ChatServiceDep
from app.modules.chat.schemas.chat_schema import (
    ChatRequest,
    ConversationPublic,
    MessagePublic,
)
from app.modules.chat.services.chat_service import PreparedTurn
from app.modules.chat.services.gemini_service import GeminiError

logger = logging.getLogger(__name__)

chat_router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Disable proxy buffering so deltas reach the client immediately
    "X-Accel-Buffering": "no",
}

_QUERY_RESPONSES = (
    {
        200: {
            "description": "SSE stream of the grounded answer",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    }
    | COMMON_ERRORS
    | error_responses(
        (401, "Not authenticated"),
        (404, "Document not found"),
        (409, "Document is not ready for questions yet, or conversation mismatch"),
    )
)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@chat_router.post(
    "/{document_id}/query",
    responses=_QUERY_RESPONSES,
)
async def query_document(
    document_id: uuid.UUID,
    payload: ChatRequest,
    current_user: CurrentUserDep,
    chat_service: ChatServiceDep,
):
    # Runs before the response starts: failures here still get the JSON envelope.
    prepared: PreparedTurn = await chat_service.start_turn(
        user_id=current_user.id, document_id=document_id, payload=payload
    )
    conversation_id = str(prepared.conversation.id)

    # Shared with the background task: on a client disconnect the generator is
    # cancelled mid-stream, and whatever text was already produced still gets
    # saved (durability promise) after the response ends.
    state: dict = {"parts": [], "status": "streaming"}

    async def persist_partial_answer() -> None:
        if state["status"] != "streaming":
            return
        answer = "".join(state["parts"])
        if not answer:
            return
        try:
            await chat_service.finish_turn(prepared, answer)
        except Exception:
            logger.exception(
                "Could not save partial answer for conversation %s",
                conversation_id,
            )

    async def event_stream():
        yield _sse("conversation_id", {"conversation_id": conversation_id})
        try:
            async for delta in chat_service.stream_answer(prepared):
                state["parts"].append(delta)
                yield _sse("delta", {"text": delta})
        except GeminiError as exc:
            state["status"] = "error"
            logger.warning("Gemini failure mid-stream: %s", exc)
            yield _sse("error", {"message": str(exc)})
            return
        except Exception:
            state["status"] = "error"
            logger.exception(
                "Unexpected failure mid-stream for conversation %s",
                conversation_id,
            )
            yield _sse("error", {"message": "The answer could not be completed"})
            return

        answer = "".join(state["parts"])
        state["status"] = "saving"
        try:
            citations = await chat_service.finish_turn(prepared, answer)
        except Exception:
            state["status"] = "error"
            logger.exception(
                "Could not save answer for conversation %s", conversation_id
            )
            yield _sse("error", {"message": "The answer could not be saved"})
            return

        yield _sse(
            "citations",
            {"citations": [c.model_dump(mode="json") for c in citations]},
        )
        yield _sse(
            "done",
            {"conversation_id": conversation_id, "document_id": str(document_id)},
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
        background=BackgroundTask(persist_partial_answer),
    )


@chat_router.get(
    "/{document_id}/conversations",
    response_model=SuccessEnvelope[list[ConversationPublic]],
    responses=COMMON_ERRORS | error_responses(
        (401, "Not authenticated"),
        (404, "Document not found"),
    ),
)
async def list_document_conversations(
    document_id: uuid.UUID,
    current_user: CurrentUserDep,
    chat_service: ChatServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    conversations = await chat_service.list_conversations(
        user_id=current_user.id,
        document_id=document_id,
        limit=limit,
        offset=offset,
    )
    return ok(conversations, "Conversations retrieved")


@chat_router.get(
    "/{document_id}/conversations/{conversation_id}/messages",
    response_model=SuccessEnvelope[list[MessagePublic]],
    responses=COMMON_ERRORS | error_responses(
        (401, "Not authenticated"),
        (404, "Conversation not found"),
    ),
)
async def list_conversation_messages(
    document_id: uuid.UUID,
    conversation_id: uuid.UUID,
    current_user: CurrentUserDep,
    chat_service: ChatServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    messages = await chat_service.get_messages(
        user_id=current_user.id,
        document_id=document_id,
        conversation_id=conversation_id,
    )
    return ok(messages[offset : offset + limit], "Messages retrieved")

import uuid

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock

from app.core.config import settings
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.message_model import Message
from app.modules.chat.schemas.chat_schema import ChatRequest
from app.modules.chat.services.chat_service import (
    ChatService,
    extract_citations,
    truncate_to_budget,
)
from app.modules.chat.services.gemini_service import GeminiError
from app.modules.rag.services.vector_search_service import ChunkSearchResult


def make_chunk(
    index: int = 0,
    content: str = "chunk content",
    token_count: int = 10,
    page: int | None = 1,
) -> ChunkSearchResult:
    return ChunkSearchResult(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=index,
        page_number=page,
        section_title=None,
        content=content,
        token_count=token_count,
        score=0.9,
    )


def make_conversation(
    document_id: uuid.UUID | None = None,
) -> Conversation:
    # id is set explicitly: column defaults only apply at flush time
    return Conversation(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        title="session",
    )


def make_document(status: str = "processed"):
    doc = MagicMock()
    doc.status = status
    return doc


def make_service(document=make_document()):
    calls: list[str] = []

    doc_repo = MagicMock()
    doc_repo.get_by_id = AsyncMock(return_value=document)

    conv_repo = MagicMock()
    conv_repo.create = AsyncMock(
        side_effect=lambda **kw: Conversation(
            id=uuid.uuid4(),
            user_id=kw["user_id"],
            document_id=kw["document_id"],
            title=kw.get("title"),
        )
    )
    conv_repo.get_by_id = AsyncMock(return_value=None)
    conv_repo.latest_messages = AsyncMock(return_value=[])
    conv_repo.list_messages = AsyncMock(return_value=[])
    conv_repo.list_by_document = AsyncMock(return_value=[])
    conv_repo.touch = AsyncMock(side_effect=lambda c: calls.append("touch"))
    conv_repo.commit = AsyncMock(side_effect=lambda: calls.append("commit"))

    msg_repo = MagicMock()
    msg_repo.create = AsyncMock(
        side_effect=lambda **kw: Message(
            conversation_id=kw["conversation_id"],
            role=kw["role"],
            content=kw["content"],
            citations=kw.get("citations") or [],
        )
    )

    search = MagicMock()
    search.search = AsyncMock(return_value=[])

    gemini = MagicMock()
    gemini.stream = MagicMock()

    service = ChatService(
        document_repository=doc_repo,
        conversation_repository=conv_repo,
        message_repository=msg_repo,
        vector_search_service=search,
        gemini_service=gemini,
    )
    return service, doc_repo, conv_repo, msg_repo, search, gemini, calls


# ---------------------------------------------------------------------------
# truncate_to_budget / extract_citations
# ---------------------------------------------------------------------------


def test_truncate_respects_budget_but_keeps_top_chunk():
    chunks = [
        make_chunk(0, token_count=100),
        make_chunk(1, token_count=100),
        make_chunk(2, token_count=100),
    ]
    kept = truncate_to_budget(chunks, budget_tokens=250)
    assert [c.chunk_index for c in kept] == [0, 1]


def test_truncate_always_keeps_first_chunk_even_over_budget():
    chunks = [make_chunk(0, token_count=10_000), make_chunk(1, token_count=1)]
    kept = truncate_to_budget(chunks, budget_tokens=10)
    assert [c.chunk_index for c in kept] == [0]


def test_extract_citations_maps_markers_in_order_dedupes_and_skips_unknown():
    first, second = make_chunk(0, content="alpha"), make_chunk(1, content="beta")
    answer = "As shown [2], and again [1], plus [2] and out-of-range [9]."
    citations = extract_citations(answer, [first, second])
    assert [c.chunk_id for c in citations] == [second.chunk_id, first.chunk_id]
    assert citations[0].snippet == "beta"
    assert citations[0].document_id == second.document_id


def test_extract_citations_without_markers_is_empty():
    assert extract_citations("no citations here", [make_chunk()]) == []


# ---------------------------------------------------------------------------
# start_turn
# ---------------------------------------------------------------------------


async def test_start_turn_404_when_document_unknown():
    service, doc_repo, *_ = make_service(document=None)
    doc_repo.get_by_id.return_value = None
    with pytest.raises(HTTPException) as exc:
        await service.start_turn(
            user_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            payload=ChatRequest(query="q?"),
        )
    assert exc.value.status_code == 404


async def test_start_turn_409_when_document_not_processed():
    service, *_ = make_service(document=make_document(status="processing"))
    with pytest.raises(HTTPException) as exc:
        await service.start_turn(
            user_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            payload=ChatRequest(query="q?"),
        )
    assert exc.value.status_code == 409


async def test_start_turn_creates_conversation_and_persists_user_message():
    service, _, conv_repo, msg_repo, search, _, calls = make_service()
    search.search.return_value = [make_chunk(0, token_count=5)]

    prepared = await service.start_turn(
        user_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        payload=ChatRequest(query="What is revenue?"),
    )

    conv_repo.create.assert_awaited_once()
    assert prepared.conversation.title == "What is revenue?"
    msg_repo.create.assert_awaited_once()
    kwargs = msg_repo.create.await_args.kwargs
    assert kwargs["role"] == "user"
    assert kwargs["content"] == "What is revenue?"
    assert calls == ["touch", "commit"]  # durable before the stream starts
    assert prepared.chunks  # retrieval ran


async def test_start_turn_reuses_existing_conversation():
    service, _, conv_repo, *_ = make_service()
    existing = make_conversation()
    conv_repo.get_by_id.return_value = existing

    prepared = await service.start_turn(
        user_id=uuid.uuid4(),
        document_id=existing.document_id,
        payload=ChatRequest(query="follow-up", conversation_id=existing.id),
    )
    assert prepared.conversation is existing
    conv_repo.create.assert_not_called()


async def test_start_turn_404_for_foreign_conversation():
    service, _, conv_repo, *_ = make_service()
    conv_repo.get_by_id.return_value = None
    with pytest.raises(HTTPException) as exc:
        await service.start_turn(
            user_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            payload=ChatRequest(query="q", conversation_id=uuid.uuid4()),
        )
    assert exc.value.status_code == 404


async def test_start_turn_409_when_conversation_belongs_to_other_document():
    service, _, conv_repo, *_ = make_service()
    conv_repo.get_by_id.return_value = make_conversation()  # different document
    with pytest.raises(HTTPException) as exc:
        await service.start_turn(
            user_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            payload=ChatRequest(query="q", conversation_id=uuid.uuid4()),
        )
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# stream_answer
# ---------------------------------------------------------------------------


async def test_stream_answer_replays_history_then_query_with_numbered_context():
    service, _, _, _, _, gemini, _ = make_service()
    chunks = [make_chunk(0, content="alpha text"), make_chunk(1, content="beta text")]

    captured: dict = {}

    async def fake_stream(*, system_instruction, messages):
        captured["system"] = system_instruction
        captured["messages"] = list(messages)
        yield "ans"
        yield "wer"

    gemini.stream = MagicMock(side_effect=fake_stream)

    conversation = make_conversation()
    history = [
        Message(
            conversation_id=conversation.id,
            role="user",
            content="earlier question",
        ),
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content="earlier answer",
        ),
    ]
    prepared = await service.start_turn(
        user_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        payload=ChatRequest(query="new question"),
    )
    prepared.history = history
    prepared.chunks = chunks

    deltas = [d async for d in service.stream_answer(prepared)]
    assert deltas == ["ans", "wer"]

    # Context is numbered so [n] markers in the answer map back to chunks
    assert "[1] (page 1)\nalpha text" in captured["system"]
    assert "[2] (page 1)\nbeta text" in captured["system"]
    # History replays oldest-first, current question last
    assert captured["messages"] == [
        ("user", "earlier question"),
        ("assistant", "earlier answer"),
        ("user", "new question"),
    ]


async def test_stream_answer_surfaces_gemini_error():
    service, _, _, _, _, gemini, _ = make_service()

    async def broken_stream(*, system_instruction, messages):
        raise GeminiError("down")
        yield  # pragma: no cover

    gemini.stream = MagicMock(side_effect=broken_stream)
    prepared = await service.start_turn(
        user_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        payload=ChatRequest(query="q"),
    )
    with pytest.raises(GeminiError):
        async for _ in service.stream_answer(prepared):
            pass


# ---------------------------------------------------------------------------
# finish_turn
# ---------------------------------------------------------------------------


async def test_finish_turn_persists_answer_with_citations_and_commits():
    service, _, conv_repo, msg_repo, *_ , calls = make_service()
    chunk = make_chunk(0, content="the answer source")

    prepared = await service.start_turn(
        user_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        payload=ChatRequest(query="q"),
    )
    prepared.chunks = [chunk]
    calls.clear()

    citations = await service.finish_turn(prepared, "Grounded answer [1].")

    assert len(citations) == 1
    kwargs = msg_repo.create.await_args.kwargs
    assert kwargs["role"] == "assistant"
    assert kwargs["content"] == "Grounded answer [1]."
    assert kwargs["citations"][0]["chunk_id"] == str(chunk.chunk_id)
    assert calls == ["touch", "commit"]


# ---------------------------------------------------------------------------
# history endpoints
# ---------------------------------------------------------------------------


async def test_list_conversations_404_for_foreign_document():
    service, doc_repo, *_ = make_service(document=None)
    doc_repo.get_by_id.return_value = None
    with pytest.raises(HTTPException) as exc:
        await service.list_conversations(
            user_id=uuid.uuid4(), document_id=uuid.uuid4()
        )
    assert exc.value.status_code == 404


async def test_get_messages_404_when_conversation_not_in_document():
    service, _, conv_repo, *_ = make_service()
    conv_repo.get_by_id.return_value = make_conversation()  # other document
    with pytest.raises(HTTPException) as exc:
        await service.get_messages(
            user_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 404


async def test_context_budget_defaults_exist():
    # Guard against accidental config drift: retrieval relies on these keys
    assert settings.CHAT_RETRIEVAL_LIMIT >= 1
    assert settings.CHAT_CONTEXT_TOKEN_BUDGET >= 512
    assert settings.CHAT_HISTORY_MAX_MESSAGES >= 2

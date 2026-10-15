import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.chat.dependencies.chat_dependency import get_gemini_service
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.message_model import Message
from app.modules.chat.services.gemini_service import GeminiError
from app.modules.documents.models.document_chunk_model import DocumentChunk
from app.modules.documents.models.document_model import Document
from app.modules.rag.dependencies import get_embedding_service
from app.modules.user.models.user_model import User

# Deterministic fake-embedder axes (same trick as test_search.py)
_E0 = [0.0] * 768
_E0[0] = 1.0
_E1 = [0.0] * 768
_E1[1] = 1.0


class FakeEmbeddingService:
    async def generate_embeddings(
        self, texts: list[str], batch_size: int = 100, is_query: bool = False
    ) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "apple" in lowered or "revenue" in lowered:
                vectors.append(list(_E0))
            else:
                vectors.append(list(_E1))
        return vectors


class FakeGeminiService:
    """Stands in for GeminiService; records what the orchestrator sent."""

    def __init__(self, deltas: tuple[str, ...] = ("The revenue ", "grew [1]."),
                 error: str | None = None):
        self.deltas = deltas
        self.error = error
        self.calls: list[dict] = []

    async def stream(self, *, system_instruction: str, messages):
        self.calls.append(
            {"system_instruction": system_instruction, "messages": list(messages)}
        )
        if self.error is not None:
            raise GeminiError(self.error)
            yield  # pragma: no cover
        for delta in self.deltas:
            yield delta


@pytest.fixture(autouse=True)
def fake_embeddings():
    app.dependency_overrides[get_embedding_service] = FakeEmbeddingService
    yield
    app.dependency_overrides.pop(get_embedding_service, None)


@pytest.fixture
def fake_gemini():
    service = FakeGeminiService()
    app.dependency_overrides[get_gemini_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_gemini_service, None)


def parse_sse(body: str) -> list[dict]:
    events, current = [], {}
    for line in body.splitlines():
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


async def _login(client: AsyncClient, db_session: AsyncSession, email: str):
    res = await client.post("/api/v1/auth/request-otp", json={"email": email})
    assert res.status_code == 202, res.text

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    otp_repo = OtpCodeRepository(db_session)
    await otp_repo.delete_otp_code(user.id)
    await otp_repo.create(
        OtpCode(
            user_id=user.id,
            code_hash=hash_otp_code("654321"),
            expires_at=otp_expiry(),
        )
    )
    res = await client.post(
        "/api/v1/auth/verify-otp", json={"email": email, "otp": "654321"}
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]["access_token"], user


async def _plant_document(
    db_session: AsyncSession,
    user: User,
    title: str,
    contents: list[str],
    status: str = "processed",
) -> tuple[Document, list[DocumentChunk]]:
    doc = Document(
        user_id=user.id,
        title=title,
        original_filename=f"{title}.pdf",
        file_size=1024,
        storage_key=f"test/{title}.pdf",
        file_type="application/pdf",
        status=status,
    )
    db_session.add(doc)
    await db_session.flush()
    chunks = []
    for index, content in enumerate(contents):
        chunk = DocumentChunk(
            document_id=doc.id,
            user_id=user.id,
            chunk_index=index,
            content=content,
            token_count=len(content.split()),
            embedding=list(_E0),
        )
        db_session.add(chunk)
        chunks.append(chunk)
    await db_session.flush()
    return doc, chunks


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _query(client, token, document_id, payload):
    return await client.post(
        f"/api/v1/documents/{document_id}/query",
        json=payload,
        headers=_headers(token),
    )


async def _messages(db_session, conversation_id) -> list[Message]:
    result = await db_session.execute(
        select(Message)
        .where(Message.conversation_id == uuid.UUID(str(conversation_id)))
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return list(result.scalars().all())


async def _conversation(db_session, conversation_id) -> Conversation:
    result = await db_session.execute(
        select(Conversation).where(
            Conversation.id == uuid.UUID(str(conversation_id))
        )
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Streaming happy path
# ---------------------------------------------------------------------------


async def test_query_streams_sse_and_persists_turn(
    client: AsyncClient, db_session: AsyncSession, fake_gemini
):
    token, user = await _login(client, db_session, "chat-happy@example.com")
    doc, chunks = await _plant_document(
        db_session, user, "quarterly", ["apple revenue growth analysis"]
    )

    res = await _query(client, token, doc.id, {"query": "apple revenue?"})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(res.text)
    names = [e["event"] for e in events]
    assert names[0] == "conversation_id"
    assert names[1:-2] == ["delta", "delta"]
    assert names[-2:] == ["citations", "done"]

    conversation_id = events[0]["data"]["conversation_id"]
    deltas = "".join(e["data"]["text"] for e in events if e["event"] == "delta")
    assert deltas == "The revenue grew [1]."
    citations = events[-2]["data"]["citations"]
    assert len(citations) == 1
    assert citations[0]["chunk_id"] == str(chunks[0].id)
    assert events[-1]["data"]["conversation_id"] == conversation_id

    # Conversation + both messages durable, citations stored on the answer
    conversation = await _conversation(db_session, conversation_id)
    assert conversation.user_id == user.id
    assert conversation.document_id == doc.id
    assert conversation.title == "apple revenue?"

    messages = await _messages(db_session, conversation_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "apple revenue?"
    assert messages[1].content == "The revenue grew [1]."
    assert messages[1].citations[0]["chunk_id"] == str(chunks[0].id)

    # The orchestrator was handed grounded, numbered context
    assert "[1]" in fake_gemini.calls[0]["system_instruction"]
    assert "apple revenue growth analysis" in fake_gemini.calls[0]["system_instruction"]
    assert fake_gemini.calls[0]["messages"][-1] == ("user", "apple revenue?")


async def test_follow_up_continues_the_same_conversation(
    client: AsyncClient, db_session: AsyncSession, fake_gemini
):
    token, user = await _login(client, db_session, "chat-followup@example.com")
    doc, _ = await _plant_document(
        db_session, user, "report", ["apple revenue details"]
    )

    first = await _query(client, token, doc.id, {"query": "apple revenue?"})
    conversation_id = parse_sse(first.text)[0]["data"]["conversation_id"]

    second = await _query(
        client,
        token,
        doc.id,
        {"query": "and margins?", "conversation_id": conversation_id},
    )
    events = parse_sse(second.text)
    assert events[0]["data"]["conversation_id"] == conversation_id
    assert events[-1]["event"] == "done"

    messages = await _messages(db_session, conversation_id)
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]
    # History replayed: the second call saw the first turn
    replayed = fake_gemini.calls[1]["messages"]
    assert replayed[0] == ("user", "apple revenue?")
    assert replayed[1][0] == "assistant"
    assert replayed[-1] == ("user", "and margins?")


async def test_second_query_without_id_starts_a_new_session(
    client: AsyncClient, db_session: AsyncSession, fake_gemini
):
    token, user = await _login(client, db_session, "chat-newsess@example.com")
    doc, _ = await _plant_document(db_session, user, "book", ["apple facts"])

    first = await _query(client, token, doc.id, {"query": "apple one?"})
    second = await _query(client, token, doc.id, {"query": "apple two?"})
    id_a = parse_sse(first.text)[0]["data"]["conversation_id"]
    id_b = parse_sse(second.text)[0]["data"]["conversation_id"]
    assert id_a != id_b

    conversations = (
        await db_session.execute(
            select(Conversation).where(Conversation.document_id == doc.id)
        )
    ).scalars().all()
    assert len(conversations) == 2


# ---------------------------------------------------------------------------
# History endpoints
# ---------------------------------------------------------------------------


async def test_conversation_and_message_endpoints(
    client: AsyncClient, db_session: AsyncSession, fake_gemini
):
    token, user = await _login(client, db_session, "chat-history@example.com")
    doc, _ = await _plant_document(db_session, user, "notes", ["apple notes"])

    res = await _query(client, token, doc.id, {"query": "apple?"})
    conversation_id = parse_sse(res.text)[0]["data"]["conversation_id"]

    res = await client.get(
        f"/api/v1/documents/{doc.id}/conversations", headers=_headers(token)
    )
    assert res.status_code == 200, res.text
    conversations = res.json()["data"]
    assert [c["id"] for c in conversations] == [conversation_id]

    res = await client.get(
        f"/api/v1/documents/{doc.id}/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    assert res.status_code == 200, res.text
    messages = res.json()["data"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["citations"][0]["snippet"] == "apple notes"


# ---------------------------------------------------------------------------
# Isolation & guards
# ---------------------------------------------------------------------------


async def test_query_404_for_another_users_document(
    client: AsyncClient, db_session: AsyncSession, fake_gemini
):
    token_a, user_a = await _login(client, db_session, "chat-owner@example.com")
    token_b, _ = await _login(client, db_session, "chat-intruder@example.com")
    doc, _ = await _plant_document(db_session, user_a, "private", ["apple secret"])

    res = await _query(client, token_b, doc.id, {"query": "apple?"})
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == 404


async def test_conversation_is_invisible_to_other_users(
    client: AsyncClient, db_session: AsyncSession, fake_gemini
):
    token_a, user_a = await _login(client, db_session, "chat-conv-owner@example.com")
    token_b, _ = await _login(client, db_session, "chat-conv-other@example.com")
    doc, _ = await _plant_document(db_session, user_a, "mine", ["apple facts"])

    res = await _query(client, token_a, doc.id, {"query": "apple?"})
    conversation_id = parse_sse(res.text)[0]["data"]["conversation_id"]

    res = await _query(
        client,
        token_b,
        doc.id,
        {"query": "sneak", "conversation_id": conversation_id},
    )
    assert res.status_code == 404, res.text

    res = await client.get(
        f"/api/v1/documents/{doc.id}/conversations/{conversation_id}/messages",
        headers=_headers(token_b),
    )
    assert res.status_code == 404, res.text


async def test_query_409_when_document_not_processed(
    client: AsyncClient, db_session: AsyncSession, fake_gemini
):
    token, user = await _login(client, db_session, "chat-pending@example.com")
    doc, _ = await _plant_document(
        db_session, user, "pending", ["apple pending"], status="processing"
    )

    res = await _query(client, token, doc.id, {"query": "apple?"})
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == 409


async def test_query_requires_auth(
    client: AsyncClient, db_session: AsyncSession, fake_gemini
):
    _, user = await _login(client, db_session, "chat-noauth@example.com")
    doc, _ = await _plant_document(db_session, user, "guarded", ["apple"])

    res = await client.post(
        f"/api/v1/documents/{doc.id}/query", json={"query": "apple?"}
    )
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


async def test_gemini_failure_yields_error_event_and_keeps_question(
    client: AsyncClient, db_session: AsyncSession
):
    token, user = await _login(client, db_session, "chat-fail@example.com")
    doc, _ = await _plant_document(db_session, user, "boom", ["apple failure"])

    broken = FakeGeminiService(error="service unavailable")
    app.dependency_overrides[get_gemini_service] = lambda: broken

    res = await _query(client, token, doc.id, {"query": "apple?"})
    assert res.status_code == 200, res.text

    events = parse_sse(res.text)
    assert [e["event"] for e in events] == ["conversation_id", "error"]
    assert "service unavailable" in events[-1]["data"]["message"]

    conversation_id = events[0]["data"]["conversation_id"]
    messages = await _messages(db_session, conversation_id)
    # The question survives the failure; no half-baked answer is written
    assert [m.role for m in messages] == ["user"]
    assert messages[0].content == "apple?"


async def test_silent_stream_still_keeps_the_turn_paired(
    client: AsyncClient, db_session: AsyncSession
):
    token, user = await _login(client, db_session, "chat-empty@example.com")
    doc, _ = await _plant_document(db_session, user, "empty", ["apple empty"])

    # No deltas and no GeminiError: ChatService itself does not guard
    # emptiness (GeminiService raises "no content" in production) — the turn
    # must still pair question and answer.
    silent = FakeGeminiService(deltas=())
    app.dependency_overrides[get_gemini_service] = lambda: silent

    res = await _query(client, token, doc.id, {"query": "apple?"})
    events = parse_sse(res.text)
    assert events[-1]["event"] == "done"
    conversation_id = events[0]["data"]["conversation_id"]
    messages = await _messages(db_session, conversation_id)
    # An empty answer is still persisted (turn stays paired)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].content == ""

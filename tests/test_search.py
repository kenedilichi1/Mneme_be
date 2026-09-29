import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.documents.models.document_chunk_model import DocumentChunk
from app.modules.documents.models.document_model import Document
from app.modules.rag.dependencies import get_embedding_service
from app.modules.user.models.user_model import User

SEARCH = "/api/v1/search"

# Fixed axes for the fake embedder: query text selects an axis, planted chunk
# embeddings choose their own, so ranking is fully deterministic.
_E0 = [0.0] * 768
_E0[0] = 1.0
_E1 = [0.0] * 768
_E1[1] = 1.0
_E2 = [0.0] * 768
_E2[2] = 1.0


class FakeEmbeddingService:
    async def generate_embeddings(
        self, texts: list[str], batch_size: int = 100, is_query: bool = False
    ) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "apple" in lowered or "revenue" in lowered:
                vectors.append(list(_E0))
            elif "banana" in lowered:
                vectors.append(list(_E1))
            else:
                vectors.append(list(_E2))
        return vectors


@pytest.fixture(autouse=True)
def fake_embeddings():
    app.dependency_overrides[get_embedding_service] = FakeEmbeddingService
    yield
    app.dependency_overrides.pop(get_embedding_service, None)


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
    return res.json()["access_token"], user


async def _plant_document(
    db_session: AsyncSession,
    user: User,
    title: str,
    chunks: list[tuple[str, list[float] | None]],
) -> Document:
    doc = Document(
        user_id=user.id,
        title=title,
        original_filename=f"{title}.pdf",
        file_size=1024,
        storage_key=f"test/{title}.pdf",
        file_type="application/pdf",
        status="processed",
    )
    db_session.add(doc)
    await db_session.flush()
    for index, (content, embedding) in enumerate(chunks):
        db_session.add(
            DocumentChunk(
                document_id=doc.id,
                user_id=user.id,
                chunk_index=index,
                content=content,
                token_count=len(content.split()),
                embedding=embedding,
            )
        )
    await db_session.flush()
    return doc


async def _search(client: AsyncClient, token: str, **params):
    return await client.get(
        SEARCH, params=params, headers={"Authorization": f"Bearer {token}"}
    )


@pytest.mark.asyncio
async def test_text_mode_returns_matching_chunk(
    client: AsyncClient, db_session: AsyncSession
):
    token, user = await _login(client, db_session, "search-text@example.com")
    doc = await _plant_document(
        db_session,
        user,
        "quarterly",
        [
            ("apple revenue growth analysis", None),
            ("unrelated gardening tips for spring", None),
        ],
    )

    res = await _search(client, token, query="apple revenue", mode="text")
    assert res.status_code == 200, res.text
    results = res.json()
    assert len(results) == 1
    assert results[0]["document_id"] == str(doc.id)
    assert results[0]["content"] == "apple revenue growth analysis"
    assert results[0]["score"] > 0
    assert "chunk_id" in results[0]


@pytest.mark.asyncio
async def test_vector_mode_returns_nearest_chunk(
    client: AsyncClient, db_session: AsyncSession
):
    token, user = await _login(client, db_session, "search-vector@example.com")
    doc = await _plant_document(
        db_session,
        user,
        "produce",
        [
            ("apple pie recipe", _E0),
            ("banana bread recipe", _E1),
        ],
    )

    res = await _search(client, token, query="apple", mode="vector")
    assert res.status_code == 200, res.text
    results = res.json()
    # The nearest chunk ranks first; farther chunks may follow below it
    assert results[0]["document_id"] == str(doc.id)
    assert results[0]["content"] == "apple pie recipe"
    assert results[0]["score"] >= 0.99
    if len(results) > 1:
        assert results[1]["score"] < results[0]["score"]


@pytest.mark.asyncio
async def test_hybrid_mode_fuses_vector_and_text(
    client: AsyncClient, db_session: AsyncSession
):
    token, user = await _login(client, db_session, "search-hybrid@example.com")
    doc = await _plant_document(
        db_session,
        user,
        "mixed",
        [
            ("apple revenue growth analysis", _E0),
            ("banana bread recipe", _E1),
        ],
    )

    res = await _search(client, token, query="apple revenue", mode="hybrid")
    assert res.status_code == 200, res.text
    results = res.json()
    # Fused result: the chunk matching in both vector and text ranks first
    assert results[0]["document_id"] == str(doc.id)
    assert results[0]["content"] == "apple revenue growth analysis"
    assert results[0]["score"] > 0


@pytest.mark.asyncio
async def test_document_id_narrows_results_to_one_document(
    client: AsyncClient, db_session: AsyncSession
):
    token, user = await _login(client, db_session, "search-docfilter@example.com")
    doc1 = await _plant_document(
        db_session, user, "first", [("apple revenue report", _E0)]
    )
    doc2 = await _plant_document(
        db_session, user, "second", [("apple revenue forecast", _E0)]
    )

    res = await _search(client, token, query="apple", mode="vector", document_id=doc1.id)
    assert res.status_code == 200, res.text
    assert [r["document_id"] for r in res.json()] == [str(doc1.id)]

    res = await _search(client, token, query="apple", mode="vector", document_id=doc2.id)
    assert [r["document_id"] for r in res.json()] == [str(doc2.id)]


@pytest.mark.asyncio
async def test_limit_caps_result_count(
    client: AsyncClient, db_session: AsyncSession
):
    token, user = await _login(client, db_session, "search-limit@example.com")
    await _plant_document(
        db_session,
        user,
        "zebras",
        [
            ("zebra stripes pattern one", None),
            ("zebra stripes pattern two", None),
            ("zebra stripes pattern three", None),
        ],
    )

    res = await _search(client, token, query="zebra stripes", mode="text", limit=2)
    assert res.status_code == 200, res.text
    assert len(res.json()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["text", "vector", "hybrid"])
async def test_cross_user_isolation(
    client: AsyncClient, db_session: AsyncSession, mode: str
):
    token_a, user_a = await _login(client, db_session, "search-alice@example.com")
    token_b, user_b = await _login(client, db_session, "search-bob@example.com")

    doc_a = await _plant_document(
        db_session, user_a, "alice-doc", [("apple revenue growth analysis", _E0)]
    )
    doc_b = await _plant_document(
        db_session, user_b, "bob-doc", [("apple revenue fraud scheme", _E0)]
    )

    res = await _search(client, token_a, query="apple revenue", mode=mode, limit=10)
    assert res.status_code == 200, res.text
    results = res.json()
    assert results, "expected results for the querying user"
    doc_ids = {r["document_id"] for r in results}
    assert doc_ids == {str(doc_a.id)}
    assert str(doc_b.id) not in doc_ids

    # Bob searching his own content still finds it (symmetry check)
    res = await _search(client, token_b, query="apple revenue", mode=mode, limit=10)
    assert {r["document_id"] for r in res.json()} == {str(doc_b.id)}


@pytest.mark.asyncio
async def test_document_id_from_other_user_returns_no_results(
    client: AsyncClient, db_session: AsyncSession
):
    token_a, user_a = await _login(client, db_session, "search-thief@example.com")
    _, user_b = await _login(client, db_session, "search-victim@example.com")
    doc_b = await _plant_document(
        db_session, user_b, "victim-doc", [("apple revenue growth analysis", _E0)]
    )
    await _plant_document(
        db_session, user_a, "thief-doc", [("apple revenue growth analysis", _E0)]
    )

    res = await _search(
        client, token_a, query="apple", mode="vector", document_id=doc_b.id
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


@pytest.mark.asyncio
async def test_search_requires_authentication(client: AsyncClient):
    res = await client.get(SEARCH, params={"query": "anything"})
    assert res.status_code in (401, 403)


@pytest.mark.asyncio
async def test_invalid_mode_or_query_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    # Auth runs before query validation, so use a real token
    token, _ = await _login(client, db_session, "search-validation@example.com")

    res = await _search(client, token, query="anything", mode="keyword")
    assert res.status_code == 422

    res = await client.get(
        SEARCH,
        params={"query": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422

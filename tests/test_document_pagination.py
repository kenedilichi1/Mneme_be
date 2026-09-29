from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.documents.models.document_model import Document
from app.modules.user.models.user_model import User
from app.utils.timezone import utcnow

DOCS = "/api/v1/documents"
EMAIL = "pagination@example.com"


async def _login(client: AsyncClient, db_session: AsyncSession) -> str:
    res = await client.post("/api/v1/auth/request-otp", json={"email": EMAIL})
    assert res.status_code == 202

    user = (
        await db_session.execute(select(User).where(User.email == EMAIL))
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
        "/api/v1/auth/verify-otp", json={"email": EMAIL, "otp": "654321"}
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]["access_token"]


async def _plant_documents(
    db_session: AsyncSession, user: User, count: int
) -> list[Document]:
    """Plant `count` documents, newest first (index 0 == newest)."""
    docs = []
    base = utcnow()
    for i in range(count):
        doc = Document(
            user_id=user.id,
            title=f"doc-{i}",
            original_filename=f"doc-{i}.pdf",
            file_size=1,
            file_type="application/pdf",
            storage_key=f"{user.id}/doc-{i}.pdf",
            status="processed",
            created_at=base - timedelta(minutes=i),
        )
        db_session.add(doc)
        docs.append(doc)
    await db_session.flush()
    return docs


async def _list(client: AsyncClient, token: str, **params):
    res = await client.get(
        DOCS,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return [d["title"] for d in res.json()["data"]]


@pytest.mark.asyncio
async def test_list_returns_newest_by_default(
    client: AsyncClient, db_session: AsyncSession
):
    token = await _login(client, db_session)
    user = (
        await db_session.execute(select(User).where(User.email == EMAIL))
    ).scalar_one()
    await _plant_documents(db_session, user, 3)

    assert await _list(client, token) == ["doc-0", "doc-1", "doc-2"]


@pytest.mark.asyncio
async def test_limit_and_offset_page_through_documents(
    client: AsyncClient, db_session: AsyncSession
):
    token = await _login(client, db_session)
    user = (
        await db_session.execute(select(User).where(User.email == EMAIL))
    ).scalar_one()
    await _plant_documents(db_session, user, 5)

    assert await _list(client, token, limit=2) == ["doc-0", "doc-1"]
    assert await _list(client, token, limit=2, offset=2) == ["doc-2", "doc-3"]
    assert await _list(client, token, limit=2, offset=4) == ["doc-4"]
    assert await _list(client, token, limit=2, offset=10) == []


@pytest.mark.asyncio
async def test_pagination_params_are_validated(
    client: AsyncClient, db_session: AsyncSession
):
    token = await _login(client, db_session)
    headers = {"Authorization": f"Bearer {token}"}

    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        res = await client.get(DOCS, params=params, headers=headers)
        assert res.status_code == 422, params


@pytest.mark.asyncio
async def test_pagination_only_returns_own_documents(
    client: AsyncClient, db_session: AsyncSession
):
    token = await _login(client, db_session)
    user = (
        await db_session.execute(select(User).where(User.email == EMAIL))
    ).scalar_one()
    await _plant_documents(db_session, user, 3)

    other = User(email="pagination-other@example.com")
    db_session.add(other)
    await db_session.flush()
    await _plant_documents(db_session, other, 4)

    titles = await _list(client, token, limit=10)
    assert titles == ["doc-0", "doc-1", "doc-2"]

import hashlib
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.models.refresh_token_model import RefreshToken
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.user.models.user_model import User
from app.utils.timezone import utcnow

AUTH = "/api/v1/auth"
EMAIL = "refresh-user@example.com"


async def _login(client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    res = await client.post(f"{AUTH}/request-otp", json={"email": EMAIL})
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
        f"{AUTH}/verify-otp", json={"email": EMAIL, "otp": "654321"}
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]


async def _refresh(client: AsyncClient, refresh_token: str):
    return await client.post(f"{AUTH}/refresh", json={"refresh_token": refresh_token})


async def _user_tokens(db_session: AsyncSession, user_id) -> list[RefreshToken]:
    result = await db_session.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .order_by(RefreshToken.created_at)
    )
    return list(result.scalars())


async def test_verify_returns_refresh_token_stored_hashed(
    client: AsyncClient, db_session: AsyncSession
):
    tokens = await _login(client, db_session)
    assert "access_token" in tokens
    raw = tokens["refresh_token"]
    assert raw

    user = (
        await db_session.execute(select(User).where(User.email == EMAIL))
    ).scalar_one()
    rows = await _user_tokens(db_session, user.id)
    assert len(rows) == 1
    # Only the sha256 of the token is persisted, never the token itself
    assert rows[0].token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert rows[0].token_hash != raw
    assert rows[0].revoked_at is None
    assert rows[0].expires_at > utcnow()


async def test_refresh_rotates_and_rejects_reuse(
    client: AsyncClient, db_session: AsyncSession
):
    first = await _login(client, db_session)
    t1 = first["refresh_token"]

    res = await _refresh(client, t1)
    assert res.status_code == 200, res.text
    second = res.json()["data"]
    # The JWT is deterministic per second for the same user — the refresh
    # token is what must differ
    assert second["refresh_token"] != t1

    # The rotated access token is usable
    me = await client.get(
        f"{AUTH}/me", headers={"Authorization": f"Bearer {second['access_token']}"}
    )
    assert me.status_code == 200

    # Replaying the old refresh token is reuse: rejected, and the whole
    # family (including the just-issued token) is revoked
    reuse = await _refresh(client, t1)
    assert reuse.status_code == 401

    replay_new = await _refresh(client, second["refresh_token"])
    assert replay_new.status_code == 401


async def test_logout_revokes_token_and_is_idempotent(
    client: AsyncClient, db_session: AsyncSession
):
    tokens = await _login(client, db_session)
    raw = tokens["refresh_token"]

    res = await client.post(f"{AUTH}/logout", json={"refresh_token": raw})
    assert res.status_code == 200
    assert (await _refresh(client, raw)).status_code == 401

    # Logging out twice still succeeds
    res = await client.post(f"{AUTH}/logout", json={"refresh_token": raw})
    assert res.status_code == 200


async def test_refresh_rejects_unknown_token(client: AsyncClient):
    res = await _refresh(client, "not-a-real-token")
    assert res.status_code == 401


async def test_refresh_rejects_expired_token(
    client: AsyncClient, db_session: AsyncSession
):
    tokens = await _login(client, db_session)
    user = (
        await db_session.execute(select(User).where(User.email == EMAIL))
    ).scalar_one()
    rows = await _user_tokens(db_session, user.id)
    rows[0].expires_at = utcnow() - timedelta(days=1)
    await db_session.flush()

    assert (await _refresh(client, tokens["refresh_token"])).status_code == 401

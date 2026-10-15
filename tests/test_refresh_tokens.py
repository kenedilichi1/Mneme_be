import hashlib
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.models.refresh_token_model import RefreshToken
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.user.models.user_model import User
from app.utils.timezone import utcnow

AUTH = "/api/v1/auth"
EMAIL = "refresh-user@example.com"
COOKIE = settings.refresh_cookie_name


async def _login(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    body_delivery: bool = False,
    email: str = EMAIL,
):
    """Run the OTP login. Returns the verify-otp response.

    body_delivery=True is the native-client mode: the refresh token is in the
    response body. Default is the browser mode: cookie only, body has none.
    """
    res = await client.post(f"{AUTH}/request-otp", json={"email": email})
    assert res.status_code == 202

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

    payload = {"email": email, "otp": "654321"}
    if body_delivery:
        payload["deliver_refresh_token"] = True
    res = await client.post(f"{AUTH}/verify-otp", json=payload)
    assert res.status_code == 200, res.text
    return res


async def _refresh(client: AsyncClient, refresh_token: str):
    return await client.post(f"{AUTH}/refresh", json={"refresh_token": refresh_token})


async def _user_tokens(db_session: AsyncSession, user_id) -> list[RefreshToken]:
    result = await db_session.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .order_by(RefreshToken.created_at)
    )
    return list(result.scalars())


def _set_cookie_header(res) -> str:
    return "; ".join(res.headers.get_list("set-cookie"))


# ---------------------------------------------------------------------------
# Body delivery (native clients)
# ---------------------------------------------------------------------------


async def test_verify_returns_refresh_token_stored_hashed(
    client: AsyncClient, db_session: AsyncSession
):
    res = await _login(client, db_session, body_delivery=True)
    tokens = res.json()["data"]
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
    first = await _login(client, db_session, body_delivery=True)
    t1 = first.json()["data"]["refresh_token"]

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
    tokens = (await _login(client, db_session, body_delivery=True)).json()["data"]
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
    tokens = (await _login(client, db_session, body_delivery=True)).json()["data"]
    user = (
        await db_session.execute(select(User).where(User.email == EMAIL))
    ).scalar_one()
    rows = await _user_tokens(db_session, user.id)
    rows[0].expires_at = utcnow() - timedelta(days=1)
    await db_session.flush()

    assert (await _refresh(client, tokens["refresh_token"])).status_code == 401


# ---------------------------------------------------------------------------
# Cookie delivery (browser clients — the default)
# ---------------------------------------------------------------------------


async def test_verify_otp_sets_http_only_cookie_and_omits_body_token(
    client: AsyncClient, db_session: AsyncSession
):
    res = await _login(client, db_session)  # browser mode: no flag
    data = res.json()["data"]

    # The token is NOT in the body — XSS cannot read it
    assert data["refresh_token"] is None
    assert data["access_token"]

    # …it travels as an httpOnly cookie scoped to the auth routes
    header = _set_cookie_header(res)
    assert header, "expected a Set-Cookie header"
    assert f"{COOKIE}=" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/api/v1/auth" in header
    assert "Max-Age=" in header

    # The cookie carries the raw token whose hash is stored server-side
    user = (
        await db_session.execute(select(User).where(User.email == EMAIL))
    ).scalar_one()
    rows = await _user_tokens(db_session, user.id)
    raw = client.cookies.get(COOKIE)
    assert raw
    assert rows[0].token_hash == hashlib.sha256(raw.encode()).hexdigest()


async def test_refresh_from_cookie_rotates_and_keeps_token_out_of_body(
    client: AsyncClient, db_session: AsyncSession
):
    await _login(client, db_session)  # cookie now in the client jar
    old_cookie = client.cookies.get(COOKIE)
    assert old_cookie

    # No body token at all — the cookie rides along automatically
    res = await client.post(f"{AUTH}/refresh", json={})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"] is None  # never handed back in the body

    # The cookie was rotated (old value no longer valid)
    new_cookie = client.cookies.get(COOKIE)
    assert new_cookie and new_cookie != old_cookie

    # New access token works; the jar now holds the rotated cookie
    me = await client.get(
        f"{AUTH}/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.status_code == 200

    # Replaying the stale cookie is reuse detection: family revoked
    client.cookies.clear()
    client.cookies.set(COOKIE, old_cookie)
    stale = await client.post(f"{AUTH}/refresh", json={})
    assert stale.status_code == 401


async def test_refresh_without_cookie_or_body_is_401(
    client: AsyncClient, db_session: AsyncSession
):
    await _login(client, db_session)
    client.cookies.clear()

    res = await client.post(f"{AUTH}/refresh", json={})
    assert res.status_code == 401
    assert res.json()["error"]["message"] == "Missing refresh token"


async def test_logout_via_cookie_revokes_and_clears_cookie(
    client: AsyncClient, db_session: AsyncSession
):
    await _login(client, db_session)
    raw = client.cookies.get(COOKIE)
    assert raw

    res = await client.post(f"{AUTH}/logout", json={})
    assert res.status_code == 200, res.text
    # Cookie is actively cleared (expired), not just left behind
    header = _set_cookie_header(res)
    assert f"{COOKIE}=" in header
    assert "Max-Age=0" in header or f'{COOKIE}=""' in header

    # The revoked token fails even when replayed by hand
    client.cookies.clear()
    client.cookies.set(COOKIE, raw)
    assert (await client.post(f"{AUTH}/refresh", json={})).status_code == 401

    # Idempotent with no session at all
    client.cookies.clear()
    assert (await client.post(f"{AUTH}/logout", json={})).status_code == 200

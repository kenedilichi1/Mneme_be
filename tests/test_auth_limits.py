"""MNE-11 + MNE-12: per-email OTP lockout and shared rate limits."""

import asyncio
import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.user.models.user_model import User

KNOWN_CODE = "654321"


async def seed_user_with_otp(db_session: AsyncSession, email: str, code: str) -> User:
    user = User(email=email)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        OtpCode(
            user_id=user.id,
            code_hash=hash_otp_code(code),
            expires_at=otp_expiry(),
        )
    )
    await db_session.flush()
    return user


async def failed_attempts(db_session: AsyncSession, email: str) -> int:
    return (
        await db_session.execute(
            text("SELECT failed_otp_attempts FROM users WHERE email = :email"),
            {"email": email},
        )
    ).scalar_one()


def unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


async def verify(client: AsyncClient, email: str, otp: str, headers: dict | None = None):
    return await client.post(
        "/api/v1/auth/verify-otp", json={"email": email, "otp": otp}, headers=headers or {}
    )


# ---------------------------------------------------------------------------
# Per-email lockout (MNE-11)
# ---------------------------------------------------------------------------


async def test_lockout_after_five_wrong_guesses(client: AsyncClient, db_session: AsyncSession):
    email = unique_email("lockout")
    await seed_user_with_otp(db_session, email, KNOWN_CODE)

    for _ in range(settings.OTP_MAX_FAILED_ATTEMPTS):
        response = await verify(client, email, "000000")
        assert response.status_code == 400

    assert await failed_attempts(db_session, email) == settings.OTP_MAX_FAILED_ATTEMPTS

    # Even the correct code is rejected while the lockout window is open
    response = await verify(client, email, KNOWN_CODE)
    assert response.status_code == 429
    assert "retry-after" in response.headers


async def test_lockout_survives_requesting_a_new_otp(
    client: AsyncClient, db_session: AsyncSession
):
    email = unique_email("no-reset")
    await seed_user_with_otp(db_session, email, KNOWN_CODE)

    for _ in range(settings.OTP_MAX_FAILED_ATTEMPTS):
        assert (await verify(client, email, "000000")).status_code == 400

    # A fresh OTP used to hand out a fresh guess budget — it must not
    reissue = await client.post("/api/v1/auth/request-otp", json={"email": email})
    assert reissue.status_code == 202

    response = await verify(client, email, KNOWN_CODE)
    assert response.status_code == 429


async def test_successful_verify_resets_the_counter(
    client: AsyncClient, db_session: AsyncSession
):
    email = unique_email("reset")
    await seed_user_with_otp(db_session, email, KNOWN_CODE)

    assert (await verify(client, email, "000000")).status_code == 400
    assert await failed_attempts(db_session, email) == 1

    assert (await verify(client, email, KNOWN_CODE)).status_code == 200
    assert await failed_attempts(db_session, email) == 0


async def test_lockout_window_expires(client: AsyncClient, db_session: AsyncSession):
    email = unique_email("window")
    await seed_user_with_otp(db_session, email, KNOWN_CODE)

    for _ in range(settings.OTP_MAX_FAILED_ATTEMPTS):
        assert (await verify(client, email, "000000")).status_code == 400
    assert (await verify(client, email, KNOWN_CODE)).status_code == 429

    # Age the window past the lockout period; the counter must start over
    await db_session.execute(
        text(
            "UPDATE users SET otp_attempts_window_started_at = now() - make_interval(mins => :m)"
        ),
        {"m": settings.OTP_LOCKOUT_MINUTES + 1},
    )

    response = await verify(client, email, KNOWN_CODE)
    assert response.status_code == 200


async def test_issuing_a_new_otp_deletes_the_older_ones(
    client: AsyncClient, db_session: AsyncSession
):
    email = unique_email("purge")
    await seed_user_with_otp(db_session, email, "111111")
    old_hash = (
        await db_session.execute(
            text("SELECT code_hash FROM otp_codes o JOIN users u ON u.id = o.user_id "
                 "WHERE u.email = :email"),
            {"email": email},
        )
    ).scalar_one()

    response = await client.post("/api/v1/auth/request-otp", json={"email": email})
    assert response.status_code == 202

    row_count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM otp_codes o JOIN users u ON u.id = o.user_id "
                "WHERE u.email = :email"
            ),
            {"email": email},
        )
    ).scalar_one()
    assert row_count == 1

    new_hash = (
        await db_session.execute(
            text("SELECT code_hash FROM otp_codes o JOIN users u ON u.id = o.user_id "
                 "WHERE u.email = :email"),
            {"email": email},
        )
    ).scalar_one()
    assert new_hash != old_hash

    # The old code no longer verifies
    assert (await verify(client, email, "111111")).status_code == 400


# ---------------------------------------------------------------------------
# Rate limits: shared storage + trusted proxy headers (MNE-12)
# ---------------------------------------------------------------------------


async def test_request_otp_limited_per_ip(client: AsyncClient):
    for i in range(settings.OTP_REQUEST_LIMIT_PER_MINUTE):
        response = await client.post(
            "/api/v1/auth/request-otp", json={"email": unique_email(f"ip-{i}")}
        )
        assert response.status_code == 202

    response = await client.post(
        "/api/v1/auth/request-otp", json={"email": unique_email("ip-over")}
    )
    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded"
    assert "retry-after" in response.headers


async def test_request_otp_limited_per_email_across_ips(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    email = unique_email("email-limit")

    for i in range(settings.OTP_REQUEST_LIMIT_PER_MINUTE):
        response = await client.post(
            "/api/v1/auth/request-otp",
            json={"email": email},
            headers={"X-Forwarded-For": f"203.0.113.{i + 1}"},
        )
        assert response.status_code == 202

    response = await client.post(
        "/api/v1/auth/request-otp",
        json={"email": email},
        headers={"X-Forwarded-For": "203.0.113.99"},
    )
    assert response.status_code == 429


async def test_forwarded_for_ignored_when_untrusted(client: AsyncClient):
    # TRUST_PROXY_HEADERS defaults to False: rotating X-Forwarded-For must not
    # buy extra budget — all requests share the connecting IP's counter.
    for i in range(settings.OTP_REQUEST_LIMIT_PER_MINUTE + 1):
        response = await client.post(
            "/api/v1/auth/request-otp",
            json={"email": unique_email(f"spoof-{i}")},
            headers={"X-Forwarded-For": f"198.51.100.{i + 1}"},
        )
        expected = 429 if i == settings.OTP_REQUEST_LIMIT_PER_MINUTE else 202
        assert response.status_code == expected


async def test_verify_otp_limited_per_email_across_ips(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "OTP_MAX_FAILED_ATTEMPTS", 10_000)  # isolate the limiter
    email = unique_email("verify-limit")
    await seed_user_with_otp(db_session, email, KNOWN_CODE)

    for i in range(settings.OTP_VERIFY_LIMIT_PER_MINUTE):
        response = await verify(
            client, email, "000000", headers={"X-Forwarded-For": f"203.0.113.{i + 1}"}
        )
        assert response.status_code == 400

    response = await verify(
        client, email, "000000", headers={"X-Forwarded-For": "203.0.113.250"}
    )
    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded"


# ---------------------------------------------------------------------------
# Parallel guesses cannot race the counter (MNE-11)
# ---------------------------------------------------------------------------


async def seed_user_with_otp_committed(engine, email: str, code: str) -> None:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email=email)
        session.add(user)
        await session.flush()
        session.add(
            OtpCode(
                user_id=user.id,
                code_hash=hash_otp_code(code),
                expires_at=otp_expiry(),
            )
        )
        await session.commit()


async def test_parallel_wrong_guesses_cannot_exceed_the_limit(
    isolated_client: AsyncClient, test_engine
):
    email = unique_email("parallel")
    await seed_user_with_otp_committed(test_engine, email, KNOWN_CODE)

    responses = await asyncio.gather(
        *(
            verify(isolated_client, email, "000000")
            for _ in range(settings.OTP_MAX_FAILED_ATTEMPTS + 5)
        )
    )

    # No parallel guess ever succeeded
    assert all(r.status_code != 200 for r in responses)

    # At most OTP_MAX_FAILED_ATTEMPTS guesses were actually checked against
    # the code; every other request was rejected by the claim or a limiter.
    checked = [r for r in responses if r.status_code == 400]
    assert len(checked) <= settings.OTP_MAX_FAILED_ATTEMPTS

    assert set(r.status_code for r in responses) <= {400, 429}

    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with factory() as session:
        attempts = (
            await session.execute(
                text("SELECT failed_otp_attempts FROM users WHERE email = :email"),
                {"email": email},
            )
        ).scalar_one()
    assert attempts <= settings.OTP_MAX_FAILED_ATTEMPTS

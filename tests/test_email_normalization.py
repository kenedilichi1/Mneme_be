"""MNE-13: email normalization, case-duplicate migration, concurrent signup.

An address is identity: `Foo@x.com` and `foo@x.com` must resolve to the same
account. The application normalizes on every write and lookup, and the unique
index on lower(email) lets the database back that up even when two signups
race.
"""

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.user.models.user_model import User
from app.modules.user.repositories.user_repository import UserRepository
from app.modules.user.services.user_service import UserService

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


def unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


async def stored_email(db_session: AsyncSession, email: str) -> str | None:
    return (
        await db_session.execute(
            text("SELECT email FROM users WHERE lower(email) = lower(:email)"),
            {"email": email},
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Normalization on write and lookup
# ---------------------------------------------------------------------------


async def test_signup_stores_the_canonical_form(client: AsyncClient, db_session):
    email = f"Canon-{uuid.uuid4().hex[:12]}@Example.COM"

    response = await client.post("/api/v1/auth/request-otp", json={"email": email})
    assert response.status_code == 202

    stored = await stored_email(db_session, email)
    assert stored == email.lower()


async def test_signup_normalizes_whitespace_and_case_at_the_service(
    db_session: AsyncSession,
):
    service = UserService(UserRepository(db_session))
    email = unique_email("Service")

    user = await service.get_or_create_by_email(f"  {email.upper()}  ")

    assert user.email == email.lower()


async def test_verify_with_a_different_casing_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    email = unique_email("casing")
    await seed_user_with_otp(db_session, email, KNOWN_CODE)

    response = await client.post(
        "/api/v1/auth/verify-otp",
        json={"email": email.upper(), "otp": KNOWN_CODE},
    )
    assert response.status_code == 200


async def test_case_variants_share_one_account(client: AsyncClient, db_session):
    base = unique_email("shared")
    variants = [base, base.upper(), base.replace("example", "Example")]

    for variant in variants:
        response = await client.post(
            "/api/v1/auth/request-otp", json={"email": variant}
        )
        assert response.status_code == 202

    count = (
        await db_session.execute(
            text("SELECT count(*) FROM users WHERE lower(email) = lower(:email)"),
            {"email": base},
        )
    ).scalar_one()
    assert count == 1


async def test_database_rejects_case_duplicate_rows(db_session: AsyncSession):
    base = unique_email("dbdup")
    db_session.add(User(email=base))
    await db_session.flush()

    # Bypasses the service on purpose: the constraint, not the code, is the
    # last line of defence.
    db_session.add(User(email=base.upper()))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Concurrent signup cannot create two accounts (MNE-13)
# ---------------------------------------------------------------------------


async def test_concurrent_signup_with_case_variants_creates_one_user(
    isolated_client: AsyncClient, test_engine
):
    base = unique_email("race")

    responses = await asyncio.gather(
        *(
            isolated_client.post("/api/v1/auth/request-otp", json={"email": email})
            for email in (base, base.upper())
        )
    )
    assert [r.status_code for r in responses] == [202, 202]

    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            await session.execute(
                text("SELECT email FROM users WHERE lower(email) = lower(:email)"),
                {"email": base},
            )
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == base.lower()

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.user.repositories.user_repository import UserRepository


async def test_full_auth_flow(client: AsyncClient, db_session: AsyncSession):
    email = "integration_test_user@example.com"

    # 1. Signup — creates user + OTP in DB, sends (or logs) OTP email
    signup_res = await client.post("/api/v1/auth/request-otp", json={"email": email})
    assert signup_res.status_code == 202
    assert signup_res.json() == {"message": "OTP sent"}

    # 2. Confirm user was created
    user_repo = UserRepository(db_session)
    user = await user_repo.get_by_email(email)
    assert user is not None

    # 3. Delete the auto-generated OTP and replace it with a known value
    otp_repo = OtpCodeRepository(db_session)
    await otp_repo.delete_otp_code(user.id)
    await otp_repo.create(
        OtpCode(
            user_id=user.id,
            code_hash=hash_otp_code("654321"),
            expires_at=otp_expiry(),
        )
    )

    # 4. Verify with the known OTP
    verify_res = await client.post(
        "/api/v1/auth/verify-otp",
        json={"email": email, "otp": "654321"},
    )
    assert verify_res.status_code == 200
    token_data = verify_res.json()
    assert "access_token" in token_data
    access_token = token_data["access_token"]

    # 5. Fetch /me with the issued token
    me_res = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_res.status_code == 200
    user_data = me_res.json()
    assert user_data["email"] == email
    assert user_data["id"] == str(user.id)


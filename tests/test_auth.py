import pytest
from app.modules.auth.services.secrets import (
    create_access_token,
    decode_access_token,
    generate_otp_code,
    hash_otp_code,
    otp_expiry,
    verify_otp_code,
)


def test_generate_otp_code():
    code = generate_otp_code()
    assert len(code) == 6
    assert code.isdigit()


def test_hash_and_verify_otp_code():
    code = "123456"
    hashed = hash_otp_code(code)

    print("hashed:", repr(hashed))
    assert hashed != code
    assert verify_otp_code("123456", hashed) is True
    assert verify_otp_code("654321", hashed) is False


def test_otp_expiry():
    expiry = otp_expiry()
    assert expiry is not None


def test_jwt_access_token():
    user_id = "12345678-1234-5678-1234-567812345678"
    token = create_access_token(user_id)
    assert isinstance(token, str)
    decoded_id = decode_access_token(token)
    assert decoded_id == user_id


def test_jwt_invalid_token():
    assert decode_access_token("invalid.token.here") is None

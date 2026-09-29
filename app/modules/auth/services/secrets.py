import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from jwt import InvalidTokenError


from app.core.config import settings


def generate_otp_code() -> str:
    """Cryptographically secure 6-digit code, e.g. '048213'."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _otp_hmac(code: str) -> str:
    # Keyed HMAC instead of a memory-hard hash: OTPs are 6 digits with a
    # 10-minute life, and Argon2 ran directly on the event loop, stalling
    # every other request while it hashed (MNE-14).
    key = (settings.otp_hash_key or "").encode()
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()


def hash_otp_code(code: str) -> str:
    return _otp_hmac(code)


def verify_otp_code(code: str, code_hash: str) -> bool:
    # Single well-known hash (no per-row salt) — compare_digest keeps it constant-time
    return hmac.compare_digest(_otp_hmac(code), code_hash)


def otp_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=settings.otp_expire_minutes)


def generate_refresh_token() -> str:
    """256-bit URL-safe random string; only its sha256 is ever stored."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    # Plain sha256 is enough: the input is high-entropy, not guessable like a
    # 6-digit OTP (which needs the keyed HMAC above).
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    """Returns the user_id (sub claim) if valid, else None."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except InvalidTokenError:
        return None

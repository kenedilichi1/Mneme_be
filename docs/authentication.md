# Authentication & security

Passwordless login by one-time code (OTP) delivered by email, short-lived JWT
access tokens, and rotating opaque refresh tokens.

Implementation lives in `app/modules/auth/` — `secrets.py` (crypto
primitives), `services/auth_service.py` (flows), `routers/auth_router.py`
(HTTP), `dependencies/auth_dependency.py` (current-user injection).

## Login flow

```
client                          API
  │  POST /api/v1/auth/request-otp    { "email": "..." }
  │──────────────────────────────────▶│  create user if missing
  │                                   │  store HMAC(code) in otp_codes
  │                                   │  email code (Resend)  [DEBUG: log it]
  │◀────── 202 Accepted ──────────────│
  │
  │  POST /api/v1/auth/verify-otp     { "email": "...", "otp": "123456" }
  │──────────────────────────────────▶│  verify HMAC + expiry + lockout
  │                                   │  delete used code
  │◀── 200 { access_token, refresh_token, token_type }
```

- The OTP is 6 digits, valid `otp_expire_minutes` (default 10), stored only as
  a keyed HMAC-SHA256 hash (`secrets.hash_otp_code`) — the raw code is never
  persisted, so a DB leak cannot log anyone in.
- `verify-otp` is single-use: the code row is deleted on success.
- In `DEBUG=true` (development only) the code is logged instead of emailed.
  Production refuses to start with `DEBUG=true`.

## Tokens

| | Access token | Refresh token |
|---|---|---|
| Type | JWT (HS256) | opaque random string |
| Lifetime | `jwt_expire_minutes` (15) | `refresh_token_expire_days` (30) |
| Storage | client memory only | hashed (SHA-256) in `refresh_tokens` table |
| Secret | `jwt_secret` | generated per token, hash stored server-side |

- `POST /auth/refresh` — **rotation**: the presented token is atomically
  claimed (`UPDATE … WHERE revoked_at IS NULL`), a new refresh token + new
  access token are issued.
- **Reuse detection**: presenting an already-rotated token means it was
  stolen. The *entire family* of live tokens for that user is revoked
  immediately and the request fails with 401. The revocation runs on the
  security session so it commits even on the 401 path.
- `POST /auth/logout` — idempotent: revokes every live refresh token for the
  user. Safe to call twice.

## Brute-force protection

Two independent layers (both Postgres-backed so restarts don't reset them):

1. **OTP lockout** (per email, across all codes):
   - `OTP_MAX_FAILED_ATTEMPTS` (5) failed verifications open a window of
     `OTP_LOCKOUT_MINUTES` (15).
   - Counters (`failed_otp_attempts`, `otp_attempts_window_started_at`) live
     on the `users` row and are updated through `get_security_session` —
     they commit even when the request fails, so a rejected verify still
     counts.
   - Correct verification resets the counter.
2. **Rate limits** (fixed window in the `rate_limits` table), each enforced
   **twice** — per IP and per email, so rotating IPs or sharing an IP can't
   dilute the budget:
   - global: `GLOBAL_RATE_LIMIT_PER_MINUTE` (100/min) on all `/api/v1` routes
   - `POST /auth/request-otp`: `OTP_REQUEST_LIMIT_PER_MINUTE` (5/min)
   - `POST /auth/verify-otp`: `OTP_VERIFY_LIMIT_PER_MINUTE` (10/min)

   The client IP comes from `X-Forwarded-For` only when
   `TRUST_PROXY_HEADERS=true`; otherwise the socket peer address is used.
   Never enable it without a trusted proxy — the header is client-controlled.

## Key material & rotation

| Setting | Used for | Rotation procedure |
|---|---|---|
| `jwt_secret` | signing/verifying access JWTs | new secret instantly invalidates all access tokens (users re-login; 15-min window anyway). ≥ 32 random chars in production: `openssl rand -hex 32` |
| `otp_hash_key` | HMAC key for OTP codes | rotate freely: only invalidates outstanding codes (≤ 10 min old). ≥ 32 random chars in production |

Both keys are validated at startup when `ENVIRONMENT=production`
(`Settings._check_production_safety`): example values like `change-me` are
rejected outright.

## Endpoint summary

| Endpoint | Auth | Rate limit |
|---|---|---|
| `POST /api/v1/auth/request-otp` | none | 5/min per IP + global |
| `POST /api/v1/auth/verify-otp` | none | 10/min per IP + global |
| `POST /api/v1/auth/refresh` | refresh token in body | global |
| `POST /api/v1/auth/logout` | refresh token in body | global |
| `GET /api/v1/auth/me` | `Authorization: Bearer <access>` | global |

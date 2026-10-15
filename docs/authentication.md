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
  │◀── 200 { access_token, token_type, refresh_token: null }
  │    + Set-Cookie: mneme_refresh=<refresh> (HttpOnly, SameSite=Lax, Path=/api/v1/auth)
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
| Delivery | `Authorization` header | browser: httpOnly cookie; native: response body (opt-in) |
| Storage | client memory only | hashed (SHA-256) in `refresh_tokens` table |
| Secret | `jwt_secret` | generated per token, hash stored server-side |

### Refresh-token delivery: cookie vs body

- **Browser (default)** — `verify-otp` sets the refresh token as an
  **httpOnly cookie** (`mneme_refresh`, `Path=/api/v1/auth`,
  `SameSite=Lax`, `Secure` in production) and returns `refresh_token: null`
  in the body. JavaScript cannot read it; XSS cannot mint a new session once
  the 15-min access token expires. `refresh` and `logout` read the cookie
  automatically — no body needed.
- **Native clients** — send `"deliver_refresh_token": true` to `verify-otp`;
  the refresh token then appears in the body, and `refresh`/`logout` take it
  in the JSON body instead of the cookie.
- Cookie attributes are configurable: `refresh_cookie_name`,
  `refresh_cookie_samesite` (`lax`/`strict`/`none`; `none` implies `Secure`
  and requires HTTPS — use only when the web app is on another registrable
  domain).

- `POST /auth/refresh` — **rotation**: the presented token (cookie or body)
  is atomically claimed (`UPDATE … WHERE revoked_at IS NULL`), a new refresh
  token + new access token are issued. The cookie is re-set with the rotated
  value; `refresh_token` stays `null` in the body for cookie-mode clients.
  No token anywhere → 401 `Missing refresh token`.
- **Reuse detection**: presenting an already-rotated token means it was
  stolen. The *entire family* of live tokens for that user is revoked
  immediately and the request fails with 401. The revocation runs on the
  security session so it commits even on the 401 path.
- `POST /auth/logout` — idempotent: revokes every live refresh token for the
  user (token from cookie or body) and always clears the cookie. Safe to
  call twice, and with no session at all.

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
| `POST /api/v1/auth/refresh` | refresh token: cookie **or** optional body | global |
| `POST /api/v1/auth/logout` | refresh token: cookie **or** optional body | global |
| `GET /api/v1/auth/me` | `Authorization: Bearer <access>` | global |

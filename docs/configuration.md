# Configuration

All settings live in `app/core/config.py` (`Settings`, pydantic-settings) and
are read from `.env` (copy `.env.example` → `.env`). Keys are
case-insensitive; unknown keys are ignored.

Some values are **required with no default** — the app refuses to start
without them: `jwt_secret`, `RESEND_API_KEY`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `B2_KEY_ID`, `B2_APPLICATION_KEY`, `B2_BUCKET_NAME`,
`B2_S3_ENDPOINT`, `RABBITMQ_URL`.

Derived values (not set directly):

| Property | Meaning |
|---|---|
| `get_db_name` | `{POSTGRES_DB}_{ENVIRONMENT}` — e.g. `mneme_development`, `mneme_production` |
| `database_url` | async URL (`postgresql+asyncpg://…`) used by the app |
| `sync_database_url` | sync URL (`postgresql+psycopg2://…`) used by Alembic |

## Environment variables

### App

| Variable | Default | Notes |
|---|---|---|
| `APP_NAME` | `Mneme API` | shown in Swagger title |
| `APP_VERSION` | `1.0.0` | |
| `DEBUG` | `false` | logs OTP codes, skips sending email. **Rejected in production** |
| `SQL_ECHO` | `false` | logs every SQL statement. **Rejected in production** |
| `LOG_LEVEL` | `INFO` | `DEBUG`…`CRITICAL` |
| `ENVIRONMENT` | `development` | `development` \| `testing` \| `production`; also names the DB and queue |
| `CORS_ORIGINS` | `["*"]` | JSON list of browser origins. `*` **rejected in production** (and disables credentials) |

### PostgreSQL

| Variable | Default | Notes |
|---|---|---|
| `POSTGRES_USER` | — | required |
| `POSTGRES_PASSWORD` | — | required |
| `POSTGRES_HOST` | `localhost` | `db` inside compose |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `mneme` | base name; suffixed with `_ENVIRONMENT` |

### Auth

| Variable | Default | Notes |
|---|---|---|
| `jwt_secret` | — | JWT signing key. Production: ≥ 32 chars, not an example value (`openssl rand -hex 32`) |
| `jwt_algorithm` | `HS256` | |
| `otp_hash_key` | `""` | HMAC key for hashing OTP codes. Production: ≥ 32 chars. Rotation only invalidates outstanding codes (≤ 10 min) |
| `otp_expire_minutes` | `10` | OTP lifetime |
| `jwt_expire_minutes` | `15` | access-token lifetime |
| `refresh_token_expire_days` | `30` | refresh-token lifetime |

### Brute-force protection

| Variable | Default | Notes |
|---|---|---|
| `OTP_MAX_FAILED_ATTEMPTS` | `5` | failures per email before lockout opens |
| `OTP_LOCKOUT_MINUTES` | `15` | lockout window length |
| `GLOBAL_RATE_LIMIT_PER_MINUTE` | `100` | all `/api/v1` routes |
| `OTP_REQUEST_LIMIT_PER_MINUTE` | `5` | request-otp, per IP **and** per email |
| `OTP_VERIFY_LIMIT_PER_MINUTE` | `10` | verify-otp, per IP **and** per email |
| `TRUST_PROXY_HEADERS` | `false` | honour `X-Forwarded-For` for rate-limit IPs — only behind a trusted proxy, or clients can spoof it |

Rate limits are stored in Postgres (`rate_limits` table), so all processes
share them; no Redis.

### Email

| Variable | Default | Notes |
|---|---|---|
| `RESEND_API_KEY` | — | required (production validator enforces it; without it OTP emails aren't sent) |
| `EMAIL_FROM` | `Mneme <onboarding@resend.dev>` | |

### Storage (Backblaze B2)

| Variable | Default | Notes |
|---|---|---|
| `B2_KEY_ID` | — | application key ID |
| `B2_APPLICATION_KEY` | — | application key secret |
| `B2_BUCKET_NAME` | — | |
| `B2_S3_ENDPOINT` | — | bucket endpoint, e.g. `https://s3.us-west-004.backblazeb2.com`; scheme-less form accepted |
| `MAX_UPLOAD_BYTES` | `104857600` (100 MB) | enforced at create and confirm |
| `UPLOAD_URL_EXPIRE_SECONDS` | `900` | presigned PUT lifetime |

### Embeddings & chunking

| Variable | Default | Notes |
|---|---|---|
| `EMBEDDING_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | local SentenceTransformer model |
| `EMBEDDING_MODEL_REVISION` | `e9b6763…d9aab` | exact HF commit. Production: must be 40 hex chars — never a branch. Bump only together with re-embedding ([search.md](search.md#changing-the-model-or-revision)) |
| `EMBEDDING_DIMENSION` | `768` | must match the model |
| `MAX_CHUNK_TOKENS` | `512` | per-chunk budget in Nomic tokens |

### RabbitMQ

| Variable | Default | Notes |
|---|---|---|
| `RABBITMQ_URL` | — | required, e.g. `amqp://guest:guest@localhost:5672/` |
| `WORKER_PREFETCH_COUNT` | `1` | jobs a worker holds at once; keep at 1 so queued jobs don't hit the broker's ack timeout |

## Production safety validator

When `ENVIRONMENT=production`, a model validator runs at startup and raises
(a single error listing every problem) if any of these is violated:

- `DEBUG` must be false
- `SQL_ECHO` must be false
- `CORS_ORIGINS` must not contain `*`
- `jwt_secret` ≥ 32 chars and not an example value
- `otp_hash_key` ≥ 32 chars
- `EMBEDDING_MODEL_REVISION` must be a pinned 40-char commit hash
- `RESEND_API_KEY` must be set

Validation errors never echo setting values into logs
(`hide_input_in_errors=True`), so secrets stay out of crash output.

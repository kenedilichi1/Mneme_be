# Local development

## Prerequisites

- Docker + Docker Compose
- [`uv`](https://github.com/astral-sh/uv) for Python/env management
- A RabbitMQ reachable from your machine — dev `.env.example` points at
  localhost; the real dev `.env` may point at a managed broker (e.g.
  CloudAMQP). Any `RABBITMQ_URL` the API and worker can resolve works.

## Setup

```bash
cp .env.example .env          # then fill in real values
uv sync                       # install dependencies (incl. dev group)

docker compose up -d db       # PostgreSQL 18 + pgvector
uv run alembic upgrade head   # apply migrations
```

Required `.env` values before the app starts:
`jwt_secret`, `RESEND_API_KEY`, `POSTGRES_*`, `B2_*`, `RABBITMQ_URL`.
See [configuration.md](configuration.md).

## Running

**All-in-compose (matches deployments):**

```bash
docker compose up -d --build  # server + worker + db
```

**Hybrid (fast API iteration with reload, DB in Docker):**

```bash
docker compose up -d db worker        # db + worker
uv run fastapi dev app/main.py        # API on :8000 with auto-reload
```

The worker has no reload loop; restart it after worker changes:
`docker compose restart worker`.

Services:

| Service | Command (compose) | Port |
|---|---|---|
| server | `fastapi dev --host 0.0.0.0 app/main.py` | 8000 |
| worker | `python -m app.worker.main` | — |
| db | `pgvector/pgvector:pg18` | 5432 |

Health checks: `GET /health`, `GET /` (root).
Interactive API docs: **http://localhost:8000/docs**.

## Testing the flow from Swagger

1. `POST /api/v1/auth/request-otp` with `{"email": "you@example.com"}`
   - With `DEBUG=true` the code is printed in the **server logs** instead of
     being emailed.
2. `POST /api/v1/auth/verify-otp` with `{"email": "...", "otp": "......"}`
   → returns `access_token` + `refresh_token`.
   Click **Authorize** (lock icon) and enter `Bearer <access_token>` — every
   protected route now sends it.
3. `POST /api/v1/documents` → returns a document plus `upload_url`.
   Upload the file with `curl`:
   ```bash
   curl -X PUT -H "<content-type header from upload_headers>" \
        --upload-file ./sample.pdf "<upload_url>"
   ```
4. `PATCH /api/v1/documents/{id}/confirm` → queues processing.
   Poll `GET /api/v1/documents/{id}` until `status` becomes `processed`
   (worker logs each stage).
5. `GET /api/v1/search?query=...&mode=hybrid` → chunks from your document.

## Worker caveats

- **First run downloads models.** The embedding model (and Docling's
  components) are fetched into the `hf-cache` Docker volume
  (`HF_HOME=/app/.cache/huggingface`) the first time a document is
  processed — hundreds of MB, one time per volume. The model is *not* baked
  into the image.
- **Broker must resolve.** If the worker logs
  `AMQPConnectionError: [Errno -5] No address associated with hostname`,
  `RABBITMQ_URL`'s host doesn't resolve from inside the container — see
  [operations.md](operations.md#troubleshooting).
- Queue names embed `ENVIRONMENT` (`document_processing.development`), so
  running against a shared broker won't collide with production.

## Useful commands

```bash
uv run pytest -q                 # full test suite (needs Docker)
uv run alembic upgrade head      # migrations
uv run alembic current           # applied revision
docker compose logs -f worker    # worker output
docker compose down              # stop everything (volumes persist)
```

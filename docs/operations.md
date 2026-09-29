# Operations

Day-2 concerns: deploying, running migrations, workers, re-embedding, and
what to do when things break.

## Deployment

Two long-lived processes, one image (see `Dockerfile`, `README.Docker.md`):

| Process | Command | Scaling |
|---|---|---|
| API | `fastapi run --host 0.0.0.0 --port 8000 app/main.py` | horizontal — stateless (rate limits and tokens are in Postgres) |
| Worker | `python -m app.worker.main` | horizontal — RabbitMQ competing consumers; keep `WORKER_PREFETCH_COUNT=1` |

Config comes from the environment (`ENVIRONMENT=production` activates the
safety validator — [configuration.md](configuration.md#production-safety-validator)).
The HuggingFace model cache should be a persistent volume shared between
deploys (`HF_HOME`); otherwise every fresh container re-downloads the model
on first document.

Liveness: `GET /health`. Startup already fails fast if the database is
unreachable (lifespan ping).

### Production checklist

- `ENVIRONMENT=production` and the validator passes at boot
- `jwt_secret` / `otp_hash_key` ≥ 32 random chars, not examples
- `RESEND_API_KEY` set (OTP delivery), `DEBUG=false`
- explicit `CORS_ORIGINS` (no `*`)
- `TRUST_PROXY_HEADERS=true` **only** behind a proxy that overwrites
  `X-Forwarded-For`
- `EMBEDDING_MODEL_REVISION` pinned; model cache volume mounted for server
  *and* worker
- backups/PITR on Postgres; B2 bucket lifecycle/retention policy for orphans

## Running migrations

```bash
uv run alembic upgrade head        # deploy step (run once, before new code)
uv run alembic current             # verify applied revision
uv run alembic revision --autogenerate -m "..."   # create new (review it!)
```

- Migrations run on the database named `{POSTGRES_DB}_{ENVIRONMENT}` — the
  same one the app uses. Double-check `ENVIRONMENT` before running against
  production.
- The head chain and per-release upgrade notes live in
  [database.md](database.md#migrations-alembic).
- Forward **and** reverse paths are exercised by `tests/test_migrations.py`;
  run it before shipping a migration.
- Alembic uses the sync `psycopg2` URL — for remote databases set
  `POSTGRES_HOST` etc. accordingly.

## Worker operation

- Startup log shows queue, prefetch, and max attempts:
  `Worker started, listening on queue: document_processing.<env> …`.
- Retries: attempts at ~0s / 30s / 150s / 750s, then the document is
  `failed` and the message lands in `document_processing.<env>.dead`
  (inspect with any AMQP browser; messages carry `x-failure-reason`).
- A redelivered message (worker crashed mid-job) counts as an attempt —
  crashing documents cannot loop forever.
- Documents deleted mid-processing stop cleanly (ack, no retry).
- Nothing is lost on worker restart: un-acked messages are redelivered.

## Re-embedding runbook

Required whenever `EMBEDDING_MODEL` / `EMBEDDING_MODEL_REVISION` /
`EMBEDDING_DIMENSION` changes — old and new vectors are not comparable.

```bash
# 1. bump the values in .env for ALL processes, deploy (or restart)
# 2. re-queue (commits status → uploaded, then publishes events):
uv run python -m scripts.reembed_documents                 # everything
uv run python -m scripts.reembed_documents --user-id <uuid>
uv run python -m scripts.reembed_documents --document-id <uuid>
# 3. watch worker logs; each document's chunks are replaced atomically
```

Safe to re-run: already-queued or processed documents are skipped or
replaced idempotently. Search may return mixed-quality results while the
backlog drains.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `AMQPConnectionError: [Errno -5] No address associated with hostname` | `RABBITMQ_URL` host doesn't resolve **where the process runs** (container DNS, VPN, stale hostname) | `dig <host>` on the host; `docker compose exec worker python -c "import socket; print(socket.gethostbyname('<host>'))"`; fix URL → `docker compose up -d --force-recreate server worker` |
| Confirm returns 503 | broker unreachable at publish time | fix connectivity, retry confirm (idempotent) |
| Documents stuck in `uploaded` | no worker running | `docker compose up -d worker`, check logs |
| Documents `failed` | retries exhausted | read `…​.dead` queue / worker logs; fix cause, re-run confirm or `reembed_documents` |
| Search returns nothing | document not `processed` yet, or wrong account | check `GET /documents/{id}` status; search is user-scoped by design |
| Worker downloads models on every start | `hf-cache` volume missing/remounted | ensure the compose volume `hf-cache` is mounted at `HF_HOME` |
| Tests fail: no Docker | testcontainers needs the daemon | start Docker Desktop; first run pulls `pgvector/pgvector:pg18` |
| App won't start in production | safety validator | read the raised list — every violated rule is named |
| Upload URL 403 | presigned PUT expired (15 min) or headers altered | request a fresh upload URL; send `upload_headers` exactly |

## Log locations

- compose: `docker compose logs -f server` / `-f worker`
- levels via `LOG_LEVEL`; `DEBUG=true` (dev only) additionally prints OTP
  codes and skips email.

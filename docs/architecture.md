# Architecture

Mneme is a backend API for personal document libraries: users upload PDF/EPUB
files, a background worker parses and embeds them, and users can then search
their own chunks with vector, full-text, or hybrid queries.

## System overview

```
                       ┌──────────────────────────────┐
   client (mobile/web) │        Backblaze B2          │
        │  presigned    │  S3-compatible file storage  │
        │  PUT / GET    └──────────────▲───────────────┘
        ▼                             │ presigned URLs
┌──────────────────┐                  │
│  FastAPI server  │──────────────────┤
│  (app/main.py)   │                  │
│                  │  confirm: commit, then publish
│  /api/v1/*       │──────┐           │
└───────▲──────────┘      ▼           │
        │        ┌──────────────────┐  │
        │        │    RabbitMQ      │  │
   HTTP │        │ documents (direct)│ │
        │        │ + retry / dead   │  │
        │        └───────┬──────────┘  │
        │                │ consume     │
┌───────┴──────────┐     ▼             │
│  PostgreSQL 18   │┌──────────────────┐
│  + pgvector      ││  Worker          │──download / parse / embed
│  (auth, docs,    ││  (app/worker/*)  │
│   chunks, rate   │└──────────────────┘
│   limits)        │        │
└──────────────────┘        ▼
                     local HF model cache
                     (compose volume hf-cache)
```

Two long-running processes share one database:

| Process | Entry point | Role |
|---|---|---|
| API server | `app/main.py` | HTTP endpoints, auth, upload/publish, search |
| Worker | `app/worker/main.py` | consumes document events, parses + embeds |

RabbitMQ (a managed CloudAMQP instance in development — see `RABBITMQ_URL`)
carries one message type: "process this document". Files never transit the
queue; only IDs do.

## Module map

```
app/
├── api/v1/router.py        # mounts every module router under /api/v1
├── main.py                 # FastAPI app, CORS, lifespan, global rate limit
├── core/
│   ├── config.py           # all settings + production-safety validator
│   ├── db/                 # engine, get_db / get_security_session, models
│   ├── queue/              # RabbitMQ client, topology, event schemas
│   ├── storage/            # B2 client + lazy storage_service singleton
│   ├── rate_limit.py       # Postgres-backed fixed-window limiter
│   ├── email.py            # Resend sender (+ normalize_email)
│   └── logging.py
├── worker/
│   ├── main.py             # process entry: asyncio.run(start_worker())
│   └── document_worker.py  # consume → parse → chunk → embed → store
└── modules/                # domain modules (routes / services / repos / models)
    ├── auth/               # OTP login, JWT, refresh tokens, lockout
    ├── user/               # user model, repository, profile
    ├── documents/          # upload, confirm, delete, chunker, parser, status
    ├── rag/                # search endpoint + vector search service
    └── library/            # placeholder endpoints (stubs)
```

Each domain module follows the same internal layout:

```
modules/<name>/
├── routers/        # FastAPI route handlers (thin: parse → service → serialize)
├── services/       # business logic (auth_service, document_service, …)
├── repositories/   # SQLAlchemy data access
├── models/         # ORM models (+ enums/constraints)
├── schemas/        # Pydantic request/response models
└── dependencies/   # FastAPI dependency providers
```

## Request/data flows

**Auth** — see [authentication.md](authentication.md).

**Document lifecycle** — see [document-pipeline.md](document-pipeline.md).

**Search** — see [search.md](search.md).

## Cross-cutting concerns

- **Transactions** — `get_db` owns commits; services flush only. Exceptions
  roll the whole request back. Documented exceptions live in
  `app/core/db/db.py` (`get_security_session`, `DocumentRepository.commit`).
  Full policy: [database.md](database.md#transaction-policy).
- **Rate limiting** — Postgres-backed so API and any future processes share
  counters (`app/core/rate_limit.py`). Applied globally as a router
  dependency in `main.py`, plus per-endpoint on OTP routes.
- **Configuration** — single `Settings` object (`app/core/config.py`) loaded
  from `.env`; production runs an extra safety validator at startup.
  Reference: [configuration.md](configuration.md).
- **Storage** — one lazy B2 client per process; nothing is constructed at
  import time (`app/core/storage/storage_service.py`).
- **Logging** — structured logging configured once at import in
  `app/core/logging.py`.

## Response envelope

Every JSON response carries the same wrapper, so clients can handle success
and failure uniformly:

```json
{"success": true,  "message": "Upload confirmed", "data": { ... }}
{"success": false, "error": {"code": 404, "message": "Document not found", "details": null}}
```

- Success: route handlers return `ok(data, message)` with
  `response_model=SuccessEnvelope[T]` (`app/core/responses.py`).
- Errors: registered exception handlers in `app/main.py` convert everything —
  raised `HTTPException`s, request validation (422, with field-level
  `details`), and unhandled exceptions (500, generic message) — into the
  `error` shape. Status codes and headers are unchanged.
- The single exception: `204 No Content` (document delete) stays bodyless,
  as HTTP requires.

## API surface (v1)

All routes are mounted under `/api/v1`; interactive docs at `/docs`.

| Method & path | Purpose |
|---|---|
| `POST /auth/request-otp` | Start login: creates user if new, emails a code (202) |
| `POST /auth/verify-otp` | Verify code → access + refresh tokens |
| `POST /auth/refresh` | Rotate refresh token, new token pair |
| `POST /auth/logout` | Revoke all refresh tokens for the user |
| `GET /auth/me` | Current profile |
| `POST /documents` | Create document + presigned upload URL (201) |
| `GET /documents` | Paginated list (limit/offset, newest first) |
| `GET /documents/{id}` | Single document |
| `PATCH /documents/{id}/confirm` | Verify upload, queue processing |
| `DELETE /documents/{id}` | Delete row first, storage second (204) |
| `GET /search` | hybrid / vector / text search over own chunks |
| `POST /library/query`, `GET /library/conversations` | Stubs (placeholder) |
| `GET /health` | Liveness probe (unversioned) |

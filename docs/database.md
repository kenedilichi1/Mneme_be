# Database

PostgreSQL 18 with the `pgvector` extension. Async access through SQLAlchemy
2.0 (`asyncpg`) for the app; Alembic migrations run through the sync
`psycopg2` driver.

## Schema

| Table | Purpose | Key columns / constraints |
|---|---|---|
| `users` | accounts | `email` unique + `uq_users_email_lower` functional index (`lower(email)`), `first_name`/`last_name` nullable, OTP lockout counters (`failed_otp_attempts`, `otp_attempts_window_started_at`) |
| `otp_codes` | pending login codes | one per user (`user_id` FK), HMAC hash, expiry, indexed by `user_id` |
| `oauth_accounts` | reserved OAuth linkage | `uq_provider_account (provider, provider_user_id)` |
| `refresh_tokens` | refresh tokens, **hashed** | unique `token_hash`, `expires_at`, `revoked_at`; `user_id` FK CASCADE |
| `documents` | uploaded files | `user_id` FK CASCADE (indexed), `storage_key`, `file_size`, `status` + `ck_documents_status` membership check, `tags` (`text[]`), `created_at` |
| `document_chunks` | parsed/embedded text | `document_id` & `user_id` FK CASCADE, `chunk_index`, `content`, `content_tsv` (stored generated), `embedding vector(768)` + HNSW index, GIN index on `content_tsv` |
| `rate_limits` | rate-limit counters | unique `key`, `window_started_at`, `hit_count` |

Deleting a user cascades to their documents and chunks; deleting a document
cascades to its chunks (`all, delete-orphan` in the ORM as well).

Database name: `{POSTGRES_DB}_{ENVIRONMENT}` (e.g. `mneme_development`) —
development and production never share tables.

### Notable indexes

- `ix_document_chunks_embedding_hnsw` — HNSW, `vector_cosine_ops`
- `ix_document_chunks_content_tsv` — GIN over the stored tsvector
- `uq_users_email_lower` — backs case-insensitive email lookup
- `documents.user_id`, `document_chunks.user_id`, `document_chunks.document_id`

Details: [search.md](search.md#indexes).

## Transaction policy

One rule, stated in `get_db`'s docstring (`app/core/db/db.py`):
**`get_db` owns the commit.** Services and repositories `flush()` but never
`commit()`; a raised exception rolls the whole request back.

Sanctioned exceptions (each called out at its call site):

1. **`get_security_session`** — rate-limit hits and OTP-lockout counters
   must survive failed requests, so this session commits unconditionally
   (including on error paths).
2. **`DocumentRepository.commit()` in `DocumentService`** — three places:
   - *Oversize at confirm*: persists `status="failed"` even though the
     request ends in 413.
   - *Normal confirm*: commits the `uploaded` state **before** publishing to
     RabbitMQ — the worker skips-and-acks events whose row isn't visible.
   - *Delete*: commits the row removal **before** touching B2 — a rolled-back
     delete must never have removed the file behind a live row.

Everything else commits via `get_db` after the response is generated.
The worker is a separate process and owns its own transactions (per-job
`AsyncSessionLocal`, committed at defined points).

## Migrations (Alembic)

Config: `alembic.ini` → `script_location = alembic` (the old top-level
`migrations/` scaffold was removed). URL comes from
`settings.sync_database_url` via `alembic/env.py`.

```bash
uv run alembic upgrade head            # apply
uv run alembic downgrade -1            # roll back one
uv run alembic current                 # what's applied
uv run alembic revision --autogenerate -m "..."
```

Autogenerate diffs models against the DB; **review the generated file** —
generated columns and extension-dependent types occasionally need manual
touch-ups.

### Head chain (linear)

```
0001                       enable pgvector
  → 03a63a31fa13           users, oauth_accounts, otp_codes
  → 03b50c9e5c31           documents, document_chunks
  → 04c61e8a9d12           index on otp_codes.user_id
  → 59f9de2b0b30           embedding 1536 → 768
  → b1c07d2e4a56           otp lockout columns + rate_limits table
  → c9e2f1a74b30           normalize legacy emails (lowercase)
  → d7a1c3e5f2b4           refresh_tokens
  → a4c6d8f0b2e1           HNSW + stored tsvector/GIN indexes
  → b5e7f9a1c3d4           ck_documents_status        ← HEAD
```

`tests/test_migrations.py` upgrades to head and back on a scratch database
so every migration's forward and reverse path stays runnable.

# Testing

```bash
uv run pytest -q          # whole suite
uv run pytest tests/test_search.py -q
uv run pytest -k refresh -q
```

**Requirement: a running Docker daemon.** The suite starts a real
`pgvector/pgvector:pg18` container via testcontainers — there is no
in-process DB substitute. Roughly 400 MB of images on first run.

## Configuration (`pyproject.toml`)

| Setting | Value | Why |
|---|---|---|
| `asyncio_mode` | `auto` | plain `async def test_*` needs no decorator |
| `asyncio_default_fixture_loop_scope` | `session` | asyncpg binds connections to the loop that created them; one loop for the whole session avoids `Task got Future attached to a different loop` |
| `asyncio_default_test_loop_scope` | `session` | same loop for tests and fixtures |

Dev deps: `pytest`, `pytest-asyncio`, `httpx` (in-process ASGI client),
`testcontainers[postgres]`.

## Fixture chain (`tests/conftest.py`)

```
postgres_container  [session]   Docker pgvector container
        │
test_engine         [session]   async engine; CREATE EXTENSION vector; create_all
        │
db_connection       [function]  BEGIN → test → ROLLBACK   ← isolation boundary
        │
db_session          [function]  AsyncSession, join_transaction_mode="create_savepoint"
        │
client              [function]  httpx AsyncClient over ASGITransport(app=app)
                                 + dependency_overrides[get_db] → db_session
```

Key points:

- **Isolation = one never-committed transaction per test.** Every write the
  test (or the handler it calls) makes lives inside `db_connection`'s
  outer `BEGIN`. The session's `commit()` only releases a savepoint, so
  committed state *within a test* is visible across handler and assertions
  (they share the connection), yet everything rolls back when the test ends.
  No `DELETE`/`TRUNCATE` teardown exists — and none is needed.
- **`client`** talks to the app in-process (no sockets): middleware,
  dependency injection, and error handling all behave as in production.
  Its `get_db` override is cleared at teardown.
- **`isolated_client`** — same thing on a *separate* connection/transaction,
  for tests that must not share `db_session` (parallel-safe tests).
- Session-scoped `test_engine` creates the schema once from the ORM models
  (`Base.metadata.create_all`) rather than via migrations; migrations get
  their own test (below).

## What the suite covers

| Area | Files |
|---|---|
| secrets unit tests (OTP HMAC, JWT, refresh hashing) | `test_auth.py` |
| repositories + pgvector round-trip | `test_database_integration.py` |
| full OTP HTTP flow, rate limits, lockout | `test_auth_endpoints_integration.py`, `test_auth_limits.py` |
| refresh rotation / reuse detection / logout | `test_refresh_tokens.py` |
| documents: confirm, delete ordering, pagination, status transitions | `test_document_service.py`, `test_document_status.py`, `test_document_pagination.py`, `test_document_worker.py` |
| search: modes, isolation, validation, indexes (EXPLAIN), fake embeddings | `test_search.py`, `test_search_indexes.py`, `test_vector_search_service.py` |
| chunker (Nomic tokenizer), parser | `test_docling_parser.py` |
| queue (declare-once, connection lock), lazy storage singleton | `test_queue.py`, `test_storage_service.py` |
| migrations up + down | `test_migrations.py` |
| **end-to-end pipeline** | `test_e2e_pipeline.py` |

## The E2E test (`tests/test_e2e_pipeline.py`)

One test exercises the whole product path without Docker services beyond
the testcontainers DB:

1. login via `client` (OTP → tokens)
2. `POST /documents`, `PATCH …/confirm` with `rabbitmq.publish` **captured**
   and the B2 size check stubbed — the real publish is asserted, not sent
3. run the real worker function `process_document(event)` in-process:
   storage download stubbed to a minimal PDF, embedding service replaced by
   a keyword-based fake (vectors in the same 768-dim space)
4. assert `status == "processed"` and chunks exist
5. `GET /search` in all three modes (`text`, `vector`, `hybrid`) — content
   found, everything scoped to this user/document

This is the pattern to extend when adding pipeline stages: stub only the
external boundary (broker, object storage, model weights), keep everything
between them real.

## Writing new tests

- Pure logic → plain `def test_…`, no fixtures.
- Anything touching the DB → take `db_session` (and `client` for HTTP);
  both share one transaction automatically.
- Need HTTP + independent state → `isolated_client`.
- Never call `session.commit()` expecting persistence across tests — it
  only releases a savepoint.
- Parallel runs require `pytest-xdist` (not currently a dependency); until
  it's added, run the suite serially. `isolated_client` tests are already
  written to be parallel-safe when you do.

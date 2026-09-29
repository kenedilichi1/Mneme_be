# Testing Guide

This document explains the entire test infrastructure for Mneme — from how the database is spun up
to how individual tests are written and isolated from each other.

---

## 1. Dependencies & Configuration

### `pyproject.toml` — dev dependencies

```toml
[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.27.0",
    "testcontainers[postgres]>=4.15.0",
]
```

| Package | Purpose |
|---|---|
| `pytest` | The core test runner |
| `pytest-asyncio` | Teaches pytest how to run `async def` test functions |
| `httpx` | Provides `AsyncClient` to make HTTP calls to the FastAPI app in-process (no real network) |
| `testcontainers[postgres]` | Spins up a real Docker container for PostgreSQL during the test run |

### `pyproject.toml` — pytest settings

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
```

| Setting | What it means |
|---|---|
| `asyncio_mode = "auto"` | Every `async def test_*` function is automatically treated as an asyncio test — no need to decorate with `@pytest.mark.asyncio` |
| `asyncio_default_fixture_loop_scope = "session"` | Session-scoped async fixtures (like `test_engine`) run on the **same** event loop that is shared for the whole test session |
| `asyncio_default_test_loop_scope = "session"` | Every test function also runs on that same session-wide event loop |

> **Why does the loop scope matter?**
> `asyncpg` (the async PostgreSQL driver) binds its internal connection objects to the event loop
> that created them. If a session-scoped fixture opens a connection on loop A, but a test runs on
> loop B, asyncpg raises:
> ```
> RuntimeError: Task got Future attached to a different loop
> ```
> Setting both scopes to `"session"` ensures there is exactly **one** event loop for everything.

---

## 2. `tests/conftest.py` — The Fixture Chain

`conftest.py` is a special pytest file whose fixtures are automatically available to every test file
in the same directory (and subdirectories) without any import.

### The full dependency chain

```
postgres_container  [session]
        │
        ▼
   test_engine  [session]
        │
        ▼
  db_connection  [function]  ← new per test: BEGIN → ... → ROLLBACK
        │
        ▼
   db_session  [function]  ← ORM session, uses SAVEPOINT internally
        │
        ▼
     client  [function]  ← FastAPI test client, overrides get_db → db_session
```

Each arrow means "depends on". Pytest constructs the chain in order and tears it down in reverse.

---

### Fixture 1 — `postgres_container` (scope: `session`)

```python
@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("pgvector/pgvector:pg18") as pg:
        yield pg
```

- **What it does**: Pulls the `pgvector/pgvector:pg18` Docker image and starts a container.
  The container exposes a real PostgreSQL 18 instance with the `pgvector` extension already
  compiled in.
- **Scope `"session"`**: The container starts **once** when the first test needs it and stays
  running until all tests have finished. This avoids the expensive startup cost for every test.
- **`with ... as pg: yield pg`**: This is a context-manager-based fixture. The `with` block keeps
  the container alive while tests run; when the session ends, the `with` block exits and
  `testcontainers` kills and removes the container.

---

### Fixture 2 — `test_engine` (scope: `session`)

```python
@pytest_asyncio.fixture(scope="session")
async def test_engine(postgres_container: PostgresContainer):
    raw_url = postgres_container.get_connection_url()
    async_url = (
        raw_url
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )

    engine = create_async_engine(async_url, echo=False)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
```

- **What it does**: Takes the connection URL from `postgres_container` and creates a
  SQLAlchemy `AsyncEngine`. Before yielding, it:
  1. Enables the `vector` extension (required for pgvector column types).
  2. Runs `Base.metadata.create_all` — this inspects every SQLAlchemy model registered with
     `Base` and creates the corresponding tables in the container's database.
- **URL rewriting**: `testcontainers` returns a `psycopg2`-style URL by default. We swap the
  driver segment to `asyncpg` because the application uses `asyncpg` for all async I/O.
- **Teardown** (after `yield`): Drops all tables (`drop_all`) and disposes the engine's
  connection pool. This is clean-up even though the container itself is destroyed anyway.
- **`@pytest_asyncio.fixture`**: Used instead of `@pytest.fixture` whenever the fixture itself
  is `async`.

---

### Fixture 3 — `db_connection` (scope: `function`)

```python
@pytest_asyncio.fixture
async def db_connection(test_engine) -> AsyncGenerator[AsyncConnection, None]:
    async with test_engine.connect() as conn:
        await conn.begin()
        yield conn
        await conn.rollback()
```

- **What it does**: Opens a **single raw database connection** from the engine and immediately
  starts a transaction with `BEGIN`.
- **Scope `"function"` (default)**: A brand-new connection+transaction is created for **each
  individual test function**.
- **`conn.rollback()` after `yield`**: This is the isolation mechanism. After the test finishes
  (regardless of pass or fail), the entire transaction is rolled back. Every `INSERT`, `UPDATE`,
  and `DELETE` the test performed vanishes. The tables are left empty for the next test.
- **Why not `conn.commit()`?** Committing would make the test's data permanent in the
  container and could leak state into subsequent tests.

---

### Fixture 4 — `db_session` (scope: `function`)

```python
@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )
    async with session_factory() as session:
        yield session
```

- **What it does**: Creates a SQLAlchemy `AsyncSession` that is **bound to the existing
  `db_connection`** rather than borrowing a new connection from the pool.
- **`join_transaction_mode="create_savepoint"`**: This is the key setting. Because the
  connection already has an open transaction (the outer `BEGIN` from `db_connection`),
  whenever the session internally would start a new transaction (e.g. when you call
  `session.flush()` or a repository calls `session.commit()`), SQLAlchemy instead issues a
  `SAVEPOINT`. Rolling back the outer transaction in `db_connection` rolls back all the
  SAVEPOINTs too — so the session's `commit()` calls are effectively no-ops from the
  persistence perspective.
- **`expire_on_commit=False`**: Prevents SQLAlchemy from expiring object attributes after a
  (nested) commit, which would cause unnecessary lazy-load I/O during testing.
- **`autoflush=False`**: Prevents automatic flushes before every query. Tests call `flush()`
  explicitly when they need to materialise an object.

---

### Fixture 5 — `client` (scope: `function`)

```python
@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
```

- **What it does**: Creates an HTTPX `AsyncClient` that talks to the FastAPI app **directly
  in-process** — no actual HTTP port is bound.
- **`ASGITransport(app=app)`**: Replaces the network transport with a direct ASGI call. HTTP
  requests go through FastAPI's router, middleware, and dependency injection exactly as in
  production, but without touching a socket.
- **`app.dependency_overrides[get_db] = _override_get_db`**: FastAPI's dependency injection
  system normally calls `get_db()` to create a database session for each request. Here we
  replace that with a function that yields the **test's own `db_session`** — the same one
  that is inside the rollback transaction. This means API endpoint code and test assertions
  see the exact same database state.
- **`app.dependency_overrides.clear()`**: Removes the override after the test so other tests
  start clean.

---

## 3. Test Files

### `tests/test_auth.py` — Pure Unit Tests (no database)

These are synchronous, pure Python tests. They do not use `db_session` or `client`.

```python
def test_generate_otp_code()        # OTP is a 6-digit string
def test_hash_and_verify_otp_code() # keyed HMAC round-trip
def test_otp_expiry()               # returns a datetime
def test_jwt_access_token()         # create + decode JWT round-trip
def test_jwt_invalid_token()        # invalid token returns None
```

These test the `app/modules/auth/services/secrets.py` utility functions in complete isolation.
No fixtures are needed.

---

### `tests/test_database_integration.py` — Repository + pgvector Tests

These tests verify that the SQLAlchemy repository layer works correctly against a **real
PostgreSQL 18 database** (running in Docker via testcontainers).

All three tests receive `db_session: AsyncSession` from conftest. Every write they make is
rolled back when the test ends, so they are fully independent of each other.

#### `test_user_repository_crud`

Tests the `UserRepository` CRUD operations:
1. `create(user)` — inserts a user row and confirms a UUID was assigned.
2. `get_by_id(id)` — fetches the user by primary key.
3. `get_by_email(email)` — fetches the user by the unique email index.

#### `test_otp_code_repository_lifecycle`

Tests the full OTP lifecycle:
1. Creates a user.
2. Creates an `OtpCode` with a hashed version of `"123456"` and an expiry timestamp.
3. `get_otp_code(user_id)` — retrieves it.
4. `delete_otp_code(user_id)` — deletes it.
5. Asserts the code is gone (`get_otp_code` returns `None`).

#### `test_document_and_chunks_with_vector`

Tests the document + pgvector pipeline:
1. Creates a user and a `Document` record.
2. Creates a `DocumentChunk` with a 768-dimensional float embedding (`[0.01] * 768`).
3. Calls `session.flush()` to send the `INSERT` to the DB (within the SAVEPOINT), then
   `session.refresh(chunk)` to reload the server-assigned fields.
4. Asserts the embedding round-tripped correctly (`len == 768`).
5. Asserts `DocumentRepository.get_all_by_user()` returns the document.

---

### `tests/test_auth_endpoints_integration.py` — Full HTTP Flow Test

This test uses **both** `client` and `db_session` to verify the entire auth flow end-to-end
via real HTTP requests.

```python
async def test_full_auth_flow(client: AsyncClient, db_session: AsyncSession):
```

Because both fixtures ultimately depend on the same `db_connection`, they share the same
database transaction. This means:

- Rows written by the HTTP handler (via `client.post(...)`) are immediately visible when
  querying through `db_session` — no commit required.
- Everything is rolled back when the test ends.

**Step by step:**

| Step | What happens |
|---|---|
| `POST /api/v1/auth/request-otp` | FastAPI creates a `User` row and an `OtpCode` row, then (in test mode) logs/sends the OTP |
| `user_repo.get_by_email(email)` | Directly queries the DB via `db_session` to confirm the user exists |
| `otp_repo.delete_otp_code(user.id)` | Deletes the auto-generated OTP so there is no ambiguity |
| `otp_repo.create(OtpCode(...))` | Seeds a new OTP with the known code `"654321"` |
| `POST /api/v1/auth/verify-otp` | FastAPI reads the OTP from DB, verifies `"654321"`, marks user as verified, returns a JWT |
| `GET /api/v1/auth/me` | FastAPI decodes the JWT and returns the user profile |

> **Why delete and re-seed the OTP?**
> The signup handler creates an OTP with a random value we do not know at test time. Rather
> than trying to intercept that value, we delete it and insert a known one (`"654321"`) so the
> `verify-otp` call is deterministic.

---

## 4. Data Isolation — How It All Fits Together

The key insight is that every test lives inside a **transaction that is never committed**.

```
┌─────────────────────────────────────────────────────────┐
│  postgres_container (Docker, lives for whole session)   │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │  test_engine  (SQLAlchemy engine, session-scoped) │  │
│  │                                                   │  │
│  │  For test_A:                                      │  │
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │ db_connection: BEGIN                        │  │  │
│  │  │   db_session: SAVEPOINT sp1                 │  │  │
│  │  │     test body runs...                       │  │  │
│  │  │   ROLLBACK TO SAVEPOINT sp1 (on commit)     │  │  │
│  │  │ ROLLBACK  ← discards everything             │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  │                                                   │  │
│  │  For test_B:                                      │  │
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │ db_connection: BEGIN (fresh connection)     │  │  │
│  │  │   ... same pattern ...                      │  │  │
│  │  │ ROLLBACK                                    │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

Each test starts with empty tables (the schema exists, but no rows from prior tests).
No manual `DELETE` or `TRUNCATE` teardown is needed — the rollback handles it automatically.

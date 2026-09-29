# Mneme API

Mneme is a backend API that allows users to upload PDF and EPUB documents, ask questions grounded within a single document, and perform cross-library synthesis queries across all their uploaded documents.

## Tech Stack

- **Framework**: FastAPI (Python 3.12+)
- **Database**: PostgreSQL (with pgvector for embeddings)
- **ORM**: SQLAlchemy (async)
- **Migrations**: Alembic
- **Package Manager**: [uv](https://github.com/astral-sh/uv)

## Documentation

Everything beyond this README lives in [`docs/`](docs/):

| | |
|---|---|
| [architecture.md](docs/architecture.md) | system overview, module map, API surface |
| [authentication.md](docs/authentication.md) | OTP login, tokens, rate limits, key rotation |
| [document-pipeline.md](docs/document-pipeline.md) | upload → worker → searchable, status lifecycle |
| [document-upload-implementation.md](docs/document-upload-implementation.md) | detailed walkthrough of the upload feature's implementation |
| [search.md](docs/search.md) | embeddings, chunking, search modes, indexes |
| [configuration.md](docs/configuration.md) | every env var + production validation |
| [database.md](docs/database.md) | schema, transaction policy, migrations |
| [queue.md](docs/queue.md) | RabbitMQ topology, retries, dead-lettering |
| [storage.md](docs/storage.md) | B2 presigned URLs, failure semantics |
| [development.md](docs/development.md) | local setup + Swagger walkthrough |
| [testing.md](docs/testing.md) | test suite, fixtures, E2E test |
| [operations.md](docs/operations.md) | deployment, runbooks, troubleshooting |

## Project Structure

The project uses a domain-driven structure:

```
├── app/
│   ├── api/v1/         # Versioned API router parent
│   ├── core/           # Core configuration, database, logging setup
│   └── modules/        # Domain modules (routes, business logic, models)
│       ├── auth/
│       ├── documents/
│       ├── library/
│       ├── rag/
│       └── user/
├── docs/               # Architecture & operational documentation
├── alembic/            # Database migrations
├── tests/              # Pytest suite (see docs/testing.md)
├── Dockerfile          # Application Docker image definition
└── compose.yaml        # Docker Compose for local development (API + worker + DB)
```

## Local Development Setup

### Prerequisites
- Docker and Docker Compose
- `uv` installed locally (optional, for local virtualenv management)

### 1. Environment Variables

Copy the example environment file and fill in the required values
(`jwt_secret`, `RESEND_API_KEY`, `POSTGRES_*`, `B2_*`, `RABBITMQ_URL` —
see [docs/configuration.md](docs/configuration.md)):

```bash
cp .env.example .env
```

### 2. Running with Docker Compose

To start the API, worker, and PostgreSQL together:

```bash
docker compose up -d --build
uv run alembic upgrade head   # apply migrations (first run / after pulls)
```

The API will be accessible at `http://localhost:8000`. You can view the interactive API documentation (Swagger UI) at `http://localhost:8000/docs`.

Full walkthrough, including how to exercise the upload → process → search
flow from Swagger: [docs/development.md](docs/development.md).

### 3. Running Locally (Without Docker)

If you prefer to run the API outside of Docker (e.g. while developing), you can start just the database:

```bash
docker compose up db -d
```

Then install dependencies and run the FastAPI development server using `uv`:

```bash
uv sync
uv run fastapi dev app/main.py
```

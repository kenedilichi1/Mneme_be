# Mneme API

Mneme is a backend API that allows users to upload PDF and EPUB documents, ask questions grounded within a single document, and perform cross-library synthesis queries across all their uploaded documents.

## Tech Stack

- **Framework**: FastAPI (Python 3.12+)
- **Database**: PostgreSQL (with pgvector for embeddings)
- **ORM**: SQLAlchemy (async)
- **Migrations**: Alembic
- **Package Manager**: [uv](https://github.com/astral-sh/uv)

## Project Structure

The project uses a domain-driven structure:

```
├── app/
│   ├── api/v1/         # Versioned API router parent
│   ├── core/           # Core configuration, database, logging setup
│   └── modules/        # Domain modules (routes, business logic, models)
│       ├── auth/
│       ├── documents/
│       └── library/
├── alembic/            # Database migrations
├── Dockerfile          # Application Docker image definition
└── compose.yaml        # Docker Compose for local development (App + DB)
```

## Local Development Setup

### Prerequisites
- Docker and Docker Compose
- `uv` installed locally (optional, for local virtualenv management)

### 1. Database Setup

Create a `password.txt` file in a `db` directory at the root to securely pass the database password to Docker Compose:

```bash
mkdir db
echo "your_secure_password" > db/password.txt
```

### 2. Environment Variables

Copy the example environment file and update the configuration:

```bash
cp .env.example .env
```
Ensure your `POSTGRES_PASSWORD` in `.env` matches what you put in `db/password.txt`.

### 3. Running with Docker Compose

To start the API and PostgreSQL database together:

```bash
docker compose up -d --build
```

The API will be accessible at `http://localhost:8000`. You can view the interactive API documentation (Swagger UI) at `http://localhost:8000/docs`.

### 4. Running Locally (Without Docker)

If you prefer to run the API outside of Docker (e.g. while developing), you can start just the database:

```bash
docker compose up db -d
```

Then install dependencies and run the FastAPI development server using `uv`:

```bash
uv sync
uv run fastapi dev app/main.py
```

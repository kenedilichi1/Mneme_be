# Document Upload Feature - Implementation Walkthrough

## Overview

This document provides a detailed walkthrough of the document upload feature implementation for the Mneme backend. The feature enables users to upload documents through a presigned URL flow, with async processing via a message queue.

---

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Mobile App │────▶│  API Server │────▶│  PostgreSQL │     │   RabbitMQ  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                           │                                       │
                           ▼                                       ▼
                    ┌─────────────┐                         ┌─────────────┐
                    │  B2 Storage │                         │   Worker    │
                    └─────────────┘                         └─────────────┘
```

### Flow Summary

1. **Client** sends document metadata to `POST /documents`
2. **API** creates document record in DB with `pending_upload` status
3. **API** generates presigned upload URL from B2
4. **API** returns document + upload URL to client
5. **Client** uploads file directly to B2 using the presigned URL
6. **Client** calls `PATCH /documents/{id}/confirm`
7. **API** updates status to `uploaded` and publishes event to RabbitMQ
8. **Worker** consumes event and processes the document

---

## 1. Database Models

### Document Model

**File:** `app/modules/documents/models/document_model.py`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique document identifier |
| user_id | UUID | FK → users.id, CASCADE | Owner of the document |
| title | String | NOT NULL | Document title |
| author | String | NULLABLE | Document author |
| original_filename | String | NOT NULL | Original file name from client |
| file_size | BigInteger | NOT NULL | File size in bytes |
| page_count | BigInteger | NULLABLE | Number of pages (set after processing) |
| storage_key | String | NOT NULL | B2 storage path |
| file_type | String | NOT NULL | MIME type (e.g., `application/pdf`) |
| status | String | NOT NULL, default `pending_upload` | Document status |
| tags | ARRAY(String) | NOT NULL, default `{}` | Tags for categorization |
| created_at | DateTime(tz) | NOT NULL | Creation timestamp |

**Status Values:**
- `pending_upload` - Document record created, awaiting file upload
- `uploaded` - File uploaded to B2, awaiting processing
- `processing` - Worker is processing the document
- `ready` - Document processed and ready for use
- `failed` - Processing failed

### DocumentChunk Model

**File:** `app/modules/documents/models/document_chunk_model.py`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique chunk identifier |
| document_id | UUID | FK → documents.id, CASCADE | Parent document |
| user_id | UUID | FK → users.id, CASCADE | Owner (denormalized for query speed) |
| chunk_index | Integer | NOT NULL | Order within document |
| page_number | Integer | NULLABLE | Page number (if applicable) |
| section_title | String | NULLABLE | Section/chapter title |
| content | String | NOT NULL | Text content of chunk |
| token_count | Integer | NOT NULL | Number of tokens |
| embedding_model | String | NULLABLE | Model used for embeddings |
| embedding | Vector(1536) | NULLABLE | pgvector embedding |
| created_at | DateTime(tz) | NOT NULL | Creation timestamp |

### Migration

**File:** `alembic/versions/03b50c9e5c31_create_documents_and_document_chunks_tables.py`

Creates both tables with proper indexes:
- `ix_documents_user_id` - Fast lookup by user
- `ix_document_chunks_document_id` - Fast lookup by document
- `ix_document_chunks_user_id` - Fast lookup by user

---

## 2. API Endpoints

**Base Path:** `/api/v1/documents`

All endpoints require Bearer token authentication.

### POST /documents

Creates a new document record and returns a presigned upload URL.

**Request Body:**
```json
{
  "title": "My Document",
  "author": "John Doe",
  "original_filename": "document.pdf",
  "file_size": 1024000,
  "file_type": "application/pdf"
}
```

**Response (201):**
```json
{
  "document": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "My Document",
    "author": "John Doe",
    "original_filename": "document.pdf",
    "file_size": 1024000,
    "page_count": null,
    "file_type": "application/pdf",
    "status": "pending_upload",
    "tags": [],
    "created_at": "2026-09-16T14:00:00Z"
  },
  "upload_url": "https://api3.backblazeb2.com/b2api/v2/b2_get_upload_url?..."
}
```

**Client Upload Step:**
```javascript
// After receiving the response, upload directly to B2
const response = await fetch(uploadUrl, {
  method: 'PUT',
  headers: {
    'Authorization': uploadAuthToken,
    'X-Bz-File-Name': encodeURIComponent(storageKey),
    'Content-Type': file.type,
  },
  body: file,
});
```

### GET /documents

Returns all documents for the authenticated user.

**Response (200):**
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "My Document",
    "status": "ready",
    ...
  }
]
```

### GET /documents/{document_id}

Returns a specific document by ID.

**Response (200):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "My Document",
  "status": "uploaded",
  ...
}
```

### PATCH /documents/{document_id}/confirm

Confirms that the file has been uploaded to B2. Transitions status from `pending_upload` to `uploaded` and triggers async processing.

**Response (200):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "uploaded",
  ...
}
```

**Events Published:**
- `document.uploaded` - Triggers worker processing

### DELETE /documents/{document_id}

Deletes the document record and the file from B2.

**Response:** 204 No Content

---

## 3. Layered Architecture

### Request Flow

```
Router → Dependencies → Service → Repository → Database
```

### Repository Layer

**File:** `app/modules/documents/repositories/document_repository.py`

Encapsulates all SQLAlchemy query logic. Never calls `commit()` directly (handled by session dependency).

```python
class DocumentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, document: Document) -> Document
    async def get_by_id(self, document_id: UUID, user_id: UUID) -> Document | None
    async def get_all_by_user(self, user_id: UUID) -> list[Document]
    async def update(self, document: Document) -> Document
    async def delete(self, document: Document) -> None
```

### Service Layer

**File:** `app/modules/documents/services/document_service.py`

Contains business logic. Receives dependencies via constructor injection.

```python
class DocumentService:
    def __init__(
        self,
        document_repository: DocumentRepository,
        storage_service: StorageService,
        rabbitmq: RabbitMQ,
    ):
```

Key methods:
- `create_document()` - Creates record + generates upload URL
- `confirm_upload()` - Updates status + publishes event
- `delete_document()` - Deletes record + removes file from B2

### Dependencies Layer

**File:** `app/modules/documents/dependencies/document_dependency.py`

FastAPI dependency injection factories.

```python
def get_document_repository(db: SessionDep) -> DocumentRepository
def get_storage_service() -> StorageService
def get_rabbitmq() -> RabbitMQ
def get_document_service(...) -> DocumentService

# Type aliases for router injection
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
```

---

## 4. Storage Integration (B2)

### B2Storage Class

**File:** `app/core/storage/b2_storage.py`

Wraps the Backblaze B2 SDK.

```python
class B2Storage:
    def generate_upload_url(self, file_name: str) -> str
    def get_download_url(self, file_name: str) -> str
    def delete_file(self, file_name: str) -> None
```

### StorageService

**File:** `app/core/storage/storage_service.py`

Higher-level service for document operations.

```python
class StorageService:
    def generate_upload_url(self, user_id: UUID, file_type: str) -> str
    def get_download_url(self, storage_key: str) -> str
    def delete_file(self, storage_key: str) -> None
```

**File Path Convention:** `{user_id}/{uuid}.{extension}`
Example: `550e8400-e29b-41d4-a716-446655440000/a1b2c3d4.pdf`

### Configuration

**File:** `app/core/config.py`

```python
B2_KEY_ID: str
B2_APPLICATION_KEY: str
B2_BUCKET_NAME: str
```

---

## 5. Message Queue (RabbitMQ)

### Queue Configuration

**File:** `app/core/queue/queue.py`

```python
class RabbitMQ:
    async def connect(self) -> None
    async def publish(self, queue_name: str, message: dict) -> None
    async def close(self) -> None
```

Uses direct exchange named `documents` with durable queues.

### Events

**File:** `app/core/queue/events.py`

```python
class DocumentEventType(str, Enum):
    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_PROCESSED = "document.processed"
    DOCUMENT_FAILED = "document.failed"

class DocumentEvent:
    event_type: DocumentEventType
    document_id: str
    user_id: str
    storage_key: str
```

### Queue Naming

Queues are environment-specific to prevent conflicts:
- `document_processing.development`
- `document_processing.staging`
- `document_processing.production`

### Configuration

**File:** `app/core/config.py`

```python
RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"
```

---

## 6. Worker Service

### Document Worker

**File:** `app/worker/document_worker.py`

Consumes messages from the `document_processing` queue.

```python
QUEUE_NAME = f"document_processing.{settings.ENVIRONMENT}"

async def process_document(event: DocumentEvent) -> None
async def start_worker() -> None
```

### Running the Worker

```bash
# Development
python -m app.worker.main

# Production (with process manager)
uvicorn app.worker.main:main
```

### Processing Pipeline (TODO)

The worker currently logs events. Full implementation will:

1. Fetch file from B2 using `storage_key`
2. Parse document (PDF, DOCX, TXT, etc.)
3. Chunk content into smaller segments
4. Generate embeddings using an embedding model
5. Store chunks in `document_chunks` table
6. Update document status to `ready`

---

## 7. Configuration

### Environment Variables

**File:** `.env.example`

```bash
# PostgreSQL
POSTGRES_USER=mneme_db
POSTGRES_PASSWORD=postgres
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=mneme

# Auth
jwt_secret=your-secret-key
jwt_algorithm=HS256

# Backblaze B2
B2_KEY_ID=your_key_id
B2_APPLICATION_KEY=your_application_key
B2_BUCKET_NAME=your_bucket_name

# RabbitMQ
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
```

### Settings Class

**File:** `app/core/config.py`

All configuration is loaded via Pydantic Settings from environment variables or `.env` file.

---

## 8. Database Migrations

### Running Migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Rollback one version
alembic downgrade -1

# Create new migration
alembic revision --autogenerate -m "description"
```

### Migration History

| Revision | Description |
|----------|-------------|
| 0001 | Enable pgvector extension |
| 03a63a31fa13 | Create users, oauth_accounts, otp_codes tables |
| 03b50c9e5c31 | Create documents, document_chunks tables |

---

## 9. Project Structure

```
app/
├── api/
│   ├── dependencies.py          # SessionDep
│   └── v1/router.py             # Central API router
├── core/
│   ├── config.py                # Settings
│   ├── db/
│   │   ├── base.py              # DeclarativeBase
│   │   ├── base_class.py        # Abstract BaseModel
│   │   ├── db.py                # Engine, session
│   │   └── models.py            # Model registry
│   ├── email.py                 # EmailService
│   └── storage/
│       ├── b2_storage.py        # B2 client
│       └── storage_service.py   # Storage service
├── modules/
│   ├── auth/                    # Authentication module
│   ├── documents/
│   │   ├── dependencies/
│   │   │   └── document_dependency.py
│   │   ├── models/
│   │   │   ├── document_model.py
│   │   │   └── document_chunk_model.py
│   │   ├── repositories/
│   │   │   └── document_repository.py
│   │   ├── routers/
│   │   │   └── router.py
│   │   ├── schemas/
│   │   │   └── document_schema.py
│   │   └── services/
│   │       └── document_service.py
│   └── user/                    # User module
├── worker/
│   ├── document_worker.py       # Queue consumer
│   └── main.py                  # Worker entry point
└── main.py                      # FastAPI app
```

---

## 10. Dependencies

### Python Packages

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | >=0.141.1 | Web framework |
| sqlalchemy[asyncio] | >=2.0.52 | ORM |
| asyncpg | >=0.31.0 | PostgreSQL async driver |
| alembic | >=1.19.2 | Database migrations |
| pgvector | >=0.5.0 | Vector embeddings |
| b2sdk | >=2.6.0 | Backblaze B2 storage |
| aio-pika | >=9.4.0 | RabbitMQ client |
| pydantic-settings | >=2.15.0 | Configuration |

### Infrastructure

- **PostgreSQL** - Primary database
- **Backblaze B2** - Object storage for documents
- **RabbitMQ** - Message queue for async processing

---

## 11. Next Steps

### Immediate

1. Run migration: `alembic upgrade head`
2. Configure B2 credentials in `.env`
3. Set up RabbitMQ instance
4. Test the upload flow

### Worker Implementation

1. Implement document parsing (PDF, DOCX, TXT)
2. Implement chunking logic
3. Integrate embedding model
4. Store chunks in database
5. Update document status on completion

### Future Enhancements

1. Add document search (vector similarity)
2. Implement retry logic for failed processing
3. Add webhook notifications
4. Implement document sharing
5. Add version control for documents

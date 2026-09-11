# Mneme — Working Document

## Problem Statement

As a book lover who needs to gather knowledge, I see access to what I've already read.
I want to interact with a single document — asking questions and getting answers grounded in that document alone — and I also want to interact with my library as a whole, asking questions that pull together relevant knowledge across everything I've read on a topic.
I want to read documents normally when I choose to, and query my own knowledge when I need to.

---

## Functional Requirements

1. The user can upload PDF and EPUB documents.
2. The user can view a list of their uploaded documents, including processing status.
3. The user can delete an uploaded document.
4. The user can open and read a single document's content.
5. While viewing a document, the user can ask questions and receive answers grounded in that document, with citations to the relevant section.
6. The user can ask questions in a general chat, and the AI answers using relevant content retrieved across all uploaded documents, citing which document(s) it used.
7. If a question can't be answered from the uploaded documents, the AI states that rather than guessing.

---

## Non-Functional Requirements

### 1. Groundedness
All answers must be derived only from the currently user's own uploaded document content. If an answer isn't supported by retrieved content, the AI must state that explicitly rather than answering from general knowledge.

### 2. Latency
- Document upload and processing (parsing + embedding) completes within a few minutes for a typical book-length document, with visible progress status throughout.
- AI query responses begin streaming within **~2 seconds** and complete within **~10–15 seconds** for simple document questions.
- Cross-library synthesis queries may take longer but must stream progressively rather than leave the user waiting.
- These targets hold at the **95th percentile** under concurrent multi-user load, not just in isolation.

### 3. Durability
Documents, processed embeddings, and AI conversation history must never be lost — the core promise of the product. Requires real backups and replication on the backend, since data lives server-side rather than solely on the user's device.

### 4. Availability
- Target: **99.5% uptime** initially (roughly 3.5 hours downtime/month), tightening over time as the user base and stakes grow.
- Downtime affects all users simultaneously in a multi-tenant system, so this is a genuine shared-infrastructure requirement, not a nice-to-have.

### 5. Scalability
- Traffic and backend load come from a growing concurrent user base with organic/gradual growth patterns (no sharp predictable spikes like major product launches). New documents are processed at their own pace.
- Data volume: retrieval quality and latency must hold up as individual libraries grow into hundreds of documents, and as total users and total documents grow system-wide.

### 6. Security & Multi-Tenant Isolation
- One user must never access, query, or retrieve another user's documents or conversations, even accidentally through bugs.
- Per-user data structures (vector store, index, roles).
- Documents encrypted at rest and in transit.
- Authenticated accounts (registration/login) required for access.
- Clear disclosure of what document content is sent to third-party LLM APIs, given the private/personal nature of reading material.

### 7. Fault Tolerance
If the AI retrieval layer is degraded or down, core reading functions — browsing the document list, opening and reading a document — remain available. Frontend and backend failures (e.g., a result breaking) must be isolated so an AI outage can't cascade into degrading unrelated functionality.

### 8. Cost Management
Since every AI query is a backend cost borne across all users, usage boundaries (rate limits, quotas, or tiers) are needed to keep per-user AI costs sustainable at scale. This ties directly into the product's monetisation model (free, subscription, or usage-capped), which should be decided early since it shapes architecture choices like caching and embedding reuse.

---

## Architecture

```
                        ┌─────────────┐
                        │  Mobile App │
                        └──────┬──────┘
                               │
                        ┌──────▼──────┐
                        │ API Gateway │
                        └──┬──────┬───┘
                           │      │
             ┌─────────────▼─┐  ┌─▼──────────────────┐
             │ Auth Service  │  │  Document Service   │
             │ signup, login,│  │  upload, list,      │
             │ tokens        │  │  view, delete       │
             └──────┬────────┘  └──────────┬──────────┘
                    │                      │
                ┌───▼───┐     ┌────────────▼──────────┐
                │User DB│     │   Processing Queue    │
                └───────┘     │   async: parse,       │
                              │   chunk, embed, tag   │
                              └────────────┬──────────┘
                                           │
                         ┌─────────────────┼───────────────────┐
                         │                 │                   │
              ┌──────────▼──────┐  ┌───────▼──────┐  ┌────────▼───────┐
              │ Object Storage  │  │  Embedding   │  │  Auto-tag      │
              │ (file store)    │  │  Worker      │  │  Worker        │
              └─────────────────┘  │  text →      │  └────────────────┘
                                   │  vectors     │
                                   └──────┬───────┘
                                          │
                                   ┌──────▼───────┐
                                   │  Vector DB   │
                                   └──────┬───────┘
                                          │         tags narrow retrieval scope
                              ┌───────────▼──────────────┐
                              │     Query Service        │
                              │  doc-level +             │
                              │  library-level Q&A       │
                              └──┬──────────────┬────────┘
                                 │              │
                    ┌────────────▼──┐   ┌───────▼──────────────┐
                    │  Query Cache  │   │  Conversation        │
                    │  repeated /   │   │  (history for        │
                    │  similar Qs   │   │  doc / library)      │
                    └───────────────┘   └──────────────────────┘
                                 │
                    ┌────────────▼──────────┐
                    │  LLM API              │
                    │  grounded, streamed   │
                    │  answer               │
                    └───────────────────────┘
```

---

## API Endpoints

### Documents
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/documents` | Upload a PDF or EPUB |
| `GET` | `/documents` | List user's documents (with processing status) |
| `GET` | `/documents/{id}` | Get document metadata/content for viewing |
| `DELETE` | `/documents/{id}` | Remove a document |

### Document-level Q&A
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/documents/{id}/query` | Ask a question scoped to one document; returns a grounded answer (streamed) |

### Library-level Q&A
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/library/query` | Ask a question across all documents; returns an answer citing which document(s) it drew from (streamed) |

### Conversations *(durability requirement)*
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/documents/{id}/conversations` | Past Q&A history for a document |
| `GET` | `/library/conversations` | Past library-wide query history |

### Auth *(multi-tenant)*
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/signup` | Register a new user |
| `POST` | `/auth/login` | Authenticate and receive a token |
| … | … | Additional auth endpoints (refresh, logout, etc.) |

# Document Q&A (chat)

Grounded question answering over a single document: the retriever finds
relevant chunks, Gemini generates a streamed answer, and every turn is saved
as a conversation scoped to `(user, document)`.

```
POST /documents/{id}/query
        │
        ▼
  ChatService.start_turn
  ├─ ownership + status checks (404 / 409)
  ├─ resolve conversation (create new, or continue by id)
  ├─ VectorSearchService.search(mode="hybrid") → token budget
  ├─ persist user message + commit          ← durable before first byte
        │
        ▼
  GeminiService.stream  ──system prompt with numbered [n] context──▶  deltas
        │
        ▼
  ChatService.finish_turn
  ├─ map [n] markers → citations
  ├─ persist assistant message + commit     ← durable before `done`
        ▼
  SSE: citations → done   (on failure: error)
```

## Endpoints

| Method & path | Purpose |
|---|---|
| `POST /api/v1/documents/{document_id}/query` | Ask a question; SSE-streamed grounded answer |
| `GET /api/v1/documents/{document_id}/conversations` | Past sessions for the document (newest first, `limit`/`offset`) |
| `GET /api/v1/documents/{document_id}/conversations/{conversation_id}/messages` | One session's messages, oldest first |

All three require a bearer token and are scoped to the authenticated user —
another user's documents, conversations, and messages return 404 (never 403:
existence itself is not disclosed).

### Request body (`POST …/query`)

```json
{ "query": "What drove revenue growth?", "conversation_id": "optional-uuid" }
```

- Omit `conversation_id` → a **new session** is created for this document
  (title = first 120 chars of the query).
- Pass a previous session's id → the turn continues it; the id must belong
  to the caller **and** to this document (otherwise 404 / 409).

Guards before the stream starts (JSON error envelope, like every other route):

| Status | When |
|---|---|
| 401 | missing/invalid token |
| 404 | document or conversation not found (or not yours) |
| 409 | document not `processed` yet; conversation belongs to another document |
| 422 | body validation (`query` 1–4000 chars) |

## SSE event protocol

`Content-Type: text/event-stream`. Each event is one JSON object:

| Event | Data | When |
|---|---|---|
| `conversation_id` | `{"conversation_id": "..."}` | first frame — lets the client shelve/attach the session immediately |
| `delta` | `{"text": "..."}` | 0..n answer fragments, in order |
| `citations` | `{"citations": [...]}` | after the model finishes |
| `done` | `{"conversation_id": "...", "document_id": "..."}` | terminal success |
| `error` | `{"message": "..."}` | terminal failure — replaces `done` |

```
event: conversation_id
data: {"conversation_id": "8f2…"}

event: delta
data: {"text": "Revenue grew "}

event: delta
data: {"text": "largely because of [1]."}

event: citations
data: {"citations": [{"chunk_id": "…", "document_id": "…", "chunk_index": 3,
                      "page_number": 12, "section_title": "Revenue",
                      "snippet": "…"}]}

event: done
data: {"conversation_id": "8f2…", "document_id": "41c…"}
```

Notes:

- Errors **before** the first byte leave as the normal JSON error envelope;
  once streaming has begun, failures are `error` events (the status line is
  already on the wire).
- On a client disconnect, the question is already committed and whatever
  answer fragments were produced are persisted by a background task — history
  never loses the turn.

## Grounding & citations

- Retrieval: hybrid search (vector + full-text, RRF) over the document's
  chunks, top `CHAT_RETRIEVAL_LIMIT`, truncated to
  `CHAT_CONTEXT_TOKEN_BUDGET` tokens (at least the top chunk is always kept).
- The system prompt numbers each passage `[1] … [n]` with page/section
  metadata and forbids answering from anything else.
- The model cites inline (`[2]`, `[1][4]`); `extract_citations` maps those
  markers back to chunk metadata (in first-appearance order, deduplicated,
  out-of-range ignored) and stores them as JSONB on the assistant message.
- History replay: last `CHAT_HISTORY_MAX_MESSAGES` messages (oldest first),
  with the current question appended — fetched *before* the turn is written
  so it is never replayed twice.

## Persistence

Two tables (migration `e7c4a9f2b3d1`, ERD `mneme_erd.mermaid`):

- `conversations(id, user_id, document_id, title, created_at, updated_at)` —
  `document_id` is nullable for future library-level sessions; chat always
  sets it. Indexed on `(user_id, document_id, updated_at)` for history lists.
- `messages(id, conversation_id, role, content, citations JSONB, created_at)`
  — `role` is `user` or `assistant` (CHECK constraint), cascades on delete.

Turns are committed twice, mid-request — sanctioned exceptions to
`get_db`-owns-the-commit ([database.md](database.md#transaction-policy)):
once for the question before streaming starts, once for the answer before
`done`.

## Configuration

See [configuration.md](configuration.md#llm-google-gemini): `GEMINI_API_KEY`
(empty ⇒ app boots but Q&A fails with an `error` event; rejected in
production), `GEMINI_MODEL`, `GEMINI_MAX_OUTPUT_TOKENS`,
`CHAT_CONTEXT_TOKEN_BUDGET`, `CHAT_HISTORY_MAX_MESSAGES`,
`CHAT_RETRIEVAL_LIMIT`.

## Tests

- `tests/test_chat_service.py` — unit: budget truncation, citation mapping,
  start/finish turn orchestration, prompt assembly, guard statuses.
- `tests/test_chat.py` — integration: SSE frame sequence, persistence,
  follow-up turns, session isolation (404s), unprocessed-document 409,
  mid-stream failure → `error` with the question kept.

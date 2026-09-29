# Document pipeline

Upload → confirm → queue → worker → parse → chunk → embed → searchable.

Implementation: `app/modules/documents/` (API side) and `app/worker/`
(worker side); queue topology in [queue.md](queue.md).

## Status lifecycle

Statuses live on `documents.status` (enum in
`app/modules/documents/models/document_status.py`) and are constrained in the
database by `ck_documents_status` — even a hand-written UPDATE cannot store
an unknown status.

```
pending_upload ──confirm──▶ uploaded ──worker picks up──▶ processing
      │                       ▲                              │
      │ confirm: oversize     │ retry / re-embed requeue      ├──▶ processed
      ▼                       │                               │
    failed ◀──attempts out────┴──── retries exhausted ────────┘

processed ──re-embed requeue──▶ uploaded      (scripts/reembed_documents.py)
failed    ──operator retry───▶ uploaded       (same script / manual)
```

All transitions go through one function, `transition(document, target)`,
which raises `InvalidStatusTransition` for anything not in
`ALLOWED_TRANSITIONS`. HTTP handlers, the worker, and the re-embed script all
use it — there is no other place that writes status ad hoc.

## Steps

### 1. Create — `POST /api/v1/documents`

- Validates `file_size ≤ MAX_UPLOAD_BYTES` (else 413).
- Inserts a `documents` row with `status="pending_upload"` and a fresh
  `storage_key` (`{user_id}/{uuid}.{ext}`).
- Returns `DocumentWithUploadUrl`: the document, a **presigned PUT URL**, the
  method (`PUT`), and the exact headers the client must send (their values
  are covered by the URL's signature).

The file itself never passes through the API — the client uploads straight to
B2 ([storage.md](storage.md)).

### 2. Client uploads to B2

Directly with `PUT <upload_url>`. The API does not know when this finishes.

### 3. Confirm — `PATCH /api/v1/documents/{id}/confirm`

The API verifies the upload really happened and queues processing:

1. Only `pending_upload` (first confirm) or `uploaded` (re-queue after a
   failed publish) may be confirmed; anything else → 400.
2. First confirm: read the object's size from B2.
   - Object missing → 400 "File has not been uploaded".
   - Oversized → delete the object, set `failed`, **commit** (documented
     transaction-policy exception), then raise 413 so the failure persists
     despite the error response.
   - OK → record actual size, `pending_upload → uploaded`, **commit**, then
     publish `DocumentEvent` to RabbitMQ.
3. If publishing fails: 503 with "retry confirm" — because the row is
   committed as `uploaded`, retrying confirm skips the size check and simply
   republishes.

Commit-before-publish matters: the worker skips-and-acks messages whose
document row isn't visible yet (`get_db` only commits after the response).

### 4. Worker processes the event

`app/worker/document_worker.py::process_document` (one DB session per job,
worker owns its own transactions):

1. Load the row (missing → ack and skip; already `processed` → ack and skip,
   duplicate message).
2. `uploaded → processing`, commit. If the row vanished between lookup and
   this write (user deleted it mid-flight), raise `DocumentDeletedError` —
   the message is acked, nothing is retried.
3. Download the file from B2 to a temp file (the DB row is the source of
   truth, not the message body).
4. Parse with Docling (`docling_parser`) — PDF and EPUB.
5. Chunk with the Nomic tokenizer under `MAX_CHUNK_TOKENS`
   ([search.md](search.md#chunking)).
6. Embed all chunk texts in one batch, insert `document_chunks` rows
   (existing chunks are deleted first so re-processing replaces them).
7. `processing → processed`, commit. Temp file removed in `finally`.

Failures (any step) → `retry_or_fail`: the message is republished to a
delayed retry queue with an incremented `x-attempt` header, and status goes
back to `processing → uploaded`. After `MAX_ATTEMPTS` (4) the document is
marked `failed` and the message dead-letters to
`document_processing.*.dead`.

If the document is deleted mid-processing, the worker's writes fail with
`NoResultFound` / `StaleDataError` / `IntegrityError` (FK), all converted to
`DocumentDeletedError`: acked cleanly, no retry, no status write.

### 5. Searchable

Once chunks + embeddings are committed the document appears in
`GET /api/v1/search` results ([search.md](search.md)).

## Deletion — `DELETE /api/v1/documents/{id}`

Safe-deletion order (the invariant: **never lose the row**):

1. Delete the `documents` row (chunks cascade).
2. **Commit** the delete (transaction-policy exception) — if this fails,
   nothing was removed and the client can retry.
3. Best-effort delete of the B2 object. Failure here only logs a warning:
   the row is gone, so the leftover object is an orphan for a cleanup job —
   not a failed deletion for the user.

A document being processed while deleted hits the worker's
`DocumentDeletedError` path above, so in-flight jobs stop cleanly.

## Listing

`GET /api/v1/documents?limit=&offset=` — `limit` default 20, max 100;
stable newest-first ordering (`created_at DESC, id DESC`); always scoped to
the authenticated user.

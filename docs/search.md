# Search & embeddings

`GET /api/v1/search` finds chunks the authenticated user owns, using vector
similarity, Postgres full-text, or a hybrid of both.

Implementation: `app/modules/rag/` (`routers/search_router.py`,
`services/vector_search_service.py`), chunking in
`app/modules/documents/services/docling_parser.py`, embedding in
`app/modules/documents/services/embedding_service.py`.

## Endpoint

```
GET /api/v1/search?query=...&document_id=<uuid>&mode=hybrid&limit=5
Authorization: Bearer <access token>
```

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | string, 1–1000 chars | required | free-text question |
| `document_id` | uuid | none | narrow to one document (must be owned) |
| `mode` | `hybrid` \| `vector` \| `text` | `hybrid` | see below |
| `limit` | int 1–50 | 5 | results returned |

Returns `list[ChunkSearchResult]`: chunk content, `document_id`,
`chunk_index`, page/section metadata, and a `score` (interpretation depends
on mode — see table).

**Isolation**: every query is filtered to `user_id = <current user>` first;
scoring only ever competes chunks *within* that scope. A `document_id` you
don't own behaves like no matches, not an access leak. Covered by tests per
mode (`tests/test_search.py`).

## Modes

| Mode | How it scores | `score` means |
|---|---|---|
| `vector` | pgvector cosine distance (`<=>`) between the query embedding and chunk embeddings, ordered ascending | distance (lower = closer) |
| `text` | Postgres full-text: `content_tsv @@ plainto_tsquery`, ordered by `ts_rank_cd` | rank (higher = better) |
| `hybrid` | both lists fused with **Reciprocal Rank Fusion (RRF)** | RRF score |

The query is embedded at request time with the *same* pinned model as
ingestion — a search before any documents are processed still works (it just
finds nothing).

## Embeddings

- Model: `EMBEDDING_MODEL` (default `nomic-ai/nomic-embed-text-v1.5`),
  dimension `EMBEDDING_DIMENSION` (768).
- **Pinned revision**: `EMBEDDING_MODEL_REVISION` is an exact HuggingFace
  commit (40-hex). The model is loaded with `revision=...`, so every process
  embeds with byte-identical weights. Production refuses to start with a
  branch name or short/non-hex value.
- Runs **locally** (SentenceTransformer, no API). First run downloads into
  the HF cache (`HF_HOME`, a Docker volume in compose).
- Every chunk row records the model it was embedded with
  (`document_chunks.embedding_model`).

### Changing the model or revision

Never bump the model/revision without re-embedding — vectors from different
weights are not comparable:

```bash
# 1. update EMBEDDING_MODEL / EMBEDDING_MODEL_REVISION in .env
# 2. re-queue documents (resets status → uploaded, publishes events):
uv run python -m scripts.reembed_documents                # all documents
uv run python -m scripts.reembed_documents --user-id <uuid>
uv run python -m scripts.reembed_documents --document-id <uuid>
# 3. worker replaces each document's chunks atomically
```

The script is safe to re-run; the worker deletes a document's old chunks
before inserting new ones.

## Chunking

`DoclingParserService` bounds every chunk by `MAX_CHUNK_TOKENS` (default
512) counted with the **Nomic tokenizer itself** — token counts and the
embedding model must agree on what a token is.

- Parser output is split recursively (order-preserving) into ≤ budget
  windows; if a tokenizer round-trip mismatch appears, the window is halved
  and retried down to single tokens.
- `token_count` stored per chunk is the exact
  `tokenizer.encode(text, add_special_tokens=False)` length, not an estimate.
- `chunk_index` is **gapless** (0, 1, 2, …) across the whole document —
  splitting happens inside one pass that appends with
  `chunk_index = len(chunks)`.

## Indexes

Both search paths are index-backed (migration `a4c6d8f0b2e1`):

| Index | Column | Backs |
|---|---|---|
| `ix_document_chunks_embedding_hnsw` | `embedding` (HNSW, cosine) | `vector` mode + hybrid vector leg |
| `ix_document_chunks_content_tsv` | `content_tsv` (GIN) | `text` mode + hybrid text leg |

`content_tsv` is a **stored generated column**:
`to_tsvector('english', content)`, so the index can't go stale and queries
avoid recomputing per row; `ts_rank_cd(content_tsv, …)` ranks from the same
column. The column is mapped `deferred()` — ORM row loads don't pay for the
tsvector until a query actually uses it.

HNSW is an *approximate* index: it may trade a sliver of recall for large
speed gains. Tests verify with `EXPLAIN` that both indexes are chosen for
their queries (`tests/test_search_indexes.py`).

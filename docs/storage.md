# Storage (Backblaze B2)

Files live in a Backblaze B2 bucket accessed through its S3-compatible API.
The API never proxies file bytes: clients upload/download directly via
presigned URLs, and the worker streams the file once for parsing.

Implementation: `app/core/storage/b2_storage.py` (boto3 client),
`app/core/storage/storage_service.py` (thin service + lazy singleton).

## Client lifecycle

- Exactly **one boto3 client per process**, created **lazily** on first use
  by the `storage_service` proxy (`_LazyStorageService`). Importing the app
  does not construct a client or validate the endpoint — misconfiguration
  fails on first use, not at import.
- All call sites (documents service, worker) go through `storage_service`;
  methods that block run via `asyncio.to_thread` on the API side.

## Endpoints & region

`B2_S3_ENDPOINT` is the bucket's endpoint from the B2 web UI
(Buckets → Endpoint), e.g. `https://s3.us-west-004.backblazeb2.com`.
`_region_from_endpoint()` derives the boto3 `region_name` by taking the
second dot-separated label (`us-west-004`). A scheme-less value
(`s3.us-west-004.backblazeb2.com`) is accepted; an unrecognised host raises
`ValueError` at client construction.

Credentials: `B2_KEY_ID` (application key ID) + `B2_APPLICATION_KEY`
(application key), signature version `s3v4`.

## Operations

| Operation | Where used | Semantics |
|---|---|---|
| `generate_upload_url(storage_key, content_type)` | create document | presigned **PUT**, valid `UPLOAD_URL_EXPIRE_SECONDS` (900s). The exact headers returned to the client are part of the signature and must be sent unchanged |
| `get_file_size(storage_key)` | confirm upload | `HEAD`; `None` means the client never uploaded |
| `get_download_url(storage_key)` | (client download) | presigned GET |
| `download_file_to_path(key, path)` | worker | full object download to a temp file for Docling |
| `delete_file(key)` | oversize / delete document | best-effort after the DB commit |

## Size enforcement

`MAX_UPLOAD_BYTES` (default 100 MB) is checked twice:

1. `POST /documents` against the client-declared size → 413 up front.
2. `PATCH …/confirm` against the **actual stored size** → oversized object
   is deleted, document marked `failed` (committed), 413 returned.

## Failure semantics vs. the database

The invariant everywhere: **the DB row wins; storage is best-effort after
the commit.**

- Confirm: row committed before publish; storage size check failures return
  4xx/5xx without corrupting state (retry confirm is safe).
- Delete: row deleted + committed first; a storage delete failure only logs
  a warning — the orphan object is left for a cleanup job, never reported as
  a failed deletion ([document-pipeline.md](document-pipeline.md#deletion--delete-apiv1documentsid)).
- Worker download failure is a retryable error (normal retry ladder).

## Credentials

Set in `.env` (`B2_KEY_ID`, `B2_APPLICATION_KEY`) — never commit real keys.
See [configuration.md](configuration.md#storage-backblaze-b2).

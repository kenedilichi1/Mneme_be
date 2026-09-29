# RabbitMQ queue

One message type — "process this document" — between the API and the worker.
Topology and client live in `app/core/queue/` (`queue.py`, `events.py`);
consumption in `app/worker/document_worker.py`.

## Topology

| Resource | Name | Notes |
|---|---|---|
| Exchange | `documents` | direct, durable |
| Work queue | `document_processing.<ENVIRONMENT>` | durable; name includes `ENVIRONMENT` so dev/test/prod never cross |
| Retry queues | `…​.retry.1`, `…​.retry.2`, `…​.retry.3` | per-delay TTL queues that dead-letter back into the exchange with the main routing key |
| Dead-letter queue | `document_processing.<env>.dead` | terminal failures |

Retry delays are one queue per delay (`RETRY_DELAYS_SECONDS = 30, 120, 600`)
so a short retry never queues behind a longer one. With
`MAX_ATTEMPTS = len(delays) + 1 = 4`, a document gets attempts at ~0s, 30s,
150s, and 750s before it gives up.

## Message shape

```json
{
  "event_type": "document.uploaded",
  "document_id": "<uuid>",
  "user_id": "<uuid>",
  "storage_key": "<uuid>/<uuid>.pdf"
}
```

Published persistent (`PERSISTENT` delivery mode). The worker treats the
**database row as the source of truth** — the message is only a pointer; a
missing/deleted row means the message is skipped or acked without work.

Headers: `x-attempt` (int, defaults to 1) counts attempts across retries.

## Publishing (API side)

`RabbitMQ.publish()` (`app/core/queue/queue.py`):

- `connect()` opens the connection under an **`asyncio.Lock`** — concurrent
  publishes race the "no connection yet" check and would otherwise open
  several connections.
- The exchange and queue are declared **once per channel** and cached
  (`_exchange`, `_declared_queues`); the cache resets on reconnect/close.
  Repeated publishes don't re-declare every time.
- Messages are published only **after** the document's `uploaded` row is
  committed (see [document-pipeline.md](document-pipeline.md#3-confirm--patch-apiv1documentsidconfirm)).
  If the publish fails the API returns 503 and the client retries confirm —
  the commit-then-publish order makes that idempotent.

The connection is created lazily (first publish); nothing connects at import
time. Shutdown closes channel + connection and clears the caches.

## Consuming (worker side)

`start_worker()`:

1. connect, `set_qos(prefetch_count=WORKER_PREFETCH_COUNT)` (default 1 —
   queued jobs must not sit un-acked long enough to hit the broker's ack
   timeout)
2. `declare_processing_queues()` — main + retry + dead queues
3. consume; every message goes through `handle_message`:

| Outcome | Action |
|---|---|
| malformed body | dead-letter with `x-failure-reason`, ack |
| `redelivered` (worker crashed mid-job) | counted as a failed attempt → retry/dead-letter (prevents infinite crash loops) |
| `DocumentDeletedError` (row gone mid-processing) | ack, no retry, no status write |
| permanent error (`is_permanent_error`) | status → `failed`, ack |
| any other exception | `retry_or_fail`: republish to `…​.retry.N` with `x-attempt+1`, status → `uploaded`; after attempt 4 → dead-letter + status `failed`; ack only after the copy is published |
| success | ack |
| handler itself failed (e.g. DB down while recording) | `nack(requeue=True)` — counted as an attempt on redelivery |

## Requirements

- `RABBITMQ_URL` must be reachable **from the container/host running the
  worker and the API**. A DNS failure shows up as
  `aiormq.exceptions.AMQPConnectionError: [Errno -5] No address associated
  with hostname` — verify the hostname resolves where the process runs
  (dev uses a managed CloudAMQP instance; see [operations.md](operations.md#troubleshooting)).
- Broker restarts are tolerated by `connect_robust`; the declare caches are
  invalidated when a new channel is opened.

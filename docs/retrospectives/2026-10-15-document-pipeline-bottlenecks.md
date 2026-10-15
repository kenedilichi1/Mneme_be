# Reflection: bottlenecks faced on document `99acf52c…` (768-page book, 44 MB)

The document eventually processed cleanly (768 pages, 1,505 embedded chunks, status `processed`), but getting there surfaced six distinct bottlenecks — three environmental, three architectural.

## 1. Network: three unreachable endpoints blocked the build and the parse

- **ghcr.io** timed out, so `COPY --from=ghcr.io/astral-sh/uv:latest` couldn't install the dependency manager. Fix: install `uv` from PyPI inside the image.
- **modelscope.cn** was unreachable from inside the containers, so RapidOCR's torch weights never downloaded — OCR stayed dead. Fix: add `onnxruntime` so Docling auto-OCR runs on the wheel-bundled ONNX weights (offline-capable).
- **CloudAMQP DNS** intermittently failed (`No address associated with hostname`), bouncing the worker ~17 times overnight. Fix: public DNS fallbacks (`1.1.1.1`, `8.8.8.8`) on the worker — later extended to the server, which hit the same error live.

**Lesson:** the pipeline's critical path ran through three third-party hosts, and container DNS was the least reliable of them. Any of the three failing stalls or corrupts throughput invisibly.

## 2. Missing system library: every parse died on import

`import cv2` (pulled in by Docling's tableformer) needs `libxcb.so.1`, which the slim image lacked. Every worker run crashed at model init. Fix: install `libgl1`, `libglib2.0-0`, `libxcb1`.

**Lesson:** image slimming is a real cost lever, but "it imports locally" is not evidence — native deps surface only inside the container.

## 3. Memory (the true bottleneck): one document, one worker, ~3.8 GiB

Converting all 768 pages in a single pass peaked well past the Docker VM's memory, and the worker was **OOM-killed (exit 137)** — repeatedly. Each kill also burned one of only four queue attempts, so a memory problem presented as a *retries-exhausted* problem. Fix: page-batched conversion (20 pages/batch, bounded threads), which held peak RSS at 1.6–2.7 GiB.

**Lesson:** Docling's memory scales with pages-in-flight, not file size. Concurrency in this system is a memory question first — 2.7 GiB per in-flight document is why concurrency is 1 here.

## 4. Attempt economics: infrastructure failures consumed user attempts

`MAX_ATTEMPTS = 4` and **redelivery counts as an attempt**. So OOM kills, broker restarts, and DNS blips each spent an attempt on work the user never failed. Under load this converts transient infra problems into permanent user-visible failures.

**Lesson:** attempt budgets should be spent on *application* errors, not on the platform dying mid-job; long jobs need resume/idempotency, not blind redelivery.

## 5. The requeue loop: a status-bookkeeping exception before the ack

A stale message for the already-`processed` document raised `InvalidStatusTransition` (`processed → failed` is illegal) *after* dead-lettering but *before* `message.ack()`. The unacked message was `nack(requeue=True)`'d, redelivered, and failed identically — **612 hot-loop iterations, zero backoff**, spraying a copy into the DLQ each pass (614 messages). Fix: `set_document_status` now returns early when the document is already `processed` and swallows illegal transitions with a warning, so the ack always runs.

**Lesson:** anything between "the work is decided" and "the ack" must be non-throwing. A bookkeeping write can turn one stale message into an unbounded hot loop that *looks* like a traffic problem.

## 6. Cleanup hazard and the measurement gap

- Purging the DLQ via the management API (`purge: true`) also dropped the queue **object**, so dead-letter routing would have silently dropped messages until the worker restarted and re-declared it. Destructive queue operations need a post-check, not a status code.
- Our only per-document timing data point is polluted: upload→chunks spanned ~13 hours of debugging/downtime, and the successful run's logs were wiped by the image rebuild. We *cannot* currently state the steady-state cost of a large document, which is the one number capacity planning needs. Any scale estimate (e.g. 100 users × 40 MB) inherits that uncertainty.

## Overall

The bottleneck chain was: **environment (network/image) → memory → retry semantics → bookkeeping exception**. Memory was the binding physical constraint (concurrency = 1); the rest made a memory problem look like an outage and burned attempts while failing. The fixes are verified and green, but attempt-budget semantics and per-document throughput telemetry are the two things to settle before adding load.

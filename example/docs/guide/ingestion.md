# Ingesting events

## Single writes

```bash
kagura events put orders --key order-1001 --data @order.json
```

A single write waits for the quorum acknowledgement, which costs roughly one network round trip
between replicas. At a few hundred events per second this is fine; beyond that, batch.

## Batch writes

```python
with client.batch_writer("orders", batch_size=500, linger_ms=20) as writer:
    for order in orders:
        writer.put(key=order.id, data=order.as_dict())
```

`batch_size` caps how many events accumulate before a flush, and `linger_ms` caps how long a
partly filled batch waits. A batch is sent when either limit is reached. Raising `linger_ms`
trades latency for throughput, and the sweet spot for most pipelines is 10 to 50 milliseconds.

Closing the writer flushes automatically. If you need a barrier mid-stream, call
`EventBatchWriter.flush()` and wait for it, because the context manager only guarantees a flush
on exit.

## Preventing duplicates

Retries are unavoidable in a distributed system: the acknowledgement can be lost even though the
write succeeded, so the client tries again and the same event is stored twice. An idempotency
key removes the problem.

```python
writer.put(key=order.id, data=order.as_dict(), idempotency_key=order.id)
```

Kagura remembers the idempotency keys it has seen for `ingest.dedup_window`, 24 hours by
default, and silently drops a repeat. The key is scoped to a single stream and partition, so the
same value in two streams is not treated as a duplicate.

Points worth knowing:

- The key must be stable across retries. A UUID generated per attempt defeats the mechanism
  entirely, which is the most common way this goes wrong.
- Widening the window costs memory: roughly 80 bytes per remembered key per partition.
- Outside the window, a repeat is accepted as a new event. For deduplication over days, add a
  unique constraint in the consumer instead.

## Compression

```yaml
ingest:
  compression: zstd    # none | gzip | zstd
  compression_level: 3
```

Compression is applied per batch, not per event, so it only helps when batches are reasonably
full. JSON payloads typically shrink by 60 to 80 percent with zstd at level 3.

## Backpressure

When the write-ahead log cannot keep up, Kagura applies backpressure rather than buffering
without bound. Clients see `ERR_BACKPRESSURE` with a `Retry-After` header, and the SDKs honour
it automatically with exponential backoff and jitter.

Watch `kagura_ingest_backpressure_seconds_total`. Sustained backpressure means the disk cannot
absorb the write rate, and the fixes, in order of effectiveness, are a faster disk, more
partitions, then `storage.fsync: interval`.

## Quotas

Per-tenant quotas are enforced at ingest. Exceeding one returns `ERR_QUOTA_EXCEEDED` together
with the limit and the current usage, so a client can log something actionable.

```bash
kagura tenant quota set team-a --events-per-second 50000 --bytes-per-day 2TiB
```

## Rejected events and the dead letter stream

Events that cannot be accepted, because of a schema violation or an oversized payload, are
written to `<stream>.dlq` when `ingest.dead_letter: true`. The original payload is preserved
alongside a `rejection_reason` header, which is what makes a bad deploy recoverable instead of
merely observable.

```bash
kagura events tail orders.dlq --format json | jq '.headers.rejection_reason' | sort | uniq -c
```

## Ordering guarantees while retrying

Within one partition, an SDK sends at most `ingest.max_inflight_batches` batches concurrently,
five by default. Set it to 1 if you need strict ordering even when a batch is retried, and
accept the throughput cost. With the default, a retried batch can land after a later one.

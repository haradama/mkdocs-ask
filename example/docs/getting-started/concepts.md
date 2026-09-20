# Concepts

## Streams

A stream is an append-only sequence of events. It resembles a table, except that nothing is ever
updated or deleted in place. Data leaves a stream only through retention and
[compaction](../guide/retention.md).

## Events

An event carries a key, a payload, a timestamp and headers.

```json
{
  "key": "order-1001",
  "data": {"amount": 2980, "currency": "JPY"},
  "time": "2026-08-14T09:12:00Z",
  "headers": {"source": "checkout-api", "trace_id": "4bf92f..."}
}
```

The payload limit defaults to 1 MiB. Anything larger is rejected with `ERR_PAYLOAD_TOO_LARGE`.

## Partitions and ordering

A stream is split into partitions. Events with the same key always land in the same partition,
so **ordering is guaranteed per key**. There is no ordering guarantee across the stream as a
whole, which is what lets Kagura scale writes horizontally.

```text
orders
|- partition 0   [e1][e4][e7] ...
|- partition 1   [e2][e5]     ...
+- partition 2   [e3][e6][e8] ...
```

## Offsets

An offset is a monotonically increasing integer identifying a position inside one partition.
Consumers record the offset they have processed so they can resume after a restart.

## Consumer groups

Consumers sharing a group name divide the partitions between themselves. Offsets are tracked per
group, so several independent applications can read the same stream at their own pace.

Rebalancing starts `consumer.rebalance_delay` after membership changes, five seconds by default.
Shorter values react faster but thrash during rolling restarts.

## Delivery semantics

| Mode | Guarantee | Use it for |
| --- | --- | --- |
| at-most-once | No duplicates, possible loss | Metrics where a gap is acceptable |
| at-least-once | No loss, possible duplicates | The default |
| effectively-once | No loss, no duplicates | Idempotency keys plus transactional writes |

If the same event keeps being processed more than once, the fix is almost always an idempotency
key. [Ingesting events](../guide/ingestion.md) explains how deduplication works and how long the
dedup window lasts.

## Schemas

A stream can be bound to an Avro or JSON Schema definition. Writes that violate the configured
compatibility mode are rejected with `ERR_SCHEMA_MISMATCH`. See [Schemas](../guide/schema.md).

## Tenants

When one cluster is shared between teams, tenants separate namespaces and quotas. Queries cannot
cross a tenant boundary, and per-tenant quotas are enforced at ingest time.

## Projections

A projection continuously aggregates a stream into a key-value view. For dashboards that only
need the current aggregate, reading a projection is orders of magnitude cheaper than scanning
the stream on every refresh.

```sql
CREATE PROJECTION orders_hourly AS
SELECT sum(amount) FROM orders GROUP BY tumble(time, 1h), currency
```

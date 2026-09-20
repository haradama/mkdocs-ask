# Querying

KQL is SQL-like, with time as a first-class concept.

## A first query

```sql
SELECT key, amount, time
FROM orders
WHERE time BETWEEN now() - 24h AND now() AND amount > 10000
ORDER BY time DESC
LIMIT 100
```

```bash
kagura query --file ./big-orders.kql --format table
```

Output formats are `table`, `json`, `ndjson` and `csv`.

## Time windows

| Function | Meaning |
| --- | --- |
| `tumble(time, 5m)` | Fixed, non-overlapping five-minute buckets |
| `hop(time, 5m, 1m)` | Five-minute windows starting every minute |
| `session(time, 30m)` | Groups by gaps shorter than thirty minutes |

```sql
SELECT tumble(time, 1h) AS hour, currency, sum(amount) AS total
FROM orders
WHERE time > now() - 7d
GROUP BY hour, currency
```

Window functions operate on event time, the `time` field on the event, not on arrival time. Late
data is therefore placed in the window it belongs to, up to `query.allowed_lateness`, one hour
by default.

## Aggregations

`count`, `sum`, `avg`, `min`, `max`, `p50`, `p95`, `p99`, `count_distinct`, `first`, `last`.

Percentiles are approximate, computed with a t-digest, and accurate to about 1 percent in the
tails. For exact percentiles, export the raw rows and compute them elsewhere.

## Paging

Large result sets are paged with an opaque cursor. Never build paging on `OFFSET`: it re-scans
from the start each time, and the cost grows quadratically across a full walk.

```bash
kagura query --file q.kql --limit 1000 --cursor-out ./cursor
kagura query --file q.kql --limit 1000 --cursor-in ./cursor
```

A cursor is valid for `query.cursor_ttl`, fifteen minutes by default. Reusing an expired cursor
returns `ERR_CURSOR_EXPIRED`.

## Streaming results

```bash
kagura query --file q.kql --follow
```

With `--follow` the query stays open and emits rows as matching events arrive. Aggregations emit
a revised row per window whenever the window changes.

## Cost controls

| Key | Default | Effect |
| --- | --- | --- |
| `query.timeout` | `60s` | Aborts with `ERR_QUERY_TIMEOUT` |
| `query.max_scanned_bytes` | `10GiB` | Aborts with `ERR_QUERY_TOO_EXPENSIVE` |
| `query.max_concurrent` | `16` | Queues beyond this, per tenant |

A query that always exceeds the scan limit usually lacks a `time` predicate. Kagura can only
skip segments when the query bounds event time, so an unbounded query reads everything.

## The slow query log

```yaml
query:
  slow_log_threshold: 5s
```

Anything slower is logged with its plan. Inspect a plan without running the query:

```bash
kagura query --file q.kql --explain
# scan orders [2026-08-01..2026-08-14]  segments=42  est_bytes=3.1GiB
#   filter amount > 10000
#   aggregate sum(amount) by tumble(time, 1h)
```

`segments=42` is the number that matters. If it is close to the total segment count, your time
predicate is not pruning anything.

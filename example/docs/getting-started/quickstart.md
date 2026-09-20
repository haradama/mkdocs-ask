# Quickstart

Five minutes from an empty machine to a stream you can query.

## 1. Start the server

```bash
kagura serve --data-dir ./data --listen 0.0.0.0:7700
```

For local experiments, `--dev` starts a single node with authentication off and fsync disabled.
Never use it in production: an unclean shutdown loses recent writes.

## 2. Create a stream

```bash
kagura stream create orders \
  --partitions 8 \
  --retention 30d \
  --schema ./schemas/order.avsc
```

Partition count can be increased later but never decreased, because that would break per-key
ordering. Read the sizing guidance in [Performance tuning](../operations/performance.md) before
you pick a number.

## 3. Write events

```bash
kagura events put orders --key order-1001 --data '{"amount": 2980, "currency": "JPY"}'
```

From an SDK it looks like this:

```python
from kagura import Client

client = Client("http://localhost:7700", api_key="kgr_live_...")
with client.batch_writer("orders", batch_size=500) as writer:
    for order in orders:
        writer.put(key=order.id, data=order.as_dict(), idempotency_key=order.id)
    writer.flush()
```

Passing `idempotency_key` is what stops a retry from storing the same order twice. See
[Ingesting events](../guide/ingestion.md) for the full story.

## 4. Query

```sql
SELECT count(*) AS orders, sum(amount) AS total
FROM orders
WHERE time > now() - 1h AND currency = 'JPY'
GROUP BY tumble(time, 5m)
```

```bash
kagura query --file ./orders.kql --format table
```

## 5. Follow the stream

```bash
kagura events tail orders --from latest --group analytics
```

## Next

- [Concepts](concepts.md) for offsets and consumer groups
- [Authentication](../guide/auth.md), which you must configure before exposing the port
- [Configuration](../guide/config.md) for how files, environment and flags interact

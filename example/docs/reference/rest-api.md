# REST API

Base path `/v1`. Every request needs a credential, see [Authentication](../guide/auth.md).

## Conventions

- Request and response bodies are JSON with `Content-Type: application/json`.
- Timestamps are RFC 3339 in UTC.
- Durations are strings such as `30d` or `100ms`.
- Every response carries `X-Request-Id`; include it when reporting a problem.

## Streams

### `GET /v1/streams`

```bash
curl -H "X-API-Key: $KEY" https://kagura.internal/v1/streams
```

```json
{"streams": [{"name": "orders", "partitions": 8, "retention": "30d", "events": 1284021}]}
```

### `POST /v1/streams`

```json
{"name": "orders", "partitions": 8, "retention": "30d", "cleanup_policy": "delete"}
```

Returns `201` on creation and `409` if the stream exists.

### `PATCH /v1/streams/{name}`

Accepts `retention`, `retention_bytes` and `cleanup_policy`. `partitions` may only increase.

### `DELETE /v1/streams/{name}`

Requires `?confirm=true`. Irreversible.

## Events

### `POST /v1/streams/{name}/events`

```json
{
  "events": [
    {"key": "order-1001", "data": {"amount": 2980}, "idempotency_key": "order-1001"}
  ]
}
```

```json
{"accepted": 1, "duplicates": 0, "offsets": [{"partition": 3, "offset": 91204}]}
```

Up to 10000 events per request, bounded by a 32 MiB body limit.

### `GET /v1/streams/{name}/events`

| Parameter | Default | Description |
| --- | --- | --- |
| `key` | none | Restrict to one partition key |
| `from` | `earliest` | `earliest`, `latest` or a timestamp |
| `limit` | `100` | Maximum 10000 |
| `cursor` | none | Opaque paging cursor |

### `GET /v1/streams/{name}/events/stream`

Server-sent events. The connection stays open and each event arrives as a `data:` frame. Send
`Last-Event-ID` to resume from a known offset after a disconnect.

## Queries

### `POST /v1/query`

```json
{"kql": "SELECT count(*) FROM orders WHERE time > now() - 1h", "limit": 1000}
```

```json
{
  "columns": ["count"],
  "rows": [[4218]],
  "stats": {"scanned_bytes": 184320000, "segments": 12, "duration_ms": 214},
  "cursor": null
}
```

Add `"explain": true` to receive the plan instead of results.

## Rate limiting

| Header | Meaning |
| --- | --- |
| `X-RateLimit-Limit` | Requests allowed in the window |
| `X-RateLimit-Remaining` | Requests left |
| `X-RateLimit-Reset` | Unix seconds when the window resets |
| `Retry-After` | Seconds to wait, on `429` |

## Status codes

| Code | Meaning |
| --- | --- |
| 200, 201, 204 | Success |
| 400 | Malformed request or invalid KQL |
| 401, 403 | Authentication or authorisation failure |
| 404 | No such stream |
| 409 | Conflict, such as creating an existing stream |
| 413 | Payload too large |
| 421 | Wrong node; retry against the leader |
| 429 | Quota or backpressure |
| 503 | No quorum, or the node is not ready |
| 504 | Query timed out |

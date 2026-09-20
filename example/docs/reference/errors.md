# Error codes

Every error carries a stable string code and a numeric `KGR-xxxx` identifier. The string is what
you should match on in code; the number is for support tickets.

## Authentication and authorisation

| Code | Number | HTTP | Cause and fix |
| --- | --- | --- | --- |
| `ERR_UNAUTHENTICATED` | KGR-4010 | 401 | No credential presented. Send `X-API-Key` or a bearer token |
| `ERR_KEY_REVOKED` | KGR-4011 | 401 | The key was revoked. Issue a new one |
| `ERR_KEY_EXPIRED` | KGR-4012 | 401 | The key passed its TTL. Rotate it |
| `ERR_TOKEN_REUSE` | KGR-4013 | 401 | A refresh token was used twice. The chain is revoked; re-authenticate |
| `ERR_TOKEN_NOT_YET_VALID` | KGR-4014 | 401 | `nbf` is in the future. Check NTP on the issuer |
| `ERR_FORBIDDEN` | KGR-4030 | 403 | Valid credential, missing scope. Grant `read:` or `write:` on the stream |
| `ERR_TENANT_MISMATCH` | KGR-4031 | 403 | The resource belongs to another tenant |

## Ingest

| Code | Number | HTTP | Cause and fix |
| --- | --- | --- | --- |
| `ERR_PAYLOAD_TOO_LARGE` | KGR-4130 | 413 | Above `ingest.max_payload_bytes`. Split it or store a reference |
| `ERR_SCHEMA_MISMATCH` | KGR-4220 | 422 | The event violates the compatibility mode. See the named field |
| `ERR_QUOTA_EXCEEDED` | KGR-4290 | 429 | Tenant quota reached. Raise the quota or slow down |
| `ERR_BACKPRESSURE` | KGR-4291 | 429 | Storage cannot keep up. Honour `Retry-After` |
| `ERR_DUPLICATE_EVENT` | KGR-2000 | 200 | Informational: an idempotency key was replayed and the write was dropped |

## Query

| Code | Number | HTTP | Cause and fix |
| --- | --- | --- | --- |
| `ERR_QUERY_TIMEOUT` | KGR-5040 | 504 | Exceeded `query.timeout`. Narrow the time range |
| `ERR_QUERY_TOO_EXPENSIVE` | KGR-4001 | 400 | Exceeded `query.max_scanned_bytes`. Add a `time` predicate |
| `ERR_CURSOR_EXPIRED` | KGR-4002 | 400 | Older than `query.cursor_ttl`. Restart the page walk |
| `ERR_SYNTAX` | KGR-4003 | 400 | KQL parse failure. The message gives line and column |

## Storage and cluster

| Code | Number | HTTP | Cause and fix |
| --- | --- | --- | --- |
| `ERR_DISK_FULL` | KGR-5070 | 507 | Below `storage.min_free_bytes`. Compact, shorten retention, or tier to S3 |
| `ERR_TOO_MANY_OPEN_FILES` | KGR-5071 | 503 | Raise `ulimit -n` to 65536 |
| `ERR_NO_QUORUM` | KGR-5030 | 503 | A majority of nodes is unreachable. Restore quorum before anything else |
| `ERR_NOT_LEADER` | KGR-4211 | 421 | Write sent to a follower. Clients redirect automatically; a proxy may not |
| `ERR_SEGMENT_CORRUPT` | KGR-5001 | 500 | Checksum failure. The node self-heals from a peer, or restore from backup |

## Client and configuration

| Code | Number | Cause and fix |
| --- | --- | --- |
| `ERR_CONNECTION_REFUSED` | KGR-6001 | Nothing is listening, or the WAL is still replaying |
| `ERR_CONNECTION_RESET` | KGR-6002 | The connection dropped mid-request. Usually an idle timeout on a proxy |
| `ERR_CONFIG_UNRESOLVED` | KGR-6010 | A `${VAR}` in the config file has no value in the environment |
| `ERR_CONFIG_UNKNOWN_KEY` | KGR-6011 | Unknown key under `--strict`. Check for a typo |

## Reading an error response

```json
{
  "error": {
    "code": "ERR_QUOTA_EXCEEDED",
    "number": "KGR-4290",
    "message": "tenant team-a exceeded events_per_second",
    "details": {"limit": 50000, "current": 61240, "window": "1s"},
    "retry_after_ms": 1200
  }
}
```

Match on `code`. The `message` is written for humans and is not covered by any stability
guarantee.

# CLI reference

## Global flags

| Flag | Default | Description |
| --- | --- | --- |
| `--config` | `/etc/kagura/kagura.yaml` | Configuration file path |
| `--profile` | none | Profile to merge over the base configuration |
| `--endpoint` | `http://localhost:7700` | Server address for client commands |
| `--api-key` | `$KAGURA_API_KEY` | Credential for client commands |
| `--format` | `table` | `table`, `json`, `ndjson` or `csv` |
| `--log-level` | `info` | `debug`, `info`, `warn` or `error` |
| `--timeout` | `30s` | Client request timeout |
| `--no-color` | off | Disable ANSI colour |

## kagura serve

Runs the server.

```bash
kagura serve --data-dir /var/lib/kagura --listen 0.0.0.0:7700
```

| Flag | Default | Description |
| --- | --- | --- |
| `--data-dir` | `/var/lib/kagura` | Storage directory |
| `--listen` | `0.0.0.0:7700` | Client API address |
| `--admin-listen` | `127.0.0.1:7701` | Admin and metrics address |
| `--peer-listen` | `0.0.0.0:7702` | Raft peer address |
| `--dev` | off | No auth, single node, no fsync. Never in production |
| `--stop` | off | Graceful shutdown of a running instance |
| `--experimental-fast-index` | off | Parallel index build on startup |

## kagura stream

```bash
kagura stream create orders --partitions 8 --retention 30d
kagura stream alter orders --retention 90d
kagura stream list
kagura stream describe orders
kagura stream delete orders --confirm
kagura stream mirror orders --to orders-v2 --transform ./transform.kql
```

| Flag | Applies to | Description |
| --- | --- | --- |
| `--partitions` | create | Partition count, increase only |
| `--retention` | create, alter | Duration such as `30d` |
| `--retention-bytes` | create, alter | Size cap such as `500GiB` |
| `--cleanup-policy` | create, alter | `delete` or `compact` |
| `--schema` | create | Avro or JSON Schema file |
| `--confirm` | delete | Required; deletion is irreversible |

## kagura events

```bash
kagura events put orders --key order-1001 --data @order.json
kagura events get orders --key order-1001 --limit 10
kagura events tail orders --from latest --group analytics
kagura events erase orders --key customer-42 --confirm
```

| Flag | Description |
| --- | --- |
| `--key` | Partition key |
| `--data` | Inline JSON, or `@file` |
| `--idempotency-key` | Deduplicates within `ingest.dedup_window` |
| `--from` | `earliest`, `latest` or an RFC 3339 timestamp |
| `--group` | Consumer group for offset tracking |

## kagura query

```bash
kagura query --file ./q.kql --format table
kagura query --file ./q.kql --explain
kagura query --file ./q.kql --follow
kagura query --file ./q.kql --limit 1000 --cursor-out ./cursor
```

| Flag | Description |
| --- | --- |
| `--file`, `-f` | KQL file. Use `-` for stdin |
| `--explain` | Print the plan without executing |
| `--follow` | Stream results as events arrive |
| `--limit` | Maximum rows |
| `--cursor-in`, `--cursor-out` | Resume and save paging state |
| `--max-scanned-bytes` | Override the per-query scan cap |

## kagura compact

```bash
kagura compact accounts --parallelism 4 --dry-run
```

| Flag | Default | Description |
| --- | --- | --- |
| `--compaction-parallelism` | `2` | Segments compacted concurrently |
| `--compaction-min-dirty-ratio` | `0.5` | Skip segments below this superseded ratio |
| `--dry-run` | off | Report reclaimable space and exit |

## kagura backup, kagura restore

```bash
kagura backup create --dest s3://kagura-backups/2026-08-14 --compress zstd --rate-limit 50MiB/s
kagura backup verify --from s3://kagura-backups/2026-08-14
kagura backup replicate --from s3://a/ --to s3://b/ --region us-east-1
kagura restore --from s3://kagura-backups/2026-08-14 --to-time "2026-08-14T09:00:00Z"
```

## kagura auth

```bash
kagura auth key create --name checkout-api --scopes write:orders --ttl 90d
kagura auth key revoke checkout-api
kagura auth whoami --api-key kgr_live_...
kagura auth service-account create etl-nightly --scopes read:orders
```

## kagura cluster

```bash
kagura cluster status
kagura cluster add kagura-2 --address kagura-2.internal:7702
kagura cluster remove kagura-2
kagura cluster step-down
```

## kagura config

```bash
kagura config dump --effective --show-origin
kagura config validate --file ./kagura.yaml --strict
kagura config reload
```

## kagura bench

```bash
kagura bench write --stream bench --events 1000000 --payload-size 512 --concurrency 16
kagura bench read --stream bench --queries 1000 --concurrency 8
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Generic failure |
| 2 | Invalid usage or configuration |
| 3 | Authentication or authorisation failure |
| 4 | Server unreachable |
| 5 | Timed out |

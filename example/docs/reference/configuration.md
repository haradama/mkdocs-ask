# Configuration keys

Reloadable keys take effect on `SIGHUP` or `kagura config reload`. The rest need a restart.

## server

| Key | Type | Default | Reloadable | Description |
| --- | --- | --- | --- | --- |
| `server.listen` | address | `0.0.0.0:7700` | no | Client API bind address |
| `server.admin_listen` | address | `127.0.0.1:7701` | no | Admin and metrics bind address |
| `server.peer_listen` | address | `0.0.0.0:7702` | no | Raft peer bind address |
| `server.timeout` | duration | `30s` | yes | Per-request timeout |
| `server.max_connections` | int | `4096` | yes | Concurrent client connections |
| `server.tls.cert_file` | path | none | yes | Server certificate |
| `server.tls.key_file` | path | none | yes | Private key |
| `server.tls.min_version` | string | `1.3` | yes | Minimum TLS version |
| `server.tls.client_auth` | enum | `none` | yes | `none`, `request` or `require` |

## storage

| Key | Type | Default | Reloadable | Description |
| --- | --- | --- | --- | --- |
| `storage.data_dir` | path | `/var/lib/kagura` | no | Data directory |
| `storage.backend` | enum | `local` | no | `local` or `s3` |
| `storage.fsync` | enum | `always` | yes | `always`, `interval` or `never` |
| `storage.fsync_interval` | duration | `100ms` | yes | Used when `fsync: interval` |
| `storage.segment_bytes` | size | `256MiB` | yes | Segment roll size |
| `storage.min_free_bytes` | size | `5GiB` | yes | Refuse writes below this |
| `storage.page_cache_bytes` | size | `2GiB` | no | Internal page cache |
| `storage.index_cache_bytes` | size | `512MiB` | no | Segment index cache |
| `storage.encryption.enabled` | bool | `false` | no | AES-256-GCM at rest |
| `storage.encryption.key_id` | string | none | no | KMS key identifier |
| `storage.s3.bucket` | string | none | no | Bucket for tiered storage |
| `storage.s3.region` | string | none | no | Bucket region |
| `storage.s3.tier_after` | duration | `7d` | yes | Age at which segments move to S3 |

## ingest

| Key | Type | Default | Reloadable | Description |
| --- | --- | --- | --- | --- |
| `ingest.batch_size` | int | `500` | yes | Server-side batch accumulation |
| `ingest.linger_ms` | int | `20` | yes | How long a partial batch waits |
| `ingest.max_payload_bytes` | size | `1MiB` | yes | Per-event payload limit |
| `ingest.max_inflight_batches` | int | `5` | yes | Concurrent batches per partition |
| `ingest.dedup_window` | duration | `24h` | yes | Idempotency key memory |
| `ingest.compression` | enum | `none` | yes | `none`, `gzip` or `zstd` |
| `ingest.compression_level` | int | `3` | yes | Codec level |
| `ingest.dead_letter` | bool | `false` | yes | Route rejects to `<stream>.dlq` |

## query

| Key | Type | Default | Reloadable | Description |
| --- | --- | --- | --- | --- |
| `query.timeout` | duration | `60s` | yes | Abort with `ERR_QUERY_TIMEOUT` |
| `query.max_scanned_bytes` | size | `10GiB` | yes | Abort with `ERR_QUERY_TOO_EXPENSIVE` |
| `query.max_concurrent` | int | `16` | yes | Per-tenant concurrency |
| `query.cursor_ttl` | duration | `15m` | yes | Paging cursor validity |
| `query.allowed_lateness` | duration | `1h` | yes | Late events still land in their window |
| `query.slow_log_threshold` | duration | `5s` | yes | Log the plan above this |

## cluster

| Key | Type | Default | Reloadable | Description |
| --- | --- | --- | --- | --- |
| `cluster.node_id` | string | hostname | no | Stable node identity |
| `cluster.peers` | list | `[]` | no | Peer addresses |
| `cluster.election_timeout` | duration | `1s` | yes | Raft election timeout |
| `cluster.heartbeat_interval` | duration | `100ms` | yes | Leader heartbeat period |
| `cluster.snapshot_interval` | int | `100000` | yes | Log entries between snapshots |

## auth

| Key | Type | Default | Reloadable | Description |
| --- | --- | --- | --- | --- |
| `auth.provider` | enum | `api_key` | yes | `none`, `api_key`, `oauth`, `jwt` or `mtls` |
| `auth.jwt.issuer` | url | none | yes | Expected `iss` |
| `auth.jwt.audience` | string | none | yes | Expected `aud` |
| `auth.jwt.jwks_url` | url | none | yes | Key set endpoint |
| `auth.jwt.jwks_refresh` | duration | `15m` | yes | Key set refresh period |
| `auth.mtls.client_ca` | path | none | yes | CA for client certificates |

## consumer

| Key | Type | Default | Reloadable | Description |
| --- | --- | --- | --- | --- |
| `consumer.rebalance_delay` | duration | `5s` | yes | Settling time before rebalance |
| `consumer.session_timeout` | duration | `30s` | yes | Member considered dead after this |
| `consumer.max_poll_records` | int | `500` | yes | Records per poll |

## compaction

| Key | Type | Default | Reloadable | Description |
| --- | --- | --- | --- | --- |
| `compaction.parallelism` | int | `2` | yes | Concurrent segment compactions |
| `compaction.min_dirty_ratio` | float | `0.5` | yes | Threshold to compact a segment |
| `compaction.tombstone_retention` | duration | `24h` | yes | How long deletions stay visible |

## telemetry and logging

| Key | Type | Default | Reloadable | Description |
| --- | --- | --- | --- | --- |
| `log.level` | enum | `info` | yes | `debug`, `info`, `warn` or `error` |
| `log.format` | enum | `json` | yes | `json` or `text` |
| `telemetry.otlp_endpoint` | url | none | yes | OTLP collector |
| `telemetry.sample_ratio` | float | `0.01` | yes | Trace sampling ratio |
| `audit.sink` | enum | `file` | yes | `file` or `otlp` |

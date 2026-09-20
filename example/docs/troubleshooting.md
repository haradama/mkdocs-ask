# Troubleshooting

Organised by symptom, because that is what you have when something breaks.

## The server will not start

Check the exit code and the last log line first.

| Symptom | Cause | Fix |
| --- | --- | --- |
| Exits with code 2 immediately | Invalid configuration | `kagura config validate --strict` |
| `ERR_CONFIG_UNRESOLVED` | A `${VAR}` has no value | Export it, or use a `*_file` key |
| Hangs for minutes, then serves | Replaying the write-ahead log | Normal after an unclean stop; watch `kagura_wal_replay_progress` |
| `ERR_TOO_MANY_OPEN_FILES` | File descriptor limit | `ulimit -n 65536`, or `LimitNOFILE` in the unit file |
| `bind: address already in use` | Port taken | `ss -lptn 'sport = :7700'` |

A node that starts but never becomes ready is usually waiting for quorum. Check
`kagura cluster status` from another node.

## Writes are being rejected

| Error | Meaning | First thing to try |
| --- | --- | --- |
| `ERR_BACKPRESSURE` | Storage cannot keep up | Look at `kagura_storage_fsync_seconds` |
| `ERR_QUOTA_EXCEEDED` | Tenant quota | `kagura tenant quota show <tenant>` |
| `ERR_DISK_FULL` | Below `storage.min_free_bytes` | See "Running out of disk space" below |
| `ERR_SCHEMA_MISMATCH` | Payload violates the schema | `kagura schema check` against the new payload |
| `ERR_NOT_LEADER` | Sent to a follower | Usually a proxy that pins connections |

## The same event is processed more than once

Expected behaviour under at-least-once delivery. Fixes, in order of preference:

1. Pass an `idempotency_key` that is stable across retries. Generating a fresh UUID per attempt
   is the usual mistake and makes deduplication impossible.
2. Confirm the repeats fall inside `ingest.dedup_window`, 24 hours by default. Older repeats are
   accepted as new events by design.
3. For deduplication over longer periods, enforce uniqueness in the consumer.

See [Ingesting events](guide/ingestion.md) for the mechanics.

## Running out of disk space

Writes stop at `storage.min_free_bytes` so compaction still has room to run.

```bash
kagura stream list --sort-by bytes --limit 10     # find the biggest streams
kagura compact <stream> --dry-run                 # see what is reclaimable now
kagura stream alter <stream> --retention 7d       # shorten the window
kagura storage tier --stream <stream> --older-than 7d
```

Adding disk is the last resort, not the first. Without a retention change you will be back in
the same place.

## Queries are slow

1. Run `kagura query --explain` and read `segments`. A number close to the total means the time
   predicate is not pruning.
2. Add or tighten the `time` bound.
3. Check `kagura_query_tier_reads_total`. Reading from S3 is roughly ten times slower than local
   disk, so a query reaching into cold data will always feel slow.
4. For a repeated dashboard query, build a projection.

## Write latency spikes

| Pattern | Likely cause | Check |
| --- | --- | --- |
| Regular, every few minutes | Compaction competing for disk | `kagura_compaction_active` |
| Irregular, seconds long | Raft leader elections | `kagura_raft_leader_changes_total` |
| Constant and high | fsync on a slow disk | `kagura_storage_fsync_seconds` |
| Nightly | An unthrottled backup | Set `--rate-limit` on the backup job |

Leader elections across availability zones usually mean `cluster.election_timeout` is too low
for the inter-zone round trip.

## Memory keeps growing

The dedup table is the usual culprit: roughly 80 bytes per remembered key per partition, held
for `ingest.dedup_window`. Many partitions plus a wide window plus high-cardinality keys adds
up quickly.

```bash
kagura metrics get kagura_dedup_entries
```

Shorten the window, or reduce the partition count on new streams.

## Connections drop mid-request

`ERR_CONNECTION_RESET` during long queries or a live tail is almost always an idle timeout on a
load balancer or proxy, not on Kagura. Raise the proxy's idle timeout above
`query.timeout`, and enable TCP keepalive on the client.

## A node will not rejoin the cluster

```bash
kagura cluster status
```

| Output | Meaning | Fix |
| --- | --- | --- |
| `state: candidate` looping | Cannot reach a majority | Check port 7702 between nodes |
| `state: follower, lag: growing` | Cannot keep up | Faster disk or network; consider re-seeding |
| `ERR_SEGMENT_CORRUPT` at startup | Damaged local data | Remove the node, wipe its directory, add it back |

Never copy a data directory from another node to bootstrap. It duplicates the node identity and
corrupts Raft state.

## Collecting diagnostics for a bug report

```bash
kagura diagnose --output ./kagura-diag.tar.gz --since 1h
```

Includes configuration with secrets redacted, recent logs, metrics snapshots, cluster state and
segment metadata. It does not include event payloads.

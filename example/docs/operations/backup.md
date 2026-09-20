# Backup and restore

## What a backup contains

A backup holds segments, the Raft snapshot, stream metadata and schemas. It does not hold
configuration or credentials, so keep `kagura.yaml` and your secrets under separate version
control.

## Snapshots

```bash
kagura backup create --dest s3://kagura-backups/$(date +%F) --compress zstd
```

Snapshots are consistent without stopping writes: Kagura pins a segment set and copies it while
new events continue to land in later segments.

| Flag | Default | Notes |
| --- | --- | --- |
| `--streams` | all | Comma-separated subset |
| `--compress` | `none` | `none`, `gzip` or `zstd` |
| `--parallelism` | `4` | Concurrent segment uploads |
| `--rate-limit` | unlimited | For example `50MiB/s`, to protect production traffic |

Set `--rate-limit` on a busy cluster. An unthrottled backup saturates the same disk that is
serving reads, and the symptom is a query latency spike every night at the same time.

## Scheduling

```yaml
backup:
  schedule: "0 3 * * *"
  dest: s3://kagura-backups/
  retain: 14
  rate_limit: 50MiB/s
```

`retain` prunes older backups after a successful new one, never before, so a failing backup job
cannot quietly erase your history.

## Point-in-time restore

Continuous WAL archiving allows restore to a chosen moment.

```yaml
backup:
  wal_archive: s3://kagura-wal/
  wal_archive_interval: 60s
```

Recovery point objective equals `wal_archive_interval`, so the default risks losing up to one
minute of events.

```bash
kagura restore --from s3://kagura-backups/2026-08-14 \
               --wal s3://kagura-wal/ \
               --to-time "2026-08-14T09:00:00Z" \
               --data-dir /var/lib/kagura
```

## Restore procedure

1. Stop every node. Restoring into a live cluster corrupts Raft state.
2. Restore onto one node and start it alone.
3. Verify with `kagura health` and a query against a known stream.
4. Wipe the data directories of the other nodes and re-add them, so they rebuild from the
   restored leader.

Restoring onto each node independently produces divergent Raft logs and a cluster that will not
form quorum.

## Verifying backups

```bash
kagura backup verify --from s3://kagura-backups/2026-08-14
# segments: 1284 ok  checksums: ok  metadata: ok  wal_continuity: ok
```

Run verification on a schedule. An unverified backup is a hypothesis, and `wal_continuity` in
particular catches a gap in archiving that would otherwise only be discovered during a restore.

## Cross-region copies

```bash
kagura backup replicate --from s3://kagura-backups/ --to s3://kagura-backups-dr/ --region us-east-1
```

Server-side copy where the provider supports it, so the data does not transit your cluster.

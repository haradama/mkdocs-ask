# Retention and compaction

Nothing is deleted by default. Left alone, a stream grows until the disk fills, so every
production stream needs a retention policy.

## Time-based retention

```bash
kagura stream alter orders --retention 30d
```

Segments whose newest event is older than the window become eligible for deletion. Deletion runs
per segment, not per event, so the effective retention is slightly longer than configured, by up
to one segment roll.

## Size-based retention

```bash
kagura stream alter orders --retention-bytes 500GiB
```

The oldest segments are dropped once the stream exceeds the limit. When both policies are set,
whichever triggers first wins.

## Compaction

Compaction keeps only the most recent event per key, turning a stream of changes into a
snapshot of current state. It suits a stream of entity updates and destroys an audit log, so
choose deliberately.

```bash
kagura stream alter accounts --cleanup-policy compact
kagura compact accounts --parallelism 4
```

| Flag | Default | Notes |
| --- | --- | --- |
| `--compaction-parallelism` | `2` | Segments compacted at once; each needs its own read buffer |
| `--compaction-min-dirty-ratio` | `0.5` | Skips a segment until half of it is superseded |
| `--dry-run` | off | Reports what would be reclaimed and exits |

A deletion is expressed as a tombstone, an event with a null payload. Tombstones survive for
`compaction.tombstone_retention`, 24 hours by default, so consumers that were offline still see
that the key was deleted.

## When the disk fills up

Running out of space is the most common way a healthy cluster stops accepting writes. Kagura
refuses writes at `storage.min_free_bytes`, 5 GiB by default, so that compaction itself still
has room to work, and returns `ERR_DISK_FULL`.

Recovering, in the order to try:

1. `kagura compact <stream> --dry-run` to see what is reclaimable now.
2. Shorten the retention window on the largest stream, which takes effect on the next sweep.
3. `kagura storage tier --stream <stream> --older-than 7d` to move cold segments to S3.
4. Only then add disk.

Forecast the problem instead of reacting to it: `kagura_storage_free_bytes` with a one-week
linear prediction gives about a week of warning.

## Tiered storage

```yaml
storage:
  backend: s3
  s3:
    bucket: kagura-cold
    region: ap-northeast-1
    tier_after: 7d
```

Segments older than `tier_after` move to object storage. Queries still read them transparently,
at roughly ten times the latency of local disk, so keep any window you query interactively on
local storage.

## Deleting data on request

Regulatory deletion of one key is not what retention is for. Use:

```bash
kagura events erase orders --key customer-42 --confirm
```

This rewrites the affected segments to physically remove the payloads, and it is irreversible.
It cannot run while compaction is in progress on the same stream.

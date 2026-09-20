# Performance tuning

Measure first. Almost every tuning mistake comes from changing a setting whose effect was never
observed.

## Benchmarking

```bash
kagura bench write --stream bench --events 1000000 --payload-size 512 --concurrency 16
kagura bench read  --stream bench --queries 1000 --concurrency 8
```

Benchmark against a disk identical to production. Results on a cloud instance with a burst
credit budget are meaningless once the credits run out.

## Partition count

Partitions bound write parallelism. The usual rule:

```text
partitions = ceil(target_events_per_second / 20000)
```

rounded up to a multiple of your consumer count. More partitions is not free: each one needs
file descriptors, memory for the dedup table and its own compaction work. Above roughly
1000 partitions per node, metadata overhead starts to dominate.

Partitions can be added but never removed, so start slightly low and grow.

## Durability and fsync

| `storage.fsync` | Durability | Relative throughput |
| --- | --- | --- |
| `always` | Survives sudden power loss | 1x |
| `interval` | Loses up to `fsync_interval` | 3x to 5x |
| `never` | Loses the OS page cache | 8x or more |

`interval` with `fsync_interval: 100ms` is a reasonable middle ground on replicated clusters,
because a quorum makes single-node loss survivable. `never` belongs in benchmarks only.

## Batch sizing

| Setting | Effect of raising it |
| --- | --- |
| `ingest.batch_size` | More throughput, more memory per producer |
| `ingest.linger_ms` | More throughput, higher write latency |
| `ingest.max_inflight_batches` | More throughput, weaker ordering under retry |

Batches of 500 to 2000 events cover most workloads. Beyond that, gains flatten while the tail
latency of an individual write keeps rising.

## Memory

```yaml
storage:
  page_cache_bytes: 8GiB
  index_cache_bytes: 1GiB
```

Leave at least a quarter of system memory to the OS page cache, which is what makes recent
segment reads fast. Oversizing the internal caches starves it and makes reads slower, not
faster, which is a counter-intuitive failure mode worth remembering.

## Query performance

- Always bound `time`. Without it, segment pruning cannot happen and the query reads everything.
- Use a projection for anything a dashboard refreshes on a timer.
- Check `--explain` and look at `segments`, not at estimated bytes.
- Prefer `count_distinct` over exporting rows to deduplicate them elsewhere.

## Common bottlenecks

| Symptom | Likely cause | Check |
| --- | --- | --- |
| Write latency spikes every few minutes | Compaction competing for disk | `kagura_compaction_active` |
| Write latency spikes at random | Raft leader elections | `kagura_raft_leader_changes_total` |
| Steady high write latency | fsync on a slow disk | `kagura_storage_fsync_seconds` |
| Slow queries on old data | Reading tiered S3 segments | `kagura_query_tier_reads_total` |
| Memory climbing over days | Dedup window too wide | `kagura_dedup_entries` |

## Scaling limits per node

| Dimension | Comfortable | Hard limit |
| --- | --- | --- |
| Events per second | 200k | 500k |
| Partitions | 1000 | 4096 |
| Streams | 500 | 2000 |
| Concurrent queries | 16 | 64 |

Past the comfortable column, add nodes rather than pushing one harder.

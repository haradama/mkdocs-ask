# FAQ

## Is Kagura a message queue?

No. A queue deletes a message once it is consumed; Kagura keeps events until retention removes
them, and any number of consumer groups can read the same data independently at their own pace.

## Can I update or delete a single event?

Not in the ordinary course of things. Streams are append-only. Write a new event with the same
key and use [compaction](guide/retention.md) if you want the latest value to win. For
regulatory deletion, `kagura events erase` physically rewrites the affected segments.

## How many partitions should I create?

Start from the target write rate divided by 20000 events per second, rounded up to a multiple of
your consumer count. You can add partitions later but never remove them, so err low.

## Can I reduce the partition count?

No. Doing so would break the guarantee that a key always maps to the same partition. Create a
new stream and mirror into it.

## Why is my consumer lagging even though the cluster looks idle?

Usually a partition and consumer mismatch. A consumer group cannot use more consumers than there
are partitions, so the extras sit idle while the others carry everything.

## Does Kagura need ZooKeeper or etcd?

No. Cluster membership and leader election use built-in Raft, so the only thing to operate is
Kagura itself.

## Can I run a single node in production?

You can, and you will lose data when that node's disk fails. Use three nodes, or accept the
recovery point your backup schedule gives you.

## What happens when the disk fills up?

Writes are refused with `ERR_DISK_FULL` once free space falls below `storage.min_free_bytes`,
which exists so compaction still has room to work. Reads keep working. See
[Retention and compaction](guide/retention.md).

## Are queries consistent?

A query sees a consistent snapshot as of its start. Events arriving during execution are not
included, except with `--follow`.

## How long are idempotency keys remembered?

For `ingest.dedup_window`, 24 hours by default. Beyond that a repeat is accepted as a new event.

## Can two streams share an idempotency key?

Yes. Keys are scoped per stream and per partition, so the same value in two streams is not a
duplicate.

## Is there a managed service?

No. Kagura is self-hosted only.

## Which clients exist?

Official SDKs for Python, Go, TypeScript and Java. Everything else can use the
[REST API](reference/rest-api.md), which the SDKs are built on.

## How do I upgrade across several versions?

One minor version at a time: 2.2 to 2.3 to 2.4. Skipping is unsupported because the segment
format migration only knows how to step forward once.

## Can I downgrade?

Not after a node writes segments in the newer format. Restore from a backup taken before the
upgrade.

## Does anything leave my network?

No. Kagura makes no outbound connections except to the object storage you configure and the
telemetry endpoint you configure.

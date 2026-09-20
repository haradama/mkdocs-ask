# Kagura

Kagura is a self-hosted engine for ingesting events and querying them. Producers append events
to a stream, Kagura persists them with per-key ordering, and you read them back with KQL, a
SQL-like query language, or by subscribing to a live tail.

!!! note "About this site"
    These are the docs of a fictional product, used to demonstrate the `mkdocs-ask` plugin.
    Press `?` or use the **Ask** button in the corner to query the whole site in plain language.

## What it does

- **Ingest.** One event at a time or batches of tens of thousands, with idempotency keys so
  retries do not store the same event twice.
- **Query.** Filters, time windows and aggregations in KQL, with cursor-based paging.
- **Retain.** Time-based and size-based retention, key compaction, and tiering to S3.
- **Operate.** Raft-backed high availability, snapshots and point-in-time restore, Prometheus
  metrics and OpenTelemetry traces.

## How the pieces fit

```text
   producers                      Kagura cluster                      consumers
+---------------+        +-----------------------------+        +----------------+
| SDK / REST    | -----> |  ingest --> WAL --> segments | -----> | KQL queries    |
| kagura CLI    |        |              ^        |      |        | live tail      |
+---------------+        |           raft log    v      |        | projections    |
                         |                  tiered S3   |        +----------------+
                         +-----------------------------+
```

## Start here

1. [Install](getting-started/install.md), one minute with Docker or a single binary.
2. [Quickstart](getting-started/quickstart.md), create a stream and push events through it.
3. [Concepts](getting-started/concepts.md), streams, partitions, offsets and delivery semantics.

## Versions

The current stable release is **v2.4.0**. See [Migrating from v1](migration.md) if you are still
on the v1.x line, which reaches end of support on 2026-12-31.

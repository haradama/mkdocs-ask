# Observability

## Metrics

Prometheus metrics are exposed on the admin port at `/metrics`.

| Metric | Type | What it tells you |
| --- | --- | --- |
| `kagura_ingest_events_total` | counter | Accepted events, by stream |
| `kagura_ingest_errors_total` | counter | Rejections, by `code` |
| `kagura_ingest_latency_seconds` | histogram | Write path, quorum ack included |
| `kagura_ingest_backpressure_seconds_total` | counter | Time spent applying backpressure |
| `kagura_query_duration_seconds` | histogram | Query wall time, by tenant |
| `kagura_query_scanned_bytes` | histogram | Bytes read per query |
| `kagura_storage_free_bytes` | gauge | Free space on the data volume |
| `kagura_segment_count` | gauge | Segments per stream |
| `kagura_raft_leader_changes_total` | counter | Leader elections |
| `kagura_raft_commit_latency_seconds` | histogram | Replication round trip |
| `kagura_consumer_lag_events` | gauge | Events behind, by group and partition |

```yaml
scrape_configs:
  - job_name: kagura
    static_configs:
      - targets: ["kagura-1.internal:7701"]
```

## Alerts worth having

```yaml
groups:
  - name: kagura
    rules:
      - alert: KaguraDiskFillingUp
        expr: predict_linear(kagura_storage_free_bytes[6h], 7*24*3600) < 0
        for: 30m
        annotations:
          summary: Data volume projected to fill within a week

      - alert: KaguraConsumerLagGrowing
        expr: deriv(kagura_consumer_lag_events[15m]) > 0 and kagura_consumer_lag_events > 100000
        for: 20m

      - alert: KaguraLeaderFlapping
        expr: increase(kagura_raft_leader_changes_total[15m]) > 3
```

Alert on lag that is *growing*, not on lag that is merely large. A consumer catching up after a
deploy is normal; a consumer falling further behind is not.

## Tracing

```yaml
telemetry:
  otlp_endpoint: http://collector.internal:4317
  sample_ratio: 0.01
```

Spans are emitted for ingest, query planning and query execution. A `trace_id` header on an
event is propagated, so a slow write can be followed back to the request that caused it.

Keep `sample_ratio` low on the ingest path. At high event rates, tracing every write costs more
than the write.

## Logs

Structured JSON by default, one object per line.

```json
{"ts":"2026-08-14T09:12:00Z","level":"warn","code":"ERR_BACKPRESSURE",
 "stream":"orders","partition":3,"wait_ms":420,"msg":"applying backpressure"}
```

Levels are `debug`, `info`, `warn` and `error`, set with `log.level` or `--log-level`, and
changeable at runtime:

```bash
kagura log level set debug --duration 5m
```

The duration matters: debug logging on a busy node produces gigabytes per hour, and the
automatic revert stops an investigation from becoming an incident.

## Audit log

Administrative actions are recorded separately, and the audit log cannot be disabled at runtime.

```bash
kagura audit tail --since 1h
# 2026-08-14T09:10:02Z  service-account/etl  stream.alter  orders  retention=30d
```

## Health endpoints

| Endpoint | Meaning | Use for |
| --- | --- | --- |
| `/health` | Process is up | Load balancer |
| `/health/ready` | Joined quorum, log replayed | Kubernetes readiness |
| `/health/live` | Not deadlocked | Kubernetes liveness |

Point readiness at `/health/ready`. Using `/health` sends traffic to a node that is still
replaying its write-ahead log, which surfaces to clients as sporadic timeouts.

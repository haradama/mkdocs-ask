# Migrating from v1

v1.x reaches end of support on 2026-12-31. Plan for roughly one day of work for a small cluster.

## What changed

| Area | v1 | v2 |
| --- | --- | --- |
| Coordination | External etcd | Built-in Raft |
| Config format | INI | YAML |
| Query language | `SELECT` over topics | KQL, with time windows |
| Auth | API keys only | API keys, OAuth, JWT, mTLS |
| Wire protocol | `/api/` | `/v1/` |
| Segment format | v1 | v2, not readable by v1 |
| Default port | 8080 | 7700 |

## Breaking changes

1. **Topics are now streams.** `kagura topic` becomes `kagura stream`, and `/api/topics`
   becomes `/v1/streams`.
2. **etcd is gone.** Remove `etcd.*` from the configuration. Cluster membership moves to
   `cluster.peers`.
3. **Configuration is YAML.** Convert with `kagura config convert --from-ini ./kagura.ini`.
4. **Offsets are per group.** v1 stored one offset per topic; v2 stores one per group and
   partition. Existing offsets are migrated into a group named `default`.
5. **`at_least_once: false` is gone.** Use an idempotency key instead.
6. **Payload limit dropped to 1 MiB** from 4 MiB. Raise `ingest.max_payload_bytes` if you
   genuinely need more.

## Procedure

### 1. Convert the configuration

```bash
kagura config convert --from-ini /etc/kagura/kagura.ini --out /etc/kagura/kagura.yaml
kagura config validate --file /etc/kagura/kagura.yaml --strict
```

### 2. Back up

```bash
kagura-v1 backup create --dest s3://kagura-backups/pre-v2
```

This is the only way back. A v2 node cannot read v1 segments in place, and a v1 node cannot read
what v2 writes.

### 3. Migrate data

```bash
kagura migrate from-v1 --source /var/lib/kagura-v1 --dest /var/lib/kagura --parallelism 4
```

Roughly 20 minutes per 100 GiB on NVMe. The source directory is opened read-only, so an aborted
migration loses nothing.

### 4. Start v2 and verify

```bash
kagura serve --data-dir /var/lib/kagura
kagura stream list
kagura query -f ./smoke.kql
```

Compare event counts per stream against the v1 numbers you recorded before migrating.

### 5. Move clients

Update the SDK, change the port from 8080 to 7700 and the path prefix from `/api/` to `/v1/`.

```python
# v1
client = Client("http://kagura:8080")
client.produce("orders", key="order-1001", value=payload)

# v2
client = Client("http://kagura:7700", api_key="kgr_live_...")
client.put("orders", key="order-1001", data=payload, idempotency_key="order-1001")
```

## Running both during the transition

```bash
kagura migrate mirror --from-v1 http://kagura-v1:8080 --stream orders --to orders
```

Mirroring is one-way, v1 to v2. Write to both from the application only if you can tolerate
divergence, because there is no conflict resolution.

## Rolling back

Point clients back at the v1 cluster, which is untouched by the migration. Anything written to
v2 after the cutover does not come back, so keep v1 running read-only until you are confident.
